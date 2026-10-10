"""test comment download and storage"""

from unittest import mock

import pytest
from video.models import Comment, CommentRefresh, Video
from video.src.comments import CommentList, Comments
from video.tests.conftest import set_config

pytestmark = pytest.mark.django_db

RAW = {
    "id": "c1",
    "parent": "root",
    "text": "nice\xa0ride",
    "timestamp": 1700000000,
    "author": "someone",
    "author_id": "UCsomeone",
    "author_thumbnail": "https://yt3.example/a",
    "like_count": 3,
    "is_favorited": True,
    "author_is_uploader": True,
}


def _raw(**overrides):
    return dict(RAW, **overrides)


def _patch_yt(info_json):
    """patch YtWrap.extract to return info_json"""
    patcher = mock.patch("video.src.comments.YtWrap")
    yt_wrap = patcher.start()
    yt_wrap.return_value.extract.return_value = (info_json, None)
    return patcher, yt_wrap


@pytest.fixture(name="enabled")
def fixture_enabled(app_config):
    del app_config
    return set_config(comment_max="10, 5", comment_sort="new")


def test_check_config_reads_stored(app_config):
    del app_config
    comments = Comments("vid00000001")
    comments.check_config()
    assert not comments.is_activated
    comments.build_json()
    assert not comments.json_data


def test_build_yt_obs(enabled):
    obs = Comments("x", config=enabled).build_yt_obs()
    assert obs["extractor_args"]["youtube"] == {
        "max_comments": ["10", "5"],
        "comment_sort": ["new"],
    }


def test_clean_comment(enabled):
    comments = Comments("x", config=enabled)
    assert not comments.clean_comment(_raw(text=""))

    cleaned = comments.clean_comment(_raw())
    assert cleaned["comment_text"] == "niceride"
    assert cleaned["comment_time_text"] == "2023-11-14 22:13"
    assert cleaned["comment_author_is_uploader"] is True

    midnight = comments.clean_comment(
        _raw(timestamp=1699920000, author=None, author_id="UCx")
    )
    assert midnight["comment_time_text"] == "2023-11-14"
    assert midnight["comment_author"] == "UCx"
    minimal = _raw()
    for key in ("like_count", "is_favorited", "author_is_uploader"):
        minimal.pop(key)
    cleaned = comments.clean_comment(minimal)
    assert cleaned["comment_likecount"] is None
    assert cleaned["comment_is_favorited"] is False
    assert cleaned["comment_author_is_uploader"] is False


def test_build_json_and_upload(enabled, video):
    patcher, yt_wrap = _patch_yt(
        {
            "channel_id": "UCchannel000000000000001",
            "comments": [_raw(), _raw(id="c2", parent="c1", text="")],
        }
    )
    try:
        comments = Comments(video.youtube_id, config=enabled)
        comments.build_json(upload=True)
    finally:
        patcher.stop()

    assert yt_wrap.return_value.extract.call_args.args == (video.youtube_id,)
    assert [c.comment_id for c in Comment.objects.all()] == ["c1"]
    assert Video.objects.get().comment_count == 1
    stored = Comments(video.youtube_id).get_comments()
    assert stored["comment_channel_id"] == "UCchannel000000000000001"
    assert stored["comment_last_refresh"] == comments.json_data[
        "comment_last_refresh"
    ]
    assert stored["comment_comments"][0]["comment_text"] == "niceride"


def test_build_json_no_info(enabled):
    patcher, _ = _patch_yt(None)
    try:
        comments = Comments("x", config=enabled)
        comments.build_json()
    finally:
        patcher.stop()
    assert not comments.json_data


def test_build_json_no_comments(enabled):
    patcher, _ = _patch_yt({"channel_id": "UC1"})
    try:
        comments = Comments("x", config=enabled)
        comments.build_json()
    finally:
        patcher.stop()
    assert comments.json_data["comment_comments"] == []


def test_delete_and_get_missing(enabled, video):
    comments = Comments(video.youtube_id, config=enabled)
    comments.json_data = {
        "comment_comments": [{"comment_id": "c1"}],
        "comment_last_refresh": 1700000000,
    }
    comments.upload_comments()
    assert Comment.objects.exists() and CommentRefresh.objects.exists()

    comments.delete_comments()
    assert not Comment.objects.exists()
    assert not CommentRefresh.objects.exists()
    assert comments.get_comments() is False


class TestReindex:
    """reindex keeps stored comments on empty results"""

    def _reindex(self, config, info_json):
        patcher, _ = _patch_yt(info_json)
        try:
            Comments("vid00000001", config=config).reindex_comments()
        finally:
            patcher.stop()

    def test_disabled(self, app_config):
        with mock.patch("video.src.comments.YtWrap") as yt_wrap:
            Comments("x", config=app_config).reindex_comments()
        yt_wrap.assert_not_called()

    def test_no_result(self, enabled):
        self._reindex(enabled, None)
        assert not CommentRefresh.objects.exists()

    def test_empty_keeps_stored(self, enabled, video):
        del video
        self._reindex(enabled, {"channel_id": "UC1", "comments": [_raw()]})
        self._reindex(enabled, {"channel_id": "UC1", "comments": []})
        assert Comment.objects.count() == 1


class TestCommentList:
    """queue and index comments in bulk"""

    def test_add_disabled(self, app_config, redis):
        del app_config
        CommentList().add(["a"])
        assert not redis.conn.keys(redis.NAME_SPACE + "*")

    def test_add_and_index(self, enabled, video, redis):
        del enabled
        task = mock.Mock()
        comment_list = CommentList(task=task)
        comment_list.add([video.youtube_id, "missing1234"])
        assert redis.conn.keys(redis.NAME_SPACE + "*")

        info = {
            video.youtube_id: {"channel_id": "UC1", "comments": [_raw()]},
            "missing1234": None,
        }
        with mock.patch("video.src.comments.YtWrap") as yt_wrap:
            yt_wrap.return_value.extract.side_effect = lambda i: (
                info[i],
                None,
            )
            comment_list.index()

        assert Comment.objects.get().video_id == video.youtube_id
        messages = [c.args[0][0] for c in task.send_progress.call_args_list]
        assert messages == [
            "Add comments for new videos 1/2",
            "Add comments for new videos 2/2",
        ]

    def test_index_without_task(self, enabled, redis):
        del enabled, redis
        comment_list = CommentList()
        comment_list.add(["missing1234"])
        with mock.patch("video.src.comments.YtWrap") as yt_wrap:
            yt_wrap.return_value.extract.return_value = (None, None)
            comment_list.index()
        yt_wrap.return_value.extract.assert_called_once()

"""tests for the video downloader and the post processing"""

import os
from datetime import datetime, timedelta, timezone

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisQueue
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download
from download.src import yt_dlp_handler
from download.src.yt_dlp_handler import (
    DownloaderBase,
    DownloadPostProcess,
    VideoDownloader,
)
from download.tests.helpers import FakeTask, set_config
from playlist.models import Playlist
from video.models import Video
from video.src.constants import VideoTypeEnum

CHANNEL_ID = "UCchannel000000000000001"


@pytest.fixture(name="dirs")
def fixture_dirs(cache_dir, monkeypatch):
    """downloader cache and media dirs in tmp_path"""
    media = cache_dir.parent / "media"
    monkeypatch.setattr(DownloaderBase, "CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(DownloaderBase, "MEDIA_DIR", str(media))
    return cache_dir, media


class FakeYtWrap:
    """YtWrap stand in: failures by id, else writes the media file"""

    failures: dict = {}
    calls: list = []

    def __init__(self, obs, config):
        self.obs = obs
        self.config = config

    def download(self, youtube_id):
        """write a fake mp4 and a thumbnail leftover into the cache"""
        FakeYtWrap.calls.append((youtube_id, self.obs))
        if youtube_id in self.failures:
            return False, self.failures[youtube_id]

        cache = os.path.dirname(self.obs["outtmpl"])
        with open(os.path.join(cache, f"{youtube_id}.mp4"), "wb") as f:
            f.write(b"media")
        with open(os.path.join(cache, f"{youtube_id}.webp"), "wb") as f:
            f.write(b"thumb")
        return True, True


def fake_index_new_video(youtube_id, video_type):
    """indexed video document"""
    assert isinstance(video_type, VideoTypeEnum)
    return {
        "youtube_id": youtube_id,
        "channel": {"channel_id": CHANNEL_ID},
        "media_url": f"{CHANNEL_ID}/{youtube_id}.mp4",
    }


@pytest.fixture(name="downloader")
def fixture_downloader(
    monkeypatch, app_config, redis_ns, dirs
):  # pylint: disable=unused-argument
    """fake yt-dlp, indexing and post processing"""
    FakeYtWrap.failures = {}
    FakeYtWrap.calls = []
    post_processed = []
    monkeypatch.setattr(yt_dlp_handler, "YtWrap", FakeYtWrap)
    monkeypatch.setattr(
        yt_dlp_handler, "index_new_video", fake_index_new_video
    )
    monkeypatch.setattr(
        DownloadPostProcess, "run", lambda self: post_processed.append(1)
    )
    return post_processed


def test_download_one(downloader, dirs):
    """downloads, indexes, moves to archive, leaves post processing"""
    _, media = dirs
    item = Download.upsert_doc(download_doc(youtube_id="ok"))
    task = FakeTask()

    assert VideoDownloader(task=task).download_one(item.to_doc())

    assert (media / CHANNEL_ID / "ok.mp4").read_bytes() == b"media"
    assert not Download.objects.exists()
    assert RedisQueue(DownloaderBase.VIDEO_QUEUE).get_all() == ["ok"]
    assert RedisQueue(DownloaderBase.CHANNEL_QUEUE).get_all() == [CHANNEL_ID]
    assert downloader == []
    assert "Processing Video: Queued video" in task.lines


def test_download_one_failed(downloader):
    """the error is stored on the item, which stays queued"""
    item = Download.upsert_doc(download_doc(youtube_id="bad"))
    FakeYtWrap.failures = {"bad": "Video unavailable"}

    assert not VideoDownloader(task=FakeTask()).download_one(item.to_doc())

    bad = Download.objects.get()
    assert (bad.youtube_id, bad.message) == ("bad", "Video unavailable")
    assert RedisQueue(DownloaderBase.VIDEO_QUEUE).get_all() == []


def test_notify_without_task(app_config):
    """nothing to notify"""
    VideoDownloader()._notify({"vid_type": "videos"}, "message")


@pytest.mark.parametrize(
    "response, expected",
    [
        (
            {
                "_total_bytes_str": "10MiB",
                "_percent_str": " 50.0%",
                "_speed_str": "1MiB/s",
                "_eta_str": "00:05",
                "eta": 5,
            },
            (
                " 50.0% of 10MiB at 1MiB/s - time left: 00:05",
                0.5,
                {"speed": "1MiB/s", "eta_seconds": 5},
            ),
        ),
        (
            {
                "_total_bytes_str": "N/A",
                "_total_bytes_estimate_str": "~12MiB",
                "_percent_str": "25%",
                "_speed_str": "1MiB/s",
                "_eta_str": "00:09",
            },
            (
                "25% of ~12MiB at 1MiB/s - time left: 00:09",
                0.25,
                {"speed": "1MiB/s", "eta_seconds": None},
            ),
        ),
        ({"_total_bytes_str": "10MiB"}, ("processing", False, None)),
    ],
)
def test_progress_hook(app_config, response, expected):
    """progress message from the yt-dlp hook"""
    response["info_dict"] = {"title": "A video"}
    task = FakeTask()
    VideoDownloader(task=task)._progress_hook(response)
    VideoDownloader()._progress_hook(response)

    message = {
        "lines": ["A video", expected[0]],
        "progress": expected[1],
        "level": "info",
    }
    if expected[2]:
        message["detail"] = expected[2]
    assert task.messages == [message]


def test_build_obs_user_config(app_config, dirs):
    """format, sort, speed limits and metadata postprocessor"""
    set_config(
        downloads={
            "format": "bestvideo+bestaudio",
            "format_sort": "res:1080, codec",
            "limit_speed": 100,
            "throttledratelimit": 50,
            "add_metadata": True,
        }
    )
    obs = VideoDownloader().obs

    assert obs["format"] == "bestvideo+bestaudio"
    assert obs["format_sort"] == ["res:1080", "codec"]
    assert obs["ratelimit"] == 100 * 1024
    assert obs["throttledratelimit"] == 50 * 1024
    assert obs["postprocessors"] == [
        {"key": "FFmpegMetadata", "add_chapters": True}
    ]
    assert obs["outtmpl"].startswith(str(dirs[0]))


def test_build_obs_defaults(app_config):
    """no user options"""
    obs = VideoDownloader().obs
    for key in ("format", "format_sort", "ratelimit", "throttledratelimit"):
        assert key not in obs
    assert obs["postprocessors"] == []


def test_dl_single_vid_overwrites_and_cleanup(downloader, dirs):
    """channel format overwrite, leftover thumbnails removed"""
    cache, _ = dirs
    Channel.save_doc(
        channel_doc(channel_overwrites={"download_format": "worst"})
    )
    handler = VideoDownloader()
    handler.obs["writethumbnail"] = True

    assert handler._dl_single_vid("vid1", CHANNEL_ID)
    assert FakeYtWrap.calls[0][1]["format"] == "worst"
    assert os.listdir(cache / "download") == ["vid1.mp4"]

    handler._dl_single_vid("vid2", "UCother")
    assert "format" not in FakeYtWrap.calls[1][1]


def test_move_to_archive_sets_owner(downloader, dirs, monkeypatch):
    """chown folder and file when host uid and gid are set"""
    cache, media = dirs
    monkeypatch.setattr(EnvironmentSettings, "HOST_UID", 1000)
    monkeypatch.setattr(EnvironmentSettings, "HOST_GID", 1001)
    chowned = []
    monkeypatch.setattr(
        yt_dlp_handler.os, "chown", lambda *args: chowned.append(args)
    )
    (cache / "download" / "vid1.mp4").write_bytes(b"media")
    (cache / "download" / "vid2.mp4").write_bytes(b"media")

    handler = VideoDownloader()
    handler.move_to_archive(fake_index_new_video("vid1", VideoTypeEnum.VIDEOS))
    handler.move_to_archive(fake_index_new_video("vid2", VideoTypeEnum.VIDEOS))

    folder = str(media / CHANNEL_ID)
    assert chowned == [
        (folder, 1000, 1001),
        (f"{folder}/vid1.mp4", 1000, 1001),
        (f"{folder}/vid2.mp4", 1000, 1001),
    ]


class FakeYoutubeVideo:
    """records media deletes and metadata embeds"""

    deleted: list = []
    embedded: list = []

    def __init__(self, youtube_id):
        self.youtube_id = youtube_id

    def delete_media_file(self):
        """record"""
        self.deleted.append(self.youtube_id)

    def embed_metadata(self):
        """record"""
        self.embedded.append(self.youtube_id)


class FakePendingList:
    """records ignore list additions"""

    calls: list = []

    def __init__(self, youtube_ids):
        self.youtube_ids = youtube_ids

    def parse_url_list(self, status):
        """record"""
        self.calls.append((self.youtube_ids, status))


class FakePlaylist:
    """YoutubePlaylist stand in, behaviour per id in FakePlaylist.data"""

    data: dict = {}
    matched: list = []
    updated: list = []

    def __init__(self, playlist_id):
        self.playlist_id = playlist_id
        self.json_data = None

    def update_playlist(self, skip_on_empty=False):
        """raise or set json_data"""
        self.updated.append((self.playlist_id, skip_on_empty))
        data = self.data.get(self.playlist_id)
        if isinstance(data, Exception):
            raise data
        self.json_data = data

    def get_from_db(self):
        """set json_data"""
        self.json_data = self.data.get(self.playlist_id)

    def match_local(self):
        """record"""
        self.matched.append(self.playlist_id)


class FakeChannel:
    """YoutubeChannel stand in, behaviour per id in FakeChannel.data"""

    data: dict = {}

    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.json_data = None
        self.all_playlists = []

    def get_from_db(self):
        """set json_data"""
        self.json_data = self.data.get(self.channel_id)

    def get_overwrites(self):
        """from json_data"""
        return self.json_data.get("channel_overwrites", {})

    def get_all_playlists(self):
        """playlist id and title tuples"""
        self.all_playlists = [("PLchan1", "one"), ("PLchan2", "two")]


class FakeCommentList:
    """records comment indexing"""

    calls: list = []

    def __init__(self, task):
        self.task = task
        self.video_ids = None

    def add(self, video_ids):
        """record"""
        self.video_ids = video_ids

    def index(self):
        """record"""
        self.calls.append(self.video_ids)


@pytest.fixture(name="post")
def fixture_post(monkeypatch, app_config, redis_ns):  # pylint: disable=W0613
    """fake remote calls of the post processing"""
    FakeYoutubeVideo.deleted = []
    FakeYoutubeVideo.embedded = []
    FakePendingList.calls = []
    FakePlaylist.data = {}
    FakePlaylist.matched = []
    FakePlaylist.updated = []
    FakeChannel.data = {}
    FakeCommentList.calls = []
    monkeypatch.setattr(yt_dlp_handler, "YoutubeVideo", FakeYoutubeVideo)
    monkeypatch.setattr(yt_dlp_handler, "PendingList", FakePendingList)
    monkeypatch.setattr(yt_dlp_handler, "YoutubePlaylist", FakePlaylist)
    monkeypatch.setattr(yt_dlp_handler, "YoutubeChannel", FakeChannel)
    monkeypatch.setattr(yt_dlp_handler, "CommentList", FakeCommentList)


def watched_video(youtube_id, days_ago, channel=None, **overrides):
    """indexed video watched days_ago"""
    watched = datetime.now(timezone.utc) - timedelta(days=days_ago)
    doc = video_doc(
        channel=channel,
        youtube_id=youtube_id,
        player={
            "duration": 100,
            "watched": True,
            "watched_date": int(watched.timestamp()),
        },
        **overrides,
    )
    return Video.save_doc(doc)


def test_auto_delete_all(post):
    """watched videos older than the cutoff, not overwritten channels"""
    Channel.save_doc(channel_doc())
    own = channel_doc(
        channel_id="UCown", channel_overwrites={"autodelete_days": 30}
    )
    Channel.save_doc(own)
    watched_video("old", 10, vid_type="shorts")
    watched_video("new", 1)
    watched_video("own", 10, channel=own)
    Video.save_doc(video_doc(youtube_id="unwatched"))

    DownloadPostProcess().auto_delete_all()
    assert not FakeYoutubeVideo.deleted

    set_config(downloads={"autodelete_days": 5})
    DownloadPostProcess().auto_delete_all()

    assert FakeYoutubeVideo.deleted == ["old"]
    assert FakePendingList.calls == [
        (
            [
                {
                    "type": "video",
                    "url": "old",
                    "vid_type": VideoTypeEnum.SHORTS,
                }
            ],
            "ignore",
        )
    ]


def test_auto_delete_overwrites(post):
    """per channel autodelete days"""
    own = channel_doc(
        channel_id="UCown", channel_overwrites={"autodelete_days": 3}
    )
    other = channel_doc(
        channel_id="UCother", channel_overwrites={"download_format": "worst"}
    )
    Channel.save_doc(own)
    Channel.save_doc(other)
    watched_video("old", 5, channel=own)
    watched_video("older", 9, channel=own)
    watched_video("recent", 1, channel=own)
    watched_video("other", 50, channel=other)

    DownloadPostProcess().auto_delete_overwrites()
    assert FakeYoutubeVideo.deleted == ["old", "older"]


def test_auto_delete_overwrites_nothing_old(post):
    """nothing to delete, ignore list not touched"""
    own = channel_doc(
        channel_id="UCown", channel_overwrites={"autodelete_days": 3}
    )
    Channel.save_doc(own)
    watched_video("recent", 1, channel=own)

    DownloadPostProcess().auto_delete_overwrites()
    assert not FakePendingList.calls


@pytest.mark.parametrize("with_task", [True, False])
def test_refresh_playlist(post, with_task):
    """subscribed, channel and video playlists are refreshed"""
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="vid1"))
    Playlist.save_doc(playlist_doc("PLsub", [], playlist_subscribed=True))
    Playlist.save_doc(playlist_doc("PLvid", ["vid1"]))
    Playlist.save_doc(playlist_doc("PLchan1", ["vid1"]))
    Playlist.save_doc(playlist_doc("PLother", ["vid9"]))
    RedisQueue(DownloaderBase.CHANNEL_QUEUE).add_list(
        ["UCindex", "UCnoindex", "UCmissing"]
    )
    RedisQueue(DownloaderBase.VIDEO_QUEUE).add("vid1")
    FakeChannel.data = {
        "UCindex": {"channel_overwrites": {"index_playlists": True}},
        "UCnoindex": {"channel_id": "UCnoindex"},
    }
    meta = {"playlist_channel": "Test Channel", "playlist_name": "Name"}
    FakePlaylist.data = {
        "PLsub": meta,
        "PLchan1": ValueError("playlist gone"),
        "PLchan2": None,
    }
    task = FakeTask() if with_task else None

    DownloadPostProcess(task=task).refresh_playlist()

    assert FakePlaylist.updated == [
        ("PLsub", True),
        ("PLchan1", True),
        ("PLchan2", True),
    ]
    assert RedisQueue(DownloaderBase.PLAYLIST_QUICK).get_all() == ["PLvid"]
    assert not RedisQueue(DownloaderBase.CHANNEL_QUEUE).get_all()
    if with_task:
        assert task.lines == [
            "Post Processing Playlists",
            "Scanning for Playlists",
            "Post Processing Playlists for: Test Channel",
            "Name [1/3]",
            "PLchan1: skip failed playlist import",
            "playlist gone",
            "PLchan2: skip failed playlist import",
            "no json data extracted for playlist",
        ]


@pytest.mark.parametrize("with_task", [True, False])
def test_match_videos(post, with_task):
    """quick queue playlists are matched, missing ones skipped"""
    RedisQueue(DownloaderBase.PLAYLIST_QUICK).add_list(["PL1", "PLgone"])
    FakePlaylist.data = {"PL1": {"playlist_name": "one"}}
    task = FakeTask() if with_task else None

    DownloadPostProcess(task=task).match_videos()

    assert FakePlaylist.matched == ["PL1"]
    if with_task:
        assert task.lines == [
            "Post Processing Playlists.",
            "Validate Playlists: - 1/2",
        ]


def test_get_comments(post):
    """comments of the downloaded videos"""
    RedisQueue(DownloaderBase.VIDEO_QUEUE).add_list(["vid1", "vid2"])
    DownloadPostProcess().get_comments()
    assert FakeCommentList.calls == [["vid1", "vid2"]]


@pytest.mark.parametrize("with_task", [True, False])
def test_embed_metadata(post, with_task):
    """only with add_metadata, every downloaded video"""
    RedisQueue(DownloaderBase.VIDEO_QUEUE).add_list(["vid1", "vid2"])
    task = FakeTask() if with_task else None

    DownloadPostProcess(task=task).embed_metadata()
    assert not FakeYoutubeVideo.embedded

    set_config(downloads={"add_metadata": True})
    DownloadPostProcess(task=task).embed_metadata()
    assert FakeYoutubeVideo.embedded == ["vid1", "vid2"]
    if with_task:
        assert task.lines[-1] == "Embed metadata: - 1/2"


def test_run(post):
    """all steps, video queue cleared"""
    RedisQueue(DownloaderBase.VIDEO_QUEUE).add("vid1")
    DownloadPostProcess().run()

    assert FakeCommentList.calls == [["vid1"]]
    assert not RedisQueue(DownloaderBase.VIDEO_QUEUE).get_all()

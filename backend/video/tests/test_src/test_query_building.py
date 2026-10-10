"""test video query building"""

from unittest import mock

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc, video_doc
from playlist.models import Playlist
from video.models import Video
from video.src.query_building import QueryBuilder

pytestmark = pytest.mark.django_db


@pytest.fixture(name="videos")
def fixture_videos():
    """three videos, a short one watched, in a playlist reversed"""
    Channel.objects.create(**Channel.fields_from_doc(channel_doc()))
    Channel.objects.create(
        **Channel.fields_from_doc(channel_doc(channel_id="UCother"))
    )
    docs = [
        video_doc(youtube_id="a", published=100, media_size=3),
        video_doc(youtube_id="b", published=300, media_size=1, vid_type="shorts"),
        video_doc(
            channel=channel_doc(channel_id="UCother"),
            youtube_id="c",
            published=200,
            media_size=2,
        ),
    ]
    docs[1]["player"]["watched"] = True
    docs[1]["streams"][0]["height"] = 720
    for doc in docs:
        Video.save_doc(doc)
    Playlist.save_doc(playlist_doc("PL1", ["c", "missing", "a"]))


def ids(queryset):
    """youtube ids in order"""
    return [video.youtube_id for video in queryset]


def test_initialization():
    """init constructor"""
    qb = QueryBuilder(user_id=1)
    assert qb.user_id == 1
    assert not qb.request_params


def test_filters(videos):
    """filters combine"""
    channel_id = channel_doc()["channel_id"]
    assert ids(QueryBuilder(1, channel=channel_id, sort="published").build_queryset()) == ["b", "a"]
    assert ids(QueryBuilder(1, watch="watched").build_queryset()) == ["b"]
    assert ids(QueryBuilder(1, watch="unwatched", sort="published", order="asc").build_queryset()) == ["a", "c"]
    assert ids(QueryBuilder(1, type="shorts").build_queryset()) == ["b"]
    assert ids(QueryBuilder(1, height="720").build_queryset()) == ["b"]
    del videos


def test_playlist_sort(videos):
    """playlist order wins over sort"""
    del videos
    result = QueryBuilder(1, playlist="PL1", sort="published").build_queryset()
    assert ids(result) == ["c", "a"]

    with pytest.raises(ValueError):
        QueryBuilder(1, playlist="nope").build_queryset()


def test_sort(videos):
    """sort field and order"""
    del videos
    assert ids(QueryBuilder(1, sort="mediasize", order="asc").build_queryset()) == ["b", "c", "a"]
    assert ids(QueryBuilder(1).build_queryset()) == ["a", "b", "c"]

    with pytest.raises(ValueError):
        QueryBuilder(user_id=1, sort="invalid").parse_sort()
    with pytest.raises(ValueError):
        QueryBuilder(user_id=1, sort="views", order="invalid").parse_sort()


def test_parse_watch_and_type():
    """invalid values raise"""
    qb = QueryBuilder(user_id=1)
    with pytest.raises(ValueError):
        qb.parse_watch("invalid")
    with pytest.raises(ValueError):
        qb.parse_type("invalid")
    assert qb.parse_type("videos") == "videos"


def test_continue(videos):
    """continue: videos with unwatched progress in redis"""
    del videos
    progress = [
        {"youtube_id": "a", "watched": False},
        {"youtube_id": "b", "watched": True},
    ]
    with mock.patch("video.src.query_building.RedisArchivist") as redis:
        redis.return_value.list_items.return_value = progress
        assert ids(QueryBuilder(1, watch="continue").build_queryset()) == ["a"]

        redis.return_value.list_items.return_value = progress[1:]
        assert QueryBuilder(1, watch="continue").build_queryset() is None

        redis.return_value.list_items.return_value = []
        assert QueryBuilder(1, watch="continue").build_queryset() is None

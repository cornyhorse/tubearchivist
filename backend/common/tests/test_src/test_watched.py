"""tests for watched state of videos, channels and playlists"""

import pytest
from channel.models import Channel
from common.src.ta_redis import RedisArchivist
from common.src.watched import WatchState
from common.tests.docs import channel_doc, playlist_doc, video_doc
from playlist.models import Playlist
from video.models import Video

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("redis_clean")]

CHANNEL_ID = "UCchannel000000000000001"
OTHER_ID = "UCchannel000000000000002"
PLAYLIST_ID = "PLplaylist000000000000000000000001"  # 34 chars
USER = 7


@pytest.fixture(name="videos")
def fixture_videos():
    """three videos of one channel, one of another, a playlist of two"""
    channel = channel_doc()
    other = channel_doc(channel_id=OTHER_ID)
    Channel.save_doc(channel)
    Channel.save_doc(other)
    for youtube_id in ("vid00000001", "vid00000002", "vid00000003"):
        Video.save_doc(video_doc(channel=channel, youtube_id=youtube_id))
    Video.save_doc(video_doc(channel=other, youtube_id="vid00000004"))
    Playlist.save_doc(playlist_doc(PLAYLIST_ID, ["vid00000002", "vid00000004"]))


def set_progress(*video_ids, user=USER):
    """store a progress position for each video"""
    for video_id in video_ids:
        RedisArchivist().set_message(
            f"{user}:progress:{video_id}",
            {"youtube_id": video_id, "position": 10},
        )


def watched() -> dict:
    """youtube_id: (watched, has watched_date)"""
    return {
        v.youtube_id: (v.watched, v.watched_date is not None)
        for v in Video.objects.all()
    }


def progress_keys() -> list[str]:
    """remaining progress keys"""
    return sorted(RedisArchivist().list_keys(""))


def test_video_watched_and_unwatched(videos):
    del videos
    set_progress("vid00000001", "vid00000002")

    WatchState("vid00000001", True, USER).change()
    assert watched()["vid00000001"] == (True, True)
    assert progress_keys() == [f"{USER}:progress:vid00000002"]

    WatchState("vid00000001", False, USER).change()
    assert watched()["vid00000001"] == (False, False)


def test_missing_video_raises(videos):
    del videos
    with pytest.raises(ValueError, match="failed to mark video"):
        WatchState("missing0001", True, USER).change()


def test_channel_watched(videos):
    del videos
    set_progress("vid00000001", "vid00000004")
    set_progress("vid00000001", user=USER + 1)
    WatchState("vid00000003", True, USER).change()
    first_date = Video.objects.get(pk="vid00000003").watched_date

    WatchState(CHANNEL_ID, True, USER).change()

    assert watched() == {
        "vid00000001": (True, True),
        "vid00000002": (True, True),
        "vid00000003": (True, True),
        "vid00000004": (False, False),
    }
    # already watched videos keep their date
    assert Video.objects.get(pk="vid00000003").watched_date == first_date
    assert progress_keys() == [
        f"{USER}:progress:vid00000004",
        f"{USER + 1}:progress:vid00000001",
    ]

    WatchState(CHANNEL_ID, False, USER).change()
    assert watched()["vid00000002"] == (False, False)


def test_playlist_watched(videos):
    del videos
    set_progress("vid00000002", "vid00000003")

    WatchState(PLAYLIST_ID, True, USER).change()

    assert watched() == {
        "vid00000001": (False, False),
        "vid00000002": (True, True),
        "vid00000003": (False, False),
        "vid00000004": (True, True),
    }
    assert progress_keys() == [f"{USER}:progress:vid00000003"]

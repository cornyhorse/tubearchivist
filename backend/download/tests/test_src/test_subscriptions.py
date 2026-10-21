"""tests for channel and playlist subscriptions"""

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc
from download.src import subscriptions
from download.src.subscriptions import (
    ChannelSubscription,
    PlaylistSubscription,
    SubscriptionHandler,
    SubscriptionScanner,
)
from download.tests.helpers import FakeTask, set_config
from playlist.models import Playlist
from video.src.constants import VideoTypeEnum

VIDEO_ID = "vid00000001"
CHANNEL_ID = "UCchannel000000000000001"
PLAYLIST_ID = "PL" + "p" * 32


class FakePendingList:
    """records how PendingList was called"""

    calls: list = []

    def __init__(self, youtube_ids, task=None, flat=False):
        self.kwargs = {
            "youtube_ids": youtube_ids,
            "task": task,
            "flat": flat,
        }

    def parse_url_list(self):
        """pretend every url added one video"""
        FakePendingList.calls.append(self.kwargs)
        return len(self.kwargs["youtube_ids"])


@pytest.fixture(name="pending")
def fixture_pending(monkeypatch, app_config):  # pylint: disable=W0613
    """fake PendingList"""
    FakePendingList.calls = []
    monkeypatch.setattr(subscriptions, "PendingList", FakePendingList)
    return FakePendingList


def test_channel_subscription(pending):
    """one query per tab of each subscribed channel"""
    set_config(subscriptions={"shorts_channel_size": 5})
    Channel.save_doc(
        channel_doc(
            channel_tabs=["videos", "shorts"],
            channel_overwrites={"subscriptions_channel_size": 3},
        )
    )
    Channel.save_doc(channel_doc(channel_id="UCnotabs", channel_tabs=[]))
    Channel.save_doc(
        channel_doc(channel_id="UCunsubscribed", channel_subscribed=False)
    )
    task = FakeTask()

    assert ChannelSubscription(task=task).find_missing() == 2

    call = pending.calls[0]
    assert call["flat"] is False
    assert call["youtube_ids"] == [
        {
            "type": "channel",
            "url": CHANNEL_ID,
            "vid_type": VideoTypeEnum.VIDEOS,
            "limit": 3,
        },
        {
            "type": "channel",
            "url": CHANNEL_ID,
            "vid_type": VideoTypeEnum.SHORTS,
            "limit": 5,
        },
    ]
    assert task.lines == ["Looking up channels.", "Scanning 2 channels"]


def test_channel_subscription_none(pending):
    """no subscribed channels"""
    assert ChannelSubscription().find_missing() == 0
    assert not pending.calls


def test_channel_subscription_without_task(pending):
    """no progress messages without task"""
    Channel.save_doc(channel_doc())
    assert ChannelSubscription().find_missing() == 2


def test_playlist_subscription(pending):
    """subscribed active playlists with the size limit"""
    set_config(subscriptions={"playlist_size": 7, "extract_flat": True})
    Playlist.save_doc(playlist_doc("PLsub", [], playlist_subscribed=True))
    Playlist.save_doc(playlist_doc("PLnot", []))

    assert PlaylistSubscription().find_missing() == 1
    call = pending.calls[0]
    assert call["flat"] is True
    assert call["youtube_ids"] == [
        {
            "type": "playlist",
            "url": "PLsub",
            "vid_type": VideoTypeEnum.UNKNOWN,
            "limit": 7,
        }
    ]


def test_playlist_subscription_none(pending):
    """no subscribed playlists"""
    assert PlaylistSubscription().find_missing() == 0


def test_scanner(pending):
    """channels then playlists, playlists only with a running task"""
    Channel.save_doc(channel_doc())
    Playlist.save_doc(playlist_doc("PLsub", [], playlist_subscribed=True))
    task = FakeTask()

    assert SubscriptionScanner(task=task).scan() == 3
    assert task.lines[0] == "Rescanning channels and playlists."

    assert SubscriptionScanner(task=FakeTask(stop_after=0)).scan() == 2
    assert SubscriptionScanner().scan() == 2


class FakeYoutubeChannel:
    """records subscribe calls"""

    subscribed: list = []

    def __init__(self, channel_id):
        self.channel_id = channel_id

    def change_subscribe(self, new_subscribe_state):
        """record"""
        self.subscribed.append((self.channel_id, new_subscribe_state))


class FakeYoutubePlaylist(FakeYoutubeChannel):
    """records subscribe calls"""

    subscribed: list = []


class FakeYoutubeVideo:
    """video whose channel is CHANNEL_ID"""

    def __init__(self, youtube_id):
        self.youtube_id = youtube_id
        self.channel_id = None

    def get_from_youtube(self):
        """nothing to fetch"""

    def process_youtube_meta(self):
        """set channel id"""
        self.channel_id = CHANNEL_ID


@pytest.fixture(name="remote")
def fixture_remote(monkeypatch):
    """fake channel, playlist and video classes"""
    FakeYoutubeChannel.subscribed = []
    FakeYoutubePlaylist.subscribed = []
    monkeypatch.setattr(subscriptions, "YoutubeChannel", FakeYoutubeChannel)
    monkeypatch.setattr(subscriptions, "YoutubePlaylist", FakeYoutubePlaylist)
    monkeypatch.setattr(subscriptions, "YoutubeVideo", FakeYoutubeVideo)


def test_subscribe(remote):
    """video subscribes its channel, playlist and channel directly"""
    task = FakeTask()
    url_str = f"{VIDEO_ID}\n{CHANNEL_ID} {PLAYLIST_ID}"

    SubscriptionHandler(url_str, task=task).subscribe()

    assert FakeYoutubeChannel.subscribed == [
        (CHANNEL_ID, True),
        (CHANNEL_ID, True),
    ]
    assert FakeYoutubePlaylist.subscribed == [(PLAYLIST_ID, True)]
    assert task.lines == [
        "Processing form content.",
        "Subscribe to Video",
        "Progress: 1/3",
        "Subscribe to Channel",
        "Progress: 2/3",
        "Subscribe to Playlist",
        "Progress: 3/3",
    ]


def test_subscribe_expected_type(remote):
    """matching expected type, without task"""
    SubscriptionHandler(CHANNEL_ID).subscribe(expected_type="channel")
    SubscriptionHandler(PLAYLIST_ID).subscribe(expected_type="playlist")

    assert FakeYoutubeChannel.subscribed == [(CHANNEL_ID, True)]
    assert FakeYoutubePlaylist.subscribed == [(PLAYLIST_ID, True)]


@pytest.mark.parametrize(
    "url, expected_type",
    [(PLAYLIST_ID, "channel"), (CHANNEL_ID, "playlist")],
)
def test_subscribe_wrong_type(remote, url, expected_type):
    """type mismatch raises, nothing subscribed"""
    with pytest.raises(TypeError):
        SubscriptionHandler(url).subscribe(expected_type=expected_type)

    assert not FakeYoutubeChannel.subscribed
    assert not FakeYoutubePlaylist.subscribed


def test_subscribe_invalid_type(remote):
    """unknown item type raises"""
    handler = SubscriptionHandler("")
    with pytest.raises(ValueError):
        handler.subscribe_type({"type": "user", "url": "x"}, False)

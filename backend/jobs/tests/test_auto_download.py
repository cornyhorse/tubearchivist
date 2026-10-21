"""per channel and per playlist auto download in subscription checks"""

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download, DownloadRule
from download.src import auto_download
from download.src import queue as queue_mod
from download.tests.test_src.test_queue import (  # noqa: F401
    FakePlaylist,
    fixture_yt,
    flat_entry,
)
from jobs.models import Job, JobStatus
from jobs.src import handlers
from jobs.tests.test_handlers import CHANNEL_ID, running
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db

PL = "PLauto"


@pytest.fixture(name="quiet")
def fixture_quiet(monkeypatch, no_broker):
    """no sleeping between videos, nothing sent to the broker"""
    # pylint: disable=unused-argument
    monkeypatch.setattr(queue_mod, "rand_sleep", lambda config: None)


def downloads() -> list[str]:
    """targets of the queued download jobs, in order"""
    return list(
        Job.objects.filter(type="download_video")
        .order_by("id")
        .values_list("target_id", flat=True)
    )


def channel(auto=True, **overrides):
    """the test channel, opted in or not"""
    return Channel.save_doc(channel_doc(auto_download=auto, **overrides))


def check_channel(**params):
    """run a scheduled check of the test channel"""
    _, ctx = running(
        "check_channel",
        CHANNEL_ID,
        params={"flat": True, **params},
        created_by="schedule",
    )
    return handlers.check_channel(ctx)


def check_playlist(**params):
    """run a scheduled check of the test playlist"""
    _, ctx = running(
        "check_playlist",
        PL,
        params={"flat": True, **params},
        created_by="schedule",
    )
    return handlers.check_playlist(ctx)


def waiting(youtube_id, **overrides):
    """a found video waiting in the queue"""
    fields = {"found_by_kind": "channel_check", **overrides}
    return Download.upsert_doc(download_doc(youtube_id=youtube_id, **fields))


def test_off_by_default():
    """new rows and existing ones migrate to off"""
    Channel.save_doc(channel_doc())
    Playlist.save_doc(playlist_doc(PL, []))
    assert Channel.objects.get().auto_download is False
    assert Playlist.objects.get().auto_download is False


def test_opted_in_channel_downloads_what_rules_allow(yt, quiet):
    channel()
    DownloadRule.objects.create(channel=None, max_duration=1000)
    yt.channel_videos = [
        flat_entry("vid1", duration=600),
        flat_entry("long", duration=5000),
    ]

    result = check_channel()

    assert result == {
        "found": 2,
        "allowed": 1,
        "ignored": 1,
        "queued_downloads": 1,
        "auto_download": True,
    }
    assert downloads() == ["vid1"]
    assert Download.objects.get(pk="long").status == "ignore"


def test_opted_out_channel_waits(yt, quiet):
    channel(auto=False)
    yt.channel_videos = [flat_entry("vid1")]

    result = check_channel()

    assert result["queued_downloads"] == 0
    assert "auto_download" not in result
    assert downloads() == []
    assert Download.objects.get().status == "pending"


def test_explicit_download_now_false_wins(yt, quiet):
    channel()
    yt.channel_videos = [flat_entry("vid1")]

    assert check_channel(download_now=False)["queued_downloads"] == 0
    assert downloads() == []


def test_explicit_download_now_true_without_opt_in(yt, quiet):
    """the add dialog's download now still works for any channel"""
    channel(auto=False)
    waiting("seen1", found_playlist_id="PLother")
    yt.channel_videos = [flat_entry("vid1"), flat_entry("seen1")]

    result = check_channel(download_now=True)

    # only new videos: already waiting ones aren't picked up
    assert downloads() == ["vid1"]
    assert "auto_download" not in result


def test_unknown_channel_is_not_opted_in(yt, quiet):
    yt.channel_videos = [flat_entry("vid1")]

    assert check_channel()["queued_downloads"] == 0


def test_channel_picks_up_video_another_source_found(yt, quiet):
    """
    found via two sources: an opted in source that sees a waiting video
    found by another source downloads it; its own backlog stays
    """
    channel()
    waiting("fromlist", found_playlist_id="PLother", found_by_kind="x")
    waiting("ownold")
    waiting("broken", found_playlist_id="PLother", message="HTTP 403")
    waiting("ignored", found_playlist_id="PLother", status="ignore")
    Video.save_doc(video_doc(youtube_id="have"))
    yt.channel_videos = [
        flat_entry(i) for i in ["fromlist", "ownold", "broken", "ignored"]
    ] + [flat_entry("have"), flat_entry("new1")]

    result = check_channel()

    assert downloads() == ["new1", "fromlist"]
    assert result["queued_downloads"] == 2
    assert result["found"] == 1


def test_playlist_picks_up_video_a_channel_found(yt, quiet):
    Playlist.save_doc(playlist_doc(PL, [], auto_download=True))
    waiting("chan1")
    waiting("ownpl", found_playlist_id=PL, found_by_kind="playlist_check")
    waiting("busy")
    running("download_video", "busy")
    waiting("short", duration_seconds=20)
    DownloadRule.objects.create(playlist_id=PL, min_duration=60)
    FakePlaylist.youtube_meta = {
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "entries": [
            flat_entry(i, duration=600)
            for i in ["chan1", "ownpl", "busy", "short", "plnew"]
        ],
    }

    result = check_playlist()

    assert downloads() == ["busy", "plnew", "chan1"]
    assert result["queued_downloads"] == 2
    new = Download.objects.get(pk="plnew")
    assert new.found_playlist_id == PL


def test_opted_out_playlist_leaves_waiting_videos(yt, quiet):
    Playlist.save_doc(playlist_doc(PL, []))
    waiting("chan1")
    FakePlaylist.youtube_meta = {
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "entries": [flat_entry("chan1")],
    }

    assert check_playlist()["queued_downloads"] == 0
    assert downloads() == []


def test_subscription_check_children_follow_their_source(quiet):
    """a scheduled check passes no download_now: each source decides"""
    channel(channel_subscribed=True)
    _, ctx = running("check_subscriptions", created_by="schedule")

    handlers.check_subscriptions(ctx)

    child = Job.objects.get(type="check_channel")
    assert child.params == {"download_now": None}


def test_add_dialog_stores_auto_download(fake_scan, monkeypatch):
    """prepare_channel and check_playlist store an asked auto_download"""
    built = []

    class Index:
        """YoutubeChannel / YoutubePlaylist stand in"""

        def __init__(self, item_id):
            self.item_id = item_id

        def build_json(self, upload=False):
            """index the channel"""
            built.append(("channel", self.item_id, upload))
            channel(auto=False)

        def update_playlist(self):
            """index the playlist"""
            built.append(("playlist", self.item_id))
            Playlist.save_doc(playlist_doc(PL, []))

    monkeypatch.setattr(handlers, "YoutubeChannel", Index)
    monkeypatch.setattr(handlers, "YoutubePlaylist", Index)

    _, ctx = running(
        "check_channel",
        CHANNEL_ID,
        params={"auto_download": True, "entries": ["x"]},
    )
    handlers.check_channel(ctx)
    Job.objects.update(status=JobStatus.DONE)
    _, ctx = running("check_playlist", PL, params={"auto_download": True})
    handlers.check_playlist(ctx)

    assert built == [("channel", CHANNEL_ID, True), ("playlist", PL)]
    assert Channel.objects.get().auto_download is True
    assert Playlist.objects.get().auto_download is True
    assert fake_scan == [("channel", CHANNEL_ID), ("playlist", PL)]


@pytest.fixture(name="fake_scan")
def fixture_fake_scan(monkeypatch):
    """record the source of each scan"""
    sources = []

    def scan(ctx, entries, kind, playlist_id=None, source=None):
        # pylint: disable=unused-argument
        sources.append(source)
        return {}

    monkeypatch.setattr(handlers, "scan", scan)
    return sources


def test_set_auto_download_returns_known_ids():
    channel(auto=False)

    changed = auto_download.set_auto_download(
        "channel", [CHANNEL_ID, "UCmissing"], True
    )

    assert changed == [CHANNEL_ID]
    assert auto_download.is_auto_download("channel", CHANNEL_ID)
    assert not auto_download.is_auto_download("playlist", PL)

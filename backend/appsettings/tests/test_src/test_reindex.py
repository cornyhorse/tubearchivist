"""tests for the reindex queue, youtube access mocked"""

from datetime import datetime, timedelta, timezone

import pytest
from appsettings.src import reindex as reindex_module
from appsettings.src.reindex import (
    ChannelFullScan,
    Reindex,
    ReindexManual,
    ReindexPopulate,
    ReindexProgress,
)
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisArchivist, RedisQueue
from common.tests.docs import channel_doc, playlist_doc, video_doc
from django_celery_beat.models import CrontabSchedule
from playlist.models import Playlist
from task.models import CustomPeriodicTask
from video.models import Video

pytestmark = pytest.mark.django_db

VIDEO_Q = "reindex:ta_video"
CHANNEL_Q = "reindex:ta_channel"
PLAYLIST_Q = "reindex:ta_playlist"


def queue(name) -> list[str]:
    """all ids in a reindex queue"""
    return RedisQueue(name).get_all()


def days_ago(days: float) -> datetime:
    """aware datetime days before now"""
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest.fixture(name="env")
def fixture_env(app_config, redis_clean):
    """config in the database and an empty redis namespace"""
    # pylint: disable=unused-argument
    return app_config


# populate


def add_videos(count, **kwargs):
    """count videos on the default channel"""
    channel = channel_doc()
    Channel.save_doc(channel)
    for n in range(count):
        Video.save_doc(
            video_doc(channel=channel, youtube_id=f"v{n:010d}", **kwargs)
        )


def test_get_interval(env):
    # pylint: disable=unused-argument
    populate = ReindexPopulate()
    populate.get_interval()
    assert populate.interval == 90

    crontab = CrontabSchedule.objects.create(minute="0", hour="2")
    task = CustomPeriodicTask.objects.create(
        name="check_reindex", task="check_reindex", crontab=crontab
    )
    populate.get_interval()
    assert populate.interval == 90

    task.task_config = {"days": 7}
    task.save()
    populate.get_interval()
    assert populate.interval == 7


def test_add_recent(env):
    # pylint: disable=unused-argument
    add_videos(2, published=int(days_ago(1).timestamp()))
    Video.objects.filter(pk="v0000000001").update(published=days_ago(2))
    Video.save_doc(video_doc(youtube_id="old00000000", published=1))
    Video.save_doc(
        video_doc(
            youtube_id="inactive000",
            published=int(days_ago(1).timestamp()),
            active=False,
        )
    )

    ReindexPopulate().add_recent()

    assert queue(VIDEO_Q) == ["v0000000000", "v0000000001"]


def test_add_recent_nothing(env):
    # pylint: disable=unused-argument
    ReindexPopulate().add_recent()
    assert not queue(VIDEO_Q)


def test_add_outdated(env):
    # pylint: disable=unused-argument
    add_videos(3)
    Video.objects.update(last_refresh=days_ago(100))
    Video.objects.filter(pk="v0000000002").update(last_refresh=days_ago(1))
    Video.objects.filter(pk="v0000000001").update(last_refresh=days_ago(200))
    Channel.objects.update(last_refresh=days_ago(95))
    doc = playlist_doc("PL1", [], playlist_last_refresh=1)
    Playlist.save_doc(doc)
    Playlist.save_doc(dict(doc, playlist_id="PL2", playlist_active=False))

    ReindexPopulate().add_outdated()

    assert queue(VIDEO_Q) == ["v0000000001"], "oldest first, daily limit 1"
    assert queue(CHANNEL_Q) == [channel_doc()["channel_id"]]
    assert queue(PLAYLIST_Q) == ["PL1"]


def test_daily_should_limits(env):
    # pylint: disable=unused-argument
    add_videos(3)
    Video.objects.update(last_refresh=days_ago(100))
    populate = ReindexPopulate()
    populate.interval = 90

    assert populate._get_daily_should(3) == 1  # pylint: disable=W0212
    assert populate._get_daily_should(180) == 3  # pylint: disable=W0212
    populate.add_outdated()

    assert queue(VIDEO_Q) == ["v0000000000"]


# manual


def test_manual_unknown_type(env):
    # pylint: disable=unused-argument
    with pytest.raises(ValueError):
        ReindexManual().extract_data({"comment": ["x"]})


def test_manual_add(env):
    # pylint: disable=unused-argument
    ReindexManual().extract_data(
        {"video": ["v1", "v2"], "channel": ["UCa"], "playlist": ["PL1"]}
    )

    assert queue(VIDEO_Q) == ["v1", "v2"]
    assert queue(CHANNEL_Q) == ["UCa"]
    assert queue(PLAYLIST_Q) == ["PL1"]


def test_manual_empty_videos(env):
    # pylint: disable=unused-argument
    ReindexManual().extract_data({"video": []})
    assert not queue(VIDEO_Q)


def test_manual_extract_videos(env):
    # pylint: disable=unused-argument
    add_videos(2)
    Playlist.save_doc(playlist_doc("PL1", ["v0000000001", "notdownload"]))

    ReindexManual(extract_videos=True).extract_data(
        {"channel": [channel_doc()["channel_id"]], "playlist": ["PL1"]}
    )

    assert queue(VIDEO_Q) == ["v0000000000", "v0000000001"]
    assert queue(PLAYLIST_Q) == ["PL1"]


def test_process_index_unknown_name(env):
    # pylint: disable=unused-argument
    ReindexManual().process_index(
        {"index_name": "ta_other", "queue_name": "x"}, ["a"]
    )
    assert not RedisQueue("x").get_all()


# progress


def test_progress_all(env):
    # pylint: disable=unused-argument
    assert ReindexProgress().get_progress() == {
        "id": None,
        "total_queued": 0,
        "queue_type": "all",
        "queue_position": None,
        "state": "empty",
    }

    ReindexManual().extract_data({"video": ["v1"], "channel": ["UCa"]})

    progress = ReindexProgress().get_progress()
    assert progress["total_queued"] == 2
    assert progress["state"] == "running"


def test_progress_unknown_type(env):
    # pylint: disable=unused-argument
    with pytest.raises(ValueError):
        ReindexProgress(request_type="comment").get_progress()


def test_progress_single(env):
    # pylint: disable=unused-argument
    ReindexManual().extract_data({"video": ["v1", "v2"]})
    key = RedisQueue(VIDEO_Q).key
    RedisArchivist().set_message(f"{key}:active", "v0")

    def state(youtube_id):
        progress = ReindexProgress("video", youtube_id).get_progress()
        return progress["queue_position"], progress["state"]

    assert state("v2") == (1, "in_queue")
    assert state("v9") == (None, "not_in_queue")
    progress = ReindexProgress("video", "v1").get_progress()
    assert progress["id"] == "v1" and progress["total_queued"] == 2
    assert progress["queue_type"] == "video"


def test_progress_processing(env):
    # pylint: disable=unused-argument
    RedisArchivist().set_message(f"{VIDEO_Q}:active", "v0")

    progress = ReindexProgress("video", "v0").get_progress()

    assert progress["state"] == "processing"


# reindex


@pytest.fixture(name="yt")
def fixture_yt(monkeypatch):
    """fake youtube facing classes, recording calls"""
    state = {
        "videos": {},
        "channels": {},
        "playlists": {},
        "meta": True,
        "playlist_active": True,
        "cookie_valid": True,
        "calls": [],
    }
    calls = state["calls"]

    class FakeVideo:
        """youtube video backed by state['videos']"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id
            self.json_data = None
            self.youtube_meta = None

        def get_from_db(self):
            """stored doc"""
            calls.append(("video_db", self.youtube_id))
            stored = state["videos"].get(self.youtube_id)
            self.json_data = dict(stored) if stored else None

        def build_json(self, media_path):
            """fresh metadata from youtube"""
            calls.append(("video_build", media_path))
            if state["meta"]:
                self.youtube_meta = {"id": self.youtube_id}
                self.json_data = {
                    "youtube_id": self.youtube_id,
                    "title": "new title",
                    "vid_thumb_url": "https://thumb",
                }

        def deactivate(self):
            """record"""
            calls.append(("video_deactivate", self.youtube_id))

        def delete_subtitles(self, subtitles):
            """record"""
            calls.append(("delete_subtitles", subtitles))

        def check_subtitles(self):
            """record"""
            calls.append(("check_subtitles",))

        def save_to_db(self):
            """store back"""
            calls.append(("video_save", dict(self.json_data)))

        def embed_metadata(self):
            """record"""
            calls.append(("video_embed", self.youtube_id))

    class FakeChannel:
        """youtube channel backed by state['channels']"""

        def __init__(self, channel_id):
            self.youtube_id = channel_id
            self.json_data = None
            self.youtube_meta = None

        def get_from_db(self):
            """stored doc"""
            stored = state["channels"].get(self.youtube_id)
            self.json_data = dict(stored) if stored else None

        def get_from_youtube(self):
            """remote meta"""
            self.youtube_meta = state["meta"]

        def deactivate(self):
            """record"""
            calls.append(("channel_deactivate", self.youtube_id))

        def process_youtube_meta(self):
            """fresh doc"""
            self.json_data = {
                "channel_id": self.youtube_id,
                "channel_subscribed": False,
            }

        def get_channel_art(self):
            """record"""
            calls.append(("channel_art", self.youtube_id))

        def save_to_db(self):
            """record"""
            calls.append(("channel_save", dict(self.json_data)))

    class FakePlaylist:
        """youtube playlist backed by state['playlists']"""

        def __init__(self, playlist_id):
            self.youtube_id = playlist_id
            self.json_data = None

        def get_from_db(self):
            """stored doc"""
            self.json_data = state["playlists"].get(self.youtube_id)

        def update_playlist(self):
            """record"""
            calls.append(("playlist_update", self.youtube_id))
            return state["playlist_active"]

        def deactivate(self):
            """record"""
            calls.append(("playlist_deactivate", self.youtube_id))

    class FakeThumbs:
        """record thumb refresh"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def delete_video_thumb(self):
            """record"""
            calls.append(("thumb_delete", self.youtube_id))

        def download_video_thumb(self, url):
            """record"""
            calls.append(("thumb_download", url))

    class FakeComments:
        """record comment refresh"""

        def __init__(self, youtube_id, config):
            assert config
            self.youtube_id = youtube_id

        def reindex_comments(self):
            """record"""
            calls.append(("comments", self.youtube_id))

    class FakeCookie:
        """cookie validation result from state"""

        def __init__(self, config):
            self.config = config

        @staticmethod
        def validate():
            """canned"""
            return state["cookie_valid"]

    class FakeFullScan:
        """record full scans"""

        def __init__(self, channel_id, config):
            assert config
            self.channel_id = channel_id

        def scan(self):
            """record"""
            calls.append(("full_scan", self.channel_id))

    monkeypatch.setattr(reindex_module, "YoutubeVideo", FakeVideo)
    monkeypatch.setattr(reindex_module, "YoutubeChannel", FakeChannel)
    monkeypatch.setattr(reindex_module, "YoutubePlaylist", FakePlaylist)
    monkeypatch.setattr(reindex_module, "ThumbManager", FakeThumbs)
    monkeypatch.setattr(reindex_module, "Comments", FakeComments)
    monkeypatch.setattr(reindex_module, "CookieHandler", FakeCookie)
    monkeypatch.setattr(reindex_module, "ChannelFullScan", FakeFullScan)
    monkeypatch.setattr(
        reindex_module, "rand_sleep", lambda config: calls.append(("sleep",))
    )
    return state


def test_reindex_all(env, yt, task):
    # pylint: disable=unused-argument
    yt["videos"] = {
        "v1": {
            "media_url": "UCa/v1.mp4",
            "subtitles": [{"lang": "en"}],
            "player": {"watched": True},
            "date_downloaded": 5,
            "vid_type": "videos",
            "channel": {"channel_id": "UCa"},
            "playlist": ["PL1"],
        },
    }
    yt["channels"] = {
        "UCa": {
            "channel_subscribed": True,
            "channel_overwrites": {"autodelete_days": 3},
        },
        "UCb": {"channel_subscribed": True},
    }
    yt["playlists"] = {
        "PL1": {"playlist_type": "regular"},
        "PLc": {"playlist_type": "custom"},
    }
    ReindexManual().extract_data(
        {
            "video": ["v1", "missing"],
            "channel": ["UCa", "UCb", "UCmissing"],
            "playlist": ["PL1", "PLc", "PLmissing"],
        }
    )
    reindex = Reindex(task=task)

    reindex.reindex_all()

    calls = yt["calls"]
    media = f"{EnvironmentSettings.MEDIA_DIR}/UCa/v1.mp4"
    assert ("video_build", media) in calls
    saved = next(c[1] for c in calls if c[0] == "video_save")
    assert saved["player"] == {"watched": True}
    assert saved["date_downloaded"] == 5
    assert saved["playlist"] == ["PL1"]
    assert saved["channel"] == {"channel_id": "UCa"}
    assert ("delete_subtitles", [{"lang": "en"}]) in calls
    assert ("thumb_download", "https://thumb") in calls
    assert ("comments", "v1") in calls and ("video_embed", "v1") in calls
    channel_saves = [c[1] for c in calls if c[0] == "channel_save"]
    assert channel_saves == [
        {
            "channel_id": "UCa",
            "channel_subscribed": True,
            "channel_overwrites": {"autodelete_days": 3},
        },
        {"channel_id": "UCb", "channel_subscribed": True},
    ]
    assert [c for c in calls if c[0] == "full_scan"] == [
        ("full_scan", "UCa"),
        ("full_scan", "UCb"),
    ]
    assert [c for c in calls if c[0] == "playlist_update"] == [
        ("playlist_update", "PL1")
    ]
    assert calls.count(("sleep",)) == 8
    assert reindex.processed == {"videos": 1, "channels": 2, "playlists": 1}
    assert reindex.build_message() == (
        "reindexed 1 videos, 2 channels, 1 playlists"
    )
    assert "Reindexing Videos 1/2" in task.lines
    assert "Reindexing Playlists 3/3" in task.lines
    assert not queue(VIDEO_Q) and not queue(CHANNEL_Q)
    assert RedisArchivist().get_message_str(f"{VIDEO_Q}:active") is None


def test_reindex_deactivates_missing_remote(env, yt):
    # pylint: disable=unused-argument
    yt["meta"] = False
    yt["playlist_active"] = False
    yt["videos"] = {"v1": {"media_url": "UCa/v1.mp4"}}
    yt["channels"] = {"UCa": {"channel_subscribed": True}}
    yt["playlists"] = {"PL1": {"playlist_type": "regular"}}
    ReindexManual().extract_data(
        {"video": ["v1"], "channel": ["UCa"], "playlist": ["PL1"]}
    )
    reindex = Reindex()

    reindex.reindex_all()

    calls = yt["calls"]
    assert ("video_deactivate", "v1") in calls
    assert ("channel_deactivate", "UCa") in calls
    assert ("playlist_deactivate", "PL1") in calls
    assert reindex.processed == {"videos": 0, "channels": 0, "playlists": 0}
    assert reindex.build_message() == ""


def test_reindex_single_video_from_download(env, yt):
    # pylint: disable=unused-argument
    yt["videos"] = {"v1": {"media_url": "UCa/v1.mp4"}}

    video = Reindex().reindex_single_video("v1", from_download=True)

    assert video.json_data["title"] == "new title"
    assert ("video_build", False) in yt["calls"]
    saved = next(c[1] for c in yt["calls"] if c[0] == "video_save")
    assert "playlist" not in saved


def test_reindex_skips_without_valid_cookie(env, yt):
    env.update_config({"downloads": {"cookie_import": True}})
    yt["cookie_valid"] = False
    ReindexManual().extract_data({"video": ["v1"]})

    Reindex().reindex_all()

    assert not yt["calls"]
    assert queue(VIDEO_Q) == ["v1"]


def test_reindex_with_valid_cookie(env, yt):
    env.update_config({"downloads": {"cookie_import": True}})

    assert Reindex().cookie_is_valid() is True
    Reindex().reindex_all()

    assert not yt["calls"]


def test_reindex_type_unknown_index(env, yt):
    # pylint: disable=unused-argument
    RedisQueue("x").add_list(["a"])

    Reindex().reindex_type(
        "other", {"index_name": "ta_other", "queue_name": "x"}
    )

    assert yt["calls"] == [("sleep",)]


# full scan


def test_channel_full_scan(env, monkeypatch):
    add_videos(3)
    Video.objects.filter(pk="v0000000001").update(vid_type="shorts")
    remote = [
        {"id": "v0000000000", "vid_type": "videos"},
        {"id": "v0000000001", "vid_type": "videos"},
        {"id": "v0000000002", "vid_type": "streams"},
    ]
    seen = []

    def fake_remote(channel_id, config, limit):
        seen.append((channel_id, config, limit))
        return remote[:-1] if len(seen) > 1 else remote

    monkeypatch.setattr(reindex_module, "get_last_channel_videos", fake_remote)
    channel_id = channel_doc()["channel_id"]
    scan = ChannelFullScan(channel_id, env.config)

    scan.scan()

    assert seen[0] == (channel_id, env.config, None)
    assert dict(Video.objects.values_list("youtube_id", "vid_type")) == {
        "v0000000000": "videos",
        "v0000000001": "videos",
        "v0000000002": "streams",
    }

    scan.scan()

    assert not scan.to_update

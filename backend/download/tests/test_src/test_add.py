"""add dialog: preview links and queue scoped scans, YouTube faked"""

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download, DownloadRule
from download.src import add as add_mod
from download.src.add import add, parse_links, preview, preview_item
from jobs.models import Job, Priority
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"


class FakeItem:
    """YoutubeChannel / YoutubePlaylist / YoutubeVideo stand in"""

    metas: dict = {}
    calls: list = []

    def __init__(self, youtube_id):
        self.youtube_id = youtube_id
        self.youtube_meta = None
        self.error = None

    def get_from_youtube(self):
        """meta from the registry"""
        FakeItem.calls.append(self.youtube_id)
        self.youtube_meta = self.metas.get(self.youtube_id)
        if not self.youtube_meta:
            self.error = "This video is unavailable"


@pytest.fixture(name="youtube")
def fixture_youtube(monkeypatch, app_config, redis_ns):
    """fake YouTube: metas by id, channel listings by channel id"""
    # pylint: disable=unused-argument
    FakeItem.metas = {}
    FakeItem.calls = []
    listings = {}
    for name in ["YoutubeChannel", "YoutubePlaylist", "YoutubeVideo"]:
        monkeypatch.setattr(add_mod, name, FakeItem)
    monkeypatch.setattr(
        add_mod,
        "get_last_channel_videos",
        lambda channel_id, config, limit: listings.get(channel_id, []),
    )

    class Parser:
        """ids only: UC.. channel, PL.. playlist, else video"""

        def __init__(self, link):
            self.link = link

        def parse(self):
            """one item"""
            if self.link == "bad":
                raise ValueError("not a valid id_str: bad")
            if self.link == "empty":
                raise ValueError()
            kind = {"UC": "channel", "PL": "playlist"}.get(
                self.link[:2], "video"
            )
            vid_type = "shorts" if self.link.startswith("s") else "unknown"
            return [{"type": kind, "url": self.link, "vid_type": vid_type}]

    monkeypatch.setattr(add_mod, "Parser", Parser)
    return FakeItem.metas, listings


def flat(youtube_id, duration=600, vid_type="videos", **extra):
    """flat channel listing entry"""
    return {
        "id": youtube_id,
        "title": f"Video {youtube_id}",
        "duration": duration,
        "vid_type": vid_type,
        **extra,
    }


def channel_meta():
    """channel page metadata"""
    return {
        "uploader": "Train Channel",
        "thumbnails": [
            {"url": "banner", "width": 2000, "height": 400},
            {"url": "avatar", "width": 900, "height": 900},
            {"url": "no-size"},
        ],
    }


def test_parse_links(youtube):
    # pylint: disable=unused-argument
    parsed, errors = parse_links("v1, UC1\nbad empty")
    assert [p[1]["type"] for p in parsed] == ["video", "channel"]
    assert errors == [
        {"url": "bad", "error": "not a valid id_str: bad"},
        {"url": "empty", "error": "not a link"},
    ]


def test_preview_channel(youtube):
    metas, listings = youtube
    metas[CHANNEL_ID] = channel_meta()
    listings[CHANNEL_ID] = [
        flat("ok1"),
        flat("short1", 30, "shorts"),
        flat("short2", 20, "shorts"),
        flat("long", 5000, timestamp=1700000000),
        flat("have"),
        flat("found"),
        flat("nodur", duration=None),
    ]
    Channel.save_doc(channel_doc(emby_numbering="year_frozen"))
    Video.save_doc(video_doc(youtube_id="have"))
    Download.upsert_doc(download_doc(youtube_id="found", status="ignore"))
    DownloadRule.objects.create(
        channel_id=CHANNEL_ID, vid_types=["videos"], max_duration=3600
    )

    item = preview_item("channel", CHANNEL_ID)

    assert item["title"] == "Train Channel"
    assert item["thumb_url"] == "avatar"
    assert (item["video_count"], item["subscribed"]) == (7, True)
    assert item["known"] == {"downloaded": 1, "waiting": 0, "ignored": 1}
    assert item["numbering"] == {"scheme": "year_frozen", "source": "channel"}
    assert item["rule"]["summary"] == "No livestreams · No Shorts · <1 h"
    assert item["verdict"] == {
        "total": 7,
        "download": 2,
        "skipped": 3,
        "already_have": 2,
        "reasons": [
            {"key": "vid_type", "label": "Shorts", "count": 2},
            {"key": "too_long", "label": ">1 h", "count": 1},
        ],
    }
    long = next(s for s in item["sample"] if s["youtube_id"] == "long")
    assert long["published"].year == 2023
    assert long["verdict"]["reason_key"] == "too_long"
    assert (
        next(s for s in item["sample"] if s["youtube_id"] == "have")["known"]
        == "downloaded"
    )

    # the listing is cached: a draft rule re-runs without YouTube
    FakeItem.calls.clear()
    draft = preview_item(
        "channel", CHANNEL_ID, rule=DownloadRule(min_duration=1000)
    )
    assert not FakeItem.calls
    assert draft["rule"] == {
        "scope": "channel",
        "target_id": CHANNEL_ID,
        "summary": ">16 min",
    }
    assert draft["verdict"]["download"] == 2  # long and nodur
    everything = preview_item("channel", CHANNEL_ID, rule=None, newest_n=3)
    assert everything["rule"] is None
    assert everything["verdict"]["total"] == 3
    assert everything["verdict"]["download"] == 3


def test_preview_channel_without_square_thumb(youtube):
    metas, _ = youtube
    metas["UCnew"] = {"uploader": None, "channel": None, "thumbnails": []}
    item = preview_item("channel", "UCnew")
    assert (item["title"], item["thumb_url"]) == ("UCnew", None)
    assert item["numbering"] == {"scheme": "date", "source": "default"}
    assert item["subscribed"] is False


def test_preview_playlist_and_video(youtube):
    metas, _ = youtube
    metas["PL1"] = {
        "title": "Best of",
        "channel": "Train Channel",
        "channel_id": CHANNEL_ID,
        "thumbnails": [{"url": "small"}, {"url": "big"}],
        "entries": [flat("p1", vid_type=None), flat("p2")],
    }
    metas["v00000001"] = {
        "id": "v00000001",
        "title": "One video",
        "channel": "Train Channel",
        "channel_id": CHANNEL_ID,
        "duration": 99.5,
        "thumbnail": "thumb",
    }
    Playlist.save_doc(
        playlist_doc("PL1", [], emby_numbering="playlist_position")
    )

    result = preview("PL1 v00000001 bad missing00")

    playlist, video = result["items"]
    assert (playlist["kind"], playlist["title"], playlist["thumb_url"]) == (
        "playlist",
        "Best of",
        "big",
    )
    assert playlist["sample"][0]["vid_type"] == "unknown"
    assert playlist["numbering"] == {
        "scheme": "playlist_position",
        "source": "playlist",
    }
    assert playlist["url"] == "PL1"
    assert (video["video_count"], video["thumb_url"]) == (1, "thumb")
    assert video["sample"][0]["duration_seconds"] == 99
    assert [e["url"] for e in result["errors"]] == ["bad", "missing00"]
    assert result["errors"][1]["error"] == "This video is unavailable"


@pytest.mark.parametrize("kind_id", ["UCgone", "PLgone"])
def test_preview_not_found(youtube, kind_id):
    # pylint: disable=unused-argument
    result = preview(kind_id)
    assert result["items"] == []
    assert result["errors"][0]["error"] == "This video is unavailable"


def jobs():
    """(type, target, params) of the queued jobs"""
    return [
        (j.type, j.target_id, j.params) for j in Job.objects.order_by("id")
    ]


def test_add_subscribe(youtube):
    # pylint: disable=unused-argument
    rule = {"min_duration": 3600}
    created, errors = add(
        "UC1 PL1 sv0000001 bad",
        "subscribe",
        rule=rule,
        numbering="year_frozen",
    )

    assert len(created) == 3 and errors[0]["url"] == "bad"
    rule_json = {
        "enabled": True,
        "min_duration": 3600,
        "max_duration": None,
        "vid_types": [],
        "title_include": "",
        "title_exclude": "",
        "published_after": None,
        "action": "ignore",
    }
    base = {"download_now": True, "subscribe": True}
    assert jobs() == [
        (
            "check_channel",
            "UC1",
            {**base, "save_rule": rule_json, "numbering": "year_frozen"},
        ),
        (
            "check_playlist",
            "PL1",
            {**base, "rule": rule_json, "numbering": "year_frozen"},
        ),
        (
            "fetch_video",
            "sv0000001",
            {**base, "vid_type": "shorts", "rule": rule_json},
        ),
    ]
    assert {j.priority for j in created} == {Priority.HIGH}


def test_add_once_and_newest(youtube):
    # pylint: disable=unused-argument
    add("UC1", "once", rule=None, download_now=False, created_by="agent")
    add("PL1", "newest_n", newest_n=5, priority=Priority.NORMAL)

    once, newest = Job.objects.order_by("id")
    assert once.params == {
        "download_now": False,
        "entries": [{"type": "channel", "url": "UC1", "vid_type": "unknown"}],
        "rule": None,
    }
    assert once.created_by == "agent"
    assert newest.params == {"download_now": True, "newest_n": 5}
    assert newest.priority == Priority.NORMAL

    # saving a rule on a one-off add is allowed for channels
    Job.objects.update(status="done")
    add("UC1", "once", rule={"max_duration": 60}, save_rule=True)
    assert "save_rule" in Job.objects.order_by("-id").first().params


@pytest.fixture(name="api")
def fixture_api(db):
    """DRF client as staff"""
    # pylint: disable=import-outside-toplevel,unused-argument
    from rest_framework.test import APIClient
    from user.models import Account

    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(Account.objects.create_superuser("a", "pw"))
    return client


def test_preview_view(youtube, api):
    metas, listings = youtube
    metas[CHANNEL_ID] = channel_meta()
    listings[CHANNEL_ID] = [flat("a", 100), flat("b", 5000)]
    url = "/api/add/preview/"

    stored = api.post(url, {"urls": CHANNEL_ID}, format="json").json()
    assert stored["items"][0]["verdict"]["download"] == 2

    draft = api.post(
        url,
        {"urls": CHANNEL_ID, "rule": {"max_duration": 3600}, "newest_n": 2},
        format="json",
    ).json()
    assert draft["items"][0]["verdict"]["skipped"] == 1

    none = api.post(url, {"urls": CHANNEL_ID, "rule": None}, format="json")
    assert none.json()["items"][0]["rule"] is None


def test_add_view(youtube, api):
    # pylint: disable=unused-argument
    response = api.post(
        "/api/add/",
        {"urls": "UC1 bad", "mode": "once", "priority": "normal"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["jobs"][0]["type"] == "check_channel"
    assert body["jobs"][0]["priority"] == Priority.NORMAL
    assert body["jobs"][0]["created_by"] == "user"
    assert body["errors"][0]["url"] == "bad"

    failed = api.post("/api/add/", {"urls": "bad", "mode": "once"})
    assert failed.status_code == 400
    assert failed.json() == {
        "jobs": [],
        "errors": [{"url": "bad", "error": "not a valid id_str: bad"}],
    }


def test_preview_sample_is_capped(youtube, monkeypatch):
    metas, listings = youtube
    monkeypatch.setattr(add_mod, "SAMPLE_SIZE", 2)
    metas["UCbig"] = channel_meta()
    listings["UCbig"] = [flat(f"v{i}") for i in range(5)]

    item = preview_item("channel", "UCbig")

    assert item["verdict"]["total"] == 5
    assert [s["youtube_id"] for s in item["sample"]] == ["v0", "v1"]

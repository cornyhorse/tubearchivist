"""rules: precedence, Rules page API, presets, preview, overrides"""

from datetime import date, datetime, timezone

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download, DownloadRule, VideoOverride
from download.src.rules import RuleSet
from jobs.models import Job, JobStatus, Priority
from jobs.src.engine import enqueue
from playlist.models import Playlist
from task.tests.helpers import (  # noqa: F401
    fixture_admin_client,
    fixture_user_client,
)
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"


def entry(**overrides):
    """what rules look at"""
    item = {
        "youtube_id": "v1",
        "channel_id": CHANNEL_ID,
        "duration_seconds": 600,
        "vid_type": "videos",
        "title": "A video",
        "published": None,
    }
    item.update(overrides)
    return item


@pytest.fixture(name="targets")
def fixture_targets():
    """a channel and two playlists in the archive"""
    Channel.save_doc(channel_doc())
    Playlist.save_doc(playlist_doc("PL1", []))
    Playlist.save_doc(playlist_doc("PL2", []))


def test_precedence(targets):
    # pylint: disable=unused-argument
    DownloadRule.objects.create(channel=None, max_duration=100)
    DownloadRule.objects.create(channel_id=CHANNEL_ID, max_duration=300)
    DownloadRule.objects.create(playlist_id="PL1", max_duration=1000)
    DownloadRule.objects.create(
        playlist_id="PL2", enabled=False, max_duration=1
    )
    rules = RuleSet()

    assert rules.check(entry(), "PL1") is None  # playlist rule allows
    assert rules.check(entry(), "PL2") is None  # disabled: no rule at all
    assert rules.check(entry())[2] == "too_long"  # channel rule
    assert rules.check(entry(channel_id="UCx"))[2] == "too_long"  # default
    assert rules.check(entry(), rule=None) is None  # one-off: no rule

    VideoOverride.objects.create(youtube_id="v1", override="download")
    VideoOverride.objects.create(youtube_id="v2", override="ignore")
    rules = RuleSet()
    assert rules.check(entry()) is None
    assert rules.check(entry(youtube_id="v2", duration_seconds=1)) == (
        "ignore",
        "always ignore, set by you",
        "always_ignore",
    )
    verdict = rules.verdict(entry(youtube_id="v2"))
    assert verdict["override"] == "ignore"
    assert verdict["rule"] == {
        "scope": "video",
        "target_id": "v2",
        "summary": "Always ignore",
    }
    playlist = rules.verdict(entry(youtube_id="v3"), "PL1")
    assert playlist["rule"] == {
        "scope": "playlist",
        "target_id": "PL1",
        "summary": "<16 min",
    }
    assert str(DownloadRule.objects.get(playlist_id="PL1")) == "rule for PL1"
    assert str(VideoOverride.objects.get(pk="v1")) == "v1: download"
    one_off = rules.verdict(entry(youtube_id="v9"), rule=DownloadRule())
    assert one_off["rule"]["summary"] == "Everything"


def test_rule_crud(admin_client, targets):
    # pylint: disable=unused-argument
    assert admin_client.get("/api/rules/default/").json() is None
    default = admin_client.put(
        "/api/rules/default/",
        {"vid_types": ["videos"], "min_duration": 600},
        format="json",
    ).json()
    assert default["summary"] == "No livestreams · No Shorts · >10 min"

    url = "/api/rules/playlist/PL1/"
    assert admin_client.get(url).json() is None
    saved = admin_client.put(url, {"max_duration": 3600}, format="json")
    assert saved.json()["summary"] == "<1 h"
    assert admin_client.get(url).json()["max_duration"] == 3600
    admin_client.put(
        f"/api/rules/channel/{CHANNEL_ID}/",
        {"title_exclude": "trailer", "action": "skip"},
        format="json",
    )

    rows = admin_client.get("/api/rules/").json()["data"]
    assert [(r["scope"], r["target_id"]) for r in rows] == [
        ("default", None),
        ("channel", CHANNEL_ID),
        ("playlist", "PL1"),
    ]
    assert rows[1]["target_title"] == "Test Channel"
    assert rows[2]["target_subscribed"] is False
    assert (
        rows[1]["rule"]["summary"] == "title not /trailer/ (others not added)"
    )

    assert admin_client.delete(url).status_code == 204
    assert admin_client.get(url).json() is None
    assert admin_client.delete("/api/rules/default/").status_code == 204
    assert not DownloadRule.objects.filter(
        channel__isnull=True, playlist__isnull=True
    ).exists()


@pytest.mark.parametrize(
    "url",
    ["/api/rules/channel/UCnope/", "/api/rules/playlist/PLnope/"],
)
def test_rule_target_not_found(admin_client, url):
    assert admin_client.get(url).status_code == 404
    assert admin_client.put(url, {}, format="json").status_code == 404
    assert admin_client.delete(url).status_code == 404
    assert admin_client.get("/api/rules/video/v1/").status_code == 404


def test_permissions(user_client):
    presets = user_client.get("/api/rules/presets/")
    assert presets.status_code == 200
    assert presets.json()[0]["key"] == "no_shorts"
    assert user_client.get("/api/rules/").status_code == 403
    schemes = user_client.get("/api/numbering/schemes/")
    assert schemes.status_code == 200
    assert user_client.get("/api/numbering/video/v1/").status_code == 403


def published(year):
    """epoch of Jan 1st"""
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())


def test_preview(admin_client, targets):
    # pylint: disable=unused-argument
    Video.save_doc(
        video_doc(
            youtube_id="long",
            published=published(2025),
            player={"duration": 5000},
        )
    )
    Download.upsert_doc(
        download_doc(
            youtube_id="short",
            vid_type="shorts",
            published=published(2024),
            duration_seconds=30,
        )
    )
    Download.upsert_doc(
        download_doc(youtube_id="nodate", published=None, channel_id="UCx")
    )
    DownloadRule.objects.create(channel_id=CHANNEL_ID, max_duration=3600)

    stored = admin_client.post(
        "/api/rules/preview/",
        {"scope": "channel", "target_id": CHANNEL_ID},
        format="json",
    ).json()
    assert (stored["total"], stored["download"], stored["skipped"]) == (
        2,
        1,
        1,
    )
    assert [v["youtube_id"] for v in stored["videos"]] == ["long", "short"]
    assert stored["reasons"] == [
        {"key": "too_long", "label": ">1 h", "count": 1}
    ]

    draft = admin_client.post(
        "/api/rules/preview/",
        {
            "scope": "default",
            "rule": {"vid_types": ["videos", "streams"]},
            "limit": 2,
        },
        format="json",
    ).json()
    assert draft["total"] == 2
    assert draft["reasons"][0]["label"] == "Shorts"
    assert draft["videos"][0]["verdict"]["rule"]["scope"] == "default"

    everything = admin_client.post(
        "/api/rules/preview/",
        {"scope": "default", "rule": None},
        format="json",
    ).json()
    assert (everything["total"], everything["skipped"]) == (3, 0)
    assert everything["videos"][-1]["youtube_id"] == "nodate"

    no_rule = admin_client.post(
        "/api/rules/preview/",
        {"scope": "playlist", "target_id": "PL1"},
        format="json",
    ).json()
    assert no_rule["total"] == 0

    missing = admin_client.post(
        "/api/rules/preview/",
        {"scope": "channel", "target_id": "UCnope"},
        format="json",
    )
    assert missing.status_code == 404
    no_target = admin_client.post(
        "/api/rules/preview/", {"scope": "playlist"}, format="json"
    )
    assert no_target.status_code == 404


def test_preview_playlist(admin_client, targets):
    # pylint: disable=unused-argument
    Playlist.save_doc(playlist_doc("PL3", ["short"]))
    Download.upsert_doc(
        download_doc(youtube_id="short", vid_type="shorts", published=None)
    )
    DownloadRule.objects.create(playlist_id="PL3", vid_types=["videos"])

    result = admin_client.post(
        "/api/rules/preview/",
        {"scope": "playlist", "target_id": "PL3"},
        format="json",
    ).json()

    assert (result["total"], result["skipped"]) == (1, 1)
    assert result["videos"][0]["verdict"]["rule"]["scope"] == "playlist"


def test_effective(admin_client, targets):
    # pylint: disable=unused-argument
    url = "/api/rules/effective/"
    assert admin_client.get(f"{url}?channel={CHANNEL_ID}").json() == {
        "source": None,
        "rule": None,
        "override": None,
    }
    DownloadRule.objects.create(channel=None, min_duration=60)
    DownloadRule.objects.create(playlist_id="PL1", max_duration=60)
    Download.upsert_doc(download_doc(youtube_id="v1", found_playlist_id="PL1"))
    Video.save_doc(video_doc(youtube_id="v2"))
    VideoOverride.objects.create(youtube_id="v2", override="ignore")

    channel = admin_client.get(f"{url}?channel={CHANNEL_ID}").json()
    assert channel["source"]["scope"] == "default"
    assert channel["rule"]["min_duration"] == 60
    playlist = admin_client.get(f"{url}?playlist=PL1").json()
    assert playlist["source"]["target_id"] == "PL1"
    found = admin_client.get(f"{url}?video=v1").json()
    assert found["source"]["scope"] == "playlist"
    downloaded = admin_client.get(f"{url}?video=v2").json()
    assert (downloaded["source"]["scope"], downloaded["override"]) == (
        "default",
        "ignore",
    )
    channel_rule = DownloadRule.objects.create(
        channel_id=CHANNEL_ID, max_duration=10
    )
    assert admin_client.get(f"{url}?video=v2").json()["source"] == {
        "scope": "channel",
        "target_id": CHANNEL_ID,
        "summary": "<10 s",
    }
    channel_rule.delete()
    assert admin_client.get(url).status_code == 400


def test_overrides(admin_client, targets):
    # pylint: disable=unused-argument
    Download.upsert_doc(
        download_doc(
            youtube_id="v1",
            status="ignore",
            ignore_reason="r",
            ignore_reason_key="too_long",
        )
    )
    Download.upsert_doc(download_doc(youtube_id="v2", title="Second"))
    enqueue("download_video", "v2", dispatch=False)
    Video.save_doc(video_doc(youtube_id="v3"))

    anyway = admin_client.put(
        "/api/rules/videos/v1/",
        {"override": "download", "note": "worth it"},
        format="json",
    ).json()
    assert (anyway["override"], anyway["note"], anyway["title"]) == (
        "download",
        "worth it",
        "Queued video",
    )
    v1 = Download.objects.get(pk="v1")
    assert (v1.status, v1.ignore_reason) == ("pending", None)
    job = Job.objects.get(target_id="v1")
    assert job.priority == Priority.HIGH

    admin_client.put(
        "/api/rules/videos/v2/", {"override": "ignore"}, format="json"
    )
    v2 = Download.objects.get(pk="v2")
    assert (v2.status, v2.ignore_reason_key) == ("ignore", "always_ignore")
    assert Job.objects.get(target_id="v2").status == JobStatus.CANCELLED

    known = admin_client.put(
        "/api/rules/videos/v3/", {"override": "ignore"}, format="json"
    ).json()
    assert known["channel_id"] == CHANNEL_ID
    unknown = admin_client.put(
        "/api/rules/videos/v4/", {"override": "ignore"}, format="json"
    ).json()
    assert unknown["title"] is None

    listed = admin_client.get("/api/rules/videos/").json()["data"]
    assert [i["youtube_id"] for i in listed] == ["v4", "v3", "v2", "v1"]

    assert admin_client.delete("/api/rules/videos/v1/").status_code == 204
    assert admin_client.delete("/api/rules/videos/v1/").status_code == 404


def test_found_override_actions(admin_client, targets):
    # pylint: disable=unused-argument
    Download.upsert_doc(download_doc(youtube_id="a"))
    Download.upsert_doc(download_doc(youtube_id="b"))

    anyway = admin_client.post(
        "/api/found/actions/",
        {"action": "download_anyway", "ids": ["a", "nope"]},
        format="json",
    ).json()
    assert (anyway["matched"], anyway["changed"], len(anyway["job_ids"])) == (
        1,
        1,
        1,
    )
    ignore = admin_client.post(
        "/api/found/actions/",
        {"action": "always_ignore", "filter": {"tab": "waiting"}},
        format="json",
    ).json()
    assert ignore["matched"] == 2
    assert set(VideoOverride.objects.values_list("override", flat=True)) == {
        "ignore"
    }

    item = admin_client.get("/api/found/?tab=ignored").json()["data"][0]
    assert item["verdict"]["override"] == "ignore"
    assert item["verdict"]["reason_key"] == "always_ignore"


def test_published_after_preset_value(targets):
    # pylint: disable=unused-argument
    rule = DownloadRule(published_after=date(2025, 1, 1))
    assert (
        RuleSet().check(entry(published=published(2024)), rule=rule)[2]
        == "published_before"
    )

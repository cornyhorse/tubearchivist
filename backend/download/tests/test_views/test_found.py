"""found videos API, /api/found/"""

from datetime import datetime, timedelta, timezone

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc, video_doc
from django.utils import timezone as dj_timezone
from download.models import Download, DownloadRule
from jobs.models import Job, JobStatus, Priority
from jobs.src.engine import enqueue
from task.tests.helpers import fixture_admin_client  # noqa: F401
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"
OCT_9 = int(datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp())


def add(youtube_id, **overrides):
    """a found video"""
    fields = {
        "youtube_id": youtube_id,
        "title": f"Video {youtube_id}",
        "timestamp": OCT_9,
        "duration_seconds": 600,
    }
    fields.update(overrides)
    return Download.upsert_doc(download_doc(**fields))


def get(client, query=""):
    """GET /api/found/ json"""
    response = client.get(f"/api/found/?{query}")
    assert response.status_code == 200, response.json()
    return response.json()


def test_tabs_and_states(admin_client, app_config):
    # pylint: disable=unused-argument
    add("wait", found_by_kind="channel_check", found_by_job=3)
    add("queued", timestamp=OCT_9 - 10)
    add("running", timestamp=OCT_9 - 20)
    add("ign", status="ignore")
    add("fail", message="HTTP 410")
    job, _ = enqueue("download_video", "queued", dispatch=False)
    job, _ = enqueue("download_video", "running", dispatch=False)
    Job.objects.filter(pk=job.pk).update(
        status=JobStatus.RUNNING, progress={"fraction": 0.25}
    )

    data = get(admin_client)

    assert data["counts"] == {
        "waiting": 3,
        "ignored": 1,
        "failed": 1,
        "downloaded": 0,
    }
    states = {i["youtube_id"]: i["state"] for i in data["data"]}
    assert states == {
        "wait": "waiting",
        "queued": "queued",
        "running": "downloading",
    }
    first = data["data"][0]
    assert first["found_by"] == {
        "kind": "channel_check",
        "label": "Channel check Oct 9",
        "job_id": 3,
        "playlist_id": None,
    }
    assert first["verdict"] == {
        "allowed": True,
        "action": "download",
        "reason_key": None,
        "reason": None,
        "rule": None,
        "override": None,
    }
    running = next(i for i in data["data"] if i["youtube_id"] == "running")
    assert running["job"]["progress"] == 0.25
    queued = next(i for i in data["data"] if i["youtube_id"] == "queued")
    assert queued["job"]["position"] == 1

    failed = get(admin_client, "tab=failed")["data"]
    assert (failed[0]["state"], failed[0]["error"]) == ("failed", "HTTP 410")
    ignored = get(admin_client, "tab=ignored")["data"]
    assert ignored[0]["state"] == "ignored"


def test_verdicts(admin_client, app_config):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    DownloadRule.objects.create(channel_id=CHANNEL_ID, vid_types=["videos"])
    DownloadRule.objects.create(channel=None, max_duration=3600)
    add(
        "ruled",
        status="ignore",
        ignore_reason="longer than 3600s (5000s)",
        ignore_reason_key="too_long",
        channel_id="UCother",
    )
    add("short", vid_type="shorts")
    add("byhand", status="ignore", found_by_kind="user")

    ignored = get(admin_client, "tab=ignored&sort=title")["data"]
    by_hand, ruled = ignored
    assert ruled["verdict"] == {
        "allowed": False,
        "action": "ignore",
        "reason_key": "too_long",
        "reason": "longer than 3600s (5000s)",
        "rule": {"scope": "default", "target_id": None, "summary": "<1 h"},
        "override": None,
    }
    assert by_hand["verdict"]["allowed"] is True
    assert by_hand["found_by"]["label"] == "Added by you"

    waiting = get(admin_client)["data"][0]
    assert waiting["verdict"]["reason_key"] == "vid_type"
    assert waiting["verdict"]["rule"] == {
        "scope": "channel",
        "target_id": CHANNEL_ID,
        "summary": "No livestreams · No Shorts",
    }


def test_filters_and_sort(admin_client, app_config):
    # pylint: disable=unused-argument
    add(
        "a",
        title="Steam trains",
        channel_name="Rail",
        duration_seconds=100,
        published=int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
    )
    add(
        "b",
        title="Coasters",
        vid_type="shorts",
        duration_seconds=5000,
        channel_id="UCb",
        found_by_kind="user",
        published=int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp()),
    )
    add(
        "c",
        status="ignore",
        ignore_reason="x",
        ignore_reason_key="too_long",
    )
    add("d", status="ignore")

    def ids(query):
        return [i["youtube_id"] for i in get(admin_client, query)["data"]]

    assert ids("q=steam") == ["a"]
    assert ids("q=rail") == ["a"]
    assert ids("q=b") == ["b"]
    assert ids("channel=UCb") == ["b"]
    assert ids("vid_type=shorts,streams") == ["b"]
    assert ids("min_duration=1000") == ["b"]
    assert ids("max_duration=1000") == ["a"]
    assert ids("published_from=2025-01-01") == ["b"]
    assert ids("published_to=2024-12-31") == ["a"]
    assert ids("found_by=user") == ["b"]
    assert ids("error=false&sort=duration") == ["a", "b"]
    assert ids("sort=-duration") == ["b", "a"]
    assert ids("sort=published") == ["a", "b"]
    assert ids("tab=ignored&skipped_by_rule=true") == ["c"]
    assert ids("tab=ignored&skipped_by_rule=false") == ["d"]
    assert ids("tab=ignored&reason_key=too_long") == ["c"]
    counts = get(admin_client, "q=steam")["counts"]
    assert counts == {"waiting": 1, "ignored": 0, "failed": 0, "downloaded": 0}

    paged = get(admin_client, "page_size=1&page=2&sort=title")
    assert [i["youtube_id"] for i in paged["data"]] == ["a"]
    assert paged["paginate"]["total_hits"] == 2


def test_downloaded_tab(admin_client, app_config):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    now = dj_timezone.now()
    recent = int(now.timestamp())
    Video.save_doc(
        video_doc(
            youtube_id="new",
            date_downloaded=recent,
            player={"duration": 3725},
            title="Long one",
        )
    )
    Video.save_doc(
        video_doc(
            youtube_id="short",
            date_downloaded=recent,
            player={"duration": 65},
        )
    )
    Video.save_doc(
        video_doc(
            youtube_id="old",
            date_downloaded=int((now - timedelta(days=30)).timestamp()),
        )
    )

    data = get(admin_client, "tab=downloaded&sort=-duration")

    assert [i["youtube_id"] for i in data["data"]] == ["new", "short"]
    item = data["data"][0]
    assert (item["state"], item["duration"]) == ("downloaded", "1:02:05")
    assert data["data"][1]["duration"] == "01:05"
    assert item["found_by"]["label"].startswith("Downloaded ")
    assert item["downloaded_at"] is not None
    assert data["counts"]["downloaded"] == 2

    def ids(query):
        found = get(admin_client, f"tab=downloaded&{query}")["data"]
        return [i["youtube_id"] for i in found]

    assert ids("downloaded_days=60&sort=found") == ["old", "new", "short"]
    assert ids("q=long") == ["new"]
    assert ids("channel=UCx") == []
    assert ids("vid_type=shorts") == []
    assert ids("min_duration=100&max_duration=4000") == ["new"]
    assert ids("error=true") == []


def test_downloaded_without_published(admin_client, app_config):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    Video.save_doc(
        video_doc(
            youtube_id="new",
            date_downloaded=int(dj_timezone.now().timestamp()),
        )
    )
    Video.objects.update(published=None)
    data = get(admin_client, "tab=downloaded")["data"]
    assert data[0]["verdict"]["allowed"] is True


def test_found_unknown_kind_label(admin_client, app_config):
    # pylint: disable=unused-argument
    add("x", found_by_kind="mystery")
    item = get(admin_client)["data"][0]
    assert item["found_by"]["kind"] == "unknown"
    assert item["found_by"]["label"] == "Found Oct 9"


def action(client, body, status=200):
    """POST /api/found/actions/"""
    response = client.post("/api/found/actions/", body, format="json")
    assert response.status_code == status, response.json()
    return response.json()


def test_download_actions(admin_client, app_config):
    # pylint: disable=unused-argument
    add("a")
    add("b", status="ignore", ignore_reason="r", ignore_reason_key="vid_type")
    add("c", message="failed before")

    now = action(admin_client, {"action": "download_now", "ids": ["a", "b"]})
    assert (now["matched"], now["changed"], len(now["job_ids"])) == (2, 2, 2)
    b = Download.objects.get(pk="b")
    assert (b.status, b.ignore_reason, b.ignore_reason_key) == (
        "pending",
        None,
        None,
    )
    assert {j.priority for j in Job.objects.all()} == {Priority.HIGH}

    later = action(
        admin_client,
        {"action": "download", "filter": {"tab": "failed"}},
    )
    assert later["matched"] == 1
    assert Download.objects.get(pk="c").message is None
    assert Job.objects.get(target_id="c").priority == Priority.NORMAL


def test_ignore_forget_clear(admin_client, app_config):
    # pylint: disable=unused-argument
    add("a")
    add("b")
    add("c", message="boom")
    enqueue("download_video", "a", dispatch=False)

    ignored = action(admin_client, {"action": "ignore", "ids": ["a"]})
    assert ignored["changed"] == 1
    assert Download.objects.get(pk="a").status == "ignore"
    assert Job.objects.get(target_id="a").status == JobStatus.CANCELLED

    cleared = action(
        admin_client, {"action": "clear_error", "ids": ["b", "c"]}
    )
    assert (cleared["matched"], cleared["changed"]) == (2, 1)

    forgot = action(
        admin_client, {"action": "forget", "filter": {"tab": "waiting"}}
    )
    assert forgot["changed"] == 2
    assert list(Download.objects.values_list("pk", flat=True)) == ["a"]


def test_action_errors(admin_client):
    downloaded = action(
        admin_client,
        {"action": "ignore", "filter": {"tab": "downloaded"}},
        status=400,
    )
    assert "downloaded" in downloaded["error"]

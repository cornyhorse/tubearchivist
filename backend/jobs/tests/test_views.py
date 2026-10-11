"""work queue API, /api/queue/"""

from datetime import timedelta

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc
from django.utils import timezone
from download.models import Download
from jobs.models import Job, JobStatus, Lane, Priority
from jobs.src.engine import enqueue, ensure_lanes
from task.models import CustomPeriodicTask
from task.src.config_schedule import ScheduleBuilder

pytestmark = pytest.mark.django_db


def make(job_type="check_channel", target="UC1", **kwargs):
    """a queued job"""
    job, _ = enqueue(job_type, target, dispatch=False, **kwargs)
    return job


def test_summary_any_user(user_client):
    make()
    claimed = make(target="UC2")
    Job.objects.filter(pk=claimed.pk).update(status=JobStatus.RUNNING)
    failed = make(target="UC3")
    Job.objects.filter(pk=failed.pk).update(
        status=JobStatus.FAILED, finished_at=timezone.now()
    )

    data = user_client.get("/api/queue/summary/").json()

    assert (data["running"], data["queued"], data["failed_24h"]) == (1, 1, 1)
    assert [i["name"] for i in data["lanes"]] == [
        "scan",
        "download",
        "postprocess",
        "maintenance",
    ]
    scan = data["lanes"][0]
    assert (scan["running"], scan["queued"], scan["concurrency"]) == (1, 1, 2)


def test_list_run_order_and_positions(admin_client):
    low = make(target="low", priority=Priority.LOW)
    high = make(target="high", priority=Priority.HIGH)
    run = make(target="run")
    Job.objects.filter(pk=run.pk).update(
        status=JobStatus.RUNNING,
        progress={"fraction": 0.5, "message": "half"},
    )
    paused = make(target="paused")
    Job.objects.filter(pk=paused.pk).update(status=JobStatus.PAUSED)
    make("emby_write", None)

    response = admin_client.get("/api/queue/jobs/?status=active&lane=scan")
    data = response.json()["data"]

    assert [i["id"] for i in data] == [run.pk, high.pk, low.pk, paused.pk]
    assert [i["position"] for i in data] == [None, 1, 2, None]
    assert data[0]["progress"]["fraction"] == 0.5
    assert data[0]["actions"] == ["stop", "pause"]
    assert data[1]["type_label"] == "Check channel for new videos"
    assert data[1]["target"] == {
        "kind": "channel",
        "id": "high",
        "title": None,
    }
    assert response.json()["paginate"]["total_hits"] == 4


def test_list_history_and_filters(admin_client):
    old = make(target="old")
    new = make(target="new")
    now = timezone.now()
    Job.objects.filter(pk=old.pk).update(
        status=JobStatus.DONE,
        started_at=now - timedelta(minutes=10),
        finished_at=now - timedelta(minutes=5),
    )
    Job.objects.filter(pk=new.pk).update(
        status=JobStatus.FAILED, finished_at=now, error="boom"
    )
    child = make("download_video", "v1", parent=old, created_by="rule")

    history = admin_client.get("/api/queue/jobs/?status=history").json()
    assert [i["id"] for i in history["data"]] == [new.pk, old.pk]
    assert history["data"][1]["duration_seconds"] == 300
    assert history["data"][1]["children"] == 1

    everything = admin_client.get("/api/queue/jobs/").json()["data"]
    assert [i["id"] for i in everything] == [child.pk, new.pk, old.pk]

    for query in [
        "type=download_video",
        "target=v1",
        f"parent={old.pk}",
        "created_by=rule",
        "status=queued,running",
    ]:
        found = admin_client.get(f"/api/queue/jobs/?{query}").json()["data"]
        assert [i["id"] for i in found] == [child.pk], query

    paged = admin_client.get("/api/queue/jobs/?page_size=1&page=2").json()
    assert [i["id"] for i in paged["data"]] == [new.pk]

    bad = admin_client.get("/api/queue/jobs/?status=nope")
    assert bad.status_code == 400


def test_list_admin_only(user_client):
    assert user_client.get("/api/queue/jobs/").status_code == 403


def test_create_job(admin_client, django_capture_on_commit_callbacks):
    Channel.save_doc(channel_doc())
    url = "/api/queue/jobs/"
    body = {"type": "check_channel", "target_id": "UCchannel000000000000001"}

    with django_capture_on_commit_callbacks(execute=True):
        created = admin_client.post(url, body, format="json")
    assert created.status_code == 201
    job = created.json()["job"]
    # answered before the pump starts it
    assert (job["status"], job["created_by"], job["priority"]) == (
        "queued",
        "user",
        Priority.HIGH,
    )
    assert job["target"]["title"] == "Test Channel"
    assert Job.objects.get().status == JobStatus.RUNNING

    queued = admin_client.post(url, {**body, "priority": "low"}, format="json")
    merged = admin_client.post(url, body, format="json")
    assert queued.status_code == 201
    assert merged.status_code == 200 and merged.json()["merged"]
    assert merged.json()["job"]["priority"] == Priority.HIGH

    missing = admin_client.post(url, {"type": "check_channel"}, format="json")
    assert missing.status_code == 400

    whole = admin_client.post(
        url, {"type": "check_subscriptions", "target_id": "x"}, format="json"
    )
    assert whole.json()["job"]["target"]["id"] is None


@pytest.mark.parametrize(
    "job_type, setup, expected",
    [
        ("fetch_video", lambda: None, None),
        (
            "fetch_video",
            lambda: Download.upsert_doc(download_doc(youtube_id="v1")),
            "Queued video",
        ),
        ("check_playlist", lambda: None, None),
    ],
)
def test_create_target_titles(admin_client, job_type, setup, expected):
    setup()
    response = admin_client.post(
        "/api/queue/jobs/",
        {"type": job_type, "target_id": "v1"},
        format="json",
    )
    assert response.json()["job"]["target"]["title"] == expected


def test_job_detail_and_action(admin_client):
    job = make()
    detail = admin_client.get(f"/api/queue/jobs/{job.pk}/").json()
    assert detail["actions"] == ["cancel", "pause", "move_to_top"]
    assert detail["position"] == 1
    assert admin_client.get("/api/queue/jobs/999/").status_code == 404

    url = f"/api/queue/jobs/{job.pk}/action/"
    paused = admin_client.post(url, {"action": "pause"}, format="json")
    assert paused.json()["status"] == "paused"
    conflict = admin_client.post(url, {"action": "stop"}, format="json")
    assert conflict.status_code == 409
    missing = admin_client.post(
        "/api/queue/jobs/999/action/", {"action": "stop"}, format="json"
    )
    assert missing.status_code == 404


def test_bulk_action(admin_client):
    first, second = make(target="a"), make(target="b")
    Job.objects.filter(pk=second.pk).update(status=JobStatus.DONE)

    response = admin_client.post(
        "/api/queue/jobs/action/",
        {"action": "cancel", "ids": [first.pk, second.pk, 999]},
        format="json",
    )

    assert response.json() == {
        "changed": [first.pk],
        "skipped": {
            str(second.pk): "can't cancel a done job",
            "999": "not found",
        },
    }


def test_lanes(admin_client):
    lanes = admin_client.get("/api/queue/lanes/").json()
    assert lanes[1]["name"] == "download"
    assert lanes[1]["min_interval_seconds"] == 10

    changed = admin_client.patch(
        "/api/queue/lanes/scan/",
        {"paused": True, "concurrency": 4},
        format="json",
    )
    assert (changed.json()["paused"], changed.json()["concurrency"]) == (
        True,
        4,
    )
    assert Lane.objects.get(name="scan").paused
    assert admin_client.patch("/api/queue/lanes/nope/", {}).status_code == 404
    bad = admin_client.patch(
        "/api/queue/lanes/scan/", {"concurrency": 0}, format="json"
    )
    assert bad.status_code == 400


def test_lane_unknown_name_in_db(admin_client):
    ensure_lanes()
    Lane.objects.create(name="extra")
    names = [i["name"] for i in admin_client.get("/api/queue/lanes/").json()]
    assert names[-1] == "extra"


@pytest.fixture(name="schedules")
def fixture_schedules():
    """two schedules, as ta_startup creates them"""
    builder = ScheduleBuilder()
    builder.update_schedule("update_subscribed", "0 8 *", None)
    builder.update_schedule("run_backup", "0 18 0", None)
    off = builder.update_schedule("version_check", "0 11 *", None)
    off.enabled = False
    off.save()


def test_scheduled_list(admin_client, schedules):
    # pylint: disable=unused-argument
    job = make("check_subscriptions", None, created_by="schedule")

    data = admin_client.get("/api/queue/scheduled/").json()

    names = [i["name"] for i in data]
    assert names[-1] == "version_check"
    subs = next(i for i in data if i["name"] == "update_subscribed")
    assert subs["label"] == "Check channels for new videos"
    assert subs["job_type"] == "check_subscriptions"
    assert subs["lane"] == "scan"
    assert subs["schedule"] == "0 8 *"
    assert subs["next_run"] is not None
    assert subs["last_job"] == job.pk
    assert data[-1]["next_run"] is None
    assert CustomPeriodicTask.objects.get(name="run_backup").kwargs == (
        '{"created_by": "schedule"}'
    )


def test_scheduled_toggle(admin_client, schedules):
    # pylint: disable=unused-argument
    url = "/api/queue/scheduled/run_backup/"
    response = admin_client.patch(url, {"enabled": False}, format="json")
    assert response.json()["enabled"] is False
    assert response.json()["next_run"] is None
    missing = admin_client.patch(
        "/api/queue/scheduled/nope/", {"enabled": True}, format="json"
    )
    assert missing.status_code == 404


def test_scheduled_run_now(admin_client, schedules):
    # pylint: disable=unused-argument
    response = admin_client.post("/api/queue/scheduled/run_backup/run/")
    assert response.status_code == 201
    assert response.json()["job"]["type"] == "backup"
    assert response.json()["job"]["created_by"] == "user"

    nothing = admin_client.post("/api/queue/scheduled/download_pending/run/")
    assert nothing.status_code == 200
    assert nothing.json() == {"job": None, "merged": False}

    Download.upsert_doc(download_doc(youtube_id="v1"))
    downloads = admin_client.post("/api/queue/scheduled/download_pending/run/")
    assert downloads.json()["job"]["target"]["id"] == "v1"

    missing = admin_client.post("/api/queue/scheduled/nope/run/")
    assert missing.status_code == 404


def test_old_task_stop_reaches_jobs(admin_client):
    job = make()
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING)
    url = f"/api/task/by-id/job-{job.pk}/"

    assert admin_client.post(url, {"command": "stop"}).status_code == 204
    assert Job.objects.get(pk=job.pk).stop_requested
    again = admin_client.post(url, {"command": "kill"})
    assert again.status_code == 400
    missing = admin_client.post(
        "/api/task/by-id/job-999/", {"command": "stop"}
    )
    assert missing.status_code == 404


def test_unknown_job_type_label():
    # pylint: disable=import-outside-toplevel
    from jobs.src.present import job_label

    assert job_label("media_notify") == "Media notify"

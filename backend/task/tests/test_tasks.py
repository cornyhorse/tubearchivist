"""the scheduler's task names only queue jobs in the work queue"""

import pytest
from common.tests.docs import download_doc
from download.models import Download
from jobs.models import Job, JobStatus, Priority
from task import tasks

pytestmark = pytest.mark.django_db


def jobs():
    """(type, target, created_by, priority, params) of every job"""
    return [
        (j.type, j.target_id, j.created_by, j.priority, j.params)
        for j in Job.objects.order_by("id")
    ]


def test_update_subscribed_user_and_schedule(
    no_broker, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        first = tasks.update_subscribed.run()
    running = Job.objects.get()
    assert first == f"queued job #{running.pk}"
    assert running.status == JobStatus.RUNNING
    assert ("run_job", (running.pk,), {}) in no_broker

    # a repeat while it runs is queued behind it, never dropped
    second = tasks.update_subscribed.run(created_by="schedule")
    # and a third merges into the waiting one
    third = tasks.update_subscribed.run()

    waiting = Job.objects.get(status=JobStatus.QUEUED)
    assert second == f"queued job #{waiting.pk}"
    assert third == f"merged into job #{waiting.pk}"
    assert (waiting.created_by, waiting.priority, waiting.merged) == (
        "schedule",
        Priority.HIGH,
        1,
    )


def test_schedule_runs_low_priority():
    tasks.thumbnail_check.run(created_by="schedule")
    assert jobs() == [("thumbnail_check", None, "schedule", Priority.LOW, {})]


@pytest.mark.parametrize("auto_only, expected", [(False, 2), (True, 1)])
def test_download_pending(auto_only, expected):
    Download.upsert_doc(download_doc(youtube_id="a", timestamp=1))
    Download.upsert_doc(
        download_doc(youtube_id="b", timestamp=2, auto_start=True)
    )
    Download.upsert_doc(download_doc(youtube_id="c", message="failed"))
    Download.upsert_doc(download_doc(youtube_id="d", status="ignore"))

    result = tasks.download_pending.run(auto_only=auto_only)

    assert result == f"queued {expected} downloads"
    targets = [i[1] for i in jobs()]
    assert targets == (["b", "a"] if not auto_only else ["b"])
    assert {i[0] for i in jobs()} == {"download_video"}


def test_extract_download_parses_links(monkeypatch):
    parsed = [
        {"type": "video", "url": "vid00000001", "vid_type": "shorts"},
        {"type": "channel", "url": "UC1", "vid_type": "videos", "limit": 5},
        {"type": "playlist", "url": "PL1", "vid_type": "unknown"},
    ]

    class FakeParser:
        def __init__(self, url_str):
            assert url_str == "links"

        def parse(self):
            return parsed

    monkeypatch.setattr(tasks, "Parser", FakeParser)

    result = tasks.extrac_dl.run("links", auto_start=True, flat=True)

    assert result.count("queued job") == 3
    base = {"download_now": True, "flat": True, "force": False}
    base["status"] = "pending"
    assert jobs() == [
        (
            "fetch_video",
            "vid00000001",
            "user",
            Priority.HIGH,
            {**base, "vid_type": "shorts"},
        ),
        (
            "check_channel",
            "UC1",
            "user",
            Priority.HIGH,
            {
                **base,
                "vid_type": "videos",
                "entries": [parsed[1]],
                "newest_n": 5,
            },
        ),
        (
            "check_playlist",
            "PL1",
            "user",
            Priority.HIGH,
            {**base, "vid_type": "unknown"},
        ),
    ]


def test_extract_download_parsed_list_with_enum():
    # pylint: disable=import-outside-toplevel
    from video.src.constants import VideoTypeEnum

    entry = {"type": "video", "url": "v1", "vid_type": VideoTypeEnum.VIDEOS}
    tasks.extrac_dl.run([entry], status="ignore")

    assert jobs()[0][4]["vid_type"] == "videos"
    assert jobs()[0][4]["status"] == "ignore"


def test_check_reindex(monkeypatch):
    extracted = []

    class FakeManual:
        def __init__(self, extract_videos):
            self.extract_videos = extract_videos

        def extract_data(self, data):
            extracted.append((data, self.extract_videos))

    monkeypatch.setattr(tasks, "ReindexManual", FakeManual)

    tasks.check_reindex.run(created_by="schedule")
    job = Job.objects.get()
    assert (job.type, job.params) == ("reindex", {"populate": True})
    job.status = JobStatus.DONE
    job.save()

    tasks.check_reindex.run(data={"video": ["v1"]}, extract_videos=True)
    assert extracted == [({"video": ["v1"]}, True)]
    assert Job.objects.order_by("-id").first().params == {"populate": False}


@pytest.mark.parametrize(
    "task, args, job_type, target, params",
    [
        (
            tasks.manual_import,
            (True, False),
            "manual_import",
            None,
            {"ignore_error": True, "prefer_local": False},
        ),
        (tasks.run_backup, (), "backup", None, {"reason": "auto"}),
        (
            tasks.run_restore_backup,
            ("b.zip",),
            "restore",
            "b.zip",
            {"filename": "b.zip"},
        ),
        (
            tasks.rescan_filesystem,
            (False, True),
            "filesystem_rescan",
            None,
            {"ignore_error": False, "prefer_local": True},
        ),
        (tasks.re_sync_metadata, (), "resync_metadata", None, {}),
        (
            tasks.subscribe_to,
            ("UC1 UC2", "channel"),
            "subscribe",
            None,
            {"urls": "UC1 UC2", "expected_type": "channel"},
        ),
        (
            tasks.index_channel_playlists,
            ("UC1",),
            "index_playlists",
            "UC1",
            {},
        ),
        (tasks.emby_sync, (), "emby_write", None, {}),
    ],
)
def test_user_tasks(task, args, job_type, target, params):
    # pylint: disable=too-many-arguments
    assert task.run(*args).startswith("queued job #")
    assert jobs() == [(job_type, target, "user", Priority.HIGH, params)]


@pytest.mark.parametrize(
    "task, job_type",
    [(tasks.db_snapshot, "snapshot"), (tasks.version_check, "version_check")],
)
def test_system_schedules(task, job_type):
    task.run()
    assert jobs() == [(job_type, None, "schedule", Priority.LOW, {})]

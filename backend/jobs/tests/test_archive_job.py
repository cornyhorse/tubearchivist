"""the archive_import job and the archived-target guards of other jobs"""

import pytest
from jobs.models import JobStatus
from jobs.src import handlers
from jobs.src.context import JobContext
from jobs.src.engine import enqueue
from jobs.src.registry import JobError, get_spec
from video.src.archive_import import ArchiveImportError, ImportReport

pytestmark = pytest.mark.django_db


def running(job_type, target=None, **kwargs):
    """a claimed job and its context"""
    job, _ = enqueue(job_type, target, dispatch=False, **kwargs)
    job.status = JobStatus.RUNNING
    job.save()
    return job, JobContext(job)


@pytest.fixture(name="importer")
def fixture_importer(monkeypatch):
    """replace the importer, the test sets its report or error"""
    state = {"report": ImportReport(channel_id="arch_x"), "calls": []}

    class Fake:
        """records its arguments"""

        def __init__(self, root, **kwargs):
            state["calls"].append((root, kwargs))

        def run(self):
            """the prepared report, or raise the prepared error"""
            if "error" in state:
                raise state["error"]
            return state["report"]

    monkeypatch.setattr(handlers, "ArchiveImporter", Fake)
    state["emby"] = []
    monkeypatch.setattr(
        handlers, "queue_emby_write", lambda ctx: state["emby"].append(ctx)
    )
    return state


def test_registered_in_the_maintenance_lane():
    spec = get_spec("archive_import")
    assert spec.lane == "maintenance" and spec.target_kind == "none"


def test_job_imports_and_queues_an_emby_write(importer):
    importer["report"].imported = ["a.mkv", "b.mkv"]
    _, ctx = running(
        "archive_import",
        params={"root": "/in", "channel_name": "Music", "mode": "copy"},
    )

    result = handlers.archive_import(ctx)

    assert result["imported"] == 2
    assert importer["calls"][0][0] == "/in"
    kwargs = importer["calls"][0][1]
    assert kwargs["channel_name"] == "Music" and kwargs["mode"] == "copy"
    assert kwargs["channel_id"] is None and kwargs["task"] is ctx
    assert importer["emby"] == [ctx]


def test_job_without_new_files_does_not_touch_emby(importer):
    importer["report"].existing = ["a.mkv"]
    _, ctx = running("archive_import", params={"root": "/in"})

    assert handlers.archive_import(ctx)["imported"] == 0
    assert not importer["emby"]
    assert importer["calls"][0][1]["mode"] == "auto"


def test_job_fails_when_files_failed(importer):
    importer["report"].imported = ["a.mkv"]
    importer["report"].failed = [("b.mkv", "no video stream")]
    _, ctx = running("archive_import", params={"root": "/in"})

    with pytest.raises(JobError, match="b.mkv: no video stream"):
        handlers.archive_import(ctx)

    # what did import is still written out
    assert importer["emby"] == [ctx]


def test_job_reports_a_bad_folder(importer):
    importer["error"] = ArchiveImportError("/in is not a folder")
    _, ctx = running("archive_import", params={"root": "/in"})

    with pytest.raises(JobError, match="not a folder"):
        handlers.archive_import(ctx)


@pytest.mark.parametrize(
    "handler, job_type",
    [
        (handlers.check_channel, "check_channel"),
        (handlers.fetch_video, "fetch_video"),
        (handlers.index_playlists, "index_playlists"),
    ],
)
def test_youtube_jobs_skip_archived_targets(handler, job_type):
    _, ctx = running(job_type, "arch_0123456789abcdef")

    assert handler(ctx) == {"skipped": "archived items have no YouTube data"}

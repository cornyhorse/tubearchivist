"""JobContext: progress and stop signal for the worker classes"""

import pytest
from common.src.ta_redis import RedisArchivist
from jobs.models import Job
from jobs.src import context
from jobs.src.context import JobContext
from jobs.src.engine import enqueue

pytestmark = pytest.mark.django_db


@pytest.fixture(name="clock")
def fixture_clock(monkeypatch):
    """controllable monotonic clock"""
    now = [1000.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: now[0])
    return now


def make(legacy_name=None):
    """context of a new job"""
    job, _ = enqueue("emby_write", dispatch=False)
    return JobContext(job, legacy_name=legacy_name)


def stored(ctx):
    """progress in the database"""
    return Job.objects.get(pk=ctx.job.pk).progress


def test_progress_is_throttled(clock):
    ctx = make()
    ctx.send_progress(["Downloading", "a video"], progress=0.1)
    assert stored(ctx)["message"] == "Downloading · a video"
    assert stored(ctx)["fraction"] == 0.1

    clock[0] += 0.5
    ctx.send_progress(["later"], progress=0.2, speed="1MiB/s")
    assert stored(ctx)["fraction"] == 0.1
    assert ctx.job.progress["speed"] == "1MiB/s"

    ctx.send_progress(["broken"], level="error")
    assert stored(ctx)["message"] == "broken"
    assert stored(ctx)["fraction"] is None

    ctx.send_progress(["done"], progress=1)
    assert stored(ctx)["fraction"] == 1

    clock[0] += 1.5
    ctx.send_progress(["x"], current=3, total=9, eta_seconds=12)
    assert (stored(ctx)["current"], stored(ctx)["total"]) == (3, 9)
    assert stored(ctx)["eta_seconds"] == 12
    assert Job.objects.get(pk=ctx.job.pk).heartbeat_at is not None


def test_legacy_message(redis_ns, monkeypatch):
    # pylint: disable=unused-argument
    monkeypatch.setattr(RedisArchivist, "bg_save", lambda self: None)
    ctx = make(legacy_name="emby_sync")
    ctx.send_progress(["Writing"], progress=0.5, title="Emby")
    message = RedisArchivist().get_message_dict(
        f"message:setting:embysync:job{ctx.job.pk}"
    )
    assert message["id"] == f"job-{ctx.job.pk}"
    assert message["messages"] == ["Writing"]
    assert (message["progress"], message["title"]) == (0.5, "Emby")

    unknown = make(legacy_name="nope")
    unknown.send_progress(["ignored"])


def test_is_stopped(clock):
    ctx = make()
    assert ctx.is_stopped() is False
    Job.objects.filter(pk=ctx.job.pk).update(stop_requested=True)
    # checked again only after the interval
    assert ctx.is_stopped() is False
    clock[0] += 2
    assert ctx.is_stopped() is True
    assert ctx.is_stopped() is True
    assert ctx.params == {}


def test_legacy_message_without_title(redis_ns, monkeypatch):
    # pylint: disable=unused-argument
    monkeypatch.setattr(RedisArchivist, "bg_save", lambda self: None)
    ctx = make(legacy_name="emby_sync")
    ctx.send_progress(["Writing"])
    message = RedisArchivist().get_message_dict(
        f"message:setting:embysync:job{ctx.job.pk}"
    )
    assert message["title"] == "Sync Emby library"

"""work queue engine: merge, lanes, limits, outcomes, actions, recovery"""

from datetime import timedelta

import pytest
from django.utils import timezone
from jobs.models import Job, JobStatus, Lane, Priority
from jobs.src import engine
from jobs.src.engine import (
    ActionNotAllowed,
    active_jobs,
    apply_action,
    cancel_waiting,
    enqueue,
    execute,
    pump,
    recover_after_restart,
)
from jobs.src.registry import SPECS, Defer, JobError, get_spec, register

pytestmark = pytest.mark.django_db


@pytest.fixture(name="handlers")
def fixture_handlers():
    """test job types: t_scan (scan lane), t_dl (download lane)"""
    calls = []
    behaviour = {}

    def handler(ctx):
        calls.append(ctx.job.pk)
        action = behaviour.get(ctx.job.target_id)
        if callable(action):
            return action(ctx)
        return {"ok": ctx.job.target_id}

    register("t_scan", "scan", "Test scan", "channel", max_attempts=2)(handler)
    register("t_dl", "download", "Test download", "video")(handler)
    register(
        "t_note", "maintenance", "Notified", legacy_name="thumbnail_check"
    )(handler)
    yield calls, behaviour
    for name in ["t_scan", "t_dl", "t_note"]:
        SPECS.pop(name)


def make(job_type="t_scan", target="a", **kwargs):
    """enqueue without dispatch"""
    job, _ = enqueue(job_type, target, dispatch=False, **kwargs)
    return job


def statuses():
    """{target: status}"""
    return dict(Job.objects.values_list("target_id", "status"))


def test_get_spec_unknown():
    with pytest.raises(KeyError):
        get_spec("nope")


def test_enqueue_merges_waiting(handlers):
    # pylint: disable=unused-argument
    first, merged = enqueue(
        "t_scan", "a", params={"x": 1}, priority=Priority.LOW, dispatch=False
    )
    assert not merged
    again, merged = enqueue(
        "t_scan",
        "a",
        target_title="Channel A",
        params={"y": 2},
        priority=Priority.HIGH,
        dispatch=False,
    )

    assert merged and again.pk == first.pk
    again.refresh_from_db()
    assert again.priority == Priority.HIGH
    assert again.params == {"x": 1, "y": 2}
    assert (again.target_title, again.merged) == ("Channel A", 1)
    assert (again.lane, again.max_attempts) == ("scan", 2)

    # lower priority doesn't lower it, an empty title keeps the old one
    enqueue("t_scan", "a", priority=Priority.LOW, dispatch=False)
    again.refresh_from_db()
    assert (again.priority, again.target_title) == (Priority.HIGH, "Channel A")


def test_enqueue_merge_clears_delay(handlers):
    # pylint: disable=unused-argument
    later = timezone.now() + timedelta(minutes=5)
    job = make(not_before=later)
    make(not_before=later)
    job.refresh_from_db()
    assert job.not_before == later
    make()
    job.refresh_from_db()
    assert job.not_before is None


def test_enqueue_race(handlers, monkeypatch):
    # pylint: disable=unused-argument
    real = engine._merge_or_create
    calls = []
    other = make()

    def racy(key, fields):
        calls.append(key)
        if len(calls) == 1:
            # didn't see the other job yet: the insert collides
            return Job.objects.create(dedupe_key=key, **fields), False
        return real(key, fields)

    monkeypatch.setattr(engine, "_merge_or_create", racy)
    job, merged = enqueue("t_scan", "a", dispatch=False)
    assert merged and job.pk == other.pk and job.merged == 1
    assert Job.objects.count() == 1


def test_enqueue_gives_up(handlers, monkeypatch):
    # pylint: disable=unused-argument
    def always(key, fields):
        raise engine.IntegrityError("duplicate")

    monkeypatch.setattr(engine, "_merge_or_create", always)
    with pytest.raises(RuntimeError):
        enqueue("t_scan", "a", dispatch=False)


def test_pump_concurrency_and_order(
    handlers, no_broker, django_capture_on_commit_callbacks
):
    # pylint: disable=unused-argument
    make(target="low", priority=Priority.LOW)
    make(target="first")
    make(target="second")
    make(target="top", priority=Priority.HIGH)
    make("t_dl", "dl1")

    with django_capture_on_commit_callbacks(execute=True):
        started = pump()

    # scan lane runs 2 at once, the download lane runs alongside
    assert statuses() == {
        "top": "running",
        "first": "running",
        "second": "queued",
        "low": "queued",
        "dl1": "running",
    }
    assert len(started) == 3
    sent = [args[0] for name, args, _ in no_broker if name == "run_job"]
    assert sorted(sent) == sorted(started)
    job = Job.objects.get(target_id="top")
    assert (job.attempts, job.started_at is not None) == (1, True)

    # full lanes start nothing more
    assert pump() == []


def test_pump_paused_lane(handlers):
    # pylint: disable=unused-argument
    engine.ensure_lanes()
    Lane.objects.filter(name="scan").update(paused=True)
    make()
    assert pump() == []
    assert statuses() == {"a": "queued"}


def test_pump_rate_limit(handlers, no_broker):
    engine.ensure_lanes()
    Lane.objects.filter(name="scan").update(
        concurrency=3, min_interval_seconds=30
    )
    make(target="a")
    make(target="b")

    assert len(pump()) == 1
    # the next start waits for the interval, a delayed pump is scheduled
    assert len(pump()) == 0
    assert [i for i in no_broker if i[0] == "jobs_pump"]

    Lane.objects.filter(name="scan").update(
        last_started_at=timezone.now() - timedelta(seconds=31)
    )
    assert len(pump()) == 1


def test_pump_not_before_and_dependency(handlers, no_broker):
    later = timezone.now() + timedelta(minutes=5)
    make(target="later", not_before=later)
    blocker = make("t_dl", "blocker")
    make(target="after", depends_on=blocker)

    started = pump()

    assert started == [blocker.pk]
    assert statuses()["later"] == "queued"
    assert statuses()["after"] == "queued"
    assert any(i[0] == "jobs_pump" for i in no_broker)

    Job.objects.filter(pk=blocker.pk).update(status=JobStatus.DONE)
    pump()
    assert statuses()["after"] == "running"


def test_one_wakeup_at_a_time(handlers, no_broker):
    # pylint: disable=unused-argument
    make(target="later", not_before=timezone.now() + timedelta(seconds=30))

    pump()
    pump()

    wakeups = [i for i in no_broker if i[0] == "jobs_pump"]
    assert len(wakeups) == 1
    # an earlier due time still gets its own wake-up
    make(target="sooner", not_before=timezone.now() + timedelta(seconds=5))
    pump()
    assert len([i for i in no_broker if i[0] == "jobs_pump"]) == 2


def test_schedule_next_pump_nothing_due(handlers, no_broker):
    # pylint: disable=unused-argument
    pump()
    assert not [i for i in no_broker if i[0] == "jobs_pump"]


def test_pump_cancels_orphans_and_stale(handlers):
    # pylint: disable=unused-argument
    blocker = make("t_dl", "blocker")
    blocker.status = JobStatus.FAILED
    blocker.save()
    make(target="after", depends_on=blocker)
    stale = make(target="stale")
    stale.status = JobStatus.RUNNING
    stale.heartbeat_at = timezone.now() - timedelta(hours=2)
    stale.save()

    pump()

    after = Job.objects.get(target_id="after")
    assert after.status == JobStatus.CANCELLED
    assert "depended" in after.error
    stale.refresh_from_db()
    assert stale.status == JobStatus.FAILED
    assert "worker lost" in stale.error


def claim(job):
    """mark running as the pump would"""
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.started_at = timezone.now()
    job.save()
    return job


def test_execute_done(handlers, no_broker):
    # pylint: disable=unused-argument
    calls, _ = handlers
    job = claim(make())

    assert execute(job.pk) == JobStatus.DONE

    job.refresh_from_db()
    assert calls == [job.pk]
    assert (job.result, job.error) == ({"ok": "a"}, None)
    assert job.finished_at is not None


def test_execute_skips_unclaimed(handlers):
    calls, _ = handlers
    job = make()
    assert execute(job.pk) is None
    assert execute(9999) is None
    assert not calls


def test_execute_failure_retries_then_fails(handlers, capsys):
    _, behaviour = handlers

    def boom(ctx):
        raise RuntimeError("network down")

    behaviour["a"] = boom
    job = claim(make())

    assert execute(job.pk) == JobStatus.QUEUED
    job.refresh_from_db()
    assert job.error == "network down"
    assert job.not_before > timezone.now()
    assert "Traceback" in capsys.readouterr().err

    claim(job)
    assert execute(job.pk) == JobStatus.FAILED
    job.refresh_from_db()
    assert (job.status, job.attempts) == (JobStatus.FAILED, 2)


def test_execute_job_error_no_traceback(handlers, capsys):
    _, behaviour = handlers

    def fail(ctx):
        raise JobError("not found")

    behaviour["v"] = fail
    job = claim(make("t_dl", "v"))

    assert execute(job.pk) == JobStatus.FAILED
    assert "Traceback" not in capsys.readouterr().err
    job.refresh_from_db()
    assert job.error == "not found"


def test_execute_failure_message_fallback(handlers):
    _, behaviour = handlers

    def fail(ctx):
        raise JobError()

    behaviour["v"] = fail
    job = claim(make("t_dl", "v"))
    execute(job.pk)
    job.refresh_from_db()
    assert job.error == "JobError"


def test_execute_defer(handlers):
    _, behaviour = handlers

    def wait(ctx):
        raise Defer(120, "waiting for downloads")

    behaviour["a"] = wait
    job = claim(make())

    assert execute(job.pk) == JobStatus.QUEUED
    job.refresh_from_db()
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 0)
    assert job.progress == {"message": "waiting for downloads"}
    assert job.not_before > timezone.now() + timedelta(seconds=100)


def test_execute_stopped(handlers):
    _, behaviour = handlers

    def stopped(ctx):
        Job.objects.filter(pk=ctx.job.pk).update(
            stop_requested=True, stop_to=JobStatus.CANCELLED
        )
        assert ctx.is_stopped()
        return {"partial": True}

    behaviour["a"] = stopped
    job = claim(make())

    assert execute(job.pk) == JobStatus.CANCELLED
    job.refresh_from_db()
    assert (job.error, job.result) == ("stopped", {"partial": True})


def test_execute_retry_merges_into_waiting(handlers):
    _, behaviour = handlers

    def fail(ctx):
        raise JobError("flaky")

    behaviour["a"] = fail
    job = claim(make())
    make()

    assert execute(job.pk) == JobStatus.CANCELLED


def test_execute_failure_while_stopping_is_final(handlers):
    _, behaviour = handlers

    def stop_and_fail(ctx):
        Job.objects.filter(pk=ctx.job.pk).update(stop_requested=True)
        raise RuntimeError("interrupted")

    behaviour["a"] = stop_and_fail
    job = claim(make())
    assert execute(job.pk) == JobStatus.FAILED


def test_execute_paused(handlers):
    _, behaviour = handlers

    def paused(ctx):
        Job.objects.filter(pk=ctx.job.pk).update(
            stop_requested=True, stop_to=JobStatus.PAUSED
        )

    behaviour["a"] = paused
    job = claim(make())

    assert execute(job.pk) == JobStatus.PAUSED
    job.refresh_from_db()
    assert (job.status, job.started_at, job.stop_requested) == (
        JobStatus.PAUSED,
        None,
        False,
    )


def test_execute_paused_merges_into_waiting(handlers):
    _, behaviour = handlers

    def paused(ctx):
        Job.objects.filter(pk=ctx.job.pk).update(
            stop_requested=True, stop_to=JobStatus.PAUSED
        )

    behaviour["a"] = paused
    job = claim(make(priority=Priority.HIGH))
    waiting = make(priority=Priority.LOW)

    assert execute(job.pk) == JobStatus.CANCELLED
    job.refresh_from_db()
    waiting.refresh_from_db()
    assert job.error == f"merged into job #{waiting.pk}"
    assert (waiting.priority, waiting.merged) == (Priority.HIGH, 1)


def test_execute_notifies(handlers, monkeypatch):
    # pylint: disable=import-outside-toplevel
    from task.src.notify import Notifications

    sent = []
    monkeypatch.setattr(
        Notifications,
        "notify",
        lambda self, title, body: sent.append((self.task_name, title, body)),
    )
    job = claim(make("t_note", None))
    execute(job.pk)

    assert sent == [
        (
            "thumbnail_check",
            "[TA] Check your Thumbnails ended: done",
            "ok: None",
        )
    ]


def test_notify_skips_unfinished(handlers, monkeypatch):
    # pylint: disable=import-outside-toplevel,unused-argument
    from task.src.notify import Notifications

    sent = []
    monkeypatch.setattr(
        Notifications, "notify", lambda *args, **kwargs: sent.append(1)
    )
    job = make("t_note", None)
    engine._notify("thumbnail_check", job)
    assert not sent


@pytest.mark.parametrize(
    "error, result, expected",
    [
        ("boom", None, "boom"),
        (None, {"a": 1, "b": 2}, "a: 1, b: 2"),
        (None, "text", "text"),
        (None, None, "done"),
    ],
)
def test_describe_result(error, result, expected):
    job = Job(status="done", error=error, result=result)
    assert engine.describe_result(job) == expected


def test_recover_after_restart(handlers):
    # pylint: disable=unused-argument
    running = claim(make(target="a"))
    claim(make(target="b"))
    make(target="b")  # a waiting job for b already exists

    assert recover_after_restart() == 1

    running.refresh_from_db()
    assert (running.status, running.attempts) == (JobStatus.QUEUED, 0)
    assert Job.objects.filter(target_id="b", status="cancelled").exists()


ACTION_CASES = [
    ("queued", "cancel", "cancelled"),
    ("queued", "pause", "paused"),
    ("paused", "resume", "queued"),
    ("paused", "cancel", "cancelled"),
    ("failed", "retry", "queued"),
    ("cancelled", "retry", "queued"),
]


@pytest.mark.parametrize("start, action, end", ACTION_CASES)
def test_actions(handlers, start, action, end):
    # pylint: disable=unused-argument
    job = make()
    Job.objects.filter(pk=job.pk).update(status=start, attempts=1, error="old")
    job = apply_action(job.pk, action)
    assert job.status == end
    if action == "retry":
        assert (job.attempts, job.error) == (0, None)


def test_stop_and_pause_running(handlers):
    # pylint: disable=unused-argument
    job = claim(make())
    job = apply_action(job.pk, "stop")
    assert (job.status, job.stop_requested, job.stop_to) == (
        JobStatus.RUNNING,
        True,
        JobStatus.CANCELLED,
    )
    # stopping already, no more actions
    with pytest.raises(ActionNotAllowed):
        apply_action(job.pk, "pause")

    other = claim(make(target="b"))
    other = apply_action(other.pk, "pause")
    assert other.stop_to == JobStatus.PAUSED


def test_action_not_allowed(handlers):
    # pylint: disable=unused-argument
    job = make()
    Job.objects.filter(pk=job.pk).update(status=JobStatus.DONE)
    with pytest.raises(ActionNotAllowed, match="can't retry a done job"):
        apply_action(job.pk, "retry")
    with pytest.raises(Job.DoesNotExist):
        apply_action(9999, "cancel")


def test_retry_merges_into_waiting(handlers):
    # pylint: disable=unused-argument
    failed = make()
    Job.objects.filter(pk=failed.pk).update(status=JobStatus.FAILED)
    waiting = make()

    assert apply_action(failed.pk, "retry").pk == waiting.pk


def test_move_to_top(handlers):
    # pylint: disable=unused-argument
    make(target="a", priority=Priority.HIGH)
    low = make(target="b", priority=Priority.LOW)
    make("t_dl", "other-lane", priority=500)

    moved = apply_action(low.pk, "move_to_top")
    assert moved.priority == Priority.HIGH + 1

    # already on top: unchanged
    assert apply_action(low.pk, "move_to_top").priority == Priority.HIGH + 1
    alone = make("t_note", None)
    assert apply_action(alone.pk, "move_to_top").priority == Priority.NORMAL


def test_cancel_waiting_and_active_jobs(handlers):
    # pylint: disable=unused-argument
    queued = make("t_dl", "v1")
    running = claim(make("t_dl", "v2"))
    make("t_dl", "v2")  # waiting behind the running one
    done = make("t_dl", "v3")
    Job.objects.filter(pk=done.pk).update(status=JobStatus.DONE)

    found = active_jobs("t_dl", ["v1", "v2", "v3"])
    assert found["v1"].pk == queued.pk
    assert found["v2"].pk == running.pk
    assert "v3" not in found

    assert cancel_waiting("t_dl", ["v1", "v2"]) == 2
    assert Job.objects.get(pk=running.pk).status == JobStatus.RUNNING


def test_execute_without_notify(handlers, monkeypatch):
    # pylint: disable=unused-argument
    sent = []
    monkeypatch.setattr(engine, "_notify", lambda *args: sent.append(args))
    register("t_quiet", "scan", "Quiet", notify=False)(lambda ctx: {})
    try:
        job = claim(make("t_quiet", None))
        execute(job.pk)
    finally:
        SPECS.pop("t_quiet")
    assert not sent


def test_str():
    assert str(Lane(name="scan")) == "scan"
    job = Job(pk=3, type="check_channel", target_id="UC1", status="queued")
    assert str(job) == "#3 check_channel UC1 (queued)"
    assert str(Job(pk=4, type="backup", status="done")) == "#4 backup  (done)"


def test_celery_entry_points(monkeypatch):
    # pylint: disable=import-outside-toplevel
    from jobs import tasks

    cleared = []
    monkeypatch.setattr(tasks, "execute", lambda job_id: f"ran {job_id}")
    monkeypatch.setattr(tasks, "pump", lambda: [1])
    monkeypatch.setattr(tasks, "clear_next_pump", lambda: cleared.append(1))
    assert tasks.run_job.run(5) == "ran 5"
    assert tasks.jobs_pump.run() == [1]
    assert cleared == [1]

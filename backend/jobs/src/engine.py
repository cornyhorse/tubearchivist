"""
Functionality:
- enqueue: add a job, merging with a waiting job for the same target
- pump: start queued jobs, per lane, within concurrency and rate limits
- execute: run one job in a celery worker, record the outcome
- actions: stop, cancel, pause, resume, retry, move to top
"""

import traceback
from datetime import datetime, timedelta

from common.src.ta_redis import RedisBase
from django.db import IntegrityError, transaction
from django.db.models import Max, Min, Q
from django.utils import timezone
from jobs.models import (
    ACTIVE,
    FINISHED,
    WAITING,
    CreatedBy,
    Job,
    JobStatus,
    Lane,
    Priority,
)
from jobs.src.context import JobContext
from jobs.src.registry import LANES, Defer, JobError, get_spec

# a running job without a sign of life for this long is presumed lost
STALE_AFTER = timedelta(minutes=60)
RETRY_BACKOFF = 30  # seconds, doubled per attempt
NEXT_PUMP_KEY = "jobs:next_pump"


class ActionNotAllowed(ValueError):
    """the action doesn't apply to the job's current status"""


def ensure_lanes() -> None:
    """create missing lanes with their defaults"""
    for name, (_, _, concurrency, interval) in LANES.items():
        Lane.objects.get_or_create(
            name=name,
            defaults={
                "concurrency": concurrency,
                "min_interval_seconds": interval,
            },
        )


def enqueue(
    job_type: str,
    target_id: str | None = None,
    *,
    target_title: str | None = None,
    params: dict | None = None,
    priority: int = Priority.NORMAL,
    created_by: str = CreatedBy.SYSTEM,
    parent: Job | None = None,
    depends_on: Job | None = None,
    not_before: datetime | None = None,
    dedupe_key: str | None = None,
    dispatch: bool = True,
) -> tuple[Job, bool]:
    """
    queue a job, returns (job, merged)
    a waiting job with the same key takes the higher priority and the newer
    params instead of a second job being created; nothing is ever dropped
    """
    spec = get_spec(job_type)
    key = dedupe_key or f"{job_type}:{target_id or ''}"
    params = params or {}
    for _ in range(3):
        try:
            with transaction.atomic():
                job, merged = _merge_or_create(
                    key,
                    {
                        "type": job_type,
                        "lane": spec.lane,
                        "target_kind": spec.target_kind,
                        "target_id": target_id,
                        "target_title": target_title,
                        "params": params,
                        "priority": priority,
                        "created_by": created_by,
                        "parent": parent,
                        "depends_on": depends_on,
                        "not_before": not_before,
                        "max_attempts": spec.max_attempts,
                    },
                )
            break
        except IntegrityError:
            # a concurrent enqueue created the same key, merge into it
            continue
    else:
        raise RuntimeError(f"could not enqueue {key}")

    if dispatch:
        transaction.on_commit(pump)
    return job, merged


def _merge_or_create(key: str, fields: dict) -> tuple[Job, bool]:
    existing = (
        Job.objects.select_for_update()
        .filter(dedupe_key=key, status__in=WAITING)
        .first()
    )
    if not existing:
        return Job.objects.create(dedupe_key=key, **fields), False

    existing.priority = max(existing.priority, fields["priority"])
    existing.params = {**existing.params, **fields["params"]}
    if fields["target_title"]:
        existing.target_title = fields["target_title"]
    if fields["not_before"] is None:
        existing.not_before = None
    existing.merged += 1
    existing.save()
    return existing, True


def pump() -> list[int]:
    """start what can start in every lane; returns started job ids"""
    ensure_lanes()
    _fail_stale()
    _cancel_orphans()
    started = []
    for name in Lane.objects.order_by("name").values_list("name", flat=True):
        started.extend(_pump_lane(name))
    _schedule_next_pump()
    return started


def _pump_lane(name: str) -> list[int]:
    now = timezone.now()
    with transaction.atomic():
        lane = Lane.objects.select_for_update().get(name=name)
        if lane.paused:
            return []
        running = Job.objects.filter(lane=name, status=JobStatus.RUNNING)
        free = lane.concurrency - running.count()
        if free <= 0:
            return []
        if lane.min_interval_seconds:
            if lane.last_started_at:
                wait = lane.last_started_at + timedelta(
                    seconds=lane.min_interval_seconds
                )
                if wait > now:
                    return []
            free = 1

        jobs = list(
            Job.objects.filter(lane=name, status=JobStatus.QUEUED)
            .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
            .filter(
                Q(depends_on__isnull=True)
                | Q(depends_on__status=JobStatus.DONE)
            )
            .run_order()
            .select_for_update(skip_locked=True, of=("self",))[:free]
        )
        for job in jobs:
            job.status = JobStatus.RUNNING
            job.started_at = now
            job.heartbeat_at = now
            job.finished_at = None
            job.attempts += 1
            job.stop_requested = False
            job.stop_to = None
            job.save()
        if jobs:
            lane.last_started_at = now
            lane.save()
        ids = [job.pk for job in jobs]
        transaction.on_commit(lambda: _send(ids))

    return ids


def _send(ids: list[int]) -> None:
    # pylint: disable=import-outside-toplevel
    from jobs.tasks import run_job

    for job_id in ids:
        run_job.delay(job_id)


def _schedule_next_pump() -> None:
    """wake up when the next delayed job or rate limit is due"""
    now = timezone.now()
    due = [
        Job.objects.filter(status=JobStatus.QUEUED, not_before__gt=now)
        .aggregate(Min("not_before"))
        .get("not_before__min")
    ]
    for lane in Lane.objects.filter(
        min_interval_seconds__gt=0, last_started_at__isnull=False
    ):
        due.append(
            lane.last_started_at + timedelta(seconds=lane.min_interval_seconds)
        )
    upcoming = [i for i in due if i and i > now]
    if not upcoming:
        return
    seconds = min(int((min(upcoming) - now).total_seconds()) + 1, 60)
    fire_at = now.timestamp() + seconds
    # at most one wake-up waits at a time: skip if one fires before this
    conn, key = RedisBase().conn, RedisBase.NAME_SPACE + NEXT_PUMP_KEY
    pending = conn.get(key)
    if pending and float(pending) <= fire_at:
        return
    conn.set(key, fire_at, ex=seconds + 5)
    # pylint: disable=import-outside-toplevel
    from jobs.tasks import jobs_pump

    jobs_pump.apply_async(countdown=seconds)


def clear_next_pump() -> None:
    """the scheduled wake-up is running, the next one may be scheduled"""
    RedisBase().conn.delete(RedisBase.NAME_SPACE + NEXT_PUMP_KEY)


def _fail_stale() -> None:
    """running jobs whose worker vanished"""
    cutoff = timezone.now() - STALE_AFTER
    Job.objects.filter(
        status=JobStatus.RUNNING, heartbeat_at__lt=cutoff
    ).update(
        status=JobStatus.FAILED,
        error="worker lost: no sign of life for 60 minutes",
        finished_at=timezone.now(),
    )


def _cancel_orphans() -> None:
    """queued jobs whose dependency will never finish"""
    Job.objects.filter(
        status__in=WAITING,
        depends_on__status__in=[JobStatus.FAILED, JobStatus.CANCELLED],
    ).update(
        status=JobStatus.CANCELLED,
        error="a job it depended on did not finish",
        finished_at=timezone.now(),
    )


def recover_after_restart() -> int:
    """
    at startup, no job can be running: put them back in the queue
    returns the number of jobs requeued
    """
    requeued = 0
    for job in Job.objects.filter(status=JobStatus.RUNNING):
        job.attempts = max(job.attempts - 1, 0)
        if not _set_waiting(job, JobStatus.QUEUED):
            continue
        requeued += 1
    return requeued


def execute(job_id: int) -> str | None:
    """run a job claimed by pump, in a worker; returns the final status"""
    job = Job.objects.filter(pk=job_id).first()
    if not job or job.status != JobStatus.RUNNING:
        return None

    spec = get_spec(job.type)
    ctx = JobContext(job, legacy_name=spec.legacy_name)
    try:
        result = spec.handler(ctx)
    except Defer as defer:
        job.attempts -= 1
        job.not_before = timezone.now() + timedelta(seconds=defer.seconds)
        job.progress = {"message": str(defer)}
        _set_waiting(job, JobStatus.QUEUED)
        status = JobStatus.QUEUED
    except Exception as err:  # pylint: disable=broad-exception-caught
        if not isinstance(err, JobError):
            traceback.print_exc()
        status = _failed(job, f"{err}" or err.__class__.__name__)
    else:
        status = _finished(job, result)

    if spec.notify:
        _notify(spec.legacy_name, job)
    pump()
    return status


def _finished(job: Job, result) -> str:
    job.refresh_from_db(fields=["stop_requested", "stop_to"])
    job.result = result
    job.finished_at = timezone.now()
    if job.stop_requested and job.stop_to == JobStatus.PAUSED:
        job.finished_at = None
        if _set_waiting(job, JobStatus.PAUSED):
            return JobStatus.PAUSED
        return JobStatus.CANCELLED
    if job.stop_requested:
        job.status = JobStatus.CANCELLED
        job.error = "stopped"
    else:
        job.status = JobStatus.DONE
        job.error = None
    job.save()
    return job.status


def _failed(job: Job, error: str) -> str:
    job.refresh_from_db(fields=["stop_requested", "stop_to"])
    job.error = error
    if job.attempts < job.max_attempts and not job.stop_requested:
        delay = RETRY_BACKOFF * 2 ** (job.attempts - 1)
        job.not_before = timezone.now() + timedelta(seconds=delay)
        if _set_waiting(job, JobStatus.QUEUED):
            return JobStatus.QUEUED
        return JobStatus.CANCELLED
    job.status = JobStatus.FAILED
    job.finished_at = timezone.now()
    job.save()
    return job.status


def _set_waiting(job: Job, status: str) -> bool:
    """
    back to queued or paused; if another job with the same key is already
    waiting, merge into it and cancel this one; False if merged
    """
    job.status = status
    job.started_at = None
    job.stop_requested = False
    job.stop_to = None
    try:
        with transaction.atomic():
            job.save()
        return True
    except IntegrityError:
        other = Job.objects.get(dedupe_key=job.dedupe_key, status__in=WAITING)
        other.priority = max(other.priority, job.priority)
        other.merged += 1
        other.save()
        Job.objects.filter(pk=job.pk).update(
            status=JobStatus.CANCELLED,
            error=f"merged into job #{other.pk}",
            finished_at=timezone.now(),
        )
        return False


def _notify(legacy_name: str | None, job: Job) -> None:
    """apprise notification, for the task types that had one"""
    if not legacy_name:
        return
    # pylint: disable=import-outside-toplevel
    from task.src.notify import Notifications
    from task.src.task_config import TASK_CONFIG

    job.refresh_from_db()
    if job.status not in FINISHED:
        return
    title = TASK_CONFIG[legacy_name]["title"]
    body = describe_result(job)
    Notifications(legacy_name).notify(
        title=f"[TA] {title} ended: {job.status}", body=body
    )


def describe_result(job: Job) -> str:
    """one line about how a job ended"""
    if job.error:
        return job.error
    if isinstance(job.result, dict):
        return ", ".join(f"{k}: {v}" for k, v in job.result.items())
    return str(job.result or job.status)


# actions


def allowed_actions(job: Job) -> list[str]:
    """actions that apply to the job now"""
    by_status = {
        JobStatus.QUEUED: ["cancel", "pause", "move_to_top"],
        JobStatus.PAUSED: ["cancel", "resume", "move_to_top"],
        JobStatus.RUNNING: ["stop", "pause"],
        JobStatus.FAILED: ["retry"],
        JobStatus.CANCELLED: ["retry"],
        JobStatus.DONE: [],
    }
    if job.status == JobStatus.RUNNING and job.stop_requested:
        return []
    return by_status[job.status]


def apply_action(job_id: int, action: str) -> Job:
    """apply an action; ActionNotAllowed, Job.DoesNotExist"""
    with transaction.atomic():
        job = Job.objects.select_for_update().get(pk=job_id)
        if action not in allowed_actions(job):
            raise ActionNotAllowed(
                f"can't {action.replace('_', ' ')} a {job.status} job"
            )
        job = ACTIONS[action](job)

    transaction.on_commit(pump)
    return job


def _stop(job: Job, to: str = JobStatus.CANCELLED) -> Job:
    job.stop_requested = True
    job.stop_to = to
    job.save()
    return job


def _cancel(job: Job) -> Job:
    job.status = JobStatus.CANCELLED
    job.error = "cancelled"
    job.finished_at = timezone.now()
    job.save()
    return job


def _pause(job: Job) -> Job:
    if job.status == JobStatus.RUNNING:
        return _stop(job, JobStatus.PAUSED)
    job.status = JobStatus.PAUSED
    job.save()
    return job


def _resume(job: Job) -> Job:
    job.status = JobStatus.QUEUED
    job.save()
    return job


def _retry(job: Job) -> Job:
    job.attempts = 0
    job.error = None
    job.result = None
    job.progress = None
    job.finished_at = None
    job.not_before = None
    if _set_waiting(job, JobStatus.QUEUED):
        return job
    # merged into a job that was already waiting
    return Job.objects.get(dedupe_key=job.dedupe_key, status__in=WAITING)


def _move_to_top(job: Job) -> Job:
    top = (
        Job.objects.filter(lane=job.lane, status__in=WAITING)
        .exclude(pk=job.pk)
        .aggregate(Max("priority"))
        .get("priority__max")
    )
    if top is not None and top >= job.priority:
        job.priority = top + 1
    job.save()
    return job


ACTIONS = {
    "stop": _stop,
    "cancel": _cancel,
    "pause": _pause,
    "resume": _resume,
    "retry": _retry,
    "move_to_top": _move_to_top,
}


def cancel_waiting(job_type: str, target_ids: list[str]) -> int:
    """cancel queued and paused jobs of a type for these targets"""
    return Job.objects.filter(
        type=job_type, target_id__in=target_ids, status__in=WAITING
    ).update(
        status=JobStatus.CANCELLED,
        error="cancelled: no longer wanted",
        finished_at=timezone.now(),
    )


def active_jobs(job_type: str, target_ids: list[str]) -> dict[str, Job]:
    """the active job per target, running before waiting"""
    jobs = Job.objects.filter(
        type=job_type, target_id__in=target_ids, status__in=ACTIVE
    )
    found: dict[str, Job] = {}
    for job in jobs:
        if job.target_id not in found or job.status == JobStatus.RUNNING:
            found[job.target_id] = job
    return found

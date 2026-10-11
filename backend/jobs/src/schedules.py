"""
Functionality:
- the scheduled jobs in plain words, with their next run time
- run a schedule now
"""

from django.db.models import Max
from django.utils import timezone
from jobs.models import CreatedBy, Job, Priority
from jobs.src.engine import enqueue
from jobs.src.registry import get_spec
from task.models import CustomPeriodicTask

# schedule name: (job type, plain words)
SCHEDULED = {
    "update_subscribed": (
        "check_subscriptions",
        "Check channels for new videos",
    ),
    "download_pending": ("download_video", "Download queued videos"),
    "check_reindex": ("reindex", "Refresh metadata from YouTube"),
    "thumbnail_check": ("thumbnail_check", "Check thumbnails"),
    "run_backup": ("backup", "Back up the database"),
    "version_check": ("version_check", "Look for a new version"),
}


def next_run(task: CustomPeriodicTask):
    """when the schedule fires next, None if disabled"""
    if not task.enabled or not task.crontab:
        return None
    schedule = task.crontab.schedule
    now = schedule.now()
    remaining = schedule.remaining_estimate(task.last_run_at or now)
    return now + remaining


def schedule_dict(task: CustomPeriodicTask) -> dict:
    """a schedule as the API returns it"""
    job_type, label = SCHEDULED.get(task.name, (task.name, task.name))
    last_job = (
        Job.objects.filter(type=job_type, created_by=CreatedBy.SCHEDULE)
        .order_by("-created_at")
        .values_list("id", flat=True)
        .first()
    )
    return {
        "name": task.name,
        "label": label,
        "job_type": job_type,
        "lane": get_spec(job_type).lane,
        "schedule": task.schedule_parsed,
        "schedule_human": task.crontab.human_readable,
        "enabled": task.enabled,
        "next_run": next_run(task),
        "last_run": task.last_run_at,
        "last_job": last_job,
    }


def schedule_dicts() -> list[dict]:
    """every schedule, next to run first"""
    tasks = CustomPeriodicTask.objects.filter(name__in=SCHEDULED)
    items = [schedule_dict(task) for task in tasks.select_related("crontab")]
    never = timezone.now().replace(year=9999)
    return sorted(items, key=lambda i: i["next_run"] or never)


def set_enabled(name: str, enabled: bool) -> dict:
    """pause or resume a schedule; DoesNotExist"""
    task = CustomPeriodicTask.objects.get(name=name, name__in=SCHEDULED)
    task.enabled = enabled
    task.save()
    return schedule_dict(task)


def run_now(name: str) -> tuple[Job | None, bool]:
    """
    queue the schedule's work now, as a user action; KeyError
    returns (job, merged); for downloads the last queued download job,
    None if nothing was waiting
    """
    job_type, _ = SCHEDULED[name]
    if job_type == "download_video":
        # pylint: disable=import-outside-toplevel
        from task.tasks import download_pending

        before = Job.objects.aggregate(Max("id"))["id__max"] or 0
        download_pending.run(created_by=CreatedBy.USER)
        job = (
            Job.objects.filter(type=job_type, pk__gt=before)
            .order_by("-id")
            .first()
        )
        return job, False
    return enqueue(job_type, created_by=CreatedBy.USER, priority=Priority.HIGH)

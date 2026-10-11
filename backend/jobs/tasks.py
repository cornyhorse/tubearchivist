"""celery entry points of the work queue"""

from celery import shared_task
from jobs.src.engine import execute, pump


@shared_task(name="run_job")
def run_job(job_id: int) -> str | None:
    """run one job claimed by the pump"""
    return execute(job_id)


@shared_task(name="jobs_pump")
def jobs_pump() -> list[int]:
    """start queued jobs; scheduled every minute as a safety net"""
    return pump()

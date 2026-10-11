"""
Functionality:
- JobContext: what a running job hands to the worker classes
- same interface as the old celery BaseTask: send_progress, is_stopped
"""

import time

from common.src.ta_redis import RedisArchivist
from django.utils import timezone
from jobs.models import Job
from task.src.task_config import TASK_CONFIG

# write progress to the database at most this often
WRITE_INTERVAL = 1.0
# re-read the stop flag at most this often
STOP_INTERVAL = 2.0


class JobContext:
    """progress and stop signal of one running job"""

    def __init__(self, job: Job, legacy_name: str | None = None):
        self.job = job
        self.legacy_name = legacy_name
        self._last_write = 0.0
        self._last_stop_check = 0.0
        self._stopped = job.stop_requested

    @property
    def params(self) -> dict:
        """job parameters"""
        return self.job.params

    def send_progress(
        self,
        message_lines: list,
        progress: float | bool = False,
        title: str | bool = False,
        level: str = "info",
        **detail,
    ) -> None:
        """record progress; detail: speed, eta_seconds, current, total"""
        message = " · ".join(str(i) for i in message_lines if i)
        fraction = None if progress is False else float(progress)
        self.job.progress = {
            "fraction": fraction,
            "message": message,
            "speed": detail.get("speed"),
            "eta_seconds": detail.get("eta_seconds"),
            "current": detail.get("current"),
            "total": detail.get("total"),
        }
        now = time.monotonic()
        due = now - self._last_write >= WRITE_INTERVAL
        if due or level == "error" or fraction == 1:
            self._last_write = now
            Job.objects.filter(pk=self.job.pk).update(
                progress=self.job.progress, heartbeat_at=timezone.now()
            )
        self._legacy_message(message_lines, fraction, title, level)

    def _legacy_message(self, lines, fraction, title, level) -> None:
        """notification for the old UI, which reads redis messages"""
        config = TASK_CONFIG.get(self.legacy_name or "")
        if not config:
            return
        message = {
            **config,
            "level": level,
            "id": f"job-{self.job.pk}",
            "messages": lines,
            "progress": fraction or False,
        }
        if title:
            message["title"] = title
        key = f"message:{config['group']}:job{self.job.pk}"
        RedisArchivist().set_message(key, message, expire=20)

    def is_stopped(self) -> bool:
        """true once stop, cancel or pause was asked for"""
        if self._stopped:
            return True
        now = time.monotonic()
        if now - self._last_stop_check >= STOP_INTERVAL:
            self._last_stop_check = now
            self._stopped = Job.objects.filter(
                pk=self.job.pk, stop_requested=True
            ).exists()
            Job.objects.filter(pk=self.job.pk).update(
                heartbeat_at=timezone.now()
            )
        return self._stopped

"""task model"""

from django.db import models
from django_celery_beat.models import PeriodicTask, cronexp


class CustomPeriodicTask(PeriodicTask):
    """add custom metadata to task"""

    task_config = models.JSONField(default=dict)

    @property
    def schedule_parsed(self):
        """parse schedule"""
        minute = cronexp(self.crontab.minute)
        hour = cronexp(self.crontab.hour)
        day_of_week = cronexp(self.crontab.day_of_week)

        return f"{minute} {hour} {day_of_week}"

    @property
    def next_run_at(self):
        """when beat runs it next, in the server TZ; None if disabled"""
        if not self.enabled or not self.crontab:
            return None
        schedule = self.crontab.schedule
        now = schedule.now()
        return now + schedule.remaining_estimate(self.last_run_at or now)

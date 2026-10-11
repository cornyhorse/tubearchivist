"""work queue: every unit of work is a job, run in lanes"""

from django.db import models
from django.db.models import Q


class JobStatus(models.TextChoices):
    """lifecycle of a job"""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


WAITING = [JobStatus.QUEUED, JobStatus.PAUSED]
ACTIVE = [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED]
FINISHED = [JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED]


class Priority(models.IntegerChoices):
    """named priorities, any integer is valid"""

    HIGH = 100
    NORMAL = 50
    LOW = 10


PRIORITY_BY_NAME = {
    "high": Priority.HIGH,
    "normal": Priority.NORMAL,
    "low": Priority.LOW,
}


class CreatedBy(models.TextChoices):
    """who asked for the job"""

    SCHEDULE = "schedule"
    USER = "user"
    RULE = "rule"
    AGENT = "agent"
    SYSTEM = "system"


class Lane(models.Model):
    """jobs of a lane run in parallel with the other lanes"""

    name = models.CharField(primary_key=True, max_length=16)
    paused = models.BooleanField(default=False)
    concurrency = models.IntegerField(default=1)
    min_interval_seconds = models.IntegerField(default=0)
    last_started_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class JobQuerySet(models.QuerySet):
    """job queries"""

    def run_order(self):
        """order a lane runs its queued jobs in"""
        return self.order_by("-priority", "created_at", "id")


class Job(models.Model):
    """one unit of work"""

    type = models.CharField(max_length=32)
    lane = models.CharField(max_length=16)
    target_kind = models.CharField(max_length=16, default="none")
    target_id = models.CharField(max_length=64, null=True, blank=True)
    target_title = models.TextField(null=True, blank=True)
    # repeat requests with the same key merge while the job waits
    dedupe_key = models.CharField(max_length=128)
    params = models.JSONField(default=dict, blank=True)
    priority = models.IntegerField(default=Priority.NORMAL)
    status = models.CharField(
        max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED
    )
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=1)
    progress = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    created_by = models.CharField(
        max_length=16, choices=CreatedBy.choices, default=CreatedBy.SYSTEM
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    # not picked before this time: retry backoff, debounce
    not_before = models.DateTimeField(null=True, blank=True)
    depends_on = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dependents",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    merged = models.IntegerField(default=0)
    stop_requested = models.BooleanField(default=False)
    # what a stop leads to: cancelled, or paused for the pause action
    stop_to = models.CharField(max_length=16, null=True, blank=True)

    objects = JobQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(
                fields=["lane", "status", "-priority", "created_at"],
                name="job_run_order",
            ),
            models.Index(fields=["target_id"], name="job_target"),
            models.Index(fields=["-finished_at"], name="job_history"),
        ]
        constraints = [
            # at most one waiting job per key, so requests merge
            models.UniqueConstraint(
                fields=["dedupe_key"],
                condition=Q(status__in=["queued", "paused"]),
                name="job_one_waiting_per_key",
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.type} {self.target_id or ''} ({self.status})"

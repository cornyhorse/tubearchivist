"""
work queue serializers, the API contract of /api/queue/
see docs/ux-api-contract.md
"""

# pylint: disable=abstract-method

from common.serializers import PaginationSerializer
from rest_framework import serializers

LANES = ["scan", "download", "postprocess", "maintenance"]
JOB_STATUSES = ["queued", "running", "paused", "done", "failed", "cancelled"]
ACTIVE_STATUSES = ["queued", "running", "paused"]
CREATED_BY = ["schedule", "user", "rule", "agent", "system"]
JOB_TYPES = [
    # scan lane
    "check_subscriptions",
    "check_channel",
    "check_playlist",
    "fetch_video",
    # download lane
    "download_video",
    # post-process lane
    "post_download",
    "emby_write",
    "media_notify",
    "media_refresh",
    # maintenance lane, the former batch tasks
    "reindex",
    "backup",
    "restore",
    "filesystem_rescan",
    "thumbnail_check",
    "resync_metadata",
    "manual_import",
    "index_playlists",
    "subscribe",
    "version_check",
    "snapshot",
]
# types a person may start with POST /api/queue/jobs/
USER_JOB_TYPES = [
    "check_subscriptions",
    "check_channel",
    "check_playlist",
    "fetch_video",
    "download_video",
    "emby_write",
    "media_refresh",
    "thumbnail_check",
    "resync_metadata",
    "backup",
]
JOB_ACTIONS = ["stop", "cancel", "pause", "resume", "retry", "move_to_top"]
TARGET_KINDS = ["channel", "playlist", "video", "library", "none"]
PRIORITY_NAMES = ["high", "normal", "low"]


class JobTargetSerializer(serializers.Serializer):
    """what a job works on"""

    kind = serializers.ChoiceField(choices=TARGET_KINDS)
    id = serializers.CharField(
        allow_null=True, help_text="youtube id, library id, or null"
    )
    title = serializers.CharField(
        allow_null=True, help_text="display name, if known"
    )


class JobProgressSerializer(serializers.Serializer):
    """live progress of a running job"""

    fraction = serializers.FloatField(
        allow_null=True, help_text="0..1, null if unknown"
    )
    message = serializers.CharField(
        allow_blank=True, help_text="current step in plain words"
    )
    speed = serializers.CharField(
        allow_null=True, help_text="download speed, e.g. '4.1MiB/s'"
    )
    eta_seconds = serializers.IntegerField(allow_null=True)
    current = serializers.IntegerField(
        allow_null=True, help_text="items done, e.g. channels checked"
    )
    total = serializers.IntegerField(allow_null=True)


class JobSerializer(serializers.Serializer):
    """one unit of work"""

    id = serializers.IntegerField()
    type = serializers.ChoiceField(choices=JOB_TYPES)
    type_label = serializers.CharField(
        help_text="plain words, e.g. 'Check channel for new videos'"
    )
    lane = serializers.ChoiceField(choices=LANES)
    target = JobTargetSerializer()
    status = serializers.ChoiceField(choices=JOB_STATUSES)
    priority = serializers.IntegerField(
        help_text="higher runs first; user 100, normal 50, schedule 10"
    )
    position = serializers.IntegerField(
        allow_null=True,
        help_text="1-based place in its lane while queued, else null",
    )
    attempts = serializers.IntegerField()
    max_attempts = serializers.IntegerField()
    progress = JobProgressSerializer(allow_null=True)
    error = serializers.CharField(allow_null=True)
    result = serializers.JSONField(
        allow_null=True,
        help_text="type specific summary, e.g. {'found': 12, 'queued': 8}",
    )
    created_by = serializers.ChoiceField(choices=CREATED_BY)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.FloatField(allow_null=True)
    depends_on = serializers.IntegerField(
        allow_null=True, help_text="job id that must finish first"
    )
    parent = serializers.IntegerField(
        allow_null=True,
        help_text="job that created this one, e.g. the channel check",
    )
    children = serializers.IntegerField(help_text="jobs this one created")
    merged = serializers.IntegerField(
        help_text="repeat requests merged into this job"
    )
    actions = serializers.ListField(
        child=serializers.ChoiceField(choices=JOB_ACTIONS),
        help_text="actions allowed in the current status",
    )


class JobListSerializer(serializers.Serializer):
    """page of jobs"""

    data = JobSerializer(many=True)
    paginate = PaginationSerializer()


class JobListQuerySerializer(serializers.Serializer):
    """filters of GET /api/queue/jobs/"""

    lane = serializers.ChoiceField(choices=LANES, required=False)
    status = serializers.CharField(
        required=False,
        help_text=(
            "comma separated statuses; 'active' = queued,running,paused; "
            "'history' = done,failed,cancelled"
        ),
    )
    type = serializers.ChoiceField(choices=JOB_TYPES, required=False)
    target = serializers.CharField(required=False, help_text="target id")
    created_by = serializers.ChoiceField(choices=CREATED_BY, required=False)
    parent = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(
        required=False, min_value=1, max_value=500
    )


class JobCreateSerializer(serializers.Serializer):
    """POST /api/queue/jobs/: start work by hand"""

    type = serializers.ChoiceField(choices=USER_JOB_TYPES)
    target_id = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="channel, playlist or video id; omit for global jobs",
    )
    priority = serializers.ChoiceField(
        choices=PRIORITY_NAMES, required=False, default="high"
    )


class JobCreateResponseSerializer(serializers.Serializer):
    """the job, and whether it merged into an existing one"""

    job = JobSerializer()
    merged = serializers.BooleanField(
        help_text="true if an identical job was already queued"
    )


class JobActionSerializer(serializers.Serializer):
    """POST /api/queue/jobs/<id>/action/"""

    action = serializers.ChoiceField(choices=JOB_ACTIONS)


class JobBulkActionSerializer(serializers.Serializer):
    """POST /api/queue/jobs/action/: same action on explicit ids"""

    action = serializers.ChoiceField(choices=JOB_ACTIONS)
    ids = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=1000
    )


class JobBulkActionResponseSerializer(serializers.Serializer):
    """how many jobs changed, and why the others didn't"""

    changed = serializers.ListField(child=serializers.IntegerField())
    skipped = serializers.DictField(
        child=serializers.CharField(),
        help_text="job id -> reason, e.g. 'already done'",
    )


class LaneSerializer(serializers.Serializer):
    """a lane runs its jobs in parallel with the other lanes"""

    name = serializers.ChoiceField(choices=LANES)
    label = serializers.CharField()
    description = serializers.CharField()
    paused = serializers.BooleanField()
    concurrency = serializers.IntegerField(min_value=1, max_value=16)
    min_interval_seconds = serializers.IntegerField(
        min_value=0,
        help_text="rate limit: wait at least this long between job starts",
    )
    running = serializers.IntegerField()
    queued = serializers.IntegerField()


class LaneUpdateSerializer(serializers.Serializer):
    """PATCH /api/queue/lanes/<lane>/"""

    paused = serializers.BooleanField(required=False)
    concurrency = serializers.IntegerField(
        required=False, min_value=1, max_value=16
    )
    min_interval_seconds = serializers.IntegerField(
        required=False, min_value=0, max_value=3600
    )


class QueueSummarySerializer(serializers.Serializer):
    """header indicator: '2 running, 14 queued'"""

    running = serializers.IntegerField()
    queued = serializers.IntegerField()
    paused = serializers.IntegerField()
    failed_24h = serializers.IntegerField()
    lanes = LaneSerializer(many=True)


class ScheduledJobSerializer(serializers.Serializer):
    """a recurring job from the scheduler"""

    name = serializers.CharField(
        help_text="schedule name, e.g. 'update_subscribed'"
    )
    label = serializers.CharField(
        help_text="plain words, e.g. 'Check channels for new videos'"
    )
    job_type = serializers.ChoiceField(choices=JOB_TYPES)
    lane = serializers.ChoiceField(choices=LANES)
    schedule = serializers.CharField(help_text="cron: minute hour weekday")
    schedule_human = serializers.CharField(help_text="e.g. 'At 18:00'")
    enabled = serializers.BooleanField()
    next_run = serializers.DateTimeField(allow_null=True)
    last_run = serializers.DateTimeField(allow_null=True)
    last_job = serializers.IntegerField(
        allow_null=True, help_text="job id of the last run"
    )


class ScheduledJobUpdateSerializer(serializers.Serializer):
    """PATCH /api/queue/scheduled/<name>/: pause or resume a schedule"""

    enabled = serializers.BooleanField()

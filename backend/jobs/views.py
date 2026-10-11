"""work queue API views, /api/queue/"""

from common.serializers import ErrorResponseSerializer
from common.views_base import AdminOnly, ApiBaseView, not_built
from drf_spectacular.utils import OpenApiResponse, extend_schema
from jobs.serializers import (
    JobActionSerializer,
    JobBulkActionResponseSerializer,
    JobBulkActionSerializer,
    JobCreateResponseSerializer,
    JobCreateSerializer,
    JobListQuerySerializer,
    JobListSerializer,
    JobSerializer,
    LaneSerializer,
    LaneUpdateSerializer,
    QueueSummarySerializer,
    ScheduledJobSerializer,
    ScheduledJobUpdateSerializer,
)

STEP = "jobs engine"
NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")
BAD_REQUEST = OpenApiResponse(
    ErrorResponseSerializer(), description="bad request"
)
CONFLICT = OpenApiResponse(
    ErrorResponseSerializer(),
    description="action not allowed in the job's current status",
)


class QueueSummaryView(ApiBaseView):
    """resolves to /api/queue/summary/
    GET: counts for the header indicator, any signed in user
    """

    @extend_schema(responses=QueueSummarySerializer())
    def get(self, request):
        """running, queued and per-lane counts"""
        return not_built(STEP)


class JobListView(ApiBaseView):
    """resolves to /api/queue/jobs/
    GET: jobs, newest first for history, run order for queued
    POST: start a job by hand, merges with an identical queued job
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        operation_id="queue_jobs_list",
        parameters=[JobListQuerySerializer()],
        responses=JobListSerializer(),
    )
    def get(self, request):
        """
        list jobs; status=active sorts in run order (running first, then
        queued by priority and age), status=history newest finished first
        """
        return not_built(STEP)

    @extend_schema(
        request=JobCreateSerializer(),
        responses={
            201: OpenApiResponse(JobCreateResponseSerializer()),
            200: OpenApiResponse(
                JobCreateResponseSerializer(),
                description="merged into an existing queued job",
            ),
            400: BAD_REQUEST,
        },
    )
    def post(self, request):
        """queue a job"""
        return not_built(STEP)


class JobView(ApiBaseView):
    """resolves to /api/queue/jobs/<job_id>/
    GET: one job
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        operation_id="queue_job_retrieve",
        responses={200: JobSerializer(), 404: NOT_FOUND},
    )
    def get(self, request, job_id):
        """job detail"""
        return not_built(STEP)


class JobActionView(ApiBaseView):
    """resolves to /api/queue/jobs/<job_id>/action/
    POST: stop, cancel, pause, resume, retry or move_to_top
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        operation_id="queue_job_action",
        request=JobActionSerializer(),
        responses={
            200: JobSerializer(),
            404: NOT_FOUND,
            409: CONFLICT,
        },
    )
    def post(self, request, job_id):
        """apply an action to one job"""
        return not_built(STEP)


class JobBulkActionView(ApiBaseView):
    """resolves to /api/queue/jobs/action/
    POST: the same action on a list of job ids
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        operation_id="queue_jobs_bulk_action",
        request=JobBulkActionSerializer(),
        responses={200: JobBulkActionResponseSerializer(), 400: BAD_REQUEST},
    )
    def post(self, request):
        """apply an action to many jobs"""
        return not_built(STEP)


class LaneListView(ApiBaseView):
    """resolves to /api/queue/lanes/
    GET: all lanes with settings and counts
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=LaneSerializer(many=True))
    def get(self, request):
        """list lanes"""
        return not_built(STEP)


class LaneView(ApiBaseView):
    """resolves to /api/queue/lanes/<lane>/
    PATCH: pause or resume, concurrency, rate limit
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=LaneUpdateSerializer(),
        responses={200: LaneSerializer(), 400: BAD_REQUEST, 404: NOT_FOUND},
    )
    def patch(self, request, lane):
        """change lane settings"""
        return not_built(STEP)


class ScheduledListView(ApiBaseView):
    """resolves to /api/queue/scheduled/
    GET: recurring jobs with their next run time
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=ScheduledJobSerializer(many=True))
    def get(self, request):
        """list schedules"""
        return not_built(STEP)


class ScheduledView(ApiBaseView):
    """resolves to /api/queue/scheduled/<name>/
    PATCH: pause or resume the schedule
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=ScheduledJobUpdateSerializer(),
        responses={200: ScheduledJobSerializer(), 404: NOT_FOUND},
    )
    def patch(self, request, name):
        """enable or disable"""
        return not_built(STEP)


class ScheduledRunView(ApiBaseView):
    """resolves to /api/queue/scheduled/<name>/run/
    POST: run now, as a high priority job
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=None,
        responses={
            201: JobCreateResponseSerializer(),
            200: OpenApiResponse(
                JobCreateResponseSerializer(),
                description="merged into an existing queued job",
            ),
            404: NOT_FOUND,
        },
    )
    def post(self, request, name):
        """queue the schedule's job now"""
        return not_built(STEP)

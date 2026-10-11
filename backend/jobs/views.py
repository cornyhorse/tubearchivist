"""work queue API views, /api/queue/"""

from channel.models import Channel
from common.serializers import ErrorResponseSerializer
from common.src.index_generic import Pagination
from common.views_base import AdminOnly, ApiBaseView
from download.models import Download
from drf_spectacular.utils import OpenApiResponse, extend_schema
from jobs.models import PRIORITY_BY_NAME, CreatedBy, Job, Lane
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
from jobs.src.engine import (
    ActionNotAllowed,
    apply_action,
    enqueue,
    ensure_lanes,
    pump,
)
from jobs.src.present import (
    filter_jobs,
    job_dict,
    jobs_dicts,
    lane_dicts,
    summary,
)
from jobs.src.registry import LANES, get_spec
from jobs.src.schedules import (
    NO_CHANGE,
    SCHEDULED,
    run_now,
    schedule_dicts,
    update,
)
from playlist.models import Playlist
from rest_framework.response import Response
from task.models import CustomPeriodicTask
from video.models import Video

NOT_FOUND = OpenApiResponse(ErrorResponseSerializer(), description="not found")
BAD_REQUEST = OpenApiResponse(
    ErrorResponseSerializer(), description="bad request"
)
CONFLICT = OpenApiResponse(
    ErrorResponseSerializer(),
    description="action not allowed in the job's current status",
)


def target_title(kind: str, target_id: str | None) -> str | None:
    """display name of a job target, if TA knows it"""
    lookups = {
        "channel": lambda: Channel.objects.filter(pk=target_id).values_list(
            "name", flat=True
        ),
        "playlist": lambda: Playlist.objects.filter(pk=target_id).values_list(
            "name", flat=True
        ),
        "video": lambda: list(
            Download.objects.filter(pk=target_id).values_list(
                "title", flat=True
            )
        )
        + list(
            Video.objects.filter(pk=target_id).values_list("title", flat=True)
        ),
    }
    if kind not in lookups or not target_id:
        return None
    return next(iter(lookups[kind]()), None)


def created_response(job: Job | None, merged: bool) -> Response:
    """201 for a new job, 200 when merged or nothing to do"""
    data = {"job": job_dict(job) if job else None, "merged": merged}
    return Response(data, status=201 if job and not merged else 200)


class QueueSummaryView(ApiBaseView):
    """resolves to /api/queue/summary/
    GET: counts for the header indicator, any signed in user
    """

    @extend_schema(responses=QueueSummarySerializer())
    def get(self, request):
        """running, queued and per-lane counts"""
        return Response(summary())


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
        query = JobListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            queryset = filter_jobs(query.validated_data)
        except ValueError as err:
            return Response({"error": str(err)}, status=400)
        pagination = Pagination(
            request, page_size=query.validated_data.get("page_size")
        )
        page = pagination.pagination
        start = page["page_from"]
        jobs = list(queryset[start : start + page["page_size"]])
        pagination.validate(queryset.count())
        return Response({"data": jobs_dicts(jobs), "paginate": page})

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
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        spec = get_spec(data["type"])
        target_id = data.get("target_id")
        if spec.target_kind != "none" and not target_id:
            return Response(
                {"error": f"{data['type']} needs a target_id"}, status=400
            )
        job, merged = enqueue(
            data["type"],
            target_id if spec.target_kind != "none" else None,
            target_title=target_title(spec.target_kind, target_id),
            priority=PRIORITY_BY_NAME[data["priority"]],
            created_by=CreatedBy.USER,
        )
        return created_response(job, merged)


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
        job = Job.objects.filter(pk=job_id).first()
        if not job:
            return Response({"error": "job not found"}, status=404)
        return Response(job_dict(job))


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
        serializer = JobActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            job = apply_action(job_id, serializer.validated_data["action"])
        except Job.DoesNotExist:
            return Response({"error": "job not found"}, status=404)
        except ActionNotAllowed as err:
            return Response({"error": str(err)}, status=409)
        return Response(job_dict(job))


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
        serializer = JobBulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        changed, skipped = [], {}
        for job_id in serializer.validated_data["ids"]:
            try:
                changed.append(apply_action(job_id, action).pk)
            except Job.DoesNotExist:
                skipped[str(job_id)] = "not found"
            except ActionNotAllowed as err:
                skipped[str(job_id)] = str(err)
        return Response({"changed": changed, "skipped": skipped})


class LaneListView(ApiBaseView):
    """resolves to /api/queue/lanes/
    GET: all lanes with settings and counts
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=LaneSerializer(many=True))
    def get(self, request):
        """list lanes"""
        return Response(lane_dicts())


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
        if lane not in LANES:
            return Response({"error": "lane not found"}, status=404)
        serializer = LaneUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ensure_lanes()
        Lane.objects.filter(name=lane).update(**serializer.validated_data)
        pump()
        return Response(next(i for i in lane_dicts() if i["name"] == lane))


class ScheduledListView(ApiBaseView):
    """resolves to /api/queue/scheduled/
    GET: recurring jobs with their next run time
    """

    permission_classes = [AdminOnly]

    @extend_schema(responses=ScheduledJobSerializer(many=True))
    def get(self, request):
        """list schedules"""
        return Response(schedule_dicts())


class ScheduledView(ApiBaseView):
    """resolves to /api/queue/scheduled/<name>/
    PATCH: pause or resume the schedule, or change when it runs
    """

    permission_classes = [AdminOnly]

    @extend_schema(
        request=ScheduledJobUpdateSerializer(),
        responses={200: ScheduledJobSerializer(), 404: NOT_FOUND},
    )
    def patch(self, request, name):
        """enable or disable"""
        serializer = ScheduledJobUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            item = update(
                name, data.get("enabled"), data.get("schedule", NO_CHANGE)
            )
        except (KeyError, CustomPeriodicTask.DoesNotExist):
            return Response({"error": "schedule not found"}, status=404)
        except ValueError as err:
            return Response({"error": str(err)}, status=400)
        return Response(item)


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
        if name not in SCHEDULED:
            return Response({"error": "schedule not found"}, status=404)
        job, merged = run_now(name)
        return created_response(job, merged)

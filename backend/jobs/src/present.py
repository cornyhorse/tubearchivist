"""
Functionality:
- jobs, lanes and schedules in the shape of the API contract
- shared by the REST views and the MCP server
"""

from datetime import timedelta

from django.db.models import Case, Count, IntegerField, Value, When
from django.utils import timezone
from jobs.models import ACTIVE, FINISHED, Job, JobStatus, Lane
from jobs.src.engine import allowed_actions, ensure_lanes
from jobs.src.registry import LANES, get_spec

STATUS_ALIASES = {"active": ACTIVE, "history": FINISHED}
TARGET_URLS = {
    "channel": "https://www.youtube.com/channel/{}",
    "playlist": "https://www.youtube.com/playlist?list={}",
    "video": "https://www.youtube.com/watch?v={}",
}


def parse_statuses(value: str | None) -> list[str] | None:
    """comma list of statuses or aliases, None for all; ValueError"""
    if not value:
        return None
    statuses: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if part in STATUS_ALIASES:
            statuses.extend(STATUS_ALIASES[part])
        elif part in JobStatus.values:
            statuses.append(part)
        else:
            raise ValueError(f"unknown status: {part}")
    return statuses


def filter_jobs(filters: dict):
    """jobs matching the list filters, in display order"""
    statuses = parse_statuses(filters.get("status"))
    queryset = Job.objects.annotate(children_count=Count("children"))
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    for key in ["lane", "type", "created_by"]:
        if filters.get(key):
            queryset = queryset.filter(**{key: filters[key]})
    if filters.get("target"):
        queryset = queryset.filter(target_id=filters["target"])
    if filters.get("parent"):
        queryset = queryset.filter(parent_id=filters["parent"])

    if statuses and set(statuses) <= set(ACTIVE):
        rank = Case(
            When(status=JobStatus.RUNNING, then=Value(0)),
            When(status=JobStatus.QUEUED, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        return queryset.order_by(rank, "-priority", "created_at", "id")
    if statuses and set(statuses) <= set(FINISHED):
        return queryset.order_by("-finished_at", "-id")
    return queryset.order_by("-created_at", "-id")


def queue_positions(jobs: list[Job]) -> dict[int, int]:
    """1-based place of each queued job in its lane"""
    lanes = {j.lane for j in jobs if j.status == JobStatus.QUEUED}
    positions = {}
    for lane in lanes:
        ids = Job.objects.filter(lane=lane, status=JobStatus.QUEUED)
        ordered = ids.run_order().values_list("id", flat=True)
        positions.update({pk: idx for idx, pk in enumerate(ordered, 1)})
    return positions


def job_label(job_type: str) -> str:
    """plain words for a job type"""
    try:
        return get_spec(job_type).label
    except KeyError:
        return job_type.replace("_", " ").capitalize()


def job_dict(job: Job, positions: dict[int, int] | None = None) -> dict:
    """a job as the API returns it"""
    if positions is None:
        positions = queue_positions([job])
    duration = None
    if job.started_at and job.finished_at:
        duration = (job.finished_at - job.started_at).total_seconds()
    progress = None
    if job.status == JobStatus.RUNNING and job.progress:
        progress = {
            "fraction": job.progress.get("fraction"),
            "message": job.progress.get("message", ""),
            "speed": job.progress.get("speed"),
            "eta_seconds": job.progress.get("eta_seconds"),
            "current": job.progress.get("current"),
            "total": job.progress.get("total"),
        }
    children = getattr(job, "children_count", None)
    if children is None:
        children = job.children.count()
    label = job_label(job.type)
    target = job.target_title or job.target_id
    url = TARGET_URLS.get(job.target_kind)
    return {
        "id": job.pk,
        "type": job.type,
        "type_label": label,
        "label": f"{label}: {target}" if target else label,
        "lane": job.lane,
        "target": {
            "kind": job.target_kind,
            "id": job.target_id,
            "title": job.target_title,
            "url": (
                url.format(job.target_id) if url and job.target_id else None
            ),
        },
        "status": job.status,
        "priority": job.priority,
        "position": positions.get(job.pk),
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "progress": progress,
        "error": job.error,
        "result": job.result,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_seconds": duration,
        "depends_on": job.depends_on_id,
        "parent": job.parent_id,
        "children": children,
        "merged": job.merged,
        "actions": allowed_actions(job),
    }


def jobs_dicts(jobs: list[Job]) -> list[dict]:
    """many jobs, positions computed once"""
    positions = queue_positions(jobs)
    return [job_dict(job, positions) for job in jobs]


def lane_dicts() -> list[dict]:
    """every lane with settings and counts"""
    ensure_lanes()
    counts = {
        (row["lane"], row["status"]): row["n"]
        for row in Job.objects.filter(status__in=ACTIVE)
        .values("lane", "status")
        .annotate(n=Count("id"))
    }
    lanes = []
    for lane in Lane.objects.all():
        label, description, _, _ = LANES.get(lane.name, (lane.name, "", 1, 0))
        lanes.append(
            {
                "name": lane.name,
                "label": label,
                "description": description,
                "paused": lane.paused,
                "concurrency": lane.concurrency,
                "min_interval_seconds": lane.min_interval_seconds,
                "running": counts.get((lane.name, JobStatus.RUNNING), 0),
                "queued": counts.get((lane.name, JobStatus.QUEUED), 0),
            }
        )
    order = list(LANES)
    lanes.sort(
        key=lambda i: (
            order.index(i["name"]) if i["name"] in order else len(order)
        )
    )
    return lanes


def summary() -> dict:
    """header indicator"""
    lanes = lane_dicts()
    day_ago = timezone.now() - timedelta(days=1)
    return {
        "running": sum(i["running"] for i in lanes),
        "queued": sum(i["queued"] for i in lanes),
        "paused": Job.objects.filter(status=JobStatus.PAUSED).count(),
        "failed_24h": Job.objects.filter(
            status=JobStatus.FAILED, finished_at__gte=day_ago
        ).count(),
        "lanes": lanes,
    }

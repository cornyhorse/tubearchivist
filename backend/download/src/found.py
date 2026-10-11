"""
Functionality:
- found videos: what TA knows about but hasn't downloaded, with the rule
  verdict and the download job of each
- bulk actions on explicit ids or on a filter
- shared by the REST views and the MCP server
"""

from datetime import datetime, timedelta

from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone
from download.models import Download, DownloadStatus
from download.src.rules import RuleSet
from jobs.models import CreatedBy, Job, JobStatus, Priority
from jobs.src.engine import active_jobs, cancel_waiting, enqueue, pump
from video.models import Video

SORT_FIELDS = {
    # sort key: (Download field, Video field)
    "published": ("published", "published"),
    "found": ("timestamp", "date_downloaded"),
    "duration": ("duration_seconds", "duration"),
    "title": ("title", "title"),
    "channel": ("channel_name", "channel__name"),
}
FOUND_BY_LABELS = {
    "channel_check": "Channel check {date}",
    "playlist_check": "Playlist check {date}",
    "user": "Added by you",
    "agent": "Added by an agent",
    "auto_delete": "Re-added after auto delete {date}",
    "import": "Imported {date}",
    "unknown": "Found {date}",
}


def _split(value: str | None) -> list[str]:
    return [i.strip() for i in (value or "").split(",") if i.strip()]


def _media_filters(filters: dict, channel_name: str, duration: str):
    """Q conditions both found and downloaded videos support"""
    conditions = []
    if query := filters.get("q"):
        conditions.append(
            Q(title__icontains=query)
            | Q(**{f"{channel_name}__icontains": query})
            | Q(youtube_id=query)
        )
    lookups = {
        "channel_id__in": _split(filters.get("channel")),
        "vid_type__in": _split(filters.get("vid_type")),
        f"{duration}__gte": filters.get("min_duration"),
        f"{duration}__lte": filters.get("max_duration"),
        "published__date__gte": filters.get("published_from"),
        "published__date__lte": filters.get("published_to"),
    }
    conditions.extend(
        Q(**{lookup: value})
        for lookup, value in lookups.items()
        if value not in (None, [])
    )
    return conditions


# filters that describe found items only
FOUND_ONLY = {
    "error": lambda value: Q(message__isnull=not value),
    "skipped_by_rule": lambda value: Q(ignore_reason__isnull=not value),
    "reason_key": lambda value: Q(ignore_reason_key=value),
    "found_by": lambda value: Q(found_by_kind=value),
}
TABS = {
    "waiting": Q(status=DownloadStatus.PENDING, message__isnull=True),
    "failed": Q(status=DownloadStatus.PENDING, message__isnull=False),
    "ignored": Q(status=DownloadStatus.IGNORE),
}


def download_queryset(filters: dict, tab: str | None = None) -> QuerySet:
    """found videos matching filters, in one tab"""
    tab = tab or filters.get("tab", "waiting")
    conditions = [TABS[tab]]
    conditions += _media_filters(filters, "channel_name", "duration_seconds")
    conditions += [
        make(filters[key])
        for key, make in FOUND_ONLY.items()
        if filters.get(key) is not None
    ]
    return Download.objects.filter(*conditions)


def video_queryset(filters: dict) -> QuerySet:
    """recently downloaded videos matching the filters that apply"""
    since = timezone.now() - timedelta(days=filters.get("downloaded_days", 7))
    queryset = Video.objects.select_related("channel").filter(
        date_downloaded__gte=since
    )
    if any(filters.get(key) is not None for key in FOUND_ONLY):
        # these describe found items, a downloaded video matches none
        return queryset.none()
    return queryset.filter(
        *_media_filters(filters, "channel__name", "duration")
    )


def tab_counts(filters: dict) -> dict:
    """count per tab with the filters, except the tab"""
    counts = {
        tab: download_queryset(filters, tab).count()
        for tab in ["waiting", "ignored", "failed"]
    }
    counts["downloaded"] = video_queryset(filters).count()
    return counts


def channel_facets(filters: dict, limit: int = 200) -> list[dict]:
    """channels of the current tab with counts, other filters applied"""
    filters = {**filters, "channel": None}
    if filters.get("tab") == "downloaded":
        rows = video_queryset(filters).values(
            "channel_id", name=F("channel__name")
        )
    else:
        rows = download_queryset(filters).values(
            "channel_id", name=F("channel_name")
        )
    rows = rows.annotate(count=Count("youtube_id")).order_by("-count", "name")[
        :limit
    ]
    return [
        {
            "channel_id": row["channel_id"],
            "channel_name": row["name"],
            "count": row["count"],
        }
        for row in rows
    ]


def found_queryset(filters: dict) -> QuerySet:
    """the sorted list of the current tab"""
    sort = filters.get("sort", "-found")
    descending = sort.startswith("-")
    download_field, video_field = SORT_FIELDS[sort.lstrip("-")]
    if filters.get("tab") == "downloaded":
        field, queryset = video_field, video_queryset(filters)
    else:
        field, queryset = download_field, download_queryset(filters)
    order = f"-{field}" if descending else field
    return queryset.order_by(order, "youtube_id")


def _label_date(value: datetime) -> str:
    return f"{value:%b} {value.day}"


def _found_by(item: Download) -> dict:
    template = FOUND_BY_LABELS.get(item.found_by_kind, "Found {date}")
    return {
        "kind": (
            item.found_by_kind
            if item.found_by_kind in FOUND_BY_LABELS
            else "unknown"
        ),
        "label": template.format(date=_label_date(item.timestamp)),
        "job_id": item.found_by_job,
        "playlist_id": item.found_playlist_id,
    }


def verdict(
    rules: RuleSet,
    entry: dict,
    stored: dict | None = None,
    playlist_id: str | None = None,
) -> dict:
    """what the rules do with a video; stored: reason a rule recorded"""
    result = rules.verdict(entry, playlist_id)
    if stored and stored.get("ignore_reason") and not result["override"]:
        result.update(
            allowed=False,
            action="ignore",
            reason_key=stored.get("ignore_reason_key"),
            reason=stored["ignore_reason"],
        )
    return result


def _state(item: Download, job: Job | None) -> str:
    if item.status == DownloadStatus.IGNORE:
        return "ignored"
    if item.message:
        return "failed"
    if job and job.status == JobStatus.RUNNING:
        return "downloading"
    if job:
        return "queued"
    return "waiting"


def _job_ref(job: Job | None, positions: dict) -> dict | None:
    if not job:
        return None
    fraction = (job.progress or {}).get("fraction")
    return {
        "id": job.pk,
        "status": job.status,
        "position": positions.get(job.pk),
        "progress": fraction if job.status == JobStatus.RUNNING else None,
    }


def found_dicts(items: list) -> list[dict]:
    """a page of Download or Video rows as found videos"""
    # pylint: disable=import-outside-toplevel
    from jobs.src.present import queue_positions

    rules = RuleSet()
    ids = [i.youtube_id for i in items]
    jobs = active_jobs("download_video", ids)
    positions = queue_positions(list(jobs.values()))
    result = []
    for item in items:
        if isinstance(item, Video):
            result.append(_video_dict(item, rules))
            continue
        doc = item.to_doc()
        job = jobs.get(item.youtube_id)
        result.append(
            {
                "youtube_id": item.youtube_id,
                "title": item.title,
                "channel_id": item.channel_id,
                "channel_name": item.channel_name,
                "channel_indexed": doc["channel_indexed"],
                "vid_type": item.vid_type,
                "duration": item.duration,
                "duration_seconds": item.duration_seconds,
                "published": item.published,
                "thumb_url": item.vid_thumb_url or None,
                "found_at": item.timestamp,
                "found_by": _found_by(item),
                "verdict": verdict(
                    rules,
                    doc,
                    {
                        "ignore_reason": item.ignore_reason,
                        "ignore_reason_key": item.ignore_reason_key,
                    },
                    item.found_playlist_id,
                ),
                "state": _state(item, job),
                "job": _job_ref(job, positions),
                "error": item.message,
                "downloaded_at": None,
            }
        )
    return result


def _video_dict(video: Video, rules: RuleSet) -> dict:
    entry = {
        "channel_id": video.channel_id,
        "duration_seconds": video.duration,
        "vid_type": video.vid_type,
        "title": video.title,
        "published": video.published.timestamp() if video.published else None,
    }
    minutes, seconds = divmod(video.duration or 0, 60)
    hours, minutes = divmod(minutes, 60)
    duration = (
        f"{hours}:{minutes:02}:{seconds:02}"
        if hours
        else (f"{minutes:02}:{seconds:02}")
    )
    return {
        "youtube_id": video.youtube_id,
        "title": video.title,
        "channel_id": video.channel_id,
        "channel_name": video.channel.name,
        "channel_indexed": True,
        "vid_type": video.vid_type,
        "duration": duration,
        "duration_seconds": video.duration,
        "published": video.published,
        "thumb_url": video.vid_thumb_url or None,
        "found_at": video.date_downloaded,
        "found_by": {
            "kind": "unknown",
            "label": f"Downloaded {_label_date(video.date_downloaded)}",
            "job_id": None,
            "playlist_id": None,
        },
        "verdict": verdict(rules, entry),
        "state": "downloaded",
        "job": None,
        "error": None,
        "downloaded_at": video.date_downloaded,
    }


def found_action(
    action: str,
    ids: list[str] | None = None,
    filters: dict | None = None,
    created_by: str = CreatedBy.USER,
) -> dict:
    """apply a bulk action; ValueError"""
    if ids is not None:
        queryset = Download.objects.filter(youtube_id__in=ids)
    elif filters and filters.get("tab") == "downloaded":
        raise ValueError("downloaded videos have no found video actions")
    else:
        queryset = download_queryset(filters or {})

    targets = list(queryset.values_list("youtube_id", "title"))
    matched = len(targets)
    target_ids = [i[0] for i in targets]
    queryset = Download.objects.filter(youtube_id__in=target_ids)
    job_ids: list[int] = []

    if action in ("download_anyway", "always_ignore"):
        # pylint: disable=import-outside-toplevel
        from download.src.rules_api import set_override

        override = "download" if action == "download_anyway" else "ignore"
        job_ids = set_override(target_ids, override, created_by=created_by)
        changed = matched
    elif action in ("download_now", "download"):
        changed = queryset.update(
            status=DownloadStatus.PENDING,
            ignore_reason=None,
            ignore_reason_key=None,
            message=None,
        )
        priority = (
            Priority.HIGH if action == "download_now" else (Priority.NORMAL)
        )
        for youtube_id, title in targets:
            job, _ = enqueue(
                "download_video",
                youtube_id,
                target_title=title,
                priority=priority,
                created_by=created_by,
                dispatch=False,
            )
            job_ids.append(job.pk)
        pump()
    elif action == "ignore":
        cancel_waiting("download_video", target_ids)
        changed = queryset.update(
            status=DownloadStatus.IGNORE,
            ignore_reason=None,
            ignore_reason_key=None,
        )
    elif action == "forget":
        cancel_waiting("download_video", target_ids)
        changed, _ = queryset.delete()
    else:  # clear_error
        changed = queryset.exclude(message__isnull=True).update(message=None)

    return {"matched": matched, "changed": changed, "job_ids": job_ids}

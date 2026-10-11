"""
Functionality:
- collect what the Emby writer changed, and tell the media server in one
  debounced media_notify job, never a full library scan
- media_refresh: the full refresh for repairs
"""

from datetime import timedelta
from pathlib import PurePosixPath

from common.src.ta_redis import RedisBase
from django.utils import timezone
from jobs.models import CreatedBy, Job, Priority
from jobs.src.context import JobContext
from jobs.src.engine import enqueue
from jobs.src.registry import JobError, register
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError

PENDING_KEY = "mediaserver:pending"


def _conn():
    return RedisBase().conn, RedisBase.NAME_SPACE + PENDING_KEY


def changed_folders(report) -> set[str]:
    """
    folders below the Emby root that hold a change; the server rescans a
    folder quickly and notices added, changed and removed files in it
    """
    paths = list(report.created) + list(report.updated)
    paths += [old for old, _ in report.renamed]
    paths += [new for _, new in report.renamed]
    paths += [p.rstrip("/") for p in report.deleted]
    return {str(PurePosixPath(p).parent) for p in paths if p}


def queue_notify(
    relpaths: set[str] | list[str],
    created_by: str = CreatedBy.SYSTEM,
    parent: Job | None = None,
    priority: int = Priority.LOW,
) -> Job | None:
    """
    add folders to the pending set and queue the notify job, which waits
    debounce_seconds so more changes join it; None if notify is off
    """
    stored = settings.load()
    if not stored["type"] or not stored["notify_enabled"] or not relpaths:
        return None
    conn, key = _conn()
    conn.sadd(key, *sorted(relpaths))
    delay = timedelta(seconds=stored["debounce_seconds"])
    job, _ = enqueue(
        "media_notify",
        priority=priority,
        created_by=created_by,
        parent=parent,
        not_before=timezone.now() + delay,
    )
    return job


def _take_pending() -> list[str]:
    conn, key = _conn()
    with conn.pipeline() as pipe:
        pipe.smembers(key)
        pipe.delete(key)
        members, _ = pipe.execute()
    return sorted(members)


def _put_back(relpaths: list[str]) -> None:
    conn, key = _conn()
    conn.sadd(key, *relpaths)


@register(
    "media_notify",
    "postprocess",
    "Tell the media server what changed",
    max_attempts=3,
    notify=False,
)
def media_notify(ctx: JobContext) -> dict:
    """one Library/Media/Updated call for everything pending"""
    conn = settings.connection()
    if not conn:
        return {"skipped": "no media server configured"}
    relpaths = _take_pending()
    if not relpaths:
        return {"paths": 0}
    updates = [
        {"Path": settings.to_server_path(rel), "UpdateType": "Modified"}
        for rel in relpaths
    ]
    ctx.send_progress([f"Notifying {conn.type} of {len(updates)} folders"])
    result = {
        "at": timezone.now().isoformat(),
        "paths": len(updates),
        "job_id": ctx.job.pk,
    }
    try:
        status = MediaServerClient(conn).media_updated(updates)
    except MediaServerError as err:
        _put_back(relpaths)
        settings.record_notify(
            {**result, "status_code": None, "error": str(err)}
        )
        raise JobError(str(err)) from err
    settings.record_notify({**result, "status_code": status, "error": None})
    return {"status_code": status, "paths": len(updates)}


@register(
    "media_refresh",
    "postprocess",
    "Refresh the media server library",
    notify=False,
)
def media_refresh(ctx: JobContext) -> dict:
    """full refresh, for repairs"""
    conn = settings.connection()
    if not conn:
        raise JobError("no media server configured")
    library_id = ctx.params.get("library_id") or settings.load()["library_id"]
    try:
        status = MediaServerClient(conn).refresh(library_id or None)
    except MediaServerError as err:
        raise JobError(str(err)) from err
    return {"status_code": status, "library_id": library_id or "all"}


def test_connection(overrides: dict | None = None) -> dict:
    """for the Test connection button; never raises"""
    conn = settings.connection(overrides)
    empty = {"server_name": None, "product": None, "version": None}
    if not conn:
        return {**empty, "ok": False, "error": "type, url and API key needed"}
    try:
        info = MediaServerClient(conn).info()
    except MediaServerError as err:
        return {**empty, "ok": False, "error": str(err)}
    return {**info, "ok": True, "error": None}


def libraries() -> list[dict]:
    """libraries of the configured server; LookupError, MediaServerError"""
    conn = settings.connection()
    if not conn:
        raise LookupError("no media server configured")
    base = settings.to_server_path("").rstrip("/")
    items = MediaServerClient(conn).libraries()
    for item in items:
        item["contains_ta"] = bool(base) and any(
            path.rstrip("/") == base or path.startswith(base + "/")
            for path in item["paths"]
        )
    return items


def manual_notify(paths: list[str] | None, everything: bool) -> Job:
    """notify of TA-side paths now, or refresh all; LookupError, ValueError"""
    if not settings.connection():
        raise LookupError("no media server configured")
    if everything:
        return queue_refresh(None, CreatedBy.USER)
    relpaths = [settings.from_ta_path(path) for path in paths or []]
    conn, key = _conn()
    conn.sadd(key, *relpaths)
    job, _ = enqueue(
        "media_notify", priority=Priority.HIGH, created_by=CreatedBy.USER
    )
    return job


def queue_refresh(library_id: str | None, created_by: str) -> Job:
    """full refresh job; LookupError"""
    if not settings.connection():
        raise LookupError("no media server configured")
    job, _ = enqueue(
        "media_refresh",
        params={"library_id": library_id} if library_id else {},
        priority=Priority.HIGH,
        created_by=created_by,
    )
    return job

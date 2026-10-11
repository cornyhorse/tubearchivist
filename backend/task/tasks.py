"""
Functionality:
- the task names the scheduler and the older API endpoints start
- every task only queues jobs in the work queue (jobs app), which runs
  them in lanes; nothing here blocks or drops a request
"""

from appsettings.src.reindex import ReindexManual
from celery import shared_task
from common.src.urlparser import ParsedURLType, Parser
from download.models import Download, DownloadStatus
from jobs.models import CreatedBy, Priority
from jobs.src.engine import enqueue

# scheduled runs pass created_by="schedule" through the schedule's kwargs
PRIORITY = {CreatedBy.SCHEDULE: Priority.LOW}


def _queue(job_type, target_id=None, created_by=CreatedBy.USER, **kwargs):
    """queue a job, low priority for schedules, high for people"""
    kwargs.setdefault("priority", PRIORITY.get(created_by, Priority.HIGH))
    job, merged = enqueue(job_type, target_id, created_by=created_by, **kwargs)
    verb = "merged into" if merged else "queued"
    return f"{verb} job #{job.pk}"


@shared_task(name="update_subscribed")
def update_subscribed(created_by=CreatedBy.USER):
    """check every subscribed channel and playlist"""
    return _queue("check_subscriptions", created_by=created_by)


@shared_task(name="download_pending")
def download_pending(auto_only=False, created_by=CreatedBy.USER):
    """queue a download job for every waiting found video"""
    items = Download.objects.filter(
        status=DownloadStatus.PENDING, message__isnull=True
    )
    if auto_only:
        items = items.filter(auto_start=True)
    queued = 0
    for youtube_id, title in items.queue_order().values_list(
        "youtube_id", "title"
    ):
        _queue(
            "download_video",
            youtube_id,
            created_by=created_by,
            target_title=title,
        )
        queued += 1
    return f"queued {queued} downloads"


def _entry_params(entry: ParsedURLType) -> dict:
    vid_type = getattr(entry["vid_type"], "value", entry["vid_type"])
    return {**entry, "vid_type": str(vid_type)}


@shared_task(name="extract_download")
def extrac_dl(
    youtube_ids: str | list[ParsedURLType],
    auto_start: bool = False,
    flat: bool = False,
    force: bool = False,
    status: str = "pending",
    created_by=CreatedBy.USER,
) -> str:
    """queue a scan job per link, downloads follow if auto_start"""
    if isinstance(youtube_ids, str):
        to_add = Parser(youtube_ids).parse()
    else:
        to_add = youtube_ids

    params = {
        "download_now": auto_start,
        "flat": flat,
        "force": force,
        "status": status,
    }
    job_types = {
        "video": "fetch_video",
        "channel": "check_channel",
        "playlist": "check_playlist",
    }
    messages = []
    for entry in to_add:
        entry_params = {**params, "vid_type": _entry_params(entry)["vid_type"]}
        if entry["type"] == "channel":
            entry_params["entries"] = [_entry_params(entry)]
        if entry.get("limit"):
            entry_params["newest_n"] = entry["limit"]
        messages.append(
            _queue(
                job_types[entry["type"]],
                entry["url"],
                created_by=created_by,
                params=entry_params,
            )
        )
    return "; ".join(messages)


@shared_task(name="check_reindex")
def check_reindex(data=False, extract_videos=False, created_by=CreatedBy.USER):
    """refresh metadata: what data names, else recent and outdated items"""
    if data:
        ReindexManual(extract_videos=extract_videos).extract_data(data)
    return _queue(
        "reindex", created_by=created_by, params={"populate": not data}
    )


@shared_task(name="manual_import")
def manual_import(ignore_error, prefer_local, created_by=CreatedBy.USER):
    """go through the import folder"""
    return _queue(
        "manual_import",
        created_by=created_by,
        params={"ignore_error": ignore_error, "prefer_local": prefer_local},
    )


@shared_task(name="run_backup")
def run_backup(reason="auto", created_by=CreatedBy.USER):
    """zip backup"""
    return _queue("backup", created_by=created_by, params={"reason": reason})


@shared_task(name="restore_backup")
def run_restore_backup(filename, created_by=CreatedBy.USER):
    """restore a zip backup"""
    return _queue(
        "restore",
        filename,
        created_by=created_by,
        params={"filename": filename},
    )


@shared_task(name="rescan_filesystem")
def rescan_filesystem(ignore_error, prefer_local, created_by=CreatedBy.USER):
    """check the media folder for mismatches"""
    return _queue(
        "filesystem_rescan",
        created_by=created_by,
        params={"ignore_error": ignore_error, "prefer_local": prefer_local},
    )


@shared_task(name="thumbnail_check")
def thumbnail_check(created_by=CreatedBy.USER):
    """validate thumbnails"""
    return _queue("thumbnail_check", created_by=created_by)


@shared_task(name="resync_metadata")
def re_sync_metadata(created_by=CreatedBy.USER):
    """embed metadata in media files"""
    return _queue("resync_metadata", created_by=created_by)


@shared_task(name="subscribe_to")
def subscribe_to(
    url_str: str,
    expected_type: str | bool = False,
    created_by=CreatedBy.USER,
):
    """subscribe to the channels and playlists in url_str"""
    return _queue(
        "subscribe",
        created_by=created_by,
        params={"urls": url_str, "expected_type": expected_type},
        dedupe_key=f"subscribe:{url_str}",
    )


@shared_task(name="index_playlists")
def index_channel_playlists(channel_id, created_by=CreatedBy.USER):
    """add all playlists of a channel"""
    return _queue("index_playlists", channel_id, created_by=created_by)


@shared_task(name="emby_sync")
def emby_sync(created_by=CreatedBy.USER):
    """write the Emby library tree"""
    return _queue("emby_write", created_by=created_by)


@shared_task(name="db_snapshot")
def db_snapshot(created_by=CreatedBy.SCHEDULE):
    """daily database snapshot, scheduled by DbSnapshot.setup"""
    return _queue("snapshot", created_by=created_by)


@shared_task(name="version_check")
def version_check(created_by=CreatedBy.SCHEDULE):
    """check for new updates"""
    return _queue("version_check", created_by=created_by)


@shared_task(name="emby_health")
def emby_health(created_by=CreatedBy.USER):
    """check and repair the Emby library sync"""
    return _queue("emby_health", created_by=created_by)

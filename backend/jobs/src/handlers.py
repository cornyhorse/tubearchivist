"""
Functionality:
- what each job type does, registered with its lane
- scans store every found video right away and queue its download, so
  downloads start while the scan goes on
"""

from datetime import timedelta

from appsettings.src.backup import DatabaseBackup
from appsettings.src.config import AppConfig, ReleaseVersion
from appsettings.src.filesystem import Scanner
from appsettings.src.manual import ImportFolderScanner
from appsettings.src.reindex import Reindex, ReindexPopulate
from appsettings.src.snapshot import DbSnapshot
from channel.models import Channel
from channel.src.index import YoutubeChannel
from django.utils import timezone
from download.models import Download, DownloadRule
from download.serializers import DownloadRuleSerializer
from download.src.queue import PendingList
from download.src.subscriptions import (
    ChannelSubscription,
    SubscriptionHandler,
)
from download.src.thumbnails import ThumbValidator
from download.src.yt_dlp_handler import DownloadPostProcess, VideoDownloader
from emby.src.sync import paths_from_env, sync_all
from jobs.models import ACTIVE, Job, Priority
from jobs.src.context import JobContext
from jobs.src.engine import enqueue
from jobs.src.registry import Defer, JobError, register
from playlist.models import Playlist
from video.models import Video
from video.src.meta_embed import MetadataEmbed

# wait this long after a download before post processing, to batch
POST_DOWNLOAD_DELAY = 60
# while downloads are queued, check again after this long
POST_DOWNLOAD_RECHECK = 120
FOUND_BY_CREATOR = {"user": "user", "agent": "agent"}


def _child(ctx: JobContext, job_type: str, target_id=None, **kwargs):
    """queue a follow-up job on behalf of the running one"""
    job = ctx.job
    kwargs.setdefault("priority", job.priority)
    return enqueue(
        job_type,
        target_id,
        created_by=job.created_by,
        parent=job,
        **kwargs,
    )


def rule_from_params(params: dict) -> DownloadRule | None | bool:
    """
    rule for a one-off add: False if params have none (use stored rules),
    None if params say no rule, else an unsaved DownloadRule
    """
    if "rule" not in params:
        return False
    if not params["rule"]:
        return None
    serializer = DownloadRuleSerializer(data=params["rule"])
    serializer.is_valid(raise_exception=True)
    return DownloadRule(**serializer.validated_data)


def scan(
    ctx: JobContext,
    entries: list,
    found_kind: str,
    playlist_id: str | None = None,
) -> dict:
    """find videos of entries, queue downloads for the allowed ones"""
    config = AppConfig().config["subscriptions"]
    params = ctx.params
    download_now = params.get("download_now")
    if download_now is None:
        download_now = bool(config.get("auto_start"))

    counts = {"found": 0, "allowed": 0, "ignored": 0, "queued_downloads": 0}

    def on_added(items: list[dict]) -> None:
        for item in items:
            counts["found"] += 1
            if item.get("status") == "ignore":
                counts["ignored"] += 1
                continue
            counts["allowed"] += 1
            if download_now:
                _child(
                    ctx,
                    "download_video",
                    item["youtube_id"],
                    target_title=item.get("title"),
                )
                counts["queued_downloads"] += 1

    PendingList(
        youtube_ids=entries,
        task=ctx,
        flat=params.get("flat", config.get("extract_flat", False)),
        force=params.get("force", False),
        on_added=on_added,
        found_by={
            "found_by_kind": FOUND_BY_CREATOR.get(
                ctx.job.created_by, found_kind
            ),
            "found_by_job": ctx.job.pk,
            "found_playlist_id": playlist_id,
        },
        rule=rule_from_params(params),
    ).parse_url_list(status=params.get("status", "pending"))
    return counts


@register(
    "check_subscriptions",
    "scan",
    "Check channels for new videos",
    legacy_name="update_subscribed",
)
def check_subscriptions(ctx: JobContext) -> dict:
    """one check job per subscribed channel and playlist"""
    params = {"download_now": ctx.params.get("download_now")}
    channels = Channel.objects.filter(subscribed=True).order_by("name")
    for channel in channels:
        _child(
            ctx,
            "check_channel",
            channel.channel_id,
            target_title=channel.name,
            params=params,
            dispatch=False,
        )
    playlists = Playlist.objects.filter(subscribed=True).order_by("name")
    for playlist in playlists:
        _child(
            ctx,
            "check_playlist",
            playlist.playlist_id,
            target_title=playlist.name,
            params=params,
            dispatch=False,
        )
    result = {"channels": channels.count(), "playlists": playlists.count()}
    ctx.send_progress(
        [f"Queued {result['channels']} channel checks"], progress=1
    )
    return result


@register(
    "check_channel",
    "scan",
    "Check channel for new videos",
    target_kind="channel",
    max_attempts=2,
    legacy_name="update_subscribed",
    notify=False,
)
def check_channel(ctx: JobContext) -> dict:
    """find new videos of one channel"""
    channel_id = ctx.job.target_id
    newest_n = ctx.params.get("newest_n")
    doc = Channel.load_doc(channel_id)
    if ctx.params.get("entries"):
        entries = ctx.params["entries"]
    elif doc and doc.get("channel_tabs") and not newest_n:
        entries = ChannelSubscription().channel_urls([doc])
    else:
        entries = [
            {
                "type": "channel",
                "url": channel_id,
                "vid_type": ctx.params.get("vid_type", "unknown"),
                "limit": newest_n,
            }
        ]
    return scan(ctx, entries, "channel_check")


@register(
    "check_playlist",
    "scan",
    "Check playlist for new videos",
    target_kind="playlist",
    max_attempts=2,
    legacy_name="update_subscribed",
    notify=False,
)
def check_playlist(ctx: JobContext) -> dict:
    """find new videos of one playlist"""
    playlist_id = ctx.job.target_id
    limit = ctx.params.get("newest_n")
    if not limit and Playlist.objects.filter(
        playlist_id=playlist_id, subscribed=True
    ):
        limit = AppConfig().config["subscriptions"]["playlist_size"]
    entry = {
        "type": "playlist",
        "url": playlist_id,
        "vid_type": "unknown",
        "limit": limit,
    }
    return scan(ctx, [entry], "playlist_check", playlist_id=playlist_id)


@register(
    "fetch_video",
    "scan",
    "Look up video",
    target_kind="video",
    max_attempts=2,
    legacy_name="extract_download",
    notify=False,
)
def fetch_video(ctx: JobContext) -> dict:
    """add one video to the found videos"""
    entry = {
        "type": "video",
        "url": ctx.job.target_id,
        "vid_type": ctx.params.get("vid_type", "unknown"),
    }
    return scan(ctx, [entry], "user")


@register(
    "download_video",
    "download",
    "Download video",
    target_kind="video",
    max_attempts=3,
    legacy_name="download_pending",
    notify=False,
)
def download_video(ctx: JobContext) -> dict:
    """download one found video"""
    youtube_id = ctx.job.target_id
    item = Download.objects.filter(youtube_id=youtube_id).first()
    if not item:
        if Video.objects.filter(youtube_id=youtube_id).exists():
            return {"skipped": "already downloaded"}
        raise JobError("not in found videos any more")
    if item.status == "ignore":
        return {"skipped": "ignored"}

    if item.message:
        Download.objects.filter(pk=youtube_id).update(message=None)
        item.message = None
    if not VideoDownloader(task=ctx).download_one(item.to_doc()):
        item.refresh_from_db()
        raise JobError(item.message or "download failed")

    _child(
        ctx,
        "post_download",
        priority=Priority.LOW,
        not_before=timezone.now() + timedelta(seconds=POST_DOWNLOAD_DELAY),
    )
    return {"downloaded": youtube_id}


@register(
    "post_download",
    "postprocess",
    "Update playlists, comments and metadata of new downloads",
    legacy_name="download_pending",
)
def post_download(ctx: JobContext) -> dict:
    """the batch work after downloads, once the download lane is idle"""
    busy = Job.objects.filter(type="download_video", status__in=ACTIVE)
    if busy.exists():
        raise Defer(POST_DOWNLOAD_RECHECK, "waiting for downloads to finish")
    DownloadPostProcess(task=ctx).run()
    queue_emby_write(ctx)
    return {"done": True}


def queue_emby_write(ctx: JobContext) -> None:
    """write the Emby library after changes, if enabled"""
    if paths_from_env():
        _child(ctx, "emby_write")


@register(
    "emby_write",
    "postprocess",
    "Write the Emby library",
    legacy_name="emby_sync",
)
def emby_write(ctx: JobContext) -> dict:
    """write the Emby tree"""
    paths = paths_from_env()
    if not paths:
        return {"skipped": "TA_EMBY_ROOT is not set"}
    ctx.send_progress(["Writing the Emby library."])
    report = sync_all(paths)
    for warning in report.warnings[:20]:
        print(f"[emby] {warning}")
    return {
        "created": len(report.created),
        "updated": len(report.updated),
        "renamed": len(report.renamed),
        "removed": len(report.deleted),
    }


@register(
    "reindex",
    "maintenance",
    "Refresh metadata from YouTube",
    legacy_name="check_reindex",
)
def reindex(ctx: JobContext) -> dict:
    """refresh queued, recent and outdated items"""
    if ctx.params.get("populate", True):
        populate = ReindexPopulate()
        ctx.send_progress(["Add recent documents to the reindex Queue."])
        populate.get_interval()
        populate.add_recent()
        ctx.send_progress(["Add outdated documents to the reindex Queue."])
        populate.add_outdated()
    handler = Reindex(task=ctx)
    handler.reindex_all()
    queue_emby_write(ctx)
    return {"message": handler.build_message()}


@register(
    "backup", "maintenance", "Back up the database", legacy_name="run_backup"
)
def backup(ctx: JobContext) -> dict:
    """zip backup"""
    DatabaseBackup(
        reason=ctx.params.get("reason", "auto"), task=ctx
    ).backup_all_indexes()
    return {"done": True}


@register(
    "restore", "maintenance", "Restore a backup", legacy_name="restore_backup"
)
def restore(ctx: JobContext) -> dict:
    """restore from a backup file"""
    filename = ctx.params["filename"]
    DatabaseBackup(task=ctx).restore(filename)
    return {"restored": filename}


@register(
    "filesystem_rescan",
    "maintenance",
    "Compare the media folder with the archive",
    legacy_name="rescan_filesystem",
)
def filesystem_rescan(ctx: JobContext) -> dict:
    """find mismatches between files and the database"""
    handler = Scanner(
        task=ctx,
        ignore_error=ctx.params.get("ignore_error", False),
        prefer_local=ctx.params.get("prefer_local", False),
    )
    handler.scan()
    handler.apply()
    _child(ctx, "thumbnail_check")
    return {"done": True}


@register(
    "thumbnail_check",
    "maintenance",
    "Check thumbnails",
    legacy_name="thumbnail_check",
)
def thumbnail_check(ctx: JobContext) -> dict:
    """download missing, remove orphaned thumbnails"""
    handler = ThumbValidator(task=ctx)
    handler.validate()
    handler.clean_up()
    return {"done": True}


@register(
    "resync_metadata",
    "maintenance",
    "Write metadata into media files",
    legacy_name="resync_metadata",
)
def resync_metadata(ctx: JobContext) -> dict:
    """embed metadata"""
    MetadataEmbed(task=ctx).embed()
    return {"done": True}


@register(
    "manual_import",
    "maintenance",
    "Import videos from the import folder",
    legacy_name="manual_import",
)
def manual_import(ctx: JobContext) -> dict:
    """go through the import folder"""
    ImportFolderScanner(
        task=ctx,
        ignore_error=ctx.params.get("ignore_error", False),
        prefer_local=ctx.params.get("prefer_local", False),
    ).scan()
    return {"done": True}


@register(
    "index_playlists",
    "maintenance",
    "Add the playlists of a channel",
    target_kind="channel",
    legacy_name="index_playlists",
)
def index_playlists(ctx: JobContext) -> dict:
    """index all playlists of a channel"""
    YoutubeChannel(ctx.job.target_id, task=ctx).index_channel_playlists()
    return {"done": True}


@register(
    "subscribe",
    "maintenance",
    "Subscribe",
    legacy_name="subscribe_to",
)
def subscribe(ctx: JobContext) -> dict:
    """subscribe to the channels and playlists of a url list"""
    SubscriptionHandler(ctx.params["urls"], task=ctx).subscribe(
        ctx.params.get("expected_type", False)
    )
    return {"done": True}


@register(
    "version_check", "maintenance", "Look for a new version", notify=False
)
def version_check(ctx: JobContext) -> dict:
    """check for updates"""
    del ctx
    ReleaseVersion().check()
    return {"done": True}


@register("snapshot", "maintenance", "Take a database snapshot", notify=False)
def snapshot(ctx: JobContext) -> dict:
    """daily snapshot"""
    del ctx
    DbSnapshot().take_snapshot_now()
    return {"done": True}

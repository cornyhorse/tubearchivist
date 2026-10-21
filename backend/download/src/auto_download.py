"""
Functionality:
- per channel and per playlist auto download, opt in, off by default
- a check of an opted in channel or playlist downloads what the rules
  allow; everything else waits in found videos
- a video another source found earlier, still waiting, downloads when an
  opted in source sees it too
"""

from channel.models import Channel
from download.models import Download, DownloadStatus
from jobs.src.engine import active_jobs
from playlist.models import Playlist

MODELS = {"channel": Channel, "playlist": Playlist}


def is_auto_download(kind: str, target_id: str) -> bool:
    """the channel or playlist is opted in to auto download"""
    return MODELS[kind].objects.filter(pk=target_id, auto_download=True).exists()


def set_auto_download(kind: str, ids: list[str], value: bool) -> list[str]:
    """switch auto download of channels or playlists, the ids changed"""
    model = MODELS[kind]
    found = list(model.objects.filter(pk__in=ids).values_list("pk", flat=True))
    model.objects.filter(pk__in=found).update(auto_download=value)
    return found


def waiting_from_other_source(
    ids: list[str], kind: str, target_id: str
) -> list[Download]:
    """
    waiting found videos of ids that a different source found (another
    channel or playlist), without an active download job; what this
    source found before it was opted in stays waiting
    """
    items = Download.objects.filter(
        youtube_id__in=ids,
        status=DownloadStatus.PENDING,
        message__isnull=True,
    )
    if kind == "playlist":
        items = items.exclude(found_playlist_id=target_id)
    else:
        items = items.exclude(
            found_playlist_id__isnull=True, channel_id=target_id
        )
    busy = active_jobs("download_video", ids)
    return [item for item in items.order_by("timestamp") if item.pk not in busy]

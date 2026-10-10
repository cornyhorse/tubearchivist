"""
Functionality:
- build the Emby tree for channels and playlists from the database
- persist episode numbers so frozen schemes never renumber
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from channel.models import Channel
from django.db import transaction
from emby.models import EpisodeNumber, SeriesKind
from emby.src.layout import EpisodeSource, SeriesSource, plan_series, series_dir
from emby.src.nfo import EpisodeInfo, SeriesInfo
from emby.src.numbering import FROZEN_SCHEMES, NumberingItem, assign_numbers
from emby.src.writer import EmbyWriter, SyncReport
from playlist.models import Playlist
from video.models import Video


@dataclass(frozen=True)
class EmbyPaths:
    """where to read from and write to"""

    media_root: Path
    cache_root: Path
    output_root: Path
    timezone: str = "UTC"

    def video_thumb(self, youtube_id: str) -> Path:
        """TA cache path of a video thumbnail"""
        return (
            self.cache_root / "videos" / youtube_id[0].lower() / f"{youtube_id}.jpg"
        )

    def channel_art(self, channel_id: str, kind: str) -> Path:
        """TA cache path of channel art: thumb, banner, tvart"""
        return self.cache_root / "channels" / f"{channel_id}_{kind}.jpg"

    def playlist_thumb(self, playlist_id: str) -> Path:
        """TA cache path of a playlist thumbnail"""
        return self.cache_root / "playlists" / f"{playlist_id}.jpg"


def paths_from_env() -> EmbyPaths | None:
    """paths from the environment, None if the Emby writer is disabled"""
    # pylint: disable=import-outside-toplevel
    from common.src.env_settings import EnvironmentSettings

    if not EnvironmentSettings.EMBY_ROOT:
        return None
    return EmbyPaths(
        media_root=Path(EnvironmentSettings.MEDIA_DIR),
        cache_root=Path(EnvironmentSettings.CACHE_DIR),
        output_root=Path(EnvironmentSettings.EMBY_ROOT),
        timezone=EnvironmentSettings.TZ,
    )


def _local_date(value: datetime | None, tz: ZoneInfo):
    if value is None:
        return None
    return value.astimezone(tz).date()


def _number_date(video: Video, tz: ZoneInfo):
    """date used for numbering: upload date, else download date"""
    return _local_date(video.published or video.date_downloaded, tz)


def ensure_numbers(
    kind: str,
    series_id: str,
    scheme: str,
    items: list[NumberingItem],
) -> tuple[dict[str, tuple[int, int]], list[str]]:
    """
    numbers for items: frozen schemes keep every stored number and store
    the new ones; other schemes are recomputed and replace what is stored
    """
    with transaction.atomic():
        stored = EpisodeNumber.objects.select_for_update().filter(
            series_kind=kind, series_id=series_id, scheme=scheme
        )
        if scheme in FROZEN_SCHEMES:
            existing = {n.youtube_id: (n.season, n.episode) for n in stored}
            numbers, warnings = assign_numbers(scheme, items, existing)
            new = {k: v for k, v in numbers.items() if k not in existing}
        else:
            numbers, warnings = assign_numbers(scheme, items)
            stored.delete()
            new = numbers

        EpisodeNumber.objects.bulk_create(
            EpisodeNumber(
                series_kind=kind,
                series_id=series_id,
                scheme=scheme,
                youtube_id=youtube_id,
                season=season,
                episode=episode,
            )
            for youtube_id, (season, episode) in new.items()
        )

    current = {i.youtube_id for i in items}
    return {k: v for k, v in numbers.items() if k in current}, warnings


def _episode(
    video: Video, number: tuple[int, int], paths: EmbyPaths, tz: ZoneInfo
) -> EpisodeSource:
    season, episode = number
    info = EpisodeInfo(
        youtube_id=video.youtube_id,
        title=video.title,
        show_title="",
        season=season,
        episode=episode,
        aired=_local_date(video.published, tz),
        plot=video.description or "",
        runtime_seconds=video.duration,
        studio=video.channel.name,
    )
    return EpisodeSource(
        info=info,
        media=paths.media_root / video.media_url,
        thumb=paths.video_thumb(video.youtube_id),
    )


def channel_series(
    channel: Channel, paths: EmbyPaths
) -> tuple[SeriesSource, list[EpisodeSource], list[str]]:
    """series and numbered episodes of a channel"""
    tz = ZoneInfo(paths.timezone)
    videos = list(
        Video.objects.select_related("channel")
        .filter(channel=channel, active=True)
        .order_by("published", "youtube_id")
    )
    items = [NumberingItem(v.youtube_id, _number_date(v, tz)) for v in videos]
    numbers, warnings = ensure_numbers(
        SeriesKind.CHANNEL, channel.channel_id, channel.emby_numbering, items
    )
    series = SeriesSource(
        kind="channels",
        info=SeriesInfo(
            series_id=channel.channel_id,
            title=channel.name,
            plot=channel.description or "",
            premiered=items[0].published if items else None,
            studio=channel.name,
        ),
        poster=paths.channel_art(channel.channel_id, "thumb"),
        fanart=paths.channel_art(channel.channel_id, "tvart"),
        banner=paths.channel_art(channel.channel_id, "banner"),
    )
    episodes = [
        _episode(v, numbers[v.youtube_id], paths, tz) for v in videos
    ]
    return series, episodes, warnings


def playlist_series(
    playlist: Playlist, paths: EmbyPaths
) -> tuple[SeriesSource, list[EpisodeSource], list[str]]:
    """series and numbered episodes of a playlist, downloaded entries only"""
    tz = ZoneInfo(paths.timezone)
    entries = {
        e.youtube_id: e.idx for e in playlist.entries.all().order_by("idx")
    }
    videos = list(
        Video.objects.select_related("channel").filter(
            youtube_id__in=entries, active=True
        )
    )
    videos.sort(key=lambda v: entries[v.youtube_id])
    items = [
        NumberingItem(v.youtube_id, _number_date(v, tz), entries[v.youtube_id])
        for v in videos
    ]
    numbers, warnings = ensure_numbers(
        SeriesKind.PLAYLIST, playlist.playlist_id, playlist.emby_numbering, items
    )
    series = SeriesSource(
        kind="playlists",
        info=SeriesInfo(
            series_id=playlist.playlist_id,
            title=playlist.name,
            plot=playlist.description or "",
            studio=playlist.channel_name,
        ),
        poster=paths.playlist_thumb(playlist.playlist_id),
    )
    episodes = [
        _episode(v, numbers[v.youtube_id], paths, tz) for v in videos
    ]
    return series, episodes, warnings


def sync_series(
    series: SeriesSource,
    episodes: list[EpisodeSource],
    writer: EmbyWriter,
    warnings: list[str] | None = None,
) -> SyncReport:
    """write one series"""
    desired, plan_warnings = plan_series(series, episodes)
    report = writer.sync(desired, scope=series_dir(series))
    report.warnings[:0] = (warnings or []) + plan_warnings
    return report


def sync_channel(
    channel_id: str, paths: EmbyPaths, dry_run: bool = False
) -> SyncReport:
    """write the series of one channel"""
    channel = Channel.objects.get(channel_id=channel_id)
    series, episodes, warnings = channel_series(channel, paths)
    writer = EmbyWriter(paths.output_root, dry_run=dry_run)
    return sync_series(series, episodes, writer, warnings)


def sync_playlist(
    playlist_id: str, paths: EmbyPaths, dry_run: bool = False
) -> SyncReport:
    """write the series of one playlist"""
    playlist = Playlist.objects.get(playlist_id=playlist_id)
    series, episodes, warnings = playlist_series(playlist, paths)
    writer = EmbyWriter(paths.output_root, dry_run=dry_run)
    return sync_series(series, episodes, writer, warnings)


def sync_all(paths: EmbyPaths, dry_run: bool = False) -> SyncReport:
    """
    write every channel and playlist in one pass over the whole root, so
    series that no longer exist (or were renamed) are cleaned up too
    """
    desired, warnings = [], []
    sources = [
        channel_series(c, paths) for c in Channel.objects.order_by("pk")
    ] + [playlist_series(p, paths) for p in Playlist.objects.order_by("pk")]
    for series, episodes, series_warnings in sources:
        files, plan_warnings = plan_series(series, episodes)
        desired.extend(files)
        warnings.extend(series_warnings + plan_warnings)

    writer = EmbyWriter(paths.output_root, dry_run=dry_run)
    report = writer.sync(desired, scope=None)
    report.warnings[:0] = warnings
    return report

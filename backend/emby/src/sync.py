"""
Functionality:
- build the Emby tree for channels and playlists from the database
- persist episode numbers so frozen schemes never renumber, and the file
  names of a series and its episodes so renamed titles never move files
- a dry run computes everything and stores nothing
- refuse to sync a database numbered by the first layout until
  `manage.py ta_emby_migrate_layout` converted it (see migrate_layout.py)
"""

import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from appsettings.models import ConfigDocument
from channel.models import Channel, EmbyNumbering, EmbyShow
from django.db import transaction
from django.db.models import Q
from emby.models import EpisodeNumber, SeriesKind
from emby.src.layout import (
    EpisodeSource,
    SeriesSource,
    plan_series,
    safe_name,
    series_dir,
)
from emby.src.nfo import EpisodeInfo, SeriesInfo
from emby.src.numbering import FROZEN_SCHEMES, NumberingItem, assign_numbers
from emby.src.writer import EmbyWriter, SyncReport, check_hardlinks
from mediaserver.src import pending
from mediaserver.src import settings as media_settings
from playlist.models import Playlist, PlaylistEntry
from video.models import Video

# Dates (aired, premiered, the season and the MMDD of date numbers) are the
# UTC date of the upload, the same for every viewer and whatever TZ is set
# to; before layout 4 they were dates in TZ, which put an upload between
# 00:00 and 04:59 UTC on the previous day (and a Jan 1 one in the previous
# season)
UTC = ZoneInfo("UTC")

# ConfigDocument key recording which Emby layout the stored numbers use:
# 1 (no document) MMDD*10+n numbers and live names, 2 frozen names,
# MMDD*100+n numbers and frozen playlists, 3 shows are channels only, the
# videos of channels without a show are in one "Other videos" show, 4 dates
# are UTC dates (a step of its own so that it happens whichever of 3 and 4
# a database was migrated to first)
LAYOUT_KEY = "emby_layout"
LAYOUT_VERSION = 4
# the show of videos whose channel has none; "other" can't be a channel id
OTHER_ID = "other"
OTHER_TITLE = "Other videos"
OTHER_PLOT = (
    "Videos from channels that have no show of their own. They are reached "
    "through playlists."
)
# ConfigDocument key of the last sync_all outcomes, read by health.py
STATE_KEY = "emby_sync_state"
_FOLDER = re.compile(r"(channels|playlists)/.* \[([^\]/]+)\]")


class EmbyLayoutOutdated(RuntimeError):
    """the stored numbers need `manage.py ta_emby_migrate_layout` first"""


@dataclass(frozen=True)
class EmbyPaths:
    """where to read from and write to"""

    media_root: Path
    cache_root: Path
    output_root: Path
    # the zone dates were numbered in before layout 4; only the layout
    # migration reads it, files are written with UTC dates
    timezone: str = "UTC"

    def video_thumb(self, youtube_id: str) -> Path:
        """TA cache path of a video thumbnail"""
        return (
            self.cache_root
            / "videos"
            / youtube_id[0].lower()
            / f"{youtube_id}.jpg"
        )

    def channel_art(self, channel_id: str, kind: str) -> Path:
        """TA cache path of channel art: thumb, banner, tvart"""
        return self.cache_root / "channels" / f"{channel_id}_{kind}.jpg"

    def playlist_thumb(self, playlist_id: str) -> Path:
        """TA cache path of a playlist thumbnail"""
        return self.cache_root / "playlists" / f"{playlist_id}.jpg"


def validate_output_root(media_root: Path, output_root: Path) -> None:
    """
    the Emby root must not overlap TA's media, except as a dot folder
    directly in it (TA's scanners skip dot folders, and being on the same
    mount is what lets media be hardlinked)
    """
    media = Path(media_root).resolve()
    output = Path(output_root).resolve()
    _check_same_device(media, output)
    if output == media or output in media.parents:
        raise ValueError(f"Emby root {output} contains the media dir")
    if media in output.parents:
        first = output.relative_to(media).parts[0]
        if not first.startswith("."):
            raise ValueError(
                f"Emby root {output} is inside the media dir; "
                f"use a dot folder such as {media / '.emby'}"
            )


def _check_same_device(media: Path, output: Path) -> None:
    """
    hardlinks can't cross mounts; compares the closest existing parent
    of the output root, so a root that is still to be created is checked
    too (the writer repeats this with a real link before writing)
    """
    existing = next(p for p in (output, *output.parents) if p.exists())
    if media.exists() and media.stat().st_dev != existing.stat().st_dev:
        raise ValueError(
            f"Emby root {output} is on another filesystem than {media}; "
            "media can't be hardlinked"
        )


def paths_from_env() -> EmbyPaths | None:
    """paths from the environment, None if the Emby writer is disabled"""
    # pylint: disable=import-outside-toplevel
    from common.src.env_settings import EnvironmentSettings

    if not EnvironmentSettings.EMBY_ROOT:
        return None
    validate_output_root(
        Path(EnvironmentSettings.MEDIA_DIR),
        Path(EnvironmentSettings.EMBY_ROOT),
    )
    return EmbyPaths(
        media_root=Path(EnvironmentSettings.MEDIA_DIR),
        cache_root=Path(EnvironmentSettings.CACHE_DIR),
        output_root=Path(EnvironmentSettings.EMBY_ROOT),
        timezone=EnvironmentSettings.TZ,
    )


def _writer(paths: EmbyPaths, dry_run: bool) -> EmbyWriter:
    writer = EmbyWriter(
        paths.output_root, dry_run=dry_run, media_root=paths.media_root
    )
    stored = media_settings.load()
    if stored["type"] and stored["notify_enabled"]:
        writer.on_update = _remember_for_refresh(writer)
    return writer


def _remember_for_refresh(writer: EmbyWriter):
    """
    the writer tells the media server's pending set about every NFO it
    rewrites, as it writes it: Emby reads a changed NFO of a known item
    only when told to, and a sync that stops halfway must not forget it
    """

    def remember(item) -> None:
        if item.role == "tvshow":
            pending.add(f"series:{PurePosixPath(item.relpath).parent}")
        elif item.role == "nfo":
            media = writer.manifest.owned_by(item.owner, "media")
            if media:
                pending.add(f"episode:{media}")

    return remember


def preflight(paths: EmbyPaths) -> None:
    """
    check the Emby root can hold hardlinks to the media (EmbyRootError if
    not); every sync does this first, call it before changing anything a
    sync would then have to finish
    """
    check_hardlinks(paths.media_root, paths.output_root)


def layout_version() -> int:
    """layout of the stored numbers and names"""
    document = ConfigDocument.objects.filter(key=LAYOUT_KEY).first()
    return (document.value or {}).get("version", 1) if document else 1


def set_layout_version(version: int = LAYOUT_VERSION) -> None:
    """record the layout of the stored numbers and names"""
    ConfigDocument.objects.update_or_create(
        key=LAYOUT_KEY, defaults={"value": {"version": version}}
    )


def check_layout(dry_run: bool = False) -> None:
    """
    refuse numbers of an older layout; a database without any numbers
    starts on the current layout
    """
    if layout_version() >= LAYOUT_VERSION:
        return
    if EpisodeNumber.objects.exists():
        raise EmbyLayoutOutdated(
            "the Emby numbers use the old layout; run "
            "`manage.py ta_emby_migrate_layout --dry-run`, then without "
            "--dry-run, before syncing (docs/fork/emby-layout-migration.md)"
        )
    if not dry_run:
        set_layout_version()


def _local_date(value: datetime | None, tz: ZoneInfo = UTC):
    if value is None:
        return None
    return value.astimezone(tz).date()


def numbering_date(video: Video, tz: ZoneInfo = UTC):
    """date used for numbering: upload date, else download date"""
    return _local_date(video.published or video.date_downloaded, tz)


@dataclass
class SeriesNumbers:
    """numbers and frozen names of a series"""

    numbers: dict[str, tuple[int, int]]
    names: dict[str, str]
    series_name: str
    warnings: list[str]


# numbers set by hand are stored under this scheme; every scheme keeps them
MANUAL = "manual"


def manual_numbers(kind: str, series_id: str) -> dict[str, tuple[int, int]]:
    """numbers set by hand in a series"""
    rows = EpisodeNumber.objects.filter(
        series_kind=kind, series_id=series_id, scheme=MANUAL
    )
    return {n.youtube_id: (n.season, n.episode) for n in rows}


def _around_manual(
    computed: dict[str, tuple[int, int]],
    manual: dict[str, tuple[int, int]],
    warnings: list[str],
) -> dict[str, tuple[int, int]]:
    """recomputed numbers that collide with a manual one move up"""
    slots = set(manual.values())
    placed = {}
    for youtube_id, (season, episode) in sorted(
        computed.items(), key=lambda i: i[1]
    ):
        if (season, episode) in slots:
            moved = max(e for s, e in slots if s == season) + 1
            warnings.append(
                f"{youtube_id}: E{episode} is set by hand on another video, "
                f"numbered E{moved}"
            )
            episode = moved
        slots.add((season, episode))
        placed[youtube_id] = (season, episode)
    return placed


def _frozen_names(rows: list[EpisodeNumber]) -> tuple[dict[str, str], str]:
    """names already frozen in any scheme of the series, oldest first"""
    names: dict[str, str] = {}
    series_name = ""
    for row in rows:
        if row.name:
            names.setdefault(row.youtube_id, row.name)
        series_name = series_name or row.series_name
    return names, series_name


def ensure_numbers(
    kind: str,
    series: SeriesInfo,
    scheme: str,
    items: list[NumberingItem],
    titles: dict[str, str],
    dry_run: bool = False,
) -> SeriesNumbers:
    """
    numbers for items: frozen schemes keep every stored number and store
    the new ones; other schemes are recomputed and replace what is stored
    names are frozen the first time a number is stored, from titles
    (youtube_id: current title) and the current series title
    a dry run stores nothing
    """
    with transaction.atomic():
        rows = list(
            EpisodeNumber.objects.select_for_update()
            .filter(series_kind=kind, series_id=series.series_id)
            .order_by("pk")
        )
        names, series_name = _frozen_names(rows)
        series_name = series_name or safe_name(series.title, series.series_id)
        for youtube_id, title in titles.items():
            names.setdefault(youtube_id, safe_name(title, youtube_id))

        manual = {
            r.youtube_id: (r.season, r.episode)
            for r in rows
            if r.scheme == MANUAL
        }
        slots = set(manual.values())
        automatic = [i for i in items if i.youtube_id not in manual]
        stored = [row for row in rows if row.scheme == scheme]
        if scheme in FROZEN_SCHEMES:
            # a stored number set aside for a manual one is replaced
            dropped = [
                r
                for r in stored
                if r.youtube_id in manual or (r.season, r.episode) in slots
            ]
            stored = [r for r in stored if r not in dropped]
            existing = {n.youtube_id: (n.season, n.episode) for n in stored}
            numbers, warnings = assign_numbers(
                scheme, automatic, {**existing, **manual}
            )
            new = {
                k: v
                for k, v in numbers.items()
                if k not in existing and k not in manual
            }
        else:
            dropped = []
            computed, warnings = assign_numbers(scheme, automatic)
            new = _around_manual(computed, manual, warnings)
            numbers = {**manual, **new}

        if not dry_run:
            EpisodeNumber.objects.filter(
                pk__in=[r.pk for r in dropped]
            ).delete()
            _store(
                kind, series.series_id, scheme, stored, new, names, series_name
            )

    current = {i.youtube_id for i in items}
    return SeriesNumbers(
        numbers={k: v for k, v in numbers.items() if k in current},
        names=names,
        series_name=series_name,
        warnings=warnings,
    )


def _store(kind, series_id, scheme, stored, new, names, series_name):
    """write new numbers, freezing names on rows that have none yet"""
    if scheme not in FROZEN_SCHEMES:
        EpisodeNumber.objects.filter(pk__in=[r.pk for r in stored]).delete()
        stored = []
    unnamed = []
    for row in stored:
        name = row.name or names.get(row.youtube_id, "")
        if (row.name, row.series_name) != (name, series_name):
            row.name, row.series_name = name, series_name
            unnamed.append(row)
    EpisodeNumber.objects.bulk_update(unnamed, ["name", "series_name"])
    EpisodeNumber.objects.bulk_create(
        EpisodeNumber(
            series_kind=kind,
            series_id=series_id,
            scheme=scheme,
            youtube_id=youtube_id,
            season=season,
            episode=episode,
            name=names.get(youtube_id, ""),
            series_name=series_name,
        )
        for youtube_id, (season, episode) in new.items()
    )


def _media_path(video: Video, paths: EmbyPaths) -> Path | None:
    """media file of a video, None if media_url leaves the media root"""
    root = paths.media_root.resolve()
    media = (root / video.media_url).resolve()
    if root not in media.parents:
        return None
    return media


def _episodes(
    videos: list[Video],
    found: SeriesNumbers,
    paths: EmbyPaths,
    tz: ZoneInfo,
    tags: dict[str, tuple[str, ...]] | None = None,
    name_channel: bool = False,
) -> list[EpisodeSource]:
    """
    episodes of videos; tags are per video (youtube_id: tags), name_channel
    puts the uploader in front of the plot, for a show of many channels
    """
    episodes = []
    for video in videos:
        media = _media_path(video, paths)
        if media is None:
            found.warnings.append(
                f"{video.youtube_id}: media_url outside the media dir, "
                f"skipped: {video.media_url}"
            )
            continue
        season, episode = found.numbers[video.youtube_id]
        info = EpisodeInfo(
            youtube_id=video.youtube_id,
            title=video.title,
            show_title="",
            season=season,
            episode=episode,
            aired=_local_date(video.published, tz),
            plot=_plot(video, name_channel),
            runtime_seconds=video.duration,
            studio=video.channel.name,
            tags=(tags or {}).get(video.youtube_id, ()),
        )
        episodes.append(
            EpisodeSource(
                info=info,
                media=media,
                thumb=paths.video_thumb(video.youtube_id),
                name=found.names.get(video.youtube_id, ""),
            )
        )
    return episodes


def _plot(video: Video, name_channel: bool) -> str:
    plot = video.description or ""
    if name_channel:
        return f"From {video.channel.name}.\n\n{plot}".strip()
    return plot


def has_show(channel: Channel) -> bool:
    """the channel gets its own show (emby_show)"""
    if channel.emby_show == EmbyShow.ALWAYS:
        return True
    return channel.emby_show == EmbyShow.AUTO and channel.subscribed


def _other_filter() -> Q:
    """videos of channels without a show of their own"""
    return ~(
        Q(channel__emby_show=EmbyShow.ALWAYS)
        | Q(channel__emby_show=EmbyShow.AUTO, channel__subscribed=True)
    )


def channel_numbers(
    channel: Channel,
    tz: ZoneInfo,
    scheme: str | None = None,
    dry_run: bool = False,
) -> tuple[list[Video], SeriesInfo, SeriesNumbers]:
    """videos, series info and numbers of a channel, oldest first"""
    videos = list(
        Video.objects.select_related("channel")
        .filter(channel=channel, active=True)
        .order_by("published", "youtube_id")
    )
    items = [
        NumberingItem(v.youtube_id, numbering_date(v, tz)) for v in videos
    ]
    info = SeriesInfo(
        series_id=channel.channel_id,
        title=channel.name,
        plot=channel.description or "",
        premiered=items[0].published if items else None,
        studio=channel.name,
        tags=tuple(channel.emby_tags),
    )
    found = ensure_numbers(
        SeriesKind.CHANNEL,
        info,
        scheme or channel.emby_numbering,
        items,
        {v.youtube_id: v.title for v in videos},
        dry_run,
    )
    return videos, info, found


def channel_series(
    channel: Channel, paths: EmbyPaths, dry_run: bool = False
) -> tuple[SeriesSource, list[EpisodeSource], list[str]]:
    """series and numbered episodes of a channel"""
    tz = UTC
    videos, info, found = channel_numbers(channel, tz, dry_run=dry_run)
    series = SeriesSource(
        kind="channels",
        info=info,
        poster=paths.channel_art(channel.channel_id, "thumb"),
        fanart=paths.channel_art(channel.channel_id, "tvart"),
        banner=paths.channel_art(channel.channel_id, "banner"),
        name=found.series_name,
    )
    episodes = _episodes(
        videos, found, paths, tz, tags=extra_tags(videos, info.tags)
    )
    return series, episodes, found.warnings


def extra_tags(
    videos: list[Video], series_tags: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    """
    the tags of each video's playlists (and channel) that the show's own
    tag doesn't give: whatever a tagged playlist holds is approved for the
    tag's users, in an untagged channel show too. An episode tag is
    enough for Emby, and only the differing ones keep the NFOs small.
    """
    inherited = {t.casefold() for t in series_tags}
    extra = {}
    for youtube_id, tags in video_tags(videos).items():
        rest = tuple(t for t in tags if t.casefold() not in inherited)
        if rest:
            extra[youtube_id] = rest
    return extra


def playlist_numbers(
    playlist: Playlist,
    tz: ZoneInfo,
    scheme: str | None = None,
    dry_run: bool = False,
) -> tuple[list[Video], SeriesInfo, SeriesNumbers]:
    """videos, series info and numbers of a playlist, in playlist order"""
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
        NumberingItem(
            v.youtube_id, numbering_date(v, tz), entries[v.youtube_id]
        )
        for v in videos
    ]
    info = SeriesInfo(
        series_id=playlist.playlist_id,
        title=playlist.name,
        plot=playlist.description or "",
        studio=playlist.channel_name,
        tags=tuple(playlist.emby_tags),
    )
    found = ensure_numbers(
        SeriesKind.PLAYLIST,
        info,
        scheme or playlist.emby_numbering,
        items,
        {v.youtube_id: v.title for v in videos},
        dry_run,
    )
    return videos, info, found


def video_tags(videos: list[Video]) -> dict[str, tuple[str, ...]]:
    """
    access tags per video for episodes outside any tagged show: those of
    its channel and of every playlist it is in
    """
    tags: dict[str, set[str]] = {
        v.youtube_id: set(v.channel.emby_tags) for v in videos
    }
    rows = PlaylistEntry.objects.filter(
        youtube_id__in=tags, playlist__emby_tags__len__gt=0
    ).values_list("youtube_id", "playlist__emby_tags")
    for youtube_id, playlist_tags in rows:
        tags[youtube_id].update(playlist_tags)
    return {k: tuple(sorted(v)) for k, v in tags.items() if v}


def other_series(
    paths: EmbyPaths, dry_run: bool = False
) -> tuple[SeriesSource, list[EpisodeSource], list[str]]:
    """
    the "Other videos" show: every video of a channel without a show, by
    upload year; the show itself carries no tag (so a tag restricted user
    never sees it), its episodes carry the tags of their playlists
    """
    tz = UTC
    videos = list(
        Video.objects.select_related("channel")
        .filter(_other_filter(), active=True)
        .order_by("published", "youtube_id")
    )
    items = [
        NumberingItem(v.youtube_id, numbering_date(v, tz)) for v in videos
    ]
    info = SeriesInfo(
        series_id=OTHER_ID,
        title=OTHER_TITLE,
        plot=OTHER_PLOT,
        premiered=items[0].published if items else None,
    )
    found = ensure_numbers(
        SeriesKind.CHANNEL,
        info,
        EmbyNumbering.YEAR_FROZEN,
        items,
        {v.youtube_id: v.title for v in videos},
        dry_run,
    )
    series = SeriesSource(kind="channels", info=info, name=found.series_name)
    episodes = _episodes(
        videos, found, paths, tz, tags=video_tags(videos), name_channel=True
    )
    return series, episodes, found.warnings


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
    check_layout(dry_run)
    channel = Channel.objects.get(channel_id=channel_id)
    if not has_show(channel):
        raise ValueError(
            f"{channel_id} has no show of its own (emby_show), "
            "its videos are in Other videos"
        )
    series, episodes, warnings = channel_series(channel, paths, dry_run)
    writer = _writer(paths, dry_run)
    return sync_series(series, episodes, writer, warnings)


def _other_source(
    _model, paths: EmbyPaths, dry_run: bool
) -> tuple[SeriesSource, list[EpisodeSource], list[str]]:
    """other_series with the signature of the per series builders"""
    return other_series(paths, dry_run)


def sync_other(paths: EmbyPaths, dry_run: bool = False) -> SyncReport:
    """write the "Other videos" show"""
    check_layout(dry_run)
    series, episodes, warnings = other_series(paths, dry_run)
    writer = _writer(paths, dry_run)
    return sync_series(series, episodes, writer, warnings)


def _folders_of(writer: EmbyWriter, kind: str, series_id: str) -> set[str]:
    """series folders the manifest knows for a series, whatever the name"""
    return {
        d
        for d in writer.manifest.dirs
        if d.startswith(f"{kind}/")
        and d.count("/") == 1
        and d.endswith(f" [{series_id}]")
    }


def sync_all(paths: EmbyPaths, dry_run: bool = False) -> SyncReport:
    """
    write every channel and playlist in one pass over the whole root, so
    series that no longer exist (or were renamed) are cleaned up too
    a series that can't be planned is reported in errors and left as it
    is on disk, the others still sync
    a real run records its outcome for the health check (health.py)
    """
    try:
        report = _sync_all(paths, dry_run)
    except Exception as err:
        if not dry_run:
            record_run(error=f"{type(err).__name__}: {err}")
        raise
    if not dry_run:
        record_run(report=report)
    return report


def _sync_all(paths: EmbyPaths, dry_run: bool) -> SyncReport:
    check_layout(dry_run)
    writer = _writer(paths, dry_run)
    desired, warnings, errors, keep = [], [], [], set()
    failed = {}
    sources = [
        ("channels", c.channel_id, channel_series, c)
        for c in Channel.objects.order_by("pk")
        if has_show(c)
    ] + [("channels", OTHER_ID, _other_source, None)]
    for kind, series_id, build, model in sources:
        try:
            series, episodes, series_warnings = build(model, paths, dry_run)
            files, plan_warnings = plan_series(series, episodes)
        except (ValueError, OSError) as err:
            errors.append(f"{kind}/{series_id}: {err}")
            failed[f"{kind}/{series_id}"] = str(err)
            keep |= _folders_of(writer, kind, series_id)
            continue
        desired.extend(files)
        warnings.extend(series_warnings + plan_warnings)

    report = writer.sync(desired, scope=None, keep=keep)
    report.warnings[:0] = warnings
    report.errors[:0] = errors
    report.failed = failed | {
        series_key(folder): error for folder, error in report.failed.items()
    }
    return report


def series_key(folder: str) -> str:
    """<kind>/<series_id> of a series folder <kind>/<Name> [<series_id>]"""
    match = _FOLDER.fullmatch(folder)
    return f"{match[1]}/{match[2]}" if match else folder


def sync_state() -> dict:
    """outcome of the last real sync_all runs"""
    document = ConfigDocument.objects.filter(key=STATE_KEY).first()
    return dict(document.value) if document else {}


def record_run(
    report: SyncReport | None = None, error: str | None = None
) -> None:
    """
    remember when sync_all last ran and succeeded, and for how many runs
    in a row each failing series has failed
    """
    now = datetime.now(dt_timezone.utc).isoformat()
    state = sync_state()
    state["last_run"] = now
    state["last_error"] = error
    if report is not None:
        state["last_success"] = now
        previous = state.get("failing", {})
        state["failing"] = {
            key: {
                "runs": previous.get(key, {}).get("runs", 0) + 1,
                "error": message,
                "since": previous.get(key, {}).get("since", now),
            }
            for key, message in report.failed.items()
        }
    ConfigDocument.objects.update_or_create(
        key=STATE_KEY, defaults={"value": state}
    )

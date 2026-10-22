"""
Functionality:
- convert the stored Emby numbers and names to the current layout in one
  transaction, so Emby sees exactly one change pass
  (docs/fork/emby-layout-migration.md); layout 2 to 3 (playlists are no
  longer shows, channels without a show move to "Other videos") changes
  no stored number, the following sync does it
- layout 1 to 2:
  - date scheme: MMDD*10+n becomes MMDD*100+n; numbers that spilled into
    the next day's block (an 11th upload on one day) move back to their
    own day
  - playlists on playlist_position switch to playlist_frozen, keeping
    their current numbers
  - file names are frozen from the current titles
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q

from channel.models import Channel, EmbyNumbering
from emby.models import EpisodeNumber, SeriesKind
from emby.src.layout import safe_name
from emby.src.numbering import DATE_SLOTS_PER_DAY, date_block
from emby.src.sync import (
    LAYOUT_VERSION,
    layout_version,
    numbering_date,
    set_layout_version,
)
from playlist.models import Playlist
from video.models import Video

OLD_DATE_SLOTS = 10


class AlreadyMigrated(RuntimeError):
    """the database already uses the current layout"""


@dataclass
class MigrationSummary:
    """what the migration changed in the database"""

    date_numbers: int = 0
    moved_to_own_day: list[str] = field(default_factory=list)
    playlists_frozen: list[str] = field(default_factory=list)
    playlists_kept: list[str] = field(default_factory=list)
    names_frozen: int = 0


def convert_date_number(episode: int) -> int:
    """MMDD*10+n -> MMDD*100+n"""
    day, slot = divmod(episode, OLD_DATE_SLOTS)
    return day * DATE_SLOTS_PER_DAY + slot


def _own_day(row: EpisodeNumber, day: date | None) -> bool:
    """converted number lies in the block of the video's day"""
    if day is None or day.year != row.season:
        # no date, or a season from another timezone: keep it
        return True
    block = date_block(day)
    return block <= row.episode < block + DATE_SLOTS_PER_DAY


def place_spilled(rows, days, summary: MigrationSummary) -> list:
    """converted rows of one series: move spilled numbers to their day"""
    taken = {(r.season, r.episode) for r in rows}
    for row in sorted(rows, key=lambda r: r.episode):
        day = days.get(row.youtube_id)
        if _own_day(row, day):
            continue
        taken.discard((row.season, row.episode))
        block = date_block(day)
        free = next(
            (
                block + n
                for n in range(DATE_SLOTS_PER_DAY)
                if (row.season, block + n) not in taken
            ),
            None,
        )
        if free is None:
            # the day is full, the spilled number stays where it is
            taken.add((row.season, row.episode))
            continue
        row.episode = free
        taken.add((row.season, free))
        summary.moved_to_own_day.append(f"{row.series_id}/{row.youtube_id}")
    return rows


def _migrate_dates(tz: ZoneInfo, summary: MigrationSummary) -> None:
    rows = list(EpisodeNumber.objects.filter(scheme=EmbyNumbering.DATE))
    if not rows:
        return
    videos = Video.objects.filter(youtube_id__in={r.youtube_id for r in rows})
    days = {v.youtube_id: numbering_date(v, tz) for v in videos}
    by_series = defaultdict(list)
    for row in rows:
        by_series[(row.series_kind, row.series_id)].append(row)

    for row in rows:
        row.episode = convert_date_number(row.episode)
    targets = {}
    for series_rows in by_series.values():
        for row in place_spilled(series_rows, days, summary):
            targets[row.pk] = row.episode
    # two passes: an in-place update could otherwise pass through a
    # number another row still holds; -pk is unique and never a real one
    for row in rows:
        row.episode = -row.pk
    EpisodeNumber.objects.bulk_update(rows, ["episode"])
    for row in rows:
        row.episode = targets[row.pk]
    EpisodeNumber.objects.bulk_update(rows, ["episode"])
    summary.date_numbers = len(rows)


def _freeze_playlists(keep: set[str], summary: MigrationSummary) -> None:
    """playlist_position -> playlist_frozen, keeping the current numbers"""
    position = Playlist.objects.filter(
        emby_numbering=EmbyNumbering.PLAYLIST_POSITION
    ).order_by("pk")
    for playlist in position:
        if playlist.playlist_id in keep:
            summary.playlists_kept.append(playlist.playlist_id)
            continue
        current = EpisodeNumber.objects.filter(
            series_kind=SeriesKind.PLAYLIST,
            series_id=playlist.playlist_id,
            scheme=EmbyNumbering.PLAYLIST_POSITION,
        )
        EpisodeNumber.objects.filter(
            series_kind=SeriesKind.PLAYLIST,
            series_id=playlist.playlist_id,
            scheme=EmbyNumbering.PLAYLIST_FROZEN,
        ).delete()
        EpisodeNumber.objects.bulk_create(
            EpisodeNumber(
                series_kind=row.series_kind,
                series_id=row.series_id,
                scheme=EmbyNumbering.PLAYLIST_FROZEN,
                youtube_id=row.youtube_id,
                season=row.season,
                episode=row.episode,
            )
            for row in current
        )
        playlist.emby_numbering = EmbyNumbering.PLAYLIST_FROZEN
        playlist.save(update_fields=["emby_numbering"])
        summary.playlists_frozen.append(playlist.playlist_id)


def _series_titles() -> dict[tuple[str, str], str]:
    titles = {
        (SeriesKind.CHANNEL.value, c.channel_id): c.name
        for c in Channel.objects.only("channel_id", "name")
    }
    titles.update(
        {
            (SeriesKind.PLAYLIST.value, p.playlist_id): p.name
            for p in Playlist.objects.only("playlist_id", "name")
        }
    )
    return titles


def _freeze_names(summary: MigrationSummary) -> None:
    """name every row after the current titles, as the files are now"""
    rows = list(EpisodeNumber.objects.filter(Q(name="") | Q(series_name="")))
    titles = dict(
        Video.objects.filter(
            youtube_id__in={r.youtube_id for r in rows}
        ).values_list("youtube_id", "title")
    )
    series = _series_titles()
    for row in rows:
        if not row.name and row.youtube_id in titles:
            row.name = safe_name(titles[row.youtube_id], row.youtube_id)
        key = (row.series_kind, row.series_id)
        if not row.series_name and key in series:
            row.series_name = safe_name(series[key], row.series_id)
    EpisodeNumber.objects.bulk_update(rows, ["name", "series_name"])
    summary.names_frozen = len(rows)


def migrate_layout(
    timezone: str, keep_position: set[str] | None = None
) -> MigrationSummary:
    """
    convert the stored numbers and names to the current layout; the
    caller syncs afterwards (in the same transaction for a dry run)
    """
    start = layout_version()
    if start >= LAYOUT_VERSION:
        raise AlreadyMigrated(f"already on Emby layout {LAYOUT_VERSION}")
    summary = MigrationSummary()
    with transaction.atomic():
        if start < 2:
            _migrate_dates(ZoneInfo(timezone), summary)
            _freeze_playlists(set(keep_position or ()), summary)
            _freeze_names(summary)
        # 2 -> 3 changes no stored number: the shows are chosen when the
        # tree is built, and the writer removes what is no longer wanted
        set_layout_version()
    return summary

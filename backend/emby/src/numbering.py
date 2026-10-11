"""
Functionality:
- assign Emby season/episode numbers to the videos of a series
- pure functions: existing assignments in, complete assignments out
- frozen schemes never change a number once assigned, so the number of a
  video never depends on videos added later (backfill safe)
"""

from dataclasses import dataclass
from datetime import date

DATE_SLOTS_PER_DAY = 100


@dataclass(frozen=True)
class NumberingItem:
    """what numbering needs to know about a video"""

    youtube_id: str
    published: date
    # position in the source playlist, playlist schemes only
    playlist_idx: int | None = None


Assignment = tuple[int, int]  # (season, episode)


class NumberingError(ValueError):
    """the scheme can't number this series"""


def _ordered(items: list[NumberingItem]) -> list[NumberingItem]:
    """first assignment order: oldest first, ties by id"""
    return sorted(items, key=lambda i: (i.published, i.youtube_id))


def _taken(existing: dict[str, Assignment]) -> set[Assignment]:
    taken = set(existing.values())
    if len(taken) != len(existing):
        raise NumberingError("existing assignments contain duplicates")
    return taken


def date_block(day: date) -> int:
    """first episode number of a day in the date scheme"""
    return (day.month * 100 + day.day) * DATE_SLOTS_PER_DAY


def number_date(
    items: list[NumberingItem], existing: dict[str, Assignment]
) -> tuple[dict[str, Assignment], list[str]]:
    """
    season = upload year, episode = MMDD * 100 + n
    n is the next free slot of that day; a 101st upload on one day takes the
    next free number after the day's block, which is reported as a warning
    """
    result = dict(existing)
    taken = _taken(existing)
    warnings = []
    for item in _ordered(items):
        if item.youtube_id in result:
            continue
        season = item.published.year
        base = date_block(item.published)
        episode = next(
            (
                base + n
                for n in range(DATE_SLOTS_PER_DAY)
                if (season, base + n) not in taken
            ),
            None,
        )
        if episode is None:
            episode = base + DATE_SLOTS_PER_DAY
            while (season, episode) in taken:
                episode += 1
            warnings.append(
                f"{item.youtube_id}: more than {DATE_SLOTS_PER_DAY} uploads "
                f"on {item.published}, numbered E{episode}"
            )
        result[item.youtube_id] = (season, episode)
        taken.add((season, episode))

    return result, warnings


def number_year_frozen(
    items: list[NumberingItem], existing: dict[str, Assignment]
) -> tuple[dict[str, Assignment], list[str]]:
    """season = upload year, episode = next number in that year"""
    result = dict(existing)
    _taken(existing)
    highest: dict[int, int] = {}
    for season, episode in existing.values():
        highest[season] = max(highest.get(season, 0), episode)

    for item in _ordered(items):
        if item.youtube_id in result:
            continue
        season = item.published.year
        highest[season] = highest.get(season, 0) + 1
        result[item.youtube_id] = (season, highest[season])

    return result, []


def number_absolute_frozen(
    items: list[NumberingItem], existing: dict[str, Assignment]
) -> tuple[dict[str, Assignment], list[str]]:
    """season 1, episode = next number across all years"""
    result = dict(existing)
    _taken(existing)
    highest = max((e for s, e in existing.values() if s == 1), default=0)
    for item in _ordered(items):
        if item.youtube_id in result:
            continue
        highest += 1
        result[item.youtube_id] = (1, highest)

    return result, []


def number_playlist_frozen(
    items: list[NumberingItem], existing: dict[str, Assignment]
) -> tuple[dict[str, Assignment], list[str]]:
    """
    season 1, episode = next number in playlist order: the first sync
    numbers the playlist as it is, later additions are appended, and a
    reorder never renumbers
    """
    result = dict(existing)
    _taken(existing)
    highest = max((e for s, e in existing.values() if s == 1), default=0)
    for item in sorted(items, key=_playlist_order):
        if item.youtube_id in result:
            continue
        highest += 1
        result[item.youtube_id] = (1, highest)

    return result, []


def _playlist_order(item: NumberingItem) -> tuple[int, str]:
    if item.playlist_idx is None:
        raise NumberingError(f"{item.youtube_id}: no playlist position")
    return item.playlist_idx, item.youtube_id


def number_playlist_position(
    items: list[NumberingItem], existing: dict[str, Assignment]
) -> tuple[dict[str, Assignment], list[str]]:
    """
    season 1, episode = playlist index + 1
    follows the playlist, so numbers change when the playlist is reordered;
    existing assignments are ignored on purpose
    """
    del existing
    result = {}
    for item in items:
        if item.playlist_idx is None:
            raise NumberingError(f"{item.youtube_id}: no playlist position")
        result[item.youtube_id] = (1, item.playlist_idx + 1)

    if len(set(result.values())) != len(result):
        raise NumberingError("duplicate playlist positions")

    return result, []


SCHEMES = {
    "date": number_date,
    "year_frozen": number_year_frozen,
    "absolute_frozen": number_absolute_frozen,
    "playlist_frozen": number_playlist_frozen,
    "playlist_position": number_playlist_position,
}
FROZEN_SCHEMES = {
    "date",
    "year_frozen",
    "absolute_frozen",
    "playlist_frozen",
}


def assign_numbers(
    scheme: str,
    items: list[NumberingItem],
    existing: dict[str, Assignment] | None = None,
) -> tuple[dict[str, Assignment], list[str]]:
    """
    complete assignments for items under scheme
    returns (assignments, warnings); assignments keep every existing entry,
    including videos no longer in items, so numbers are never reused
    """
    if scheme not in SCHEMES:
        raise NumberingError(f"unknown numbering scheme: {scheme}")

    ids = [i.youtube_id for i in items]
    if len(set(ids)) != len(ids):
        raise NumberingError("duplicate youtube_id in series")

    return SCHEMES[scheme](items, dict(existing or {}))

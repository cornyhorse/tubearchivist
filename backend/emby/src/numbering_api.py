"""
Functionality:
- numbering schemes in plain words
- what a series looks like in Emby, and what a scheme change renumbers
- season and episode set by hand per video
- shared by the REST views and the MCP server
"""

from zoneinfo import ZoneInfo

from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from django.db import transaction
from emby.models import EpisodeNumber
from emby.src.numbering import FROZEN_SCHEMES
from emby.src.sync import (
    MANUAL,
    channel_numbers,
    manual_numbers,
    paths_from_env,
    playlist_numbers,
)
from jobs.models import CreatedBy, Job, Priority
from jobs.src.engine import enqueue
from playlist.models import Playlist, PlaylistEntry
from video.models import Video

SCHEMES = [
    {
        "key": "date",
        "label": "By date",
        "description": (
            "Season is the upload year, episode is month, day and a counter."
        ),
        "example": "S2025E6200",
    },
    {
        "key": "year_frozen",
        "label": "By year, in order",
        "description": (
            "Season is the upload year, episodes count up in upload order."
        ),
        "example": "S2025E14",
    },
    {
        "key": "absolute_frozen",
        "label": "One season, in order",
        "description": "Season 1, episodes count up in upload order.",
        "example": "S01E347",
    },
    {
        "key": "playlist_frozen",
        "label": "Playlist order, kept",
        "description": (
            "Season 1, numbered in playlist order the first time; later "
            "additions are appended and a reorder never renumbers."
        ),
        "example": "S01E05",
    },
    {
        "key": "playlist_position",
        "label": "Playlist order",
        "description": (
            "Season 1, episode is the position in the playlist; changes "
            "when the playlist is reordered."
        ),
        "example": "S01E05",
    },
]
MODELS = {"channel": Channel, "playlist": Playlist}
SAMPLE = 20


class NumberConflict(ValueError):
    """another video already has this season and episode"""


def schemes() -> list[dict]:
    """every scheme"""
    return [
        {
            **scheme,
            "frozen": scheme["key"] in FROZEN_SCHEMES,
            "playlist_only": scheme["key"] == "playlist_position",
        }
        for scheme in SCHEMES
    ]


def episode_name(season: int, episode: int, title: str) -> str:
    """how Emby shows it: S2025E6200 - Title"""
    return f"S{season:02d}E{episode:02d} - {title}"


def _series(kind: str, series_id: str):
    """the channel or playlist; DoesNotExist, KeyError for a bad kind"""
    return MODELS[kind].objects.get(pk=series_id)


def _numbers(kind: str, series, scheme: str | None = None):
    """(videos, numbers, names) of a series, as a dry run"""
    tz = ZoneInfo(EnvironmentSettings.TZ)
    build = channel_numbers if kind == "channel" else playlist_numbers
    videos, _, found = build(series, tz, scheme=scheme, dry_run=True)
    return videos, found.numbers, found.names


def _episode(video: Video, number, manual: set, names: dict) -> dict:
    season, episode = number
    name = names.get(video.youtube_id) or video.title
    return {
        "youtube_id": video.youtube_id,
        "title": video.title,
        "published": video.published,
        "season": season,
        "episode": episode,
        "name": episode_name(season, episode, name),
        "manual": video.youtube_id in manual,
    }


def series_numbering(kind: str, series_id: str) -> dict:
    """scheme and the newest episodes as Emby will name them"""
    series = _series(kind, series_id)
    videos, numbers, names = _numbers(kind, series)
    manual = set(manual_numbers(kind, series_id))
    newest = sorted(
        videos, key=lambda v: (v.published is not None, v.published)
    )
    return {
        "kind": kind,
        "id": series_id,
        "title": series.name,
        "scheme": series.emby_numbering,
        "episodes": len(videos),
        "manual": len(manual & set(numbers)),
        "sample": [
            _episode(v, numbers[v.youtube_id], manual, names)
            for v in reversed(newest[-SAMPLE:])
        ],
    }


def _check_scheme(kind: str, scheme: str) -> None:
    if scheme.startswith("playlist_") and kind != "playlist":
        raise ValueError(f"{scheme} only numbers playlists")


def preview_scheme(kind: str, series_id: str, scheme: str) -> dict:
    """what switching to scheme renumbers; stores nothing"""
    _check_scheme(kind, scheme)
    series = _series(kind, series_id)
    videos, old, names = _numbers(kind, series)
    _, new, _ = _numbers(kind, series, scheme)
    manual = set(manual_numbers(kind, series_id))
    changed = [v for v in videos if old[v.youtube_id] != new[v.youtube_id]]
    sample = []
    for video in reversed(changed[-SAMPLE:]):
        season, episode = old[video.youtube_id]
        sample.append(
            {
                **_episode(video, new[video.youtube_id], manual, names),
                "old_season": season,
                "old_episode": episode,
                "old_name": episode_name(
                    season, episode, names.get(video.youtube_id) or video.title
                ),
            }
        )
    return {
        "scheme": scheme,
        "episodes": len(videos),
        "renumbers": len(changed),
        "sample": sample,
    }


def _queue_emby_write(created_by: str) -> Job | None:
    if not paths_from_env():
        return None
    job, _ = enqueue(
        "emby_write", priority=Priority.HIGH, created_by=created_by
    )
    return job


def set_scheme(
    kind: str,
    series_id: str,
    scheme: str,
    created_by: str = CreatedBy.USER,
) -> dict:
    """change the scheme and queue the Emby write"""
    renumbers = preview_scheme(kind, series_id, scheme)["renumbers"]
    MODELS[kind].objects.filter(pk=series_id).update(emby_numbering=scheme)
    job = _queue_emby_write(created_by)
    return {
        "scheme": scheme,
        "renumbered": renumbers,
        "job_id": job.pk if job else None,
    }


def _series_of(youtube_id: str) -> list[tuple[str, object]]:
    """[(kind, series)] a downloaded video belongs to"""
    video = Video.objects.select_related("channel").get(pk=youtube_id)
    playlist_ids = PlaylistEntry.objects.filter(youtube_id=youtube_id).values(
        "playlist_id"
    )
    playlists = Playlist.objects.filter(pk__in=playlist_ids).order_by("name")
    return [("channel", video.channel)] + [("playlist", p) for p in playlists]


def video_numbers(youtube_id: str) -> list[dict]:
    """the video's number in every series; Video.DoesNotExist"""
    result = []
    for kind, series in _series_of(youtube_id):
        series_id = series.pk
        videos, numbers, names = _numbers(kind, series)
        video = next((v for v in videos if v.youtube_id == youtube_id), None)
        if not video:
            continue
        manual = set(manual_numbers(kind, series_id))
        episode = _episode(video, numbers[youtube_id], manual, names)
        result.append(
            {
                "series_kind": kind,
                "series_id": series_id,
                "series_title": series.name,
                "scheme": series.emby_numbering,
                "season": episode["season"],
                "episode": episode["episode"],
                "name": episode["name"],
                "manual": episode["manual"],
            }
        )
    return result


def set_manual(
    youtube_id: str,
    kind: str,
    series_id: str,
    season: int,
    episode: int,
    created_by: str = CreatedBy.USER,
) -> dict:
    """
    season and episode by hand; LookupError if the video isn't in the
    series, NumberConflict if another video has the slot
    """
    series = _series(kind, series_id)
    videos, numbers, _ = _numbers(kind, series)
    if youtube_id not in {v.youtube_id for v in videos}:
        raise LookupError(f"{youtube_id} is not in this {kind}")
    taken_by = next(
        (
            other
            for other, number in numbers.items()
            if number == (season, episode) and other != youtube_id
        ),
        None,
    )
    if taken_by:
        raise NumberConflict(
            f"S{season:02d}E{episode:02d} belongs to {taken_by}"
        )
    with transaction.atomic():
        EpisodeNumber.objects.update_or_create(
            series_kind=kind,
            series_id=series_id,
            scheme=MANUAL,
            youtube_id=youtube_id,
            defaults={"season": season, "episode": episode},
        )
    _queue_emby_write(created_by)
    return next(
        i
        for i in video_numbers(youtube_id)
        if (i["series_kind"], i["series_id"]) == (kind, series_id)
    )


def delete_manual(youtube_id: str, kind: str, series_id: str) -> bool:
    """back to automatic numbering; False if it wasn't set by hand"""
    deleted, _ = EpisodeNumber.objects.filter(
        series_kind=kind,
        series_id=series_id,
        scheme=MANUAL,
        youtube_id=youtube_id,
    ).delete()
    if deleted:
        _queue_emby_write(CreatedBy.USER)
    return bool(deleted)

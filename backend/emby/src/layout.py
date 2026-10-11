"""
Functionality:
- turn a series (channel or playlist) and its numbered episodes into the
  list of files the Emby tree should contain
- naming: <kind>/<Name> [<id>]/Season <s>/<Name> - S<s>E<e> - <Title>.ext
  the SxxEyy in the filename always equals the NFO, so Emby's filename
  parser and the NFO agree and two videos can never share a number
- <Name> and <Title> are frozen names (stored with the episode number),
  so renaming a channel or video changes the NFO title, never a path;
  without a frozen name the current title is used
- an episode file name stays under MAX_BASENAME_BYTES, well inside the
  255 byte limit of a name even with "-thumb.jpg" appended
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from emby.src.nfo import EpisodeInfo, SeriesInfo, render_episode, render_tvshow
from emby.src.writer import DesiredFile

# characters Windows/SMB clients and Emby can't handle in names
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')
MAX_TITLE_BYTES = 120
# episode file name without extension; the show part gets at most
# MAX_SHOW_BYTES of it, the title gets the rest
MAX_BASENAME_BYTES = 200
MAX_SHOW_BYTES = 80


@dataclass(frozen=True)
class EpisodeSource:
    """an episode plus where its files come from"""

    info: EpisodeInfo
    media: Path
    thumb: Path | None = None
    # frozen title part of the file name, "" for the current title
    name: str = ""


@dataclass(frozen=True)
class SeriesSource:
    """a series plus where its artwork comes from"""

    kind: str  # "channels" or "playlists"
    info: SeriesInfo
    poster: Path | None = None
    fanart: Path | None = None
    banner: Path | None = None
    # frozen folder name, "" for the current title
    name: str = ""


def _truncate(value: str, max_bytes: int) -> str:
    """at most max_bytes of utf-8, never splitting a character"""
    encoded = value.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore").strip(" .")


def safe_name(
    value: str, fallback: str, max_bytes: int = MAX_TITLE_BYTES
) -> str:
    """filesystem safe, single spaced, trimmed to max_bytes"""
    value = _UNSAFE.sub(" ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return _truncate(value, max_bytes) or fallback


def series_name(series: SeriesSource) -> str:
    """frozen folder name, else the current title"""
    return series.name or safe_name(series.info.title, series.info.series_id)


def series_dir(series: SeriesSource) -> str:
    """relpath of the series folder, stable across renames of the title"""
    return f"{series.kind}/{series_name(series)} [{series.info.series_id}]"


def episode_basename(
    series: SeriesSource, info: EpisodeInfo, name: str = ""
) -> str:
    """file name without extension, at most MAX_BASENAME_BYTES"""
    show = safe_name(
        series_name(series), series.info.series_id, MAX_SHOW_BYTES
    )
    prefix = f"{show} - S{info.season:02d}E{info.episode:02d} - "
    room = MAX_BASENAME_BYTES - len(prefix.encode("utf-8"))
    title = safe_name(name or info.title, info.youtube_id, room)
    return prefix + title


def _read(path: Path | None) -> bytes | None:
    if path and path.is_file():
        return path.read_bytes()
    return None


def plan_series(
    series: SeriesSource, episodes: list[EpisodeSource]
) -> tuple[list[DesiredFile], list[str]]:
    """desired files for one series, plus warnings; nothing if no episodes"""
    warnings = []
    available = []
    for episode in episodes:
        if episode.media.is_file():
            available.append(episode)
        else:
            youtube_id = episode.info.youtube_id
            warnings.append(f"{youtube_id}: missing media {episode.media}")
    if not available:
        # a series without any media would show up empty in Emby
        return [], warnings
    base = series_dir(series)
    # owners are qualified by series: one video can be in several series
    sid = f"{series.kind}/{series.info.series_id}"
    desired = [
        DesiredFile(
            f"{base}/tvshow.nfo",
            "data",
            sid,
            "tvshow",
            data=render_tvshow(series.info),
        )
    ]
    for role, path in (
        ("poster", series.poster),
        ("fanart", series.fanart),
        ("banner", series.banner),
    ):
        if data := _read(path):
            desired.append(
                DesiredFile(f"{base}/{role}.jpg", "data", sid, role, data=data)
            )
        elif path:
            warnings.append(f"{sid}: no cached {role} at {path}")

    for episode in available:
        info = replace(episode.info, show_title=series.info.title)
        basename = episode_basename(series, info, episode.name)
        stem = f"{base}/Season {info.season}/{basename}"
        owner = f"{sid}/{info.youtube_id}"
        desired.append(
            DesiredFile(
                f"{stem}{episode.media.suffix}",
                "link",
                owner,
                "media",
                source=episode.media,
            )
        )
        desired.append(
            DesiredFile(
                f"{stem}.nfo", "data", owner, "nfo", data=render_episode(info)
            )
        )
        if thumb := _read(episode.thumb):
            desired.append(
                DesiredFile(
                    f"{stem}-thumb.jpg", "data", owner, "thumb", data=thumb
                )
            )
        else:
            warnings.append(f"{info.youtube_id}: no cached thumbnail")

    return desired, warnings

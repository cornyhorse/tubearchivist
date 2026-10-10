"""
Functionality:
- turn a series (channel or playlist) and its numbered episodes into the
  list of files the Emby tree should contain
- naming: <kind>/<Name> [<id>]/Season <s>/<Name> - S<s>E<e> - <Title>.ext
  the SxxEyy in the filename always equals the NFO, so Emby's filename
  parser and the NFO agree and two videos can never share a number
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from emby.src.nfo import EpisodeInfo, SeriesInfo, render_episode, render_tvshow
from emby.src.writer import DesiredFile

# characters Windows/SMB clients and Emby can't handle in names
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')
MAX_TITLE_BYTES = 120


@dataclass(frozen=True)
class EpisodeSource:
    """an episode plus where its files come from"""

    info: EpisodeInfo
    media: Path
    thumb: Path | None = None


@dataclass(frozen=True)
class SeriesSource:
    """a series plus where its artwork comes from"""

    kind: str  # "channels" or "playlists"
    info: SeriesInfo
    poster: Path | None = None
    fanart: Path | None = None
    banner: Path | None = None


def safe_name(value: str, fallback: str) -> str:
    """filesystem safe, single spaced, trimmed to MAX_TITLE_BYTES"""
    value = _UNSAFE.sub(" ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    encoded = value.encode("utf-8")[:MAX_TITLE_BYTES]
    value = encoded.decode("utf-8", errors="ignore").strip(" .")
    return value or fallback


def series_dir(series: SeriesSource) -> str:
    """relpath of the series folder, stable across renames of the title"""
    name = safe_name(series.info.title, series.info.series_id)
    return f"{series.kind}/{name} [{series.info.series_id}]"


def episode_basename(series: SeriesSource, info: EpisodeInfo) -> str:
    """file name without extension"""
    show = safe_name(series.info.title, series.info.series_id)
    title = safe_name(info.title, info.youtube_id)
    return f"{show} - S{info.season:02d}E{info.episode:02d} - {title}"


def _read(path: Path | None) -> bytes | None:
    if path and path.is_file():
        return path.read_bytes()
    return None


def plan_series(
    series: SeriesSource, episodes: list[EpisodeSource]
) -> tuple[list[DesiredFile], list[str]]:
    """desired files for one series, plus warnings; nothing if no episodes"""
    warnings = []
    if not episodes:
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

    for episode in episodes:
        info = replace(episode.info, show_title=series.info.title)
        if not episode.media.is_file():
            warnings.append(f"{info.youtube_id}: missing media {episode.media}")
            continue

        stem = f"{base}/Season {info.season}/{episode_basename(series, info)}"
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
                DesiredFile(f"{stem}-thumb.jpg", "data", owner, "thumb", data=thumb)
            )
        else:
            warnings.append(f"{info.youtube_id}: no cached thumbnail")

    return desired, warnings

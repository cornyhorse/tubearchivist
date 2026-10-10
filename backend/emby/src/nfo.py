"""
Functionality:
- render Emby/Kodi NFO sidecars for series and episodes
- deterministic: same input, same bytes (fixed element order, no timestamps)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

# XML 1.0 can't carry most control characters; YouTube descriptions can
_INVALID_XML = dict.fromkeys(
    c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)
)


@dataclass(frozen=True)
class SeriesInfo:
    """a channel or playlist as an Emby series"""

    series_id: str
    title: str
    plot: str = ""
    premiered: date | None = None
    studio: str = ""


@dataclass(frozen=True)
class EpisodeInfo:
    """a video as an Emby episode"""

    youtube_id: str
    title: str
    show_title: str
    season: int
    episode: int
    aired: date | None
    plot: str = ""
    runtime_seconds: int = 0
    studio: str = ""
    thumb: str = ""


def _clean(text: str | None) -> str:
    return (text or "").translate(_INVALID_XML).strip()


def _add(parent: ET.Element, tag: str, text, **attrib) -> None:
    element = ET.SubElement(parent, tag, attrib)
    element.text = _clean(str(text)) if text is not None else ""


def _render(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=False)
    header = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
    return (header + body + "\n").encode("utf-8")


def render_tvshow(series: SeriesInfo) -> bytes:
    """
    tvshow.nfo; lockdata stops Emby matching the series to an online
    provider by name and renaming it
    """
    root = ET.Element("tvshow")
    _add(root, "title", series.title)
    _add(root, "originaltitle", series.title)
    _add(root, "sorttitle", series.title)
    _add(root, "plot", series.plot)
    if series.premiered:
        _add(root, "premiered", series.premiered.isoformat())
    _add(root, "studio", series.studio)
    _add(root, "uniqueid", series.series_id, type="youtube", default="true")
    _add(root, "lockdata", "true")
    return _render(root)


def render_episode(episode: EpisodeInfo) -> bytes:
    """<episodedetails> sidecar for one video"""
    root = ET.Element("episodedetails")
    _add(root, "title", episode.title)
    _add(root, "showtitle", episode.show_title)
    _add(root, "season", episode.season)
    _add(root, "episode", episode.episode)
    if episode.aired:
        _add(root, "aired", episode.aired.isoformat())
        _add(root, "premiered", episode.aired.isoformat())
        _add(root, "year", episode.aired.year)
    _add(root, "plot", episode.plot)
    if episode.runtime_seconds:
        _add(root, "runtime", max(1, round(episode.runtime_seconds / 60)))
    _add(root, "studio", episode.studio)
    if episode.thumb:
        _add(root, "thumb", episode.thumb)
    # no lockdata here: a locked episode ignores later NFO changes
    _add(root, "uniqueid", episode.youtube_id, type="youtube", default="true")
    return _render(root)

"""
Functionality:
- render Emby/Kodi NFO sidecars for series and episodes
- deterministic: same input, same bytes (fixed element order, no timestamps)
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

# everything outside the XML 1.0 Char production: most C0 controls, lone
# surrogates and U+FFFE/U+FFFF; YouTube descriptions can carry any of them
_INVALID_XML = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


@dataclass(frozen=True)
class SeriesInfo:
    """a channel or playlist as an Emby series"""

    series_id: str
    title: str
    plot: str = ""
    premiered: date | None = None
    studio: str = ""
    # Emby access tags, see emby/src/tags.py
    tags: tuple[str, ...] = ()


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
    # access tags of an episode that has no tagged show to inherit from
    tags: tuple[str, ...] = ()


def _clean(text: str | None) -> str:
    return _INVALID_XML.sub("", text or "").strip()


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
    for tag in sorted(set(series.tags)):
        _add(root, "tag", tag)
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
    for tag in sorted(set(episode.tags)):
        _add(root, "tag", tag)
    # thumbs are <name>-thumb.jpg sidecars, which Emby picks up by name
    # no lockdata here: a locked episode ignores later NFO changes
    _add(root, "uniqueid", episode.youtube_id, type="youtube", default="true")
    return _render(root)

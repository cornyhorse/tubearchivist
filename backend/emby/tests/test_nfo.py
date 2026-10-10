"""tests for NFO rendering"""

import xml.etree.ElementTree as ET
from datetime import date

from emby.src.nfo import EpisodeInfo, SeriesInfo, render_episode, render_tvshow

EPISODE = EpisodeInfo(
    youtube_id="abc123",
    title="Ride <Front> & Back \x0b row",
    show_title="Channel",
    season=2025,
    episode=3140,
    aired=date(2025, 3, 14),
    plot="line one\nline two",
    runtime_seconds=458,
    studio="Channel",
)


def test_episode_fields():
    root = ET.fromstring(render_episode(EPISODE))

    assert root.tag == "episodedetails"
    assert root.findtext("title") == "Ride <Front> & Back  row"
    assert root.findtext("season") == "2025"
    assert root.findtext("episode") == "3140"
    assert root.findtext("aired") == "2025-03-14"
    assert root.findtext("premiered") == "2025-03-14"
    assert root.findtext("runtime") == "8"
    assert root.find("uniqueid").attrib == {
        "type": "youtube",
        "default": "true",
    }
    assert root.findtext("uniqueid") == "abc123"
    assert root.find("lockdata") is None


def test_rendering_is_deterministic():
    assert render_episode(EPISODE) == render_episode(EPISODE)


def test_tvshow_locked():
    root = ET.fromstring(render_tvshow(SeriesInfo("UC1", "Channel", "about")))

    assert root.tag == "tvshow"
    assert root.findtext("title") == "Channel"
    assert root.findtext("lockdata") == "true"


def test_episode_without_date_or_runtime():
    root = ET.fromstring(
        render_episode(EpisodeInfo("abc", "t", "show", 1, 1, aired=None))
    )

    assert root.find("aired") is None
    assert root.find("runtime") is None

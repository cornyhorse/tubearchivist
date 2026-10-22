"""tests for building the Emby tree from the database"""

import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import pytest

from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc
from emby.models import EpisodeNumber
from emby.src.sync import (
    paths_from_env,
    playlist_numbers,
    sync_all,
    sync_channel,
    validate_output_root,
)
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db


def playlist_assign(dry_run=False):
    """numbers of the playlist; playlists are no Emby shows, but keep them"""
    _, _, found = playlist_numbers(
        Playlist.objects.get(), ZoneInfo("UTC"), dry_run=dry_run
    )
    return found.numbers


def numbers(root):
    """{youtube_id: (season, episode)} from the NFOs under root"""
    result = {}
    for path in root.rglob("*.nfo"):
        nfo = ET.parse(path).getroot()
        if nfo.tag == "episodedetails":
            result[nfo.findtext("uniqueid")] = (
                int(nfo.findtext("season")),
                int(nfo.findtext("episode")),
            )
    return result


def test_playlist_position_numbers(library):
    Playlist.objects.update(emby_numbering="playlist_position")

    assert playlist_assign() == {"v3": (1, 1), "v1": (1, 3)}


def test_playlist_reorder_renumbers(library):
    Playlist.objects.update(emby_numbering="playlist_position")
    playlist_assign()
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    playlist.entries.create(idx=0, youtube_id="v1")
    playlist.entries.create(idx=1, youtube_id="v3")

    assert playlist_assign() == {"v1": (1, 1), "v3": (1, 2)}
    stored = EpisodeNumber.objects.filter(series_id="PL1")
    assert {(n.youtube_id, n.episode) for n in stored} == {
        ("v1", 1),
        ("v3", 2),
    }


def test_channel_art_from_cache(library):
    sync_channel(channel_doc()["channel_id"], library)

    series = next((library.output_root / "channels").iterdir())
    assert (series / "fanart.jpg").read_bytes() == b"tvart"
    assert (series / "banner.jpg").read_bytes() == b"banner"
    assert len(numbers(series)) == 3


def test_sync_all_and_removed_series(library):
    report = sync_all(library)

    assert len(numbers(library.output_root / "channels")) == 3
    assert not (library.output_root / "playlists").exists()
    assert not report.conflicts

    Channel.objects.update(emby_show="never")
    report = sync_all(library)

    shows = [p.name for p in (library.output_root / "channels").iterdir()]
    assert shows == ["Other videos [other]"]
    assert any(p.endswith("/") for p in report.deleted)
    assert sync_all(library).changed == 0


def test_dry_run_sync_all(library):
    report = sync_all(library, dry_run=True)

    assert report.created
    assert not library.output_root.exists()


def test_video_without_published_date(library):
    Video.objects.filter(pk="v2").update(published=None)

    sync_channel(channel_doc()["channel_id"], library)

    nfos = [
        ET.parse(p).getroot()
        for p in (library.output_root / "channels").rglob("*.nfo")
        if p.name != "tvshow.nfo"
    ]
    undated = next(n for n in nfos if n.findtext("uniqueid") == "v2")
    assert undated.find("aired") is None
    assert (
        len({(n.findtext("season"), n.findtext("episode")) for n in nfos}) == 3
    )


@pytest.mark.parametrize(
    "output, ok",
    [
        ("emby", True),
        ("media/.emby", True),
        ("media/.emby/deeper", True),
        ("media/channels", False),
        ("media", False),
        (".", False),
    ],
)
def test_validate_output_root(tmp_path, output, ok):
    media = tmp_path / "media"
    media.mkdir()
    if ok:
        validate_output_root(media, tmp_path / output)
    else:
        with pytest.raises(ValueError):
            validate_output_root(media, tmp_path / output)


def test_paths_from_env(monkeypatch, tmp_path):
    env = EnvironmentSettings
    monkeypatch.setattr(env, "EMBY_ROOT", "")
    assert paths_from_env() is None

    monkeypatch.setattr(env, "MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setattr(env, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(env, "TZ", "America/New_York")
    monkeypatch.setattr(env, "EMBY_ROOT", str(tmp_path / "media" / ".emby"))
    paths = paths_from_env()
    assert paths.output_root == tmp_path / "media" / ".emby"
    assert paths.timezone == "America/New_York"

    monkeypatch.setattr(env, "EMBY_ROOT", str(tmp_path / "media" / "x"))
    with pytest.raises(ValueError):
        paths_from_env()

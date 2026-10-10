"""tests for building the Emby tree from the database"""

import xml.etree.ElementTree as ET

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc, video_doc
from emby.models import EpisodeNumber
from common.src.env_settings import EnvironmentSettings
from emby.src.sync import (
    EmbyPaths,
    paths_from_env,
    sync_all,
    sync_channel,
    sync_playlist,
    validate_output_root,
)
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(name="paths")
def fixture_paths(tmp_path):
    """scratch roots with art for channel and playlist in the cache"""
    paths = EmbyPaths(tmp_path / "media", tmp_path / "cache", tmp_path / "emby")
    channel_id = channel_doc()["channel_id"]
    for kind in ("thumb", "tvart", "banner"):
        art = paths.channel_art(channel_id, kind)
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_bytes(kind.encode())
    thumb = paths.playlist_thumb("PL1")
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"playlist")
    return paths


def add_video(paths, youtube_id, published):
    """video with placeholder media and thumb"""
    video = Video.from_doc(video_doc(youtube_id=youtube_id, published=published))
    video.save()
    media = paths.media_root / video.media_url
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(youtube_id.encode())
    thumb = paths.video_thumb(youtube_id)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"jpg")
    return video


@pytest.fixture(name="library")
def fixture_library(paths):
    """channel with three videos, playlist with two of them reversed"""
    Channel.objects.create(**Channel.fields_from_doc(channel_doc()))
    for n, youtube_id in enumerate(["v1", "v2", "v3"]):
        add_video(paths, youtube_id, 1700000000 + n * 86400)
    doc = playlist_doc("PL1", ["v3", "notdl", "v1"])
    Playlist.objects.create(**Playlist.fields_from_doc(doc))
    for entry in Playlist.entries_from_doc(doc):
        entry.save()
    return paths


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


def test_playlist_position(library):
    report = sync_playlist("PL1", library)

    root = library.output_root / "playlists"
    assert numbers(root) == {"v3": (1, 1), "v1": (1, 3)}
    assert (next(root.iterdir()) / "poster.jpg").read_bytes() == b"playlist"
    assert not report.conflicts


def test_playlist_reorder_renumbers(library):
    sync_playlist("PL1", library)
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    playlist.entries.create(idx=0, youtube_id="v1")
    playlist.entries.create(idx=1, youtube_id="v3")

    report = sync_playlist("PL1", library)

    assert numbers(library.output_root) == {"v1": (1, 1), "v3": (1, 2)}
    assert len(report.renamed) == 6
    stored = EpisodeNumber.objects.filter(series_id="PL1")
    assert {(n.youtube_id, n.episode) for n in stored} == {("v1", 1), ("v3", 2)}


def test_channel_art_from_cache(library):
    sync_channel(channel_doc()["channel_id"], library)

    series = next((library.output_root / "channels").iterdir())
    assert (series / "fanart.jpg").read_bytes() == b"tvart"
    assert (series / "banner.jpg").read_bytes() == b"banner"
    assert len(numbers(series)) == 3


def test_sync_all_and_removed_series(library):
    report = sync_all(library)

    assert len(numbers(library.output_root / "channels")) == 3
    assert len(numbers(library.output_root / "playlists")) == 2
    assert not report.conflicts

    Playlist.objects.all().delete()
    report = sync_all(library)

    assert not (library.output_root / "playlists").exists() or not any(
        (library.output_root / "playlists").iterdir()
    )
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
    assert len({(n.findtext("season"), n.findtext("episode")) for n in nfos}) == 3


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

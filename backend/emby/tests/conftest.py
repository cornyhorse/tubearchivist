"""shared fixtures of the Emby writer tests"""

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc, playlist_doc, video_doc
from emby.src.sync import EmbyPaths
from playlist.models import Playlist
from video.models import Video


@pytest.fixture(name="media")
def fixture_media(tmp_path):
    """fake archive with three small media files and thumbs"""
    root = tmp_path / "media"
    cache = tmp_path / "cache"
    (root / "UC1").mkdir(parents=True)
    (cache / "videos").mkdir(parents=True)
    for youtube_id in ("aaa", "bbb", "ccc"):
        (root / "UC1" / f"{youtube_id}.mp4").write_bytes(
            b"video " + youtube_id.encode()
        )
        thumb_dir = cache / "videos" / youtube_id[0]
        thumb_dir.mkdir(exist_ok=True)
        (thumb_dir / f"{youtube_id}.jpg").write_bytes(
            b"jpg " + youtube_id.encode()
        )
    return root, cache


@pytest.fixture(name="paths")
def fixture_paths(tmp_path):
    """scratch roots with art for channel and playlist in the cache"""
    paths = EmbyPaths(
        tmp_path / "media", tmp_path / "cache", tmp_path / "emby"
    )
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
    video = Video.from_doc(
        video_doc(youtube_id=youtube_id, published=published)
    )
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


@pytest.fixture(name="settings_env")
def fixture_settings_env(library, monkeypatch):
    """environment pointed at the scratch library, as the commands read it"""
    env = EnvironmentSettings
    monkeypatch.setattr(env, "MEDIA_DIR", str(library.media_root))
    monkeypatch.setattr(env, "CACHE_DIR", str(library.cache_root))
    monkeypatch.setattr(env, "EMBY_ROOT", str(library.output_root))
    monkeypatch.setattr(env, "TZ", "UTC")
    return env

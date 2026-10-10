"""shared fixtures for video tests"""

import copy

import pytest
from appsettings.models import ConfigDocument
from appsettings.src.config import AppConfig
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisArchivist
from common.tests.docs import channel_doc, video_doc
from download.src.thumbnails import ThumbManagerBase
from video.models import Video
from video.src.meta_embed import IndexFromEmbed


@pytest.fixture(name="app_config")
def fixture_app_config(db):
    """stored default app config, returns the config dict"""
    del db
    config = copy.deepcopy(AppConfig.CONFIG_DEFAULTS)
    config["downloads"]["sleep_interval"] = 0
    ConfigDocument.objects.create(key=AppConfig.KEY, value=config)
    return config


def set_config(**downloads) -> dict:
    """update stored downloads config, returns the new config"""
    return AppConfig().update_config({"downloads": downloads})


@pytest.fixture(name="channel")
def fixture_channel(db):
    """saved test channel"""
    del db
    return Channel.objects.create(**Channel.fields_from_doc(channel_doc()))


@pytest.fixture(name="video")
def fixture_video(channel):
    """saved test video"""
    del channel
    return Video.save_doc(video_doc())


@pytest.fixture(name="dirs")
def fixture_dirs(tmp_path, monkeypatch):
    """point media and cache dirs at tmp_path"""
    media = tmp_path / "media"
    cache = tmp_path / "cache"
    media.mkdir()
    cache.mkdir()
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(EnvironmentSettings, "HOST_UID", 0)
    monkeypatch.setattr(EnvironmentSettings, "HOST_GID", 0)
    monkeypatch.setattr(IndexFromEmbed, "VIDEOS_BASE", str(media))
    monkeypatch.setattr(IndexFromEmbed, "CACHE_DIR", str(cache))
    monkeypatch.setattr(IndexFromEmbed, "HOST_UID", 0)
    monkeypatch.setattr(IndexFromEmbed, "HOST_GID", 0)
    monkeypatch.setattr(ThumbManagerBase, "CACHE_DIR", str(cache))
    monkeypatch.setattr(ThumbManagerBase, "MEDIA_DIR", str(media))
    monkeypatch.setattr(
        ThumbManagerBase, "VIDEO_DIR", str(cache / "videos")
    )
    monkeypatch.setattr(
        ThumbManagerBase, "CHANNEL_DIR", str(cache / "channels")
    )
    monkeypatch.setattr(
        ThumbManagerBase, "PLAYLIST_DIR", str(cache / "playlists")
    )
    return {"media": media, "cache": cache}


@pytest.fixture(name="redis")
def fixture_redis():
    """redis of this test namespace, cleaned before and after"""
    redis_con = RedisArchivist()

    def clean():
        keys = redis_con.conn.keys(redis_con.NAME_SPACE + "*")
        if keys:
            redis_con.conn.delete(*keys)

    clean()
    yield redis_con
    clean()

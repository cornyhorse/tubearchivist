"""shared fixtures for the download tests"""

import copy

import pytest
from appsettings.models import ConfigDocument
from appsettings.src.config import AppConfig
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisArchivist, RedisBase
from download.src.thumbnails import ThumbManagerBase
from PIL import Image


@pytest.fixture(name="app_config")
def fixture_app_config(db):  # pylint: disable=unused-argument
    """default app config in the database, without sleeping"""
    config = copy.deepcopy(AppConfig.CONFIG_DEFAULTS)
    config["downloads"]["sleep_interval"] = 0
    ConfigDocument.objects.update_or_create(
        key=AppConfig.KEY, defaults={"value": config}
    )
    return config


@pytest.fixture(name="redis_ns")
def fixture_redis_ns(monkeypatch):
    """clean the test redis namespace around a test, no background saves"""
    monkeypatch.setattr(RedisArchivist, "bg_save", lambda self: None)
    conn = RedisBase().conn
    pattern = f"{RedisBase.NAME_SPACE}*"

    def clear():
        keys = conn.keys(pattern)
        if keys:
            conn.delete(*keys)

    clear()
    yield conn
    clear()


@pytest.fixture(name="cache_dir")
def fixture_cache_dir(tmp_path, monkeypatch):
    """point the thumbnail cache and media dirs at tmp_path"""
    cache = tmp_path / "cache"
    media = tmp_path / "media"
    for folder in ("videos", "channels", "playlists", "download"):
        (cache / folder).mkdir(parents=True)
    media.mkdir()

    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(ThumbManagerBase, "CACHE_DIR", str(cache))
    monkeypatch.setattr(ThumbManagerBase, "MEDIA_DIR", str(media))
    monkeypatch.setattr(ThumbManagerBase, "VIDEO_DIR", str(cache / "videos"))
    monkeypatch.setattr(
        ThumbManagerBase, "CHANNEL_DIR", str(cache / "channels")
    )
    monkeypatch.setattr(
        ThumbManagerBase, "PLAYLIST_DIR", str(cache / "playlists")
    )
    return cache


@pytest.fixture(name="app_dir")
def fixture_app_dir(tmp_path, monkeypatch):
    """APP_DIR with the default artwork the fallback opens"""
    app_dir = tmp_path / "app"
    img_dir = app_dir / "static" / "img"
    img_dir.mkdir(parents=True)
    for name in (
        "default-video-thumb",
        "default-playlist-thumb",
        "default-channel-icon",
        "default-channel-banner",
        "default-channel-art",
    ):
        Image.new("RGB", (32, 18), "gray").save(img_dir / f"{name}.jpg")

    monkeypatch.setattr(EnvironmentSettings, "APP_DIR", str(app_dir))
    return app_dir

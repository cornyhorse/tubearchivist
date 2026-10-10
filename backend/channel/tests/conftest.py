"""shared fixtures for channel and playlist tests: no network, no real media"""

import copy

import pytest
from appsettings.models import ConfigDocument
from appsettings.src.config import AppConfig
from common.src.env_settings import EnvironmentSettings
from download.src.thumbnails import ThumbManager
from download.src.yt_dlp_base import YtWrap
from rest_framework.test import APIClient
from user.models import Account


class FakeYt:
    """stands in for yt-dlp: canned responses by url, records every call"""

    def __init__(self):
        self.responses: dict = {}
        self.calls: list[tuple[str, dict]] = []

    def extract(self, wrap, url):
        """YtWrap.extract replacement"""
        self.calls.append((url, wrap.obs_request))
        if url not in self.responses:
            raise AssertionError(f"unexpected yt-dlp call: {url}")

        response = self.responses[url]
        if response is None:
            return None, "failed"

        return copy.deepcopy(response), None


class FakeThumbs:
    """records ThumbManager.download calls instead of downloading"""

    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []

    def download(self, manager, url):
        """ThumbManager.download replacement"""
        self.calls.append((manager.item_id, manager.item_type, url))


class FakeTask:
    """celery task stand-in, records progress messages"""

    def __init__(self):
        self.messages: list = []

    def send_progress(self, message, progress=None):
        """record message"""
        self.messages.append((message, progress))


class FakeDelay:
    """celery shared_task stand-in, records .delay calls"""

    def __init__(self):
        self.calls: list = []

    def delay(self, *args, **kwargs):
        """record call"""
        self.calls.append((args, kwargs))
        return type("Result", (), {"id": "task-id-1"})()


@pytest.fixture(name="yt", autouse=True)
def fixture_yt(monkeypatch):
    """never call real yt-dlp, unknown urls fail the test"""
    fake = FakeYt()
    monkeypatch.setattr(YtWrap, "build_obs", lambda self: None)
    monkeypatch.setattr(
        YtWrap, "extract", lambda self, url: fake.extract(self, url)
    )
    return fake


@pytest.fixture(name="thumbs", autouse=True)
def fixture_thumbs(monkeypatch):
    """never download artwork"""
    fake = FakeThumbs()
    monkeypatch.setattr(
        ThumbManager, "download", lambda self, url: fake.download(self, url)
    )
    return fake


@pytest.fixture(name="dirs", autouse=True)
def fixture_dirs(monkeypatch, tmp_path):
    """media and cache in tmp_path"""
    media = tmp_path / "media"
    cache = tmp_path / "cache"
    media.mkdir()
    cache.mkdir()
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    return {"media": media, "cache": cache}


@pytest.fixture(name="app_config")
def fixture_app_config(db):  # pylint: disable=unused-argument
    """default appsettings in the database"""
    config = copy.deepcopy(AppConfig.CONFIG_DEFAULTS)
    config["downloads"]["sleep_interval"] = 0
    ConfigDocument.objects.create(key=AppConfig.KEY, value=config)
    return config


@pytest.fixture(name="admin")
def fixture_admin(db):  # pylint: disable=unused-argument
    """staff user"""
    return Account.objects.create_superuser(name="admin", password="test")


@pytest.fixture(name="client")
def fixture_client(admin, app_config):  # pylint: disable=unused-argument
    """api client authenticated as staff"""
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user=admin)
    return client


@pytest.fixture(name="task")
def fixture_task():
    """celery task stand-in"""
    return FakeTask()


@pytest.fixture(name="fake_delay")
def fixture_fake_delay():
    """factory for celery task stand-ins"""
    return FakeDelay

"""shared fixtures for appsettings tests"""

import pytest
from appsettings.src.config import AppConfig
from common.src.ta_redis import RedisBase


class FakeTask:
    """records progress messages like a celery task would receive them"""

    def __init__(self):
        self.messages = []

    def send_progress(self, message_lines, progress=False, level="info"):
        """record one progress call"""
        self.messages.append((list(message_lines), progress, level))

    @property
    def lines(self) -> list[str]:
        """all message lines sent so far"""
        return [line for lines, _, _ in self.messages for line in lines]


def _clear_namespace():
    conn = RedisBase().conn
    keys = conn.keys(f"{RedisBase.NAME_SPACE}*")
    if keys:
        conn.delete(*keys)


@pytest.fixture(name="redis_clean")
def fixture_redis_clean():
    """empty this test run's redis namespace before and after"""
    _clear_namespace()
    yield
    _clear_namespace()


@pytest.fixture(name="app_config")
def fixture_app_config(db):
    """default app config stored in the database"""
    # pylint: disable=unused-argument
    AppConfig.__new__(AppConfig).sync_defaults()
    return AppConfig()


@pytest.fixture(name="task")
def fixture_task():
    """fake task collecting progress"""
    return FakeTask()

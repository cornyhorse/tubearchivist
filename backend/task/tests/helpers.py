"""shared fixtures for task, config and user tests"""

from types import SimpleNamespace

import pytest
from celery.app.task import Task
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisArchivist, TaskRedis
from rest_framework.test import APIClient
from user.models import Account


@pytest.fixture(name="redis_ns")
def fixture_redis_ns(monkeypatch):
    """
    real redis, task results moved into the test namespace,
    no BGSAVE, every key created during the test removed afterwards
    """
    name_space = EnvironmentSettings.REDIS_NAME_SPACE
    monkeypatch.setattr(TaskRedis, "BASE", f"{name_space}celery-task-meta-")
    monkeypatch.setattr(RedisArchivist, "bg_save", lambda self: None)
    conn = RedisArchivist().conn
    before = set(conn.keys(f"{name_space}*"))
    yield conn
    created = set(conn.keys(f"{name_space}*")) - before
    if created:
        conn.delete(*created)


@pytest.fixture(name="no_broker", autouse=True)
def fixture_no_broker(monkeypatch):
    """never send anything to the celery broker, record instead"""
    sent = []

    def apply_async(self, args=None, kwargs=None, **options):
        sent.append((self.name, args, kwargs))
        return SimpleNamespace(
            id=f"fake-{len(sent)}", status="PENDING", name=self.name
        )

    monkeypatch.setattr(Task, "apply_async", apply_async)
    return sent


@pytest.fixture(name="admin_client")
def fixture_admin_client(db):
    """api client authenticated as staff"""
    # pylint: disable=unused-argument
    user = Account.objects.create_superuser("admin", "secret-pw")
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user=user)
    client.user = user
    return client


@pytest.fixture(name="user_client")
def fixture_user_client(db):
    """api client authenticated as a regular user"""
    # pylint: disable=unused-argument
    user = Account.objects.create_user("viewer", "secret-pw")
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user=user)
    client.user = user
    return client

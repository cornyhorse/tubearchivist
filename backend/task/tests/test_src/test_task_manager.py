"""task results in redis and task commands"""

from types import SimpleNamespace

import pytest
import task.tasks  # noqa: F401 pylint: disable=unused-import
from common.src.ta_redis import TaskRedis
from task.celery import app as celery_app
from task.src.task_manager import TaskCommand, TaskManager


def fake_task(name="download_pending", task_id="task-1"):
    """the parts of a bound task the manager reads"""
    return SimpleNamespace(name=name, request=SimpleNamespace(id=task_id))


def test_empty(redis_ns):
    # pylint: disable=unused-argument
    manager = TaskManager()

    assert manager.get_all_results() is False
    assert manager.get_tasks_by_name("download_pending") is False
    assert manager.is_pending(fake_task()) is False
    assert manager.get_pending("download_pending") is False
    assert manager.get_task("nope") == {}
    assert manager.fail_pending() is None


def test_init_pending_and_fail(redis_ns):
    # pylint: disable=unused-argument
    manager = TaskManager()
    manager.init(fake_task())
    manager.init(fake_task(name="run_backup", task_id="task-2"))
    TaskRedis().set_key("task-3", {"status": "SUCCESS", "task_id": "task-3"})

    assert manager.is_pending(fake_task()) is True
    assert manager.is_pending(fake_task(name="thumbnail_check")) is False
    assert [i["task_id"] for i in manager.get_pending("run_backup")] == [
        "task-2"
    ]
    assert len(manager.get_all_results()) == 3

    manager.fail_pending()

    assert manager.get_task("task-1")["status"] == "FAILED"
    assert manager.get_task("task-3")["status"] == "SUCCESS"
    assert manager.is_pending(fake_task()) is False


def test_stop_command(redis_ns, capsys):
    # pylint: disable=unused-argument
    manager = TaskManager()
    manager.init(fake_task())
    assert manager.is_stopped("task-1") is False

    TaskCommand().stop("task-1")

    assert manager.is_stopped("task-1") is True
    assert "received STOP signal" in capsys.readouterr().out


def test_start(no_broker):
    message = TaskCommand().start("thumbnail_check")
    with_kwargs = TaskCommand().start("check_reindex", {"data": 1})

    assert message["task_name"] == "thumbnail_check"
    assert message["status"] == "PENDING"
    assert no_broker == [
        ("thumbnail_check", (), {}),
        ("check_reindex", (), {"data": 1}),
    ]
    assert with_kwargs["task_id"] == "fake-2"


def test_kill(monkeypatch):
    revoked = []
    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, terminate: revoked.append((task_id, terminate)),
    )

    TaskCommand().kill("task-1")

    assert revoked == [("task-1", True)]


@pytest.mark.parametrize("command", ["NOPE"])
def test_invalid_command_rejected(redis_ns, command):
    # pylint: disable=unused-argument
    with pytest.raises(ValueError):
        TaskRedis().set_command("task-1", command)

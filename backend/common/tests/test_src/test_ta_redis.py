"""tests for redis wrappers, against the dev redis in this run's namespace"""

import json

import pytest
import redis
from common.src.ta_redis import RedisArchivist, RedisQueue, TaskRedis

pytestmark = pytest.mark.usefixtures("redis_clean")

NS = RedisArchivist.NAME_SPACE


def test_set_message_dict_and_str(redis_clean):
    archivist = RedisArchivist()
    archivist.set_message("test:dict", {"a": 1})
    archivist.set_message("test:str", "plain")

    assert redis_clean.get(f"{NS}test:dict") == json.dumps({"a": 1})
    assert archivist.get_message_dict("test:dict") == {"a": 1}
    assert archivist.get_message_str("test:str") == "plain"
    assert redis_clean.ttl(f"{NS}test:dict") == -1


def test_set_message_expire_default_and_custom(redis_clean):
    archivist = RedisArchivist()
    archivist.set_message("test:default", "x", expire=True)
    archivist.set_message("test:custom", "x", expire=500)

    assert 0 < redis_clean.ttl(f"{NS}test:default") <= 20
    assert 20 < redis_clean.ttl(f"{NS}test:custom") <= 500


def test_set_and_del_message_save_calls_bg_save(monkeypatch):
    calls = []
    archivist = RedisArchivist()
    monkeypatch.setattr(archivist, "bg_save", lambda: calls.append(1))

    archivist.set_message("test:save", "x", save=True)
    assert archivist.del_message("test:save", save=True) == 1
    assert archivist.del_message("test:save") == 0
    assert len(calls) == 2


def test_bg_save_swallows_response_error(monkeypatch):
    archivist = RedisArchivist()
    calls = []

    def fail():
        calls.append("fail")
        raise redis.exceptions.ResponseError("save in progress")

    monkeypatch.setattr(archivist.conn, "bgsave", fail)
    archivist.bg_save()

    monkeypatch.setattr(archivist.conn, "bgsave", lambda: calls.append("ok"))
    archivist.bg_save()

    assert calls == ["fail", "ok"]


def test_get_message_dict_missing():
    assert RedisArchivist().get_message_dict("test:missing") == {}
    assert RedisArchivist().get_message_str("test:missing") is None


def test_get_message_legacy_json(monkeypatch):
    archivist = RedisArchivist()
    commands = []

    def fake_execute(*args):
        commands.append(args)
        return '{"status": "ok"}' if args[1].endswith("found") else None

    monkeypatch.setattr(archivist.conn, "execute_command", fake_execute)

    assert archivist.get_message("test:found") == {"status": "ok"}
    assert archivist.get_message("test:none") == {"status": False}
    assert commands[0] == ("JSON.GET", f"{NS}test:found")


def test_list_keys_and_items():
    archivist = RedisArchivist()
    assert archivist.list_keys("message:") == []
    assert archivist.list_items("message:") == []

    # keys starting with namespace characters must survive prefix removal
    archivist.set_message("message:one", {"id": 1})
    archivist.set_message("message:two", {"id": 2})
    archivist.set_message("other", {"id": 3})

    assert sorted(archivist.list_keys("message:")) == [
        "message:one",
        "message:two",
    ]
    items = archivist.list_items("message:")
    assert sorted(i["id"] for i in items) == [1, 2]


def test_queue_add_and_order():
    queue = RedisQueue("test:queue")
    assert queue.get_all() == []
    assert queue.length() == 0
    assert queue.max_score() is None
    assert queue.get_next() == (None, None)

    queue.add("")
    queue.add_list([])
    assert queue.length() == 0

    queue.add("first")
    queue.add_list(["second", "third"])
    queue.add("fourth")

    assert queue.get_all() == ["first", "second", "third", "fourth"]
    assert queue.length() == 4
    assert queue.get_rank("third") == 2
    assert queue.get_rank("missing") is None
    assert queue.max_score() == 4

    assert queue.get_next() == ("first", 1)
    assert queue.get_all() == ["second", "third", "fourth"]

    queue.clear()
    assert queue.length() == 0


def test_queue_key_is_namespaced():
    assert RedisQueue("test:queue").key == f"{NS}test:queue"


@pytest.fixture(name="task_redis")
def fixture_task_redis(monkeypatch):
    """TaskRedis with its keys moved into this run's namespace"""
    monkeypatch.setattr(TaskRedis, "BASE", f"{NS}celery-task-meta-")
    return TaskRedis()


def test_task_redis_set_get_delete(task_redis, redis_clean):
    assert task_redis.get_all() == []
    assert task_redis.get_single("t1") == {}

    task_redis.set_key("t1", {"status": "PENDING"})
    task_redis.set_key("t2", {"status": "STARTED"}, expire=True)

    assert sorted(task_redis.get_all()) == ["t1", "t2"]
    assert task_redis.get_single("t1") == {"status": "PENDING"}
    assert redis_clean.ttl(f"{task_redis.BASE}t1") == -1
    assert 0 < redis_clean.ttl(f"{task_redis.BASE}t2") <= TaskRedis.EXPIRE

    task_redis.del_task("t1")
    assert task_redis.get_all() == ["t2"]

    task_redis.del_all()
    assert task_redis.get_all() == []


def test_task_redis_set_command(task_redis):
    task_redis.set_key("t1", {"status": "STARTED"})
    task_redis.set_command("t1", "STOP")
    assert task_redis.get_single("t1") == {
        "status": "STARTED",
        "command": "STOP",
    }

    with pytest.raises(ValueError):
        task_redis.set_command("t1", "PAUSE")

    with pytest.raises(KeyError):
        task_redis.set_command("missing", "KILL")

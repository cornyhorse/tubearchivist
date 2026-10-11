"""task api views"""

import pytest
from common.src.ta_redis import TaskRedis
from task.celery import app as celery_app
from task.models import CustomPeriodicTask
from task.src.config_schedule import ScheduleBuilder
from task.src.notify import Notifications

pytestmark = pytest.mark.django_db


def store_result(task_id, name, status="SUCCESS", **extra):
    """task result as celery would store it"""
    result = {
        "status": status,
        "result": None,
        "traceback": None,
        "date_done": "2026-10-10T00:00:00",
        "name": name,
        "task_id": task_id,
    }
    result.update(extra)
    TaskRedis().set_key(task_id, result)
    return result


def test_admin_only(user_client):
    assert user_client.get("/api/task/by-name/").status_code == 403
    assert user_client.get("/api/task/schedule/").status_code == 403


def test_notifications_admin_only(user_client, monkeypatch):
    def fake_test(self, url):
        raise AssertionError(f"non-admin reached apprise with {url}")

    monkeypatch.setattr(Notifications, "test", fake_test)
    Notifications("run_backup").add_url("json://secret")
    url = "/api/task/notification/"
    body = {"task_name": "run_backup", "url": "json://x"}

    assert user_client.get(url).status_code == 403
    assert user_client.post(url, body, format="json").status_code == 403
    assert user_client.delete(url, body, format="json").status_code == 403
    test_url = "/api/task/notification/test/"
    test_body = {"url": "json://169.254.169.254"}
    assert (
        user_client.post(test_url, test_body, format="json").status_code == 403
    )
    assert Notifications("run_backup").get_urls() == ["json://secret"]


def test_task_list(admin_client, redis_ns):
    # pylint: disable=unused-argument
    assert admin_client.get("/api/task/by-name/").json() == []

    store_result("t1", "run_backup")
    response = admin_client.get("/api/task/by-name/")

    assert response.status_code == 200
    assert response.json()[0]["task_id"] == "t1"


def test_task_name_list(admin_client, redis_ns):
    # pylint: disable=unused-argument
    url = "/api/task/by-name/run_backup/"
    assert admin_client.get("/api/task/by-name/nope/").status_code == 404
    assert admin_client.get(url).json() == []

    store_result("t1", "run_backup")
    store_result("t2", "thumbnail_check")
    response = admin_client.get(url)

    assert [i["task_id"] for i in response.json()] == ["t1"]


def test_task_name_start(admin_client, no_broker):
    unknown = admin_client.post("/api/task/by-name/nope/")
    not_startable = admin_client.post("/api/task/by-name/check_reindex/")
    started = admin_client.post("/api/task/by-name/run_backup/")

    assert unknown.status_code == 404
    assert not_startable.status_code == 404
    assert "can not start" in not_startable.json()["error"]
    assert started.status_code == 200
    assert started.json()["task_id"] == "fake-1"
    assert no_broker == [("run_backup", (), {})]


def test_task_id_get(admin_client, redis_ns):
    # pylint: disable=unused-argument
    store_result("t1", "run_backup")

    assert admin_client.get("/api/task/by-id/nope/").status_code == 404
    assert admin_client.get("/api/task/by-id/t1/").json()["name"] == (
        "run_backup"
    )


def test_task_id_commands(admin_client, redis_ns, monkeypatch):
    # pylint: disable=unused-argument
    revoked = []
    monkeypatch.setattr(
        celery_app.control, "revoke", lambda i, terminate: revoked.append(i)
    )
    store_result("dl", "download_pending", status="PENDING")
    store_result("bk", "run_backup", status="PENDING")
    store_result("snap", "db_snapshot", status="PENDING")

    def post(task_id, command):
        return admin_client.post(
            f"/api/task/by-id/{task_id}/", {"command": command}, format="json"
        )

    assert post("dl", "pause").status_code == 400
    assert post("nope", "stop").status_code == 404
    assert post("bk", "stop").json() == {"error": "task can not be stopped"}
    assert post("bk", "kill").json() == {"error": "task can not be killed"}
    assert post("snap", "stop").status_code == 400
    assert post("dl", "stop").status_code == 204
    assert TaskRedis().get_single("dl")["command"] == "STOP"
    assert post("dl", "kill").status_code == 204
    assert revoked == ["dl"]


def test_schedule_list_and_get(admin_client):
    ScheduleBuilder().update_schedule("check_reindex", "0 3 *", {"days": 9})

    listed = admin_client.get("/api/task/schedule/").json()
    single = admin_client.get("/api/task/schedule/check_reindex/")

    assert listed[0]["name"] == "check_reindex"
    assert single.json()["schedule"] == "0 3 *"
    assert single.json()["config"] == {"days": 9}
    assert single.json()["schedule_human"]
    assert single.json()["next_run_at"] is not None
    task = CustomPeriodicTask.objects.get(name="check_reindex")
    task.enabled = False
    task.save()
    off = admin_client.get("/api/task/schedule/check_reindex/").json()
    assert off["next_run_at"] is None
    missing = admin_client.get("/api/task/schedule/run_backup/")
    assert missing.status_code == 404


def test_schedule_post(admin_client, capsys):
    url = "/api/task/schedule/check_reindex/"

    def post(data, task_url=url):
        return admin_client.post(task_url, data, format="json")

    assert post({}).json() == {"error": "expected schedule or config key"}
    assert post({"schedule": "* * *"}).status_code == 400
    assert post({"config": {"rotate": 1}}).status_code == 400
    assert post({"schedule": "0 1 *"}, "/api/task/schedule/nope/").json() == {
        "error": "task name not found"
    }

    assert post({"config": {"days": 5}}).json() == {
        "error": "no schedule for this task, set a schedule first"
    }

    created = post({"schedule": "0 1 *"})
    configured = post({"config": {"days": 5}})

    assert created.json()["schedule"] == "0 1 *"
    assert configured.json()["config"] == {"days": 5}
    output = capsys.readouterr().out
    assert "update schedule for task check_reindex\n" in output
    assert "with config {'days': 5}" in output


def test_schedule_delete(admin_client):
    ScheduleBuilder().update_schedule("run_backup", "auto", None)
    url = "/api/task/schedule/run_backup/"

    assert admin_client.delete(url).status_code == 204
    assert admin_client.delete(url).status_code == 404
    assert not CustomPeriodicTask.objects.exists()


def test_notifications(admin_client):
    url = "/api/task/notification/"

    missing = admin_client.post(
        url, {"task_name": "run_backup"}, format="json"
    )
    added = admin_client.post(
        url, {"task_name": "run_backup", "url": "json://a"}, format="json"
    )
    admin_client.post(
        url, {"task_name": "run_backup", "url": "json://b"}, format="json"
    )
    admin_client.post(
        url, {"task_name": "thumbnail_check", "url": "json://c"}, format="json"
    )

    assert missing.status_code == 400
    assert missing.json() == {"error": "missing url"}
    assert added.json() == {
        "run_backup": {"urls": ["json://a"], "title": "Index Backup"}
    }

    removed_url = admin_client.delete(
        url, {"task_name": "run_backup", "url": "json://a"}, format="json"
    )
    removed_task = admin_client.delete(
        url, {"task_name": "thumbnail_check"}, format="json"
    )

    assert removed_url.status_code == 204
    assert removed_task.status_code == 204
    assert admin_client.get(url).json() == {
        "run_backup": {"urls": ["json://b"], "title": "Index Backup"}
    }


def test_notification_test(admin_client, monkeypatch):
    calls = []

    def fake_test(self, url):
        calls.append((self.task_name, url))
        return url == "json://ok", "msg"

    monkeypatch.setattr(Notifications, "test", fake_test)
    url = "/api/task/notification/test/"

    good = admin_client.post(url, {"url": "json://ok"}, format="json")
    bad = admin_client.post(
        url, {"url": "json://bad", "task_name": "run_backup"}, format="json"
    )

    assert good.status_code == 200
    assert good.json() == {"success": True, "message": "msg"}
    assert bad.status_code == 400
    assert calls == [
        ("manual_test", "json://ok"),
        ("run_backup", "json://bad"),
    ]

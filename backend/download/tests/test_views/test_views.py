"""tests for the download API views"""

from types import SimpleNamespace

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc
from download import views
from download.models import Download, DownloadRule
from rest_framework.test import APIClient
from user.models import Account

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"


class FakeTaskCall:
    """celery task stand in, records delay calls"""

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        """record and return a task with an id"""
        self.calls.append((args, kwargs))
        return SimpleNamespace(id="task-1")


@pytest.fixture(name="tasks")
def fixture_tasks(monkeypatch):
    """fake extrac_dl and download_pending"""
    tasks = SimpleNamespace(
        extrac_dl=FakeTaskCall(), download_pending=FakeTaskCall()
    )
    monkeypatch.setattr(views, "extrac_dl", tasks.extrac_dl)
    monkeypatch.setattr(views, "download_pending", tasks.download_pending)
    return tasks


@pytest.fixture(name="client")
def fixture_client(app_config, redis_ns):  # pylint: disable=W0613
    """api client logged in as admin"""
    user = Account.objects.create_superuser("admin", "password")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def add(youtube_id, **overrides):
    """add a queue item from a legacy doc"""
    return Download.upsert_doc(
        download_doc(youtube_id=youtube_id, **overrides)
    )


def ids(response) -> list[str]:
    """youtube ids of a list response"""
    return [i["youtube_id"] for i in response.json()["data"]]


def test_admin_only(app_config, redis_ns):
    """regular users can't see the queue"""
    user = Account.objects.create_user("user", "password")
    client = APIClient()
    client.force_authenticate(user=user)

    assert client.get("/api/download/").status_code == 403


def test_list(client):
    """queue order, filters and search"""
    Channel.save_doc(channel_doc())
    add("a", timestamp=2, title="Coaster ride")
    add("b", timestamp=1, title="Train ride", vid_type="shorts")
    add("c", timestamp=3, auto_start=True)
    add("d", status="ignore", channel_id="UCother", message="failed")

    response = client.get("/api/download/")
    assert response.status_code == 200
    assert ids(response) == ["c", "b", "a", "d"]
    assert response.json()["data"][0]["channel_indexed"] is True

    assert ids(client.get("/api/download/?filter=pending&q=ride")) == [
        "b",
        "a",
    ]
    assert ids(client.get("/api/download/?vid_type=shorts")) == ["b"]
    assert ids(client.get("/api/download/?channel=UCother")) == ["d"]
    assert ids(client.get("/api/download/?error=true")) == ["d"]
    assert client.get("/api/download/?bogus=1").status_code == 400


def test_add_to_queue(client, tasks):
    """pending items are extracted in a task"""
    data = {
        "data": [
            {"youtube_id": "vid00000001", "status": "pending"},
            {"youtube_id": "vid00000002", "status": "ignore-force"},
            {"youtube_id": "vid00000003", "status": "pending"},
        ]
    }

    response = client.post(
        "/api/download/?autostart=true&flat=true", data, format="json"
    )

    assert response.json() == {
        "message": "add to queue task started",
        "task_id": "task-1",
    }
    assert tasks.extrac_dl.calls == [
        (
            ("vid00000001 vid00000003",),
            {"auto_start": True, "flat": True, "force": False},
        )
    ]


def test_add_to_queue_invalid(client, tasks):
    """bad status rejected"""
    data = {"data": [{"youtube_id": "x", "status": "maybe"}]}
    assert client.post("/api/download/", data, format="json").status_code == (
        400
    )
    assert not tasks.extrac_dl.calls


def test_bulk_update(client, tasks):
    """status change by filter, priority starts the queue"""
    add("a", status="ignore")
    add("b", status="ignore", channel_id="UCother")
    add("c")

    response = client.patch(
        "/api/download/?filter=ignore&channel=UCother",
        {"status": "pending"},
        format="json",
    )
    assert response.status_code == 204
    assert Download.objects.get(pk="b").status == "pending"
    assert Download.objects.get(pk="a").status == "ignore"
    assert not tasks.download_pending.calls

    client.patch(
        "/api/download/?filter=pending", {"status": "priority"}, format="json"
    )
    assert Download.objects.get(pk="c").auto_start
    assert tasks.download_pending.calls == [((), {"auto_only": True})]


@pytest.mark.parametrize(
    "query, remaining",
    [
        ("filter=pending", ["c"]),
        ("filter=pending&channel=UCother", ["a", "c"]),
        ("filter=pending&vid_type=shorts", ["a", "c"]),
    ],
)
def test_bulk_delete(client, query, remaining):
    """delete by status, channel and type"""
    add("a")
    add("b", channel_id="UCother", vid_type="shorts")
    add("c", status="ignore")

    assert client.delete(f"/api/download/?{query}").status_code == 204
    assert sorted(Download.objects.values_list("pk", flat=True)) == remaining


def test_item_get(client):
    """single item, 404 when missing"""
    add("a", message="failed")

    response = client.get("/api/download/a/")
    assert response.status_code == 200
    assert response.json()["message"] == "failed"
    assert response.json()["vid_thumb_url"].endswith("videos/a/a.jpg")

    response = client.get("/api/download/missing/")
    assert response.status_code == 404
    assert response.json() == {"error": "Download item not found"}


def test_item_update(client, tasks):
    """status change, priority starts the queue"""
    add("a")

    response = client.post("/api/download/a/", {"status": "ignore"})
    assert response.json() == {"status": "ignore"}
    assert Download.objects.get(pk="a").status == "ignore"
    assert not tasks.download_pending.calls

    client.post("/api/download/a/", {"status": "priority"})
    item = Download.objects.get(pk="a")
    assert (item.status, item.auto_start) == ("pending", True)
    assert tasks.download_pending.calls == [((), {"auto_only": True})]


def test_item_update_missing_and_force(client, tasks):
    """404 for unknown items, ignore-force extracts as ignored"""
    response = client.post("/api/download/missing/", {"status": "pending"})
    assert response.status_code == 404

    response = client.post(
        "/api/download/vid00000001/", {"status": "ignore-force"}
    )
    assert response.status_code == 200
    assert tasks.extrac_dl.calls == [(("vid00000001",), {"status": "ignore"})]


def test_item_delete(client):
    """removed from queue"""
    add("a")
    assert client.delete("/api/download/a/").status_code == 204
    assert not Download.objects.exists()


def test_aggs(client):
    """count per channel, top 30 and the rest"""
    for idx in range(32):
        add(f"v{idx}", channel_id=f"UC{idx:02}", channel_name=f"C{idx:02}")
    add("extra", channel_id="UC05", channel_name="C05")
    add("ignored", channel_id="UC05", channel_name="C05", status="ignore")

    response = client.get("/api/download/aggs/?filter=pending")

    data = response.json()
    assert data["sum_other_doc_count"] == 2
    assert len(data["buckets"]) == 30
    assert data["buckets"][0] == {
        "key": ["C05", "UC05"],
        "key_as_string": "C05|UC05",
        "doc_count": 2,
    }

    data = client.get("/api/download/aggs/?filter=ignore").json()
    assert data["buckets"] == [
        {"key": ["C05", "UC05"], "key_as_string": "C05|UC05", "doc_count": 1}
    ]
    assert client.get("/api/download/aggs/").status_code == 400


def test_default_rule(client):
    """get, set and delete the default rule"""
    # no rule renders as json null
    empty = client.get("/api/download/rule/")
    assert (empty.status_code, empty.json()) == (200, None)

    response = client.post(
        "/api/download/rule/",
        {"min_duration": 60, "vid_types": ["videos"]},
        format="json",
    )
    assert response.json()["min_duration"] == 60
    assert DownloadRule.objects.get().channel_id is None

    rule = client.get("/api/download/rule/").json()
    assert (rule["vid_types"], rule["action"]) == (["videos"], "ignore")

    bad = client.post(
        "/api/download/rule/", {"title_include": "("}, format="json"
    )
    assert bad.status_code == 400

    assert client.delete("/api/download/rule/").status_code == 204
    assert not DownloadRule.objects.exists()

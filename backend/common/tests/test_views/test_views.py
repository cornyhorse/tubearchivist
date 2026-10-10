"""tests for the common API views"""

from types import SimpleNamespace

import pytest
from channel.models import Channel
from common import views
from common.src.ta_redis import RedisArchivist, RedisQueue
from common.tests.docs import channel_doc, video_doc
from django.conf import settings
from django.contrib.auth.models import Group
from rest_framework.test import APIClient
from video.models import Video

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("redis_clean")]


@pytest.fixture(name="client")
def fixture_client(account):
    """client logged in as a regular user"""
    client = APIClient()
    client.force_authenticate(user=account)
    return client


@pytest.fixture(name="admin_client")
def fixture_admin_client(admin_account, app_config):
    """client logged in as staff, with app settings"""
    del app_config
    client = APIClient()
    client.force_authenticate(user=admin_account)
    return client


def test_health_without_auth():
    response = APIClient().get("/api/health/")
    assert response.status_code == 200
    assert response.data == "OK"


@pytest.mark.parametrize(
    "url",
    [
        "/api/ping/",
        "/api/search/?query=x",
        "/api/notification/",
        "/api/refresh/",
    ],
)
def test_auth_required(url):
    assert APIClient().get(url).status_code in (401, 403)


def test_ping(client, account):
    response = client.get("/api/ping/")
    assert response.status_code == 200
    assert response.data == {
        "response": "pong",
        "user": account.id,
        "version": settings.TA_VERSION,
        "ta_update": None,
    }

    update = {"status": True, "version": "v0.6.0", "is_breaking": False}
    RedisArchivist().set_message("versioncheck:new", update)
    assert client.get("/api/ping/").data["ta_update"] == update


def test_refresh_admin_only(client):
    assert client.get("/api/refresh/").status_code == 403
    assert client.post("/api/refresh/", {}, format="json").status_code == 403


def test_refresh_admin_group_member(client, account, app_config):
    del app_config
    account.groups.add(Group.objects.create(name="admin"))
    assert client.get("/api/refresh/").status_code == 200


def test_refresh_progress(admin_client):
    response = admin_client.get("/api/refresh/")
    assert response.status_code == 200
    assert response.data == {
        "id": None,
        "state": "empty",
        "total_queued": 0,
        "queue_type": "all",
        "queue_position": None,
    }

    RedisQueue("reindex:ta_video").add_list(["vid00000001", "vid00000002"])
    response = admin_client.get("/api/refresh/?type=video&id=vid00000002")
    assert response.data == {
        "id": "vid00000002",
        "state": "in_queue",
        "total_queued": 2,
        "queue_type": "video",
        "queue_position": 1,
    }


def test_refresh_bad_requests(admin_client, monkeypatch):
    response = admin_client.get("/api/refresh/?id=vid00000001")
    assert response.status_code == 400
    assert response.data == {"error": "specified id also needs type"}

    assert admin_client.get("/api/refresh/?type=other").status_code == 400

    def fail(self):
        raise ValueError

    monkeypatch.setattr(views.ReindexProgress, "get_progress", fail)
    response = admin_client.get("/api/refresh/?type=channel")
    assert response.status_code == 400
    assert response.data == {"error": "bad request"}


def test_refresh_post_starts_task(admin_client, monkeypatch):
    calls = []

    def fake_delay(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(views.check_reindex, "delay", fake_delay)
    response = admin_client.post(
        "/api/refresh/?extract_videos=true",
        {"channel": ["UCchannel000000000000001"]},
        format="json",
    )

    assert response.status_code == 200
    assert response.data == {
        "message": "reindex task started",
        "task_id": "task-123",
    }
    assert calls == [
        {
            "data": {"channel": ["UCchannel000000000000001"]},
            "extract_videos": True,
        }
    ]

    response = admin_client.post(
        "/api/refresh/", {"video": "not-a-list"}, format="json"
    )
    assert response.status_code == 400
    assert len(calls) == 1


def test_watched(client):
    channel = channel_doc()
    Channel.save_doc(channel)
    Video.save_doc(video_doc(channel=channel))
    payload = {"id": "vid00000001", "is_watched": True}

    response = client.post("/api/watched/", payload, format="json")

    assert response.status_code == 200
    assert response.data == payload
    assert Video.objects.get().watched is True

    for invalid in ({"id": "vid00000001"}, {"id": "", "is_watched": True}):
        response = client.post("/api/watched/", invalid, format="json")
        assert response.status_code == 400
    assert Video.objects.get().watched is True


def test_search(client):
    channel = channel_doc(channel_name="Coaster Fans")
    Channel.save_doc(channel)
    Video.save_doc(video_doc(channel=channel))

    response = client.get("/api/search/", {"query": "coaster"})

    assert response.status_code == 200
    assert response.data["queryType"] == "simple"
    results = response.data["results"]
    assert [v["youtube_id"] for v in results["video_results"]] == [
        "vid00000001"
    ]
    assert [c["channel_id"] for c in results["channel_results"]] == [
        "UCchannel000000000000001"
    ]


@pytest.mark.parametrize("params", [{}, {"query": ""}, {"query": "   "}])
def test_search_without_query(client, params):
    response = client.get("/api/search/", params)
    assert response.status_code == 400
    assert response.data == {"message": "no search query specified"}


def notification(group: str, idx: int) -> dict:
    """notification message as tasks store them"""
    return {
        "id": f"{group}-{idx}",
        "title": f"{group} task",
        "group": f"{group}:task",
        "api_start": True,
        "api_stop": False,
        "level": "info",
        "messages": ["working"],
        "progress": 0.5,
    }


def test_notifications(client):
    archivist = RedisArchivist()
    archivist.set_message("message:download:1", notification("download", 1))
    archivist.set_message("message:channel:1", notification("channel", 1))

    every = client.get("/api/notification/")
    assert every.status_code == 200
    assert sorted(n["id"] for n in every.data) == ["channel-1", "download-1"]

    filtered = client.get("/api/notification/", {"filter": "download"})
    assert filtered.data == [notification("download", 1)]

    assert client.get("/api/notification/", {"filter": "settings"}).data == []
    invalid = client.get("/api/notification/", {"filter": "other"})
    assert invalid.status_code == 400

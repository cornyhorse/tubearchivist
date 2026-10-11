"""tests for the channel API"""

import pytest
from channel import views
from channel.models import Channel
from channel.src.nav import ChannelNav
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download, DownloadRule
from download.src.rules import set_rule
from playlist.models import Playlist
from rest_framework.test import APIClient
from user.models import Account
from video.models import Video

CH = "UCchannel000000000000001"
OTHER = "UCother00000000000000001"
URL = f"/api/channel/{CH}/"


@pytest.fixture(name="delays")
def fixture_delays(monkeypatch, fake_delay):
    """celery tasks the views start"""
    fakes = {
        "subscribe_to": fake_delay(),
        "index_channel_playlists": fake_delay(),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(views, name, fake)
    return fakes


def add_channels():
    """one subscribed, one not"""
    Channel.save_doc(channel_doc())
    Channel.save_doc(
        channel_doc(
            channel_id=OTHER,
            channel_name="Another",
            channel_subscribed=False,
            channel_banner_url="",
            channel_thumb_url="",
            channel_tvart_url="",
        )
    )


def test_list(client):
    add_channels()

    response = client.get("/api/channel/")

    assert response.status_code == 200
    names = [i["channel_name"] for i in response.data["data"]]
    assert names == ["Another", "Test Channel"]
    assert response.data["data"][0]["channel_banner_url"] is None
    assert response.data["paginate"]["total_hits"] == 2


def test_list_sorts_names_case_insensitive(client):
    add_channels()
    Channel.save_doc(
        channel_doc(channel_id="UClower", channel_name="beta lowercase")
    )

    response = client.get("/api/channel/")

    names = [i["channel_name"] for i in response.data["data"]]
    assert names == ["Another", "beta lowercase", "Test Channel"]


@pytest.mark.parametrize(
    "query_filter, expected",
    [("subscribed", [CH]), ("unsubscribed", [OTHER])],
)
def test_list_filter(client, query_filter, expected):
    add_channels()

    response = client.get("/api/channel/", {"filter": query_filter})

    assert [i["channel_id"] for i in response.data["data"]] == expected


def test_list_empty(client):
    response = client.get("/api/channel/")

    assert response.status_code == 200
    assert response.data["data"] == []


def test_list_post_missing_data(client):
    response = client.post("/api/channel/", {}, format="json")

    assert response.status_code == 400
    assert response.data == {"message": "missing expected data key"}


def test_list_post_subscribe_and_unsubscribe(client, delays):
    add_channels()
    data = {
        "data": [
            {"channel_id": CH, "channel_subscribed": False},
            {"channel_id": OTHER, "channel_subscribed": True},
            {
                "channel_id": "UCnew0000000000000000001",
                "channel_subscribed": 1,
            },
        ]
    }

    response = client.post("/api/channel/", data, format="json")

    assert response.status_code == 200
    assert Channel.objects.get(pk=CH).subscribed is False
    assert delays["subscribe_to"].calls == [
        (
            (f"{OTHER} UCnew0000000000000000001",),
            {"expected_type": "channel"},
        )
    ]


def test_list_post_unsubscribe_only(client, delays):
    add_channels()
    data = {"data": [{"channel_id": CH, "channel_subscribed": False}]}

    client.post("/api/channel/", data, format="json")

    assert not delays["subscribe_to"].calls


def test_list_post_needs_admin(app_config):  # pylint: disable=unused-argument
    user = Account.objects.create_user(name="viewer", password="test")
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user=user)

    assert client.get("/api/channel/").status_code == 200
    response = client.post("/api/channel/", {"data": []}, format="json")
    assert response.status_code == 403


def test_detail_not_found(client):
    response = client.get(URL)

    assert response.status_code == 404
    assert response.data == {"error": "channel not found"}


def test_detail_with_fork_settings(client):
    Channel.save_doc(channel_doc(channel_overwrites={"autodelete_days": 7}))

    response = client.get(URL)

    assert response.status_code == 200
    assert response.data["channel_name"] == "Test Channel"
    assert response.data["emby_numbering"] == "date"
    assert response.data["download_rule"] is None
    overwrites = response.data["channel_overwrites"]
    assert overwrites["autodelete_days"] == 7
    assert overwrites["download_format"] is None


def test_detail_with_download_rule(client):
    Channel.save_doc(channel_doc())
    set_rule(
        CH,
        {
            "enabled": True,
            "min_duration": 60,
            "vid_types": ["videos"],
            "published_after": "2024-01-01",
            "action": "skip",
        },
    )

    response = client.get(URL)

    rule = response.data["download_rule"]
    assert rule["min_duration"] == 60
    assert rule["vid_types"] == ["videos"]
    assert rule["published_after"] == "2024-01-01"
    assert rule["action"] == "skip"


def test_update_not_found(client):
    response = client.post(URL, {}, format="json")

    assert response.status_code == 404


def test_update_invalid(client):
    Channel.save_doc(channel_doc())

    response = client.post(
        URL, {"emby_numbering": "playlist_position"}, format="json"
    )

    assert response.status_code == 400


def test_update_nothing(client, delays):
    Channel.save_doc(channel_doc())

    response = client.post(URL, {}, format="json")

    assert response.status_code == 200
    assert Channel.objects.get().to_doc() == channel_doc()
    assert not delays["index_channel_playlists"].calls


def test_update_all(client, delays):
    Channel.save_doc(channel_doc(channel_subscribed=False))
    data = {
        "channel_subscribed": True,
        "channel_overwrites": {"index_playlists": True, "autodelete_days": 2},
        "emby_numbering": "year_frozen",
        "download_rule": {"max_duration": 600},
    }

    response = client.post(URL, data, format="json")

    assert response.status_code == 200
    channel = Channel.objects.get()
    assert channel.subscribed is True
    assert channel.overwrites == {
        "index_playlists": True,
        "autodelete_days": 2,
    }
    assert channel.emby_numbering == "year_frozen"
    rule = DownloadRule.objects.get(channel_id=CH)
    assert (rule.max_duration, rule.action) == (600, "ignore")
    assert delays["index_channel_playlists"].calls == [((CH,), {})]


def test_update_overwrites_without_playlists(client, delays):
    Channel.save_doc(channel_doc(channel_overwrites={"autodelete_days": 2}))
    data = {"channel_overwrites": {"autodelete_days": None}}

    client.post(URL, data, format="json")

    assert Channel.objects.get().overwrites == {}
    assert not delays["index_channel_playlists"].calls


def test_update_download_rule_null_deletes(client):
    Channel.save_doc(channel_doc())
    set_rule(CH, {"min_duration": 1})

    response = client.post(URL, {"download_rule": None}, format="json")

    assert response.status_code == 200
    assert not DownloadRule.objects.exists()


def test_delete(client):
    Channel.save_doc(channel_doc())

    assert client.delete(URL).status_code == 204
    assert not Channel.objects.exists()


def test_delete_not_found(client):
    response = client.delete(URL)

    assert response.status_code == 404
    assert response.data == {"error": "channel not found"}


def test_aggs(client):
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(media_size=1000, player={"duration": 60}))
    Video.save_doc(
        video_doc(
            youtube_id="vid00000002",
            media_size=500,
            player={"duration": 3600},
        )
    )

    response = client.get(f"{URL}aggs/")

    assert response.data == {
        "total_items": {"value": 2},
        "total_size": {"value": 1500},
        "total_duration": {"value": 3660, "value_str": "1h 01m"},
    }


def test_aggs_empty(client):
    response = client.get(f"{URL}aggs/")

    assert response.data["total_items"] == {"value": 0}
    assert response.data["total_duration"]["value"] == 0


def test_nav(client):
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc())
    Video.save_doc(video_doc(youtube_id="vid00000002", vid_type="shorts"))
    Download.upsert_doc(download_doc(youtube_id="dl1"))
    Playlist.save_doc(playlist_doc("PL1", []))

    response = client.get(f"{URL}nav/")

    assert response.data == {
        "has_pending": True,
        "has_ignored": False,
        "has_playlists": True,
        "has_videos": True,
        "has_streams": False,
        "has_shorts": True,
    }


def test_nav_ignored_only(db):  # pylint: disable=unused-argument
    Download.upsert_doc(download_doc(youtube_id="dl1", status="ignore"))

    nav = ChannelNav(CH).get_nav()

    assert nav["has_pending"] is False
    assert nav["has_ignored"] is True
    assert nav["has_playlists"] is False


def test_search_missing_q(client):
    response = client.get("/api/channel/search/")

    assert response.status_code == 400


def test_search_invalid(client):
    response = client.get("/api/channel/search/", {"q": "nope"})

    assert response.status_code == 404
    assert response.data == {"error": "channel not found: nope"}


def test_search_not_a_channel(client):
    response = client.get("/api/channel/search/", {"q": "vid00000001"})

    assert response.status_code == 400
    assert response.data == {"error": "expected channel data"}


def test_search_not_indexed(client):
    response = client.get("/api/channel/search/", {"q": CH})

    assert response.status_code == 404


def test_search_found(client):
    Channel.save_doc(channel_doc())

    response = client.get(
        "/api/channel/search/",
        {"q": f"https://www.youtube.com/channel/{CH}"},
    )

    assert response.status_code == 200
    assert response.data["channel_id"] == CH


def test_delete_and_numbering_queue_emby_write(client, monkeypatch):
    """review T2: the Emby tree follows deletes and numbering changes"""
    # pylint: disable=import-outside-toplevel
    from jobs.models import Job
    from jobs.src import handlers

    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    add_channels()
    client.post(URL, {"emby_numbering": "year_frozen"}, format="json")
    assert Job.objects.filter(type="emby_write").count() == 1
    Job.objects.update(status="done")

    assert client.delete(URL).status_code == 204
    assert Job.objects.filter(type="emby_write", status="queued").exists()


def _table_data():
    """channels with videos, found items and a rule"""
    add_channels()
    Channel.save_doc(channel_doc(channel_id="UCthird", channel_name="Third"))
    Video.save_doc(video_doc(youtube_id="v1", published=1700000000))
    Video.save_doc(video_doc(youtube_id="v2", published=1600000000))
    Download.upsert_doc(download_doc(youtube_id="q1", channel_id=OTHER))
    Download.upsert_doc(download_doc(youtube_id="q2", channel_id=OTHER))
    Download.upsert_doc(
        download_doc(youtube_id="e1", channel_id=CH, message="HTTP 410")
    )
    Download.upsert_doc(
        download_doc(youtube_id="i1", channel_id=CH, status="ignore")
    )
    set_rule(CH, {"min_duration": 600})


def table(client, **params):
    """GET /api/channel/ rows"""
    response = client.get("/api/channel/", params)
    assert response.status_code == 200, response.data
    return response.json()["data"]


def test_table_row(client):
    _table_data()

    row = next(r for r in table(client) if r["channel_id"] == CH)

    assert row["counts"] == {
        "downloaded": 2,
        "queued": 0,
        "ignored": 1,
        "errors": 1,
    }
    assert row["latest_upload"].startswith("2023-11-14")
    assert row["download_rule"]["min_duration"] == 600
    assert row["rule_summary"] == ">10 min"
    assert row["emby_numbering"] == "date"
    other = next(r for r in table(client) if r["channel_id"] == OTHER)
    assert other["counts"]["queued"] == 2
    assert other["latest_upload"] is None
    assert other["download_rule"] is None
    assert other["rule_summary"] is None


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"q": "anoth"}, [OTHER]),
        ({"q": "@third"}, ["UCthird"]),
        ({"q": "UCchannel"}, [CH]),
        ({"has_queued": "true"}, [OTHER]),
        ({"has_queued": "false"}, [CH, "UCthird"]),
        ({"has_errors": "true"}, [CH]),
        ({"has_rule": "true"}, [CH]),
        ({"has_rule": "false", "filter": "subscribed"}, ["UCthird"]),
        ({"sort": "queued", "order": "desc"}, [OTHER, CH, "UCthird"]),
        ({"sort": "downloaded", "order": "desc"}, [CH, OTHER, "UCthird"]),
        ({"sort": "latest_upload", "order": "desc"}, [CH, OTHER, "UCthird"]),
        ({"sort": "latest_upload"}, [CH, OTHER, "UCthird"]),
        ({"sort": "name", "order": "desc"}, ["UCthird", CH, OTHER]),
        ({"sort": "subscribed"}, [OTHER, CH, "UCthird"]),
        ({"sort": "ignored", "order": "desc"}, [CH, OTHER, "UCthird"]),
        ({"sort": "last_checked"}, [OTHER, CH, "UCthird"]),
    ],
)
def test_table_filters_and_sort(client, params, expected):
    _table_data()
    assert [r["channel_id"] for r in table(client, **params)] == expected


def test_table_page_size(client):
    _table_data()
    response = client.get("/api/channel/", {"page_size": 2, "page": 2})
    data = response.json()
    assert [r["channel_id"] for r in data["data"]] == ["UCthird"]
    assert data["paginate"]["page_size"] == 2
    too_big = client.get("/api/channel/", {"page_size": 101})
    assert too_big.status_code == 400

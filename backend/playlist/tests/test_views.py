"""tests for the playlist API"""

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc, video_doc
from playlist import views
from playlist.models import Playlist, PlaylistEntry
from user.src.user_config import UserConfig
from video.models import Video

CH = "UCchannel000000000000001"
PL = "PLtest"
CUSTOM = "TA_playlist_custom"
LIST = "/api/playlist/"


@pytest.fixture(name="subscribe_to")
def fixture_subscribe_to(monkeypatch, fake_delay):
    """celery subscribe task"""
    fake = fake_delay()
    monkeypatch.setattr(views, "subscribe_to", fake)
    return fake


def add_videos(*youtube_ids, watched=()):
    """index channel and videos"""
    Channel.save_doc(channel_doc())
    for youtube_id in youtube_ids:
        Video.save_doc(
            video_doc(
                youtube_id=youtube_id,
                player={"duration": 10, "watched": youtube_id in watched},
            )
        )


def add_playlists():
    """regular subscribed, regular of other channel, custom"""
    Playlist.save_doc(
        playlist_doc(PL, ["vid00000001"], playlist_subscribed=True)
    )
    Playlist.save_doc(
        playlist_doc(
            "PLother",
            [],
            playlist_name="Another",
            playlist_channel_id="UCother00000000000000001",
        )
    )
    Playlist.save_doc(
        playlist_doc(
            CUSTOM,
            [],
            playlist_name="Mine",
            playlist_type="custom",
            playlist_channel="",
            playlist_channel_id="",
        )
    )


def ids(response) -> list[str]:
    """playlist ids of a list response"""
    return [i["playlist_id"] for i in response.data["data"]]


def test_list(client):
    add_playlists()

    response = client.get(LIST)

    assert response.status_code == 200
    assert ids(response) == ["PLother", CUSTOM, PL]
    first = response.data["data"][0]
    assert first["playlist_thumbnail"].endswith("/playlists/PLother.jpg")
    assert response.data["paginate"]["total_hits"] == 3


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"channel": CH}, [PL]),
        ({"subscribed": "true"}, [PL]),
        ({"subscribed": "false"}, ["PLother", CUSTOM]),
        ({"type": "custom"}, [CUSTOM]),
        ({"type": "regular", "channel": CH}, [PL]),
    ],
)
def test_list_filters(client, params, expected):
    add_playlists()

    assert ids(client.get(LIST, params)) == expected


def test_list_invalid_type(client):
    response = client.get(LIST, {"type": "bogus"})

    assert response.status_code == 400


def test_list_query_builder_error(client, monkeypatch):
    def fail(self):
        raise ValueError("bad query")

    monkeypatch.setattr(views.QueryBuilder, "build_queryset", fail)

    response = client.get(LIST)

    assert response.status_code == 400
    assert response.data == {"error": "bad query"}


def test_list_subscribe(client, subscribe_to):
    data = {
        "data": [
            {"playlist_id": "PLa", "playlist_subscribed": True},
            {"playlist_id": "PLb", "playlist_subscribed": True},
        ]
    }

    response = client.post(LIST, data, format="json")

    assert response.status_code == 200
    assert response.data == {
        "message": "playlist subscribe task started",
        "task_id": "task-id-1",
    }
    assert subscribe_to.calls == [
        (("PLa PLb",), {"expected_type": "playlist"})
    ]


def test_list_subscribe_nothing(client, subscribe_to):
    response = client.post(LIST, {"data": []}, format="json")

    assert response.status_code == 400
    assert response.data == {"error": "nothing to subscribe"}
    assert not subscribe_to.calls


def test_create_custom(client, thumbs):
    response = client.post(
        f"{LIST}custom/", {"playlist_name": "Favorites"}, format="json"
    )

    assert response.status_code == 200
    playlist_id = response.data["playlist_id"]
    assert playlist_id.startswith("TA_playlist_")
    assert response.data["playlist_name"] == "Favorites"
    assert response.data["playlist_type"] == "custom"
    assert response.data["playlist_description"] is None
    assert response.data["playlist_entries"] == []
    assert Playlist.objects.get().pk == playlist_id
    assert thumbs.calls == [(playlist_id, "playlist", False)]


def test_custom_not_found(client):
    data = {"action": "create", "video_id": "vid00000001"}

    response = client.post(f"{LIST}custom/{CUSTOM}/", data, format="json")

    assert response.status_code == 404


def test_custom_on_regular_playlist(client):
    add_playlists()
    data = {"action": "create", "video_id": "vid00000001"}

    response = client.post(f"{LIST}custom/{PL}/", data, format="json")

    assert response.status_code == 400
    assert response.data == {"error": f"playlist with ID {PL} is not custom"}


def test_custom_add_video(client):
    add_videos("vid00000001")
    add_playlists()
    data = {"action": "create", "video_id": "vid00000001"}

    response = client.post(f"{LIST}custom/{CUSTOM}/", data, format="json")

    assert response.status_code == 200
    assert response.data["playlist_entries"][0]["youtube_id"] == (
        "vid00000001"
    )
    assert response.data["playlist_entries"][0]["downloaded"] is True


def test_custom_add_video_not_indexed(client):
    add_playlists()
    data = {"action": "create", "video_id": "vid00000009"}

    response = client.post(f"{LIST}custom/{CUSTOM}/", data, format="json")

    assert response.status_code == 400
    assert response.data == {
        "error": "failed to add video vid00000009 to playlist"
    }


def test_custom_move_hides_watched(client, admin):
    entries = ["vid0000000a", "vid0000000b", "vid0000000c"]
    add_videos(*entries, watched=["vid0000000b"])
    Playlist.save_doc(playlist_doc(CUSTOM, entries, playlist_type="custom"))
    UserConfig(admin.id).set_value("hide_watched", True)
    data = {"action": "up", "video_id": "vid0000000c"}

    response = client.post(f"{LIST}custom/{CUSTOM}/", data, format="json")

    assert response.status_code == 200
    order = [i["youtube_id"] for i in response.data["playlist_entries"]]
    assert order == ["vid0000000c", "vid0000000a", "vid0000000b"]


def test_custom_move_missing_video(client):
    add_playlists()
    data = {"action": "remove", "video_id": "vid00000009"}

    response = client.post(f"{LIST}custom/{CUSTOM}/", data, format="json")

    assert response.status_code == 400
    assert response.data == {"error": "video vid00000009 not in playlist"}


def test_detail(client):
    add_playlists()
    Playlist.objects.filter(pk=PL).update(emby_numbering="date")

    response = client.get(f"{LIST}{PL}/")

    assert response.status_code == 200
    assert response.data["playlist_id"] == PL
    assert response.data["emby_numbering"] == "date"
    assert response.data["playlist_entries"][0]["downloaded"] is False


def test_detail_not_found(client):
    response = client.get(f"{LIST}{PL}/")

    assert response.status_code == 404
    assert response.data == {"error": "playlist not found"}


def test_update_not_found(client):
    response = client.post(
        f"{LIST}{PL}/", {"playlist_subscribed": True}, format="json"
    )

    assert response.status_code == 404


def test_update_custom(client):
    add_playlists()

    response = client.post(
        f"{LIST}{CUSTOM}/", {"playlist_subscribed": True}, format="json"
    )

    assert response.status_code == 400
    assert response.data == {"error": f"playlist with ID {CUSTOM} is custom"}


def test_update_nothing(client):
    add_playlists()

    response = client.post(f"{LIST}{PL}/", {}, format="json")

    assert response.status_code == 400


def test_update_subscribe_and_sort(client):
    add_playlists()
    data = {"playlist_subscribed": False, "playlist_sort_order": "bottom"}

    response = client.post(f"{LIST}{PL}/", data, format="json")

    assert response.status_code == 200
    assert response.data["playlist_sort_order"] == "bottom"
    playlist = Playlist.objects.get(pk=PL)
    assert (playlist.subscribed, playlist.sort_order) == (False, "bottom")


def test_update_subscribe_only(client):
    add_playlists()

    response = client.post(
        f"{LIST}{PL}/", {"playlist_subscribed": False}, format="json"
    )

    assert response.status_code == 200
    assert response.data["playlist_subscribed"] is False


def test_update_emby_numbering(client):
    add_playlists()

    response = client.post(
        f"{LIST}{PL}/", {"emby_numbering": "absolute_frozen"}, format="json"
    )

    assert response.status_code == 200
    assert response.data["emby_numbering"] == "absolute_frozen"
    assert Playlist.objects.get(pk=PL).emby_numbering == "absolute_frozen"


def test_delete_metadata_only(client):
    add_videos("vid00000001")
    add_playlists()

    response = client.delete(f"{LIST}{PL}/")

    assert response.status_code == 204
    assert not Playlist.objects.filter(pk=PL).exists()
    assert not PlaylistEntry.objects.filter(playlist_id=PL).exists()
    assert Video.objects.exists()


def test_delete_with_videos(client):
    add_videos("vid00000001")
    add_playlists()

    response = client.delete(f"{LIST}{PL}/?delete_videos=true")

    assert response.status_code == 204
    assert not Playlist.objects.filter(pk=PL).exists()
    assert not Video.objects.exists()


def test_auto_download_read_update_filter(client):
    add_playlists()
    assert client.get(f"{LIST}{PL}/").data["auto_download"] is False

    response = client.post(
        f"{LIST}{PL}/", {"auto_download": True}, format="json"
    )

    assert response.status_code == 200
    assert response.data["auto_download"] is True
    assert Playlist.objects.get(pk=PL).auto_download is True
    assert ids(client.get(LIST, {"auto_download": "true"})) == [PL]
    assert ids(client.get(LIST, {"auto_download": "false"})) == [
        "PLother",
        CUSTOM,
    ]


def test_subscribe_keeps_auto_download_off(client):
    """subscribing never opts in unless the default says so"""
    add_playlists()

    client.post(
        f"{LIST}PLother/", {"playlist_subscribed": True}, format="json"
    )

    assert Playlist.objects.get(pk="PLother").auto_download is False


def test_emby_tags_read_update_filter(client):
    add_playlists()
    assert client.get(f"{LIST}{PL}/").data["emby_tags"] == []

    response = client.post(
        f"{LIST}{PL}/", {"emby_tags": ["David"]}, format="json"
    )

    assert response.status_code == 200
    assert response.data["emby_tags"] == ["David"]
    assert Playlist.objects.get(pk=PL).emby_tags == ["David"]
    assert ids(client.get(LIST, {"emby_tag": "David"})) == [PL]
    assert Playlist.objects.get(pk=PL).to_doc()["emby_tags"] == ["David"]


def test_emby_playlist_read_and_update(client):
    add_playlists()
    assert client.get(f"{LIST}{PL}/").data["emby_playlist"] is True

    response = client.post(
        f"{LIST}{PL}/", {"emby_playlist": False}, format="json"
    )

    assert response.status_code == 200
    assert response.data["emby_playlist"] is False
    assert Playlist.objects.get(pk=PL).emby_playlist is False
    assert Playlist.objects.get(pk=PL).to_doc()["emby_playlist"] is False

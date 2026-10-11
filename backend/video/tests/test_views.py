"""test the video api views"""

import pytest
from common.tests.docs import playlist_doc, video_doc
from django.db import connection
from django.test.utils import CaptureQueriesContext
from playlist.models import Playlist
from rest_framework.test import APIClient
from video.models import Video
from video.src.comments import Comments

pytestmark = pytest.mark.django_db

YOUTUBE_ID = "vid00000001"
OTHER_ID = "vid00000002"

COMMENT = {
    "comment_id": "c1",
    "comment_parent": "root",
    "comment_author": "someone",
    "comment_author_id": "UCsomeone",
    "comment_author_is_uploader": False,
    "comment_author_thumbnail": "https://yt3.example/a",
    "comment_text": "nice ride",
    "comment_time_text": "1 year ago",
    "comment_timestamp": 1785000000,
    "comment_likecount": 3,
    "comment_is_favorited": True,
}


@pytest.fixture(name="user")
def fixture_user(django_user_model):
    return django_user_model.objects.create_superuser(
        name="admin", password="test-password"
    )


@pytest.fixture(name="client")
def fixture_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(name="videos")
def fixture_videos(video, redis):
    """two similar videos, redis cleaned"""
    del redis
    Video.save_doc(video_doc(youtube_id=OTHER_ID, published=1600000000))
    return video


def _progress_key(user, youtube_id=YOUTUBE_ID):
    return f"{user.id}:progress:{youtube_id}"


class TestList:
    """GET /api/video/"""

    def test_list(self, client, user, videos, redis):
        del videos
        redis.set_message(
            _progress_key(user),
            {"youtube_id": YOUTUBE_ID, "position": 229, "watched": False},
        )
        response = client.get("/api/video/", {"sort": "published"})

        assert response.status_code == 200
        data = response.json()
        assert [i["youtube_id"] for i in data["data"]] == [
            YOUTUBE_ID,
            OTHER_ID,
        ]
        assert data["data"][0]["player"]["progress"] == 50
        assert data["paginate"]["total_hits"] == 2

    def test_list_loads_playlists_once(self, client, videos):
        """no PlaylistEntry query per video on a page"""
        del videos
        for i in range(3, 8):
            Video.save_doc(video_doc(youtube_id=f"vid0000000{i}"))
        Playlist.save_doc(playlist_doc("PL1", [YOUTUBE_ID, OTHER_ID]))

        with CaptureQueriesContext(connection) as context:
            response = client.get("/api/video/", {"sort": "published"})

        assert response.json()["paginate"]["total_hits"] == 7
        entry_queries = [
            q for q in context.captured_queries if "playlistentry" in q["sql"]
        ]
        assert len(entry_queries) == 1

    def test_continue_nothing(self, client, videos):
        del videos
        response = client.get("/api/video/", {"watch": "continue"})
        assert response.status_code == 200
        assert response.json() == []

    def test_bad_query(self, client):
        response = client.get("/api/video/", {"sort": "nope"})
        assert response.status_code == 400


class TestVideo:
    """GET and DELETE /api/video/<id>/"""

    def test_get(self, client, videos):
        del videos
        response = client.get(f"/api/video/{YOUTUBE_ID}/")
        assert response.status_code == 200
        assert response.json()["youtube_id"] == YOUTUBE_ID

    def test_get_missing(self, client, redis):
        del redis
        response = client.get("/api/video/missing0000/")
        assert response.status_code == 404
        assert response.json() == {"error": "video not found"}

    def test_delete(self, client, app_config, videos, dirs):
        del app_config, videos, dirs
        response = client.delete(f"/api/video/{YOUTUBE_ID}/")
        assert response.status_code == 204
        assert not Video.objects.filter(pk=YOUTUBE_ID).exists()

    def test_delete_missing(self, client, app_config):
        del app_config
        response = client.delete("/api/video/missing0000/")
        assert response.status_code == 404


class TestComments:
    """GET /api/video/<id>/comment/"""

    def test_get(self, client, video):
        reply = dict(COMMENT, comment_id="c2", comment_parent="c1")
        comments = Comments(video.youtube_id)
        comments.json_data = {
            "comment_comments": [COMMENT, reply],
            "comment_last_refresh": 1785000000,
        }
        comments.upload_comments()

        response = client.get(f"/api/video/{YOUTUBE_ID}/comment/")
        assert response.status_code == 200
        data = response.json()
        assert [i["comment_id"] for i in data] == ["c1"]
        assert data[0]["comment_replies"][0]["comment_id"] == "c2"

    def test_missing(self, client):
        response = client.get(f"/api/video/{YOUTUBE_ID}/comment/")
        assert response.status_code == 404


class TestNav:
    """GET /api/video/<id>/nav/"""

    def test_missing(self, client):
        response = client.get("/api/video/missing0000/nav/")
        assert response.status_code == 404

    def test_no_playlist(self, client, videos):
        del videos
        response = client.get(f"/api/video/{YOUTUBE_ID}/nav/")
        assert response.status_code == 200
        assert response.json() == []

    def test_nav(self, client, app_config, videos):
        del app_config, videos
        Playlist.save_doc(playlist_doc("PL1", [YOUTUBE_ID, OTHER_ID]))
        # only this video is downloaded: no nav
        Playlist.save_doc(playlist_doc("PL2", [YOUTUBE_ID, "missing0000"]))

        response = client.get(f"/api/video/{YOUTUBE_ID}/nav/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["playlist_meta"]["playlist_id"] == "PL1"
        assert data[0]["playlist_previous"] is None
        assert data[0]["playlist_next"]["youtube_id"] == OTHER_ID


class TestProgress:
    """POST and DELETE /api/video/<id>/progress/"""

    URL = f"/api/video/{YOUTUBE_ID}/progress/"

    def test_missing(self, client, redis):
        del redis
        response = client.post("/api/video/missing0000/progress/", {})
        assert response.status_code == 404

    def test_short_position_not_stored(self, client, user, videos, redis):
        del videos
        response = client.post(self.URL, {"position": 3}, format="json")
        assert response.status_code == 200
        assert response.json()["position"] == 3
        assert not redis.get_message_dict(_progress_key(user))

    def test_store_and_delete(self, client, user, videos, redis):
        del videos
        response = client.post(self.URL, {"position": 100}, format="json")
        assert response.json()["watched"] is False
        stored = redis.get_message_dict(_progress_key(user))
        assert stored["position"] == 100

        response = client.delete(self.URL)
        assert response.status_code == 204
        assert not redis.get_message_dict(_progress_key(user))

    def test_watched(self, client, user, videos, redis):
        del videos
        response = client.post(self.URL, {"position": 450}, format="json")
        assert response.json()["watched"] is True
        assert Video.objects.get(pk=YOUTUBE_ID).watched is True
        key = redis.NAME_SPACE + _progress_key(user)
        assert 0 < redis.conn.ttl(key) <= 60

        # stored watched progress stays watched
        response = client.post(self.URL, {"position": 10}, format="json")
        assert response.json()["watched"] is True


def test_similar(client, videos):
    del videos
    response = client.get(f"/api/video/{YOUTUBE_ID}/similar/")
    assert response.status_code == 200
    assert [i["youtube_id"] for i in response.json()] == [OTHER_ID]

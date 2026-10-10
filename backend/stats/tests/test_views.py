"""stats API views"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from stats.tests.conftest import CHANNEL_A, CHANNEL_B, CHANNEL_C
from video.models import Video

pytestmark = pytest.mark.django_db

ENDPOINTS = [
    "video",
    "channel",
    "playlist",
    "download",
    "watch",
    "downloadhist",
    "biggestchannels",
]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_auth_required(endpoint):
    response = APIClient().get(f"/api/stats/{endpoint}/")
    assert response.status_code in (401, 403)


def test_video(client, library):
    del library
    data = client.get("/api/stats/video/").data
    assert data["doc_count"] == 5
    assert data["duration_str"] == "1h 07m 40s"
    assert data["type_shorts"] == {
        "doc_count": 1,
        "media_size": 500,
        "duration": 50,
        "duration_str": "50s",
    }
    assert data["active_false"]["media_size"] == 3000


def test_video_empty(client):
    assert client.get("/api/stats/video/").data == {
        "doc_count": 0,
        "media_size": 0,
        "duration": 0,
        "duration_str": "NA",
        "type_videos": None,
        "type_shorts": None,
        "type_streams": None,
        "active_true": None,
        "active_false": None,
    }


def test_channel_and_playlist(client, library):
    del library
    assert client.get("/api/stats/channel/").data == {
        "doc_count": 3,
        "active_true": 2,
        "active_false": 1,
        "subscribed_true": 1,
        "subscribed_false": 2,
    }
    assert client.get("/api/stats/playlist/").data == {
        "doc_count": 2,
        "active_true": 2,
        "active_false": None,
        "subscribed_false": 1,
        "subscribed_true": 1,
    }


def test_download(client, library):
    del library
    assert client.get("/api/stats/download/").data == {
        "pending": 3,
        "ignore": 1,
        "pending_videos": 2,
        "pending_shorts": 1,
        "pending_streams": None,
    }


def test_watch(client, library):
    del library
    data = client.get("/api/stats/watch/").data
    assert data["total"] == {
        "duration": 4060,
        "duration_str": "1h 07m 40s",
        "items": 5,
    }
    assert data["watched"]["items"] == 2
    assert data["unwatched"]["progress"] == pytest.approx(310 / 4060)


def test_download_hist(client, library):
    del library
    assert client.get("/api/stats/downloadhist/").data == []

    now = timezone.now()
    Video.objects.filter(pk="vid00000001").update(date_downloaded=now)
    assert client.get("/api/stats/downloadhist/").data == [
        {"date": now.date().isoformat(), "count": 1, "media_size": 1000}
    ]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", [CHANNEL_A, CHANNEL_B, CHANNEL_C]),
        ("?order=duration", [CHANNEL_B, CHANNEL_A, CHANNEL_C]),
        ("?order=media_size", [CHANNEL_C, CHANNEL_B, CHANNEL_A]),
    ],
)
def test_biggest_channels(client, library, query, expected):
    del library
    data = client.get(f"/api/stats/biggestchannels/{query}").data
    assert [row["id"] for row in data] == expected
    assert {row["name"] for row in data} == {
        "Coaster Fans",
        "Rail Adventures",
        "Big Files",
    }


def test_biggest_channels_bad_order(client):
    response = client.get("/api/stats/biggestchannels/?order=title")
    assert response.status_code == 400

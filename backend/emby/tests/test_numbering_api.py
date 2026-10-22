"""numbering: manual numbers, scheme previews and the API"""

from datetime import date, datetime, timezone

import pytest

from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc, video_doc
from emby.models import EpisodeNumber
from emby.src import numbering_api
from emby.src.nfo import SeriesInfo
from emby.src.numbering import NumberingItem
from emby.src.sync import MANUAL, ensure_numbers, manual_numbers
from jobs.models import Job
from playlist.models import Playlist
from task.tests.helpers import fixture_admin_client  # noqa: F401
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"


def item(youtube_id, day, idx=None):
    """numbering item published on 2025-01-<day>"""
    return NumberingItem(youtube_id, date(2025, 1, day), idx)


def manual(youtube_id, season, episode, kind="channel", series="UC1"):
    """number set by hand"""
    EpisodeNumber.objects.create(
        series_kind=kind,
        series_id=series,
        scheme=MANUAL,
        youtube_id=youtube_id,
        season=season,
        episode=episode,
    )


def numbers(kind, series_id, scheme, items, dry_run=False):
    """ensure_numbers with plain titles"""
    info = SeriesInfo(series_id=series_id, title=f"Series {series_id}")
    titles = {i.youtube_id: f"Title {i.youtube_id}" for i in items}
    return ensure_numbers(kind, info, scheme, items, titles, dry_run)


def test_frozen_keeps_manual():
    items = [item("a", 1), item("b", 2), item("c", 3)]
    found = numbers("channel", "UC1", "absolute_frozen", items)
    assert found.numbers == {"a": (1, 1), "b": (1, 2), "c": (1, 3)}

    # b set by hand to a's slot: a moves, b keeps its manual number
    manual("b", 1, 1)
    found = numbers(
        "channel", "UC1", "absolute_frozen", items + [item("d", 4)]
    )
    assert found.numbers == {
        "a": (1, 4),
        "b": (1, 1),
        "c": (1, 3),
        "d": (1, 5),
    }
    stored = dict(
        EpisodeNumber.objects.filter(scheme="absolute_frozen").values_list(
            "youtube_id", "episode"
        )
    )
    assert stored == {"a": 4, "c": 3, "d": 5}
    # stable on the next run
    again = numbers(
        "channel", "UC1", "absolute_frozen", items + [item("d", 4)]
    )
    assert again.numbers == found.numbers


def test_non_frozen_moves_collisions():
    items = [item("a", 1, 0), item("b", 2, 1), item("c", 3, 2)]
    manual("c", 1, 1, kind="playlist", series="PL1")

    found = numbers("playlist", "PL1", "playlist_position", items)

    assert found.numbers == {"c": (1, 1), "a": (1, 2), "b": (1, 3)}
    assert found.warnings == [
        "a: E1 is set by hand on another video, numbered E2",
        "b: E2 is set by hand on another video, numbered E3",
    ]
    stored = EpisodeNumber.objects.filter(scheme="playlist_position")
    assert stored.count() == 2


def test_manual_numbers_helper():
    manual("x", 3, 4)
    assert manual_numbers("channel", "UC1") == {"x": (3, 4)}


def published(day):
    """epoch of 2025-01-<day>"""
    return int(datetime(2025, 1, day, 12, tzinfo=timezone.utc).timestamp())


@pytest.fixture(name="series")
def fixture_series(monkeypatch):
    """a channel with three videos, a playlist with two of them"""
    monkeypatch.setattr(numbering_api, "paths_from_env", lambda: "paths")
    Channel.save_doc(channel_doc(emby_numbering="absolute_frozen"))
    for day, youtube_id in [(1, "vid1"), (2, "vid2"), (3, "vid3")]:
        Video.save_doc(
            video_doc(
                youtube_id=youtube_id,
                title=f"Video {day}",
                published=published(day),
            )
        )
    Playlist.save_doc(
        playlist_doc(
            "PL1", ["vid3", "vid1"], emby_numbering="playlist_position"
        )
    )


def test_schemes(admin_client):
    schemes = admin_client.get("/api/numbering/schemes/").json()
    keys = {s["key"]: s for s in schemes}
    assert keys["date"]["frozen"] and not keys["date"]["playlist_only"]
    assert keys["playlist_position"]["playlist_only"]
    assert keys["playlist_frozen"]["frozen"]
    assert keys["date"]["example"] == "S2025E6200"


def test_series_numbering(admin_client, series):
    # pylint: disable=unused-argument
    data = admin_client.get(f"/api/numbering/channel/{CHANNEL_ID}/").json()

    assert (data["scheme"], data["episodes"], data["manual"]) == (
        "absolute_frozen",
        3,
        0,
    )
    assert [s["name"] for s in data["sample"]] == [
        "S01E03 - Video 3",
        "S01E02 - Video 2",
        "S01E01 - Video 1",
    ]
    playlist = admin_client.get("/api/numbering/playlist/PL1/").json()
    assert [s["youtube_id"] for s in playlist["sample"]] == ["vid3", "vid1"]
    assert admin_client.get("/api/numbering/channel/UCx/").status_code == 404
    assert admin_client.get("/api/numbering/odd/x/").status_code == 404


def test_preview_and_change_scheme(admin_client, series):
    # pylint: disable=unused-argument
    url = f"/api/numbering/channel/{CHANNEL_ID}/"

    preview = admin_client.post(
        f"{url}preview/", {"scheme": "year_frozen"}, format="json"
    ).json()
    assert (preview["episodes"], preview["renumbers"]) == (3, 3)
    first = preview["sample"][0]
    assert (first["old_name"], first["name"]) == (
        "S01E03 - Video 3",
        "S2025E03 - Video 3",
    )

    same = admin_client.post(
        f"{url}preview/", {"scheme": "absolute_frozen"}, format="json"
    ).json()
    assert (same["renumbers"], same["sample"]) == (0, [])

    bad = admin_client.post(
        f"{url}preview/", {"scheme": "playlist_position"}, format="json"
    )
    assert bad.status_code == 400
    assert (
        admin_client.put(
            url, {"scheme": "playlist_position"}, format="json"
        ).status_code
        == 400
    )
    missing = admin_client.post(
        "/api/numbering/channel/UCx/preview/", {"scheme": "date"}
    )
    assert missing.status_code == 404
    assert (
        admin_client.put(
            "/api/numbering/channel/UCx/", {"scheme": "date"}
        ).status_code
        == 404
    )

    changed = admin_client.put(
        url, {"scheme": "year_frozen"}, format="json"
    ).json()
    assert changed["renumbered"] == 3
    assert Job.objects.get(pk=changed["job_id"]).type == "emby_write"
    assert Channel.objects.get().emby_numbering == "year_frozen"


def test_change_scheme_without_emby(admin_client, series, monkeypatch):
    # pylint: disable=unused-argument
    monkeypatch.setattr(numbering_api, "paths_from_env", lambda: None)
    changed = admin_client.put(
        "/api/numbering/playlist/PL1/", {"scheme": "date"}, format="json"
    ).json()
    assert changed["job_id"] is None


def test_video_numbers(admin_client, series):
    # pylint: disable=unused-argument
    Playlist.save_doc(playlist_doc("PL2", ["other"]))
    data = admin_client.get("/api/numbering/video/vid1/").json()["data"]

    assert [(d["series_kind"], d["series_id"]) for d in data] == [
        ("channel", CHANNEL_ID),
        ("playlist", "PL1"),
    ]
    assert data[1]["name"] == "S01E02 - Video 1"
    assert data[0]["manual"] is False
    assert admin_client.get("/api/numbering/video/nope/").status_code == 404

    # vid2 is in PL2's entries list only as another id
    Playlist.save_doc(playlist_doc("PL3", ["vid2"]))
    Video.objects.filter(pk="vid2").update(active=False)
    only = admin_client.get("/api/numbering/video/vid2/").json()["data"]
    assert only == []


def test_manual_number(admin_client, series):
    # pylint: disable=unused-argument
    url = "/api/numbering/video/vid1/"
    body = {
        "series_kind": "channel",
        "series_id": CHANNEL_ID,
        "season": 7,
        "episode": 1,
    }

    response = admin_client.put(url, body, format="json")

    assert response.status_code == 200
    assert response.json()["name"] == "S07E01 - Video 1"
    assert response.json()["manual"] is True
    numbers = admin_client.get(f"/api/numbering/channel/{CHANNEL_ID}/")
    assert numbers.json()["manual"] == 1
    assert Job.objects.filter(type="emby_write").exists()

    # a scheme change keeps it
    Channel.objects.update(emby_numbering="year_frozen")
    kept = admin_client.get(url).json()["data"][0]
    assert (kept["season"], kept["episode"]) == (7, 1)

    taken = admin_client.put(
        "/api/numbering/video/vid2/",
        {**body, "season": 7, "episode": 1},
        format="json",
    )
    assert taken.status_code == 409
    assert "belongs to vid1" in taken.json()["error"]
    elsewhere = admin_client.put(
        "/api/numbering/video/vid2/",
        {
            "series_kind": "playlist",
            "series_id": "PL1",
            "season": 1,
            "episode": 9,
        },
        format="json",
    )
    assert elsewhere.status_code == 404
    unknown = admin_client.put(
        url, {**body, "series_id": "UCx"}, format="json"
    )
    assert unknown.status_code == 404

    delete = f"{url}?series_kind=channel&series_id={CHANNEL_ID}"
    assert admin_client.delete(delete).status_code == 204
    assert admin_client.delete(delete).status_code == 404

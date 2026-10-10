"""tests for formatting stored documents for the frontend"""

import pytest
from common.src.env_settings import EnvironmentSettings
from common.src.search_processor import SearchProcess
from common.src.ta_redis import RedisArchivist
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)

CACHE = EnvironmentSettings().get_cache_root()
MEDIA = EnvironmentSettings().get_media_root()
CHANNEL_ID = "UCchannel000000000000001"


def test_channel():
    processed = SearchProcess().process_one("ta_channel", channel_doc(), 1.234)

    assert processed["_index"] == "ta_channel"
    assert processed["_score"] == 1.23
    assert processed["channel_last_refresh"] == "2026-07-30T13:24:38+00:00"
    art = f"{CACHE}/channels/{CHANNEL_ID}"
    assert processed["channel_banner_url"] == f"{art}_banner.jpg"
    assert processed["channel_thumb_url"] == f"{art}_thumb.jpg"
    assert processed["channel_tvart_url"] == f"{art}_tvart.jpg"
    assert list(processed)[:-2] == sorted(processed)[2:]


def test_channel_without_art():
    doc = channel_doc(
        channel_banner_url="", channel_thumb_url=None, channel_tvart_url=""
    )
    del doc["channel_description"]
    processed = SearchProcess().process_one("ta_channel", doc)

    assert processed["channel_banner_url"] is None
    assert processed["channel_thumb_url"] is None
    assert processed["channel_tvart_url"] is None
    assert processed["channel_description"] is None
    assert processed["_score"] == 0


def test_video():
    doc = video_doc(youtube_id="vid 0000001")
    processed = SearchProcess().process_one("ta_video", doc, 2.0)

    assert processed["media_url"] == f"{MEDIA}/{CHANNEL_ID}/vid%200000001.mp4"
    assert processed["vid_thumb_url"] == f"{CACHE}/videos/v/vid 0000001.jpg"
    assert processed["published"] == "2023-11-14T22:13:20+00:00"
    assert processed["vid_last_refresh"] == "2026-07-30T14:05:49+00:00"
    assert processed["subtitles"] == []
    assert processed["playlist"] == []
    assert processed["channel"]["channel_thumb_url"].endswith("_thumb.jpg")
    assert "progress" not in processed["player"]
    assert processed["_score"] == 2.0


def test_video_subtitles_and_playlist_kept():
    doc = video_doc(
        subtitles=[{"lang": "en", "media_url": "UC1/vid.en.vtt"}],
        playlist=["PL1"],
    )
    processed = SearchProcess().process_one("ta_video", doc)

    assert processed["subtitles"] == [
        {"lang": "en", "media_url": f"{MEDIA}/UC1/vid.en.vtt"}
    ]
    assert processed["playlist"] == ["PL1"]


@pytest.mark.usefixtures("redis_clean")
def test_video_user_progress():
    archivist = RedisArchivist()
    archivist.set_message(
        "42:progress:vid00000001",
        {"youtube_id": "vid00000001", "position": 229},
    )
    archivist.set_message(
        "42:progress:vid00000002",
        {"youtube_id": "vid00000002", "position": 100, "watched": True},
    )
    archivist.set_message(
        "42:progress:vid00000003",
        {"youtube_id": "vid00000003", "position": 50},
    )
    processor = SearchProcess(match_video_user_progress=42)
    assert processor.position_index == {"vid00000001": 229, "vid00000003": 50}

    first = processor.process_one("ta_video", video_doc())
    assert first["player"]["progress"] == 50.0
    assert first["player"]["position"] == 229

    # no position for this video, and no duration for the other
    second = processor.process_one(
        "ta_video", video_doc(youtube_id="vid00000002")
    )
    assert "progress" not in second["player"]
    third_doc = video_doc(youtube_id="vid00000003")
    third_doc["player"]["duration"] = 0
    third = processor.process_one("ta_video", third_doc)
    assert "progress" not in third["player"]


@pytest.mark.usefixtures("redis_clean")
def test_user_progress_none():
    assert SearchProcess().position_index is None
    assert SearchProcess(match_video_user_progress=43).position_index is None


def test_playlist():
    doc = playlist_doc("PL1", ["vid00000001"])
    del doc["playlist_description"]
    processed = SearchProcess().process_one("ta_playlist", doc)

    assert processed["playlist_thumbnail"] == f"{CACHE}/playlists/PL1.jpg"
    assert processed["playlist_last_refresh"] == "2026-07-25T17:20:00+00:00"
    assert processed["playlist_description"] is None
    assert list(processed)[:-2] == sorted(processed)[2:]


def test_download_many():
    docs = [download_doc(), download_doc(youtube_id="dl2", vid_thumb_url="")]
    first, second = SearchProcess().process_many("ta_download", docs)

    assert first["vid_thumb_url"] == f"{CACHE}/videos/d/dl000000001.jpg"
    assert first["published"] == "2023-11-14T22:13:20+00:00"
    assert first["_index"] == "ta_download"
    assert second["vid_thumb_url"] is None


def test_comment_thread():
    comments = [
        {"comment_id": "a", "comment_parent": "root"},
        {"comment_id": "b", "comment_parent": "a"},
        {"comment_id": "c", "comment_parent": "b"},
        {"comment_id": "d", "comment_parent": "root"},
        {"comment_id": "e", "comment_parent": "gone"},
    ]
    doc = {"youtube_id": "vid00000001", "comment_comments": comments}
    tree = SearchProcess().process_one("ta_comment", doc)

    assert [c["comment_id"] for c in tree] == ["a", "d"]
    reply = tree[0]["comment_replies"][0]
    assert reply["comment_id"] == "b"
    assert reply["comment_replies"][0]["comment_id"] == "c"
    assert tree[1]["comment_replies"] == []
    # input untouched
    assert "comment_replies" not in comments[0]


def test_subtitle_with_and_without_highlight():
    highlight = {"subtitle_line": ["<span>big</span> drop"]}
    marked = SearchProcess().process_one(
        "ta_subtitle", subtitle_doc("vid00000001", 0, "big drop"), 1, highlight
    )
    plain = SearchProcess().process_one(
        "ta_subtitle", subtitle_doc("vid00000001", 1, "big drop")
    )

    assert marked["subtitle_line"] == "<span>big</span> drop"
    assert plain["subtitle_line"] == "big drop"
    assert plain["vid_thumb_url"] == "/cache/videos/v/vid00000001.jpg"


def test_unknown_index():
    assert SearchProcess().process_one("ta_other", {"x": 1}) is False

"""tests for model helpers not covered by the round trip tests"""

from datetime import datetime, timezone

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)
from download.models import Download
from emby.models import EpisodeNumber
from playlist.models import Playlist
from video.models import Comment, SubtitleLine, Video

pytestmark = pytest.mark.django_db

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


@pytest.fixture(name="channel")
def fixture_channel():
    """saved test channel"""
    return Channel.objects.create(**Channel.fields_from_doc(channel_doc()))


def test_str():
    channel = Channel(channel_id="UC1", name="Chan")
    assert str(channel) == "UC1 (Chan)"
    assert str(Video(youtube_id="v1", title="Vid")) == "v1 (Vid)"
    assert str(Playlist(playlist_id="PL1", name="List")) == "PL1 (List)"
    assert str(Download(youtube_id="d1", status="ignore")) == "d1 (ignore)"
    assert str(ConfigDocument(key="appsettings")) == "appsettings"
    number = EpisodeNumber(
        series_id="UC1", scheme="date", youtube_id="v1", season=2025, episode=10
    )
    assert str(number) == "UC1/date: v1 S2025E10"


def test_video_optional_fields_round_trip(channel):
    doc = video_doc(
        subtitles=[{"lang": "en", "media_url": "x.en.vtt"}],
        sponsorblock={"is_enabled": True, "segments": []},
        comment_count=4,
    )
    doc["player"]["watched"] = True
    doc["player"]["watched_date"] = 1785000000
    doc["streams"] = [{"type": "audio", "index": 0}]
    Video.from_doc(doc).save()

    video = Video.objects.get()
    assert video.width is None and video.height is None
    assert video.to_doc(playlists=[]) == doc


def test_video_playlists_queried(channel):
    Video.from_doc(video_doc()).save()
    Playlist.objects.create(**Playlist.fields_from_doc(playlist_doc("PL1", [])))
    playlist = Playlist.objects.get()
    playlist.entries.create(idx=0, youtube_id="vid00000001")

    assert Video.objects.get().to_doc()["playlist"] == ["PL1"]


def test_subtitle_line_to_doc(channel):
    Video.from_doc(video_doc()).save()
    doc = subtitle_doc("vid00000001", 3, "hello")
    SubtitleLine.objects.create(**SubtitleLine.fields_from_doc(doc))

    assert SubtitleLine.objects.get().to_doc() == doc


def test_comment_round_trip(channel):
    video = Video.from_doc(video_doc())
    video.save()
    Comment.objects.create(video=video, **Comment.fields_from_doc(COMMENT, 0))

    assert Comment.objects.get().to_doc() == COMMENT


def test_playlist_channel_and_entries(channel):
    Video.from_doc(video_doc()).save()
    doc = playlist_doc("PL1", ["vid00000001", "missing0001"])
    Playlist.objects.create(**Playlist.fields_from_doc(doc))
    for entry in Playlist.entries_from_doc(doc):
        entry.save()

    playlist = Playlist.objects.get()
    assert playlist.channel == channel
    downloaded = [e["downloaded"] for e in playlist.to_doc()["playlist_entries"]]
    assert downloaded == [True, False]


def test_download_channel_indexed(channel):
    item = Download.upsert_doc(download_doc())
    other = Download.upsert_doc(download_doc(youtube_id="d2", channel_id="UCx"))

    assert item.channel_indexed is True
    assert other.to_doc()["channel_indexed"] is False
    assert channel.channel_id == item.channel_id


def test_download_matching_without_error_and_plain_status():
    Download.upsert_doc(download_doc(youtube_id="ok"))
    Download.upsert_doc(download_doc(youtube_id="bad", message="boom"))

    assert list(
        Download.objects.matching(error=False).values_list("pk", flat=True)
    ) == ["ok"]
    assert Download.objects.matching(error=False).set_status("ignore") == 1
    assert Download.objects.get(pk="ok").status == "ignore"


def test_download_timestamp_is_aware():
    item = Download.from_doc(download_doc())
    assert item.timestamp.tzinfo == timezone.utc
    assert item.published == datetime.fromtimestamp(1700000000, tz=timezone.utc)

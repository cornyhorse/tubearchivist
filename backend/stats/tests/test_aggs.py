"""stats aggregations against hand computed expectations"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from common.src.env_settings import EnvironmentSettings
from stats.src import aggs
from stats.tests.conftest import CHANNEL_A, CHANNEL_B, CHANNEL_C
from video.models import Video

pytestmark = pytest.mark.django_db


def totals(doc_count, media_size, duration, duration_str):
    """one _totals bucket"""
    return {
        "doc_count": doc_count,
        "media_size": media_size,
        "duration": duration,
        "duration_str": duration_str,
    }


def test_video(library):
    del library
    assert aggs.Video().process() == {
        **totals(5, 34500, 4060, "1h 07m 40s"),
        "type_videos": totals(3, 24000, 310, "5m 10s"),
        "type_shorts": totals(1, 500, 50, "50s"),
        "type_streams": totals(1, 10000, 3700, "1h 01m 40s"),
        "active_true": totals(4, 31500, 3860, "1h 04m 20s"),
        "active_false": totals(1, 3000, 200, "3m 20s"),
    }


def test_video_empty():
    assert aggs.Video().process() == totals(0, 0, 0, "NA")


def test_channel(library):
    del library
    assert aggs.Channel().process() == {
        "doc_count": 3,
        "active_true": 2,
        "active_false": 1,
        "subscribed_true": 1,
        "subscribed_false": 2,
    }


def test_playlist_leaves_out_empty_buckets(library):
    del library
    assert aggs.Playlist().process() == {
        "doc_count": 2,
        "active_true": 2,
        "subscribed_true": 1,
        "subscribed_false": 1,
    }


def test_channel_and_playlist_empty():
    assert aggs.Channel().process() == {"doc_count": 0}
    assert aggs.Playlist().process() == {"doc_count": 0}


def test_download(library):
    del library
    assert aggs.Download().process() == {
        "pending": 3,
        "ignore": 1,
        "pending_videos": 2,
        "pending_shorts": 1,
    }


def test_download_empty():
    assert aggs.Download().process() == {}


def test_watch_progress(library):
    del library
    assert aggs.WatchProgress().process() == {
        "total": {"duration": 4060, "duration_str": "1h 07m 40s", "items": 5},
        "unwatched": {
            "duration": 310,
            "duration_str": "5m 10s",
            "progress": 310 / 4060,
            "items": 3,
        },
        "watched": {
            "duration": 3750,
            "duration_str": "1h 02m 30s",
            "progress": 3750 / 4060,
            "items": 2,
        },
    }


def test_watch_progress_empty():
    assert aggs.WatchProgress().process() == {
        "total": {"duration": 0, "duration_str": "NA", "items": 0}
    }


def test_watch_progress_without_duration(library):
    """no division by zero when nothing has a duration"""
    del library
    Video.objects.update(duration=0, watched=False)
    assert aggs.WatchProgress().process() == {
        "total": {"duration": 0, "duration_str": "NA", "items": 5},
        "unwatched": {
            "duration": 0,
            "duration_str": "NA",
            "progress": 0,
            "items": 5,
        },
    }


def set_downloaded(when: dict[str, datetime]):
    """set date_downloaded and media_size per video"""
    for youtube_id, date_downloaded in when.items():
        Video.objects.filter(pk=youtube_id).update(
            date_downloaded=date_downloaded
        )


def test_download_hist(library):
    """per day, newest first, gaps filled, older than a week left out"""
    del library
    today = datetime.now(ZoneInfo("UTC")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    set_downloaded(
        {
            "vid00000001": today + timedelta(hours=1),
            "vid00000002": today + timedelta(hours=2),
            "vid00000003": today - timedelta(days=2, hours=-5),
            "vid00000004": today - timedelta(days=7),
            "vid00000005": today - timedelta(days=7, seconds=1),
        }
    )

    def day(days_ago):
        return (today - timedelta(days=days_ago)).date().isoformat()

    assert aggs.DownloadHist().process() == [
        {"date": day(0), "count": 2, "media_size": 1500},
        {"date": day(1), "count": 0, "media_size": 0},
        {"date": day(2), "count": 1, "media_size": 3000},
        {"date": day(3), "count": 0, "media_size": 0},
        {"date": day(4), "count": 0, "media_size": 0},
        {"date": day(5), "count": 0, "media_size": 0},
        {"date": day(6), "count": 0, "media_size": 0},
        {"date": day(7), "count": 1, "media_size": 10000},
    ]


def test_download_hist_days_in_tz(library, monkeypatch):
    """days are calendar days of TZ, not UTC"""
    del library
    monkeypatch.setattr(EnvironmentSettings, "TZ", "America/New_York")
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    set_downloaded(
        {
            "vid00000001": today + timedelta(minutes=30),
            "vid00000002": today - timedelta(minutes=30),
        }
    )

    assert aggs.DownloadHist().process() == [
        {"date": today.date().isoformat(), "count": 1, "media_size": 1000},
        {
            "date": (today - timedelta(days=1)).date().isoformat(),
            "count": 1,
            "media_size": 500,
        },
    ]


def test_download_hist_empty(library):
    """nothing downloaded in the last week"""
    del library
    assert aggs.DownloadHist().process() == []


def channel_row(channel_id, name, doc_count, duration, duration_str, size):
    """one biggest channel row"""
    return {
        "id": channel_id,
        "name": name,
        "doc_count": doc_count,
        "duration": duration,
        "duration_str": duration_str,
        "media_size": size,
    }


ROW_A = channel_row(CHANNEL_A, "Coaster Fans", 2, 150, "2m 30s", 1500)
ROW_B = channel_row(CHANNEL_B, "Rail Adventures", 2, 3900, "1h 05m", 13000)
ROW_C = channel_row(CHANNEL_C, "Big Files", 1, 10, "10s", 20000)


@pytest.mark.parametrize(
    "order, expected",
    [
        ("doc_count", [ROW_A, ROW_B, ROW_C]),
        ("duration", [ROW_B, ROW_A, ROW_C]),
        ("media_size", [ROW_C, ROW_B, ROW_A]),
    ],
)
def test_biggest_channel(library, order, expected):
    del library
    assert aggs.BiggestChannel(order).process() == expected


def test_biggest_channel_top_ten(library):
    """at most 10 channels"""
    del library
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel
    from common.tests.docs import channel_doc, video_doc

    for idx in range(10):
        channel = channel_doc(channel_id=f"UCextra{idx:017d}")
        Channel.save_doc(channel)
        Video.save_doc(
            video_doc(channel=channel, youtube_id=f"extra{idx:06d}")
        )

    rows = aggs.BiggestChannel("media_size").process()
    assert len(rows) == 10
    assert rows[:3] == [ROW_C, ROW_B, ROW_A]

"""tests for helper functions"""

from types import SimpleNamespace

import pytest
import requests
from common.src import helper
from common.src.helper import (
    date_parser,
    get_duration_str,
    is_shorts,
    randomizor,
    time_parser,
)


def test_randomizor_with_positive_length():
    """test randomizer"""
    length = 10
    result = randomizor(length)
    assert len(result) == length
    assert result.isalnum()


def test_date_parser_with_int():
    """unix timestamp"""
    timestamp = 1621539600
    expected_date = "2021-05-20T19:40:00+00:00"
    assert date_parser(timestamp) == expected_date


def test_date_parser_with_digit():
    """unix timestamp"""
    timestamp = "1621539600"
    expected_date = "2021-05-20T19:40:00+00:00"
    assert date_parser(timestamp) == expected_date


def test_date_parser_with_float():
    """iso timestamp"""
    date_float = 1766210400.0
    expected_date = "2025-12-20T06:00:00+00:00"
    assert date_parser(date_float) == expected_date


def test_date_parser_with_str():
    """iso timestamp"""
    date_str = "2021-05-21"
    expected_date = "2021-05-21T00:00:00+00:00"
    assert date_parser(date_str) == expected_date


def test_date_parser_with_invalid_input():
    """invalid type"""
    invalid_input = [1621539600]
    with pytest.raises(TypeError):
        date_parser(invalid_input)


def test_date_parser_with_invalid_string_format():
    """invalid date string"""
    invalid_date_str = "21/05/2021"
    with pytest.raises(ValueError):
        date_parser(invalid_date_str)


def test_time_parser_with_numeric_string():
    """as number"""
    timestamp = "100"
    expected_seconds = 100
    assert time_parser(timestamp) == expected_seconds


def test_time_parser_with_hh_mm_ss_format():
    """to seconds"""
    timestamp = "01:00:00"
    expected_seconds = 3600.0
    assert time_parser(timestamp) == expected_seconds


def test_time_parser_with_empty_string():
    """handle empty"""
    timestamp = ""
    assert time_parser(timestamp) is False


def test_time_parser_with_invalid_format():
    """not enough to unpack"""
    timestamp = "01:00"
    with pytest.raises(ValueError):
        time_parser(timestamp)


def test_time_parser_with_non_numeric_input():
    """non numeric"""
    timestamp = "1a:00:00"
    with pytest.raises(ValueError):
        time_parser(timestamp)


class FakeHead:
    """record requests.head calls, answer with status_code"""

    def __init__(self, status_code=200, error=None):
        self.status_code = status_code
        self.error = error
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status_code)


def test_is_shorts(monkeypatch):
    """is shorts id: the shorts url answers 200"""
    fake_head = FakeHead(200)
    monkeypatch.setattr(helper.requests, "head", fake_head)

    assert is_shorts("YG3-Pw3rixU")

    url, kwargs = fake_head.calls[0]
    assert url == "https://www.youtube.com/shorts/YG3-Pw3rixU"
    assert kwargs["cookies"] == {"SOCS": "CAI"}
    assert kwargs["timeout"] == 10
    assert kwargs["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_is_not_shorts(monkeypatch):
    """is not shorts id: youtube redirects to the watch page"""
    monkeypatch.setattr(helper.requests, "head", FakeHead(303))
    assert is_shorts("Ogr9kbypSNg") is False


def test_is_shorts_request_error(monkeypatch):
    """assume regular video on request errors"""
    error = requests.exceptions.ConnectionError("offline")
    monkeypatch.setattr(helper.requests, "head", FakeHead(error=error))
    assert is_shorts("Ogr9kbypSNg") is False


def test_get_duration_str():
    """only seconds"""
    assert get_duration_str(None) == "NA"
    assert get_duration_str(5) == "5s"
    assert get_duration_str(10) == "10s"
    assert get_duration_str(500) == "8m 20s"
    assert get_duration_str(1000) == "16m 40s"
    assert get_duration_str(5000) == "1h 23m 20s"
    assert get_duration_str(500000) == "5d 18h 53m 20s"
    assert get_duration_str(5000000) == "57d 20h 53m 20s"
    assert get_duration_str(50000000) == "1y 213d 16h 53m 20s"


def test_date_parser_with_none():
    """no timestamp"""
    assert date_parser(None) is None


def test_ignore_filelist():
    """hidden and NAS temp files are dropped"""
    filelist = [
        "video.mp4",
        ".DS_Store",
        "@eaDir",
        "Icon\r\r",
        "Network Trash Folder",
        "Temporary Items",
        "thumb.jpg",
    ]
    assert helper.ignore_filelist(filelist) == ["video.mp4", "thumb.jpg"]


def test_rand_sleep(monkeypatch):
    """sleep between half and one and a half the configured interval"""
    slept = []
    monkeypatch.setattr(helper, "sleep", slept.append)

    helper.rand_sleep({"downloads": {"sleep_interval": 0}})
    helper.rand_sleep({"downloads": {}})
    assert not slept

    for _ in range(20):
        helper.rand_sleep({"downloads": {"sleep_interval": 10}})
    assert len(slept) == 20
    assert all(5 <= secs < 15 for secs in slept)


def test_deep_merge():
    """nested dicts merge, other values replace"""
    target = {"a": {"b": 1, "c": {"d": 2}}, "e": [1], "f": {"g": 1}}
    helper.deep_merge(
        target, {"a": {"c": {"x": 3}, "y": 4}, "e": [2], "f": "flat", "h": 5}
    )
    assert target == {
        "a": {"b": 1, "c": {"d": 2, "x": 3}, "y": 4},
        "e": [2],
        "f": "flat",
        "h": 5,
    }


def test_clear_dl_cache(tmp_path):
    """leftover downloads are removed, hidden files kept"""
    download = tmp_path / "download"
    download.mkdir()
    (download / "part.mp4").write_text("x")
    (download / "other.webm").write_text("x")
    (download / ".keep").write_text("x")

    assert helper.clear_dl_cache(str(tmp_path)) == 2
    assert [p.name for p in download.iterdir()] == [".keep"]


@pytest.mark.parametrize(
    "stdout, expected", [(b"458.123\n", 458), (b"N/A\n", 0)]
)
def test_get_duration_sec(monkeypatch, stdout, expected):
    """ffprobe output parsed to whole seconds"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(helper.subprocess, "run", fake_run)

    assert helper.get_duration_sec("/media/vid.mp4") == expected
    cmd, kwargs = calls[0]
    assert cmd[0] == "ffprobe" and cmd[-1] == "/media/vid.mp4"
    assert kwargs == {"capture_output": True, "check": True}


def test_ta_host_parser():
    """hosts with and without scheme and port"""
    allowed, csrf = helper.ta_host_parser(
        "tube.example.com https://secure.example:8443  http://10.0.0.5:8000"
    )
    assert allowed == [
        "localhost",
        "tubearchivist",
        "tube.example.com",
        "secure.example",
        "10.0.0.5",
    ]
    assert csrf == [
        "http://localhost",
        "http://tubearchivist",
        "http://tube.example.com",
        "https://secure.example:8443",
        "http://10.0.0.5:8000",
    ]


def test_stylesheets():
    """the six themes pass, legacy values migrate, anything else is device"""
    assert helper.get_stylesheets() == [
        "device",
        "light",
        "dark",
        "paper",
        "contrast",
        "colourblind",
    ]
    for theme in helper.get_stylesheets():
        assert helper.check_stylesheet(theme) == theme
    assert helper.check_stylesheet("dark.css") == "dark"
    assert helper.check_stylesheet("light.css") == "light"
    assert helper.check_stylesheet("matrix.css") == "dark"
    assert helper.check_stylesheet("midnight.css") == "dark"
    assert helper.check_stylesheet("custom.css") == "device"
    assert helper.check_stylesheet("../evil.css") == "device"
    assert helper.check_stylesheet(None) == "device"


@pytest.mark.parametrize(
    "duration, position, expected",
    [
        (0, 10, False),
        (None, 10, False),
        (-5, 10, False),
        (30, 14, False),
        (30, 15, True),
        (600, 539, False),
        (600, 540, True),
        (1800, 1619, False),
        (1800, 1620, True),
    ],
)
def test_calc_is_watched(duration, position, expected):
    """half for short, 90 percent for medium, last 3 minutes for long"""
    assert helper.calc_is_watched(duration, position) is expected


@pytest.fixture(name="indexed")
def fixture_indexed(db):  # pylint: disable=unused-argument
    """two channels with videos, playlists and a queued download"""
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel
    from common.tests.docs import (
        channel_doc,
        download_doc,
        playlist_doc,
        video_doc,
    )
    from download.models import Download
    from playlist.models import Playlist
    from video.models import Video

    first = channel_doc(
        channel_id="UCchannel000000000000001",
        channel_name="Zebra Rides",
        channel_overwrites={"autodelete_days": 7, "index_playlists": True},
    )
    second = channel_doc(
        channel_id="UCchannel000000000000002",
        channel_name="Alpha Trains",
        channel_subscribed=False,
    )
    Channel.save_doc(first)
    Channel.save_doc(second)
    Video.save_doc(video_doc(channel=first))
    Download.upsert_doc(download_doc())
    Playlist.save_doc(playlist_doc("PLa", [], playlist_channel="Alpha Trains"))
    Playlist.save_doc(
        playlist_doc(
            "PLz",
            [],
            playlist_channel="Zebra Rides",
            playlist_subscribed=True,
        )
    )
    Playlist.save_doc(
        playlist_doc(
            "PLgone",
            [],
            playlist_channel="Zebra Rides",
            playlist_subscribed=True,
            playlist_active=False,
        )
    )


@pytest.mark.django_db
def test_is_missing(indexed):
    """ids not in any of the given indexes"""
    del indexed
    to_check = ["vid00000001", "dl000000001", "nothere0001"]
    assert helper.is_missing(to_check) == ["nothere0001"]
    assert helper.is_missing("vid00000001") == []
    assert helper.is_missing(to_check, index_name="ta_video") == [
        "dl000000001",
        "nothere0001",
    ]
    channels = ["UCchannel000000000000002", "UCmissing"]
    assert helper.is_missing(
        channels, index_name="ta_channel", on_key="channel_id"
    ) == ["UCmissing"]
    assert helper.is_missing(["PLa", "PLb"], index_name="ta_playlist") == [
        "PLb"
    ]


@pytest.mark.django_db
def test_get_channel_overwrites(indexed):
    """only channels with overwrites set"""
    del indexed
    assert helper.get_channel_overwrites() == {
        "UCchannel000000000000001": {
            "autodelete_days": 7,
            "index_playlists": True,
        }
    }


@pytest.mark.django_db
def test_get_channels(indexed):
    """sorted by name, subscribed filter, source projection"""
    del indexed
    every = helper.get_channels(subscribed_only=False)
    assert [c["channel_name"] for c in every] == [
        "Alpha Trains",
        "Zebra Rides",
    ]
    assert every[1]["channel_overwrites"]["autodelete_days"] == 7

    subscribed = helper.get_channels(
        subscribed_only=True, source=["channel_id", "not_a_field"]
    )
    assert subscribed == [{"channel_id": "UCchannel000000000000001"}]


@pytest.mark.django_db
def test_get_playlists(indexed):
    """active only, by channel name descending, subscribed filter"""
    del indexed
    every = helper.get_playlists(subscribed_only=False)
    assert [p["playlist_id"] for p in every] == ["PLz", "PLa"]

    subscribed = helper.get_playlists(
        subscribed_only=True, source=["playlist_id"]
    )
    assert subscribed == [{"playlist_id": "PLz"}]


@pytest.mark.django_db
def test_name_sorts_are_case_insensitive(indexed):
    """like the ES to_lower keyword sorts"""
    del indexed
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel
    from common.tests.docs import channel_doc, playlist_doc
    from playlist.models import Playlist

    Channel.save_doc(channel_doc(channel_id="UClower", channel_name="beta"))
    Playlist.save_doc(playlist_doc("PLb", [], playlist_channel="beta"))

    channels = helper.get_channels(subscribed_only=False)
    assert [c["channel_name"] for c in channels] == [
        "Alpha Trains",
        "beta",
        "Zebra Rides",
    ]
    playlists = helper.get_playlists(subscribed_only=False)
    assert [p["playlist_channel"] for p in playlists] == [
        "Zebra Rides",
        "beta",
        "Alpha Trains",
    ]

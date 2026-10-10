"""test video query building"""

# pylint: disable=redefined-outer-name

from enum import Enum

import pytest
from channel.src.remote_query import VideoQueryBuilder, get_last_channel_videos
from video.src.constants import VideoTypeEnum


@pytest.fixture
def default_config():
    """from appsettings"""
    return {
        "subscriptions": {
            "channel_size": 5,
            "live_channel_size": 3,
            "shorts_channel_size": 2,
        }
    }


@pytest.fixture
def empty_overwrites():
    """from channel overwrites"""
    return {}


@pytest.fixture
def overwrites():
    """from channel overwrites"""
    return {
        "subscriptions_channel_size": 10,
        "subscriptions_live_channel_size": 0,
        "subscriptions_shorts_channel_size": None,
    }


def test_build_all_queries_with_limit(default_config, empty_overwrites):
    """default, empty overwrite"""
    builder = VideoQueryBuilder(default_config, empty_overwrites)
    result = builder.build_queries()
    expected = [
        (VideoTypeEnum.VIDEOS, 5),
        (VideoTypeEnum.STREAMS, 3),
        (VideoTypeEnum.SHORTS, 2),
    ]
    assert result == expected


def test_build_all_queries_without_limit(default_config, empty_overwrites):
    """limit disabled"""
    builder = VideoQueryBuilder(default_config, empty_overwrites, limit=False)
    result = builder.build_queries()
    expected = [
        (VideoTypeEnum.VIDEOS, None),
        (VideoTypeEnum.STREAMS, None),
        (VideoTypeEnum.SHORTS, None),
    ]
    assert result == expected


def test_build_specific_query(default_config, empty_overwrites):
    """single vid_type"""
    builder = VideoQueryBuilder(default_config, empty_overwrites)
    result = builder.build_query_type(VideoTypeEnum.VIDEOS)
    assert result == (VideoTypeEnum.VIDEOS, 5)


def test_build_unknown_type(default_config, empty_overwrites):
    """unknown vid_type build list"""
    builder = VideoQueryBuilder(default_config, empty_overwrites, limit=None)
    result = builder.build_queries()
    assert result == [
        (VideoTypeEnum.VIDEOS, None),
        (VideoTypeEnum.STREAMS, None),
        (VideoTypeEnum.SHORTS, None),
    ]


def test_build_multiple_queries(default_config, empty_overwrites):
    """vid_type list"""
    builder = VideoQueryBuilder(default_config, empty_overwrites)
    result = builder.build_queries(
        [VideoTypeEnum.VIDEOS, VideoTypeEnum.SHORTS]
    )
    assert result == [(VideoTypeEnum.VIDEOS, 5), (VideoTypeEnum.SHORTS, 2)]


def test_overwrite_applied(default_config, overwrites):
    """with overwrite from channel config"""
    builder = VideoQueryBuilder(default_config, overwrites)
    result = builder.build_queries()
    expected = [
        (VideoTypeEnum.VIDEOS, 10),  # Overwritten
        # STREAMS is overwritten to 0, should be excluded
        (VideoTypeEnum.SHORTS, 2),  # None in overwrite, fallback to config
    ]
    assert result == expected


def test_no_limit_ignores_config_and_overwrites(default_config, overwrites):
    """no limit single vid_type"""
    builder = VideoQueryBuilder(default_config, overwrites, limit=False)
    result = builder.build_queries([VideoTypeEnum.STREAMS])
    assert result == [(VideoTypeEnum.STREAMS, None)]


def test_zero_query_not_included(default_config):
    """overwrite to zero to disable"""
    overwrites = {"subscriptions_live_channel_size": 0}
    builder = VideoQueryBuilder(default_config, overwrites, limit=True)
    result = builder.build_queries([VideoTypeEnum.STREAMS])
    assert not result  # Should be skipped due to 0


def test_zero_config_overwrite(default_config):
    """zero default config but with overwrite"""
    new_default = default_config.copy()
    new_default["subscriptions"]["shorts_channel_size"] = 0
    new_overwrites = {
        "subscriptions_channel_size": 20,
        "subscriptions_live_channel_size": 20,
        "subscriptions_shorts_channel_size": 8,
    }

    builder = VideoQueryBuilder(new_default, new_overwrites, limit=True)
    result = builder.build_queries()
    assert result == [
        (VideoTypeEnum.VIDEOS, 20),
        (VideoTypeEnum.STREAMS, 20),
        (VideoTypeEnum.SHORTS, 8),
    ]


def test_invalid_video_type_is_ignored(default_config):
    """invalid enum"""
    builder = VideoQueryBuilder(default_config)

    class FakeEnum(Enum):
        """invalid"""

        INVALID = "invalid"

    result = builder.build_queries([FakeEnum.INVALID])
    assert not result


def test_zero_config_without_overwrite(default_config):
    """zero in config disables the type"""
    default_config["subscriptions"]["live_channel_size"] = 0
    builder = VideoQueryBuilder(default_config)
    assert builder.build_query_type(VideoTypeEnum.STREAMS) is None


def test_missing_config_means_no_limit(default_config):
    """no config value and no overwrite: unlimited"""
    default_config["subscriptions"]["channel_size"] = None
    builder = VideoQueryBuilder(default_config)
    result = builder.build_query_type(VideoTypeEnum.VIDEOS)
    assert result == (VideoTypeEnum.VIDEOS, None)


def test_int_limit(default_config, overwrites):
    """int limit wins over config and overwrites"""
    builder = VideoQueryBuilder(default_config, overwrites, limit=7)
    result = builder.build_query_type(VideoTypeEnum.STREAMS)
    assert result == (VideoTypeEnum.STREAMS, 7)


def test_unexpected_limit_type_means_no_limit(default_config):
    """anything else than None, bool or int"""
    builder = VideoQueryBuilder(default_config, limit="3")
    result = builder.build_query_type(VideoTypeEnum.SHORTS)
    assert result == (VideoTypeEnum.SHORTS, None)


CHANNEL = "UCchannel000000000000001"
BASE = f"https://www.youtube.com/channel/{CHANNEL}"


def test_last_videos_all_types_without_limit(yt, default_config):
    """None filter queries every known type, failed tabs are skipped"""
    yt.responses[f"{BASE}/videos"] = {"entries": [{"id": "v1"}, {"id": "v2"}]}
    yt.responses[f"{BASE}/streams"] = None
    yt.responses[f"{BASE}/shorts"] = {"entries": [{"id": "s1"}]}

    result = get_last_channel_videos(CHANNEL, default_config)

    assert result == [
        {"id": "v1", "vid_type": "videos"},
        {"id": "v2", "vid_type": "videos"},
        {"id": "s1", "vid_type": "shorts"},
    ]
    assert all("playlist_items" not in obs for _, obs in yt.calls)


def test_last_videos_unknown_filter_with_limit(yt, default_config):
    """UNKNOWN is like None, limits from config"""
    for tab in ("videos", "streams", "shorts"):
        yt.responses[f"{BASE}/{tab}"] = {"entries": []}

    result = get_last_channel_videos(
        CHANNEL, default_config, limit=True, query_filter=VideoTypeEnum.UNKNOWN
    )

    assert not result
    assert [obs["playlist_items"] for _, obs in yt.calls] == [
        ":5:1",
        ":3:1",
        ":2:1",
    ]


def test_last_videos_list_filter(yt, default_config):
    """list of types"""
    yt.responses[f"{BASE}/shorts"] = {"entries": [{"id": "s1"}]}

    result = get_last_channel_videos(
        CHANNEL, default_config, limit=4, query_filter=[VideoTypeEnum.SHORTS]
    )

    assert result == [{"id": "s1", "vid_type": "shorts"}]
    assert yt.calls[0][1]["playlist_items"] == ":4:1"


def test_last_videos_single_filter(yt, default_config):
    """single type"""
    yt.responses[f"{BASE}/streams"] = {"entries": [{"id": "l1"}]}

    result = get_last_channel_videos(
        CHANNEL, default_config, query_filter=VideoTypeEnum.STREAMS
    )

    assert result == [{"id": "l1", "vid_type": "streams"}]


def test_last_videos_disabled_type_queries_nothing(yt, default_config):
    """config zero leaves no query"""
    default_config["subscriptions"]["shorts_channel_size"] = 0

    result = get_last_channel_videos(
        CHANNEL, default_config, limit=True, query_filter=VideoTypeEnum.SHORTS
    )

    assert not result
    assert not yt.calls

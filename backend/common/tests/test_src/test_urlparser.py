"""tests for url parser"""

import pytest
from common.src import urlparser
from common.src.ta_redis import RedisArchivist
from common.src.urlparser import Parser

# channel handles as youtube resolves them, for the fake yt-dlp below
HANDLES = {
    "@tomscottgo": {"channel_id": "UCBa659QWEk1AI4Tg--mrJ2A"},
    "@ibracorp": {"channel_id": "UC7aW7chIafJG6ECYAd3N5uQ"},
    "@oldname": {"url": "https://www.youtube.com/channel/UColdname"},
    "@empty": {"title": "no ids here"},
}


class FakeYtWrap:
    """resolve handles from HANDLES instead of asking youtube"""

    calls: list[str] = []

    def __init__(self, obs_request, config=False):
        self.obs_request = obs_request
        self.config = config

    def extract(self, url):
        """canned response by handle, error when unknown"""
        self.calls.append(url)
        handle = urlparser.urlparse(url).path.strip("/").split("/")[0]
        response = HANDLES.get(handle.lower())
        if response is None:
            return None, "not found"
        return response, None


@pytest.fixture(autouse=True, name="fake_yt")
def fixture_fake_yt(monkeypatch):
    """never ask youtube"""
    monkeypatch.setattr(urlparser, "YtWrap", FakeYtWrap)
    FakeYtWrap.calls = []
    return FakeYtWrap


# video id parsing
VIDEO_URL_IN = [
    "7DKv5H5Frt0",
    "https://www.youtube.com/watch?v=7DKv5H5Frt0",
    "https://www.youtube.com/watch?v=7DKv5H5Frt0&t=113&feature=shared",
    "https://www.youtube.com/watch?v=7DKv5H5Frt0&list=PL96C35uN7xGJu6skU4TBYrIWxggkZBrF5&index=1&pp=iAQB",  # noqa: E501
    "https://youtu.be/7DKv5H5Frt0",
    "https://www.youtube.com/live/7DKv5H5Frt0",
]
VIDEO_OUT = [{"type": "video", "url": "7DKv5H5Frt0", "vid_type": "unknown"}]
VIDEO_TEST_CASES = [(i, VIDEO_OUT) for i in VIDEO_URL_IN]

# shorts id parsing
SHORTS_URL_IN = [
    "https://www.youtube.com/shorts/YG3-Pw3rixU",
    "https://youtube.com/shorts/YG3-Pw3rixU?feature=shared",
]
SHORTS_OUT = [{"type": "video", "url": "YG3-Pw3rixU", "vid_type": "shorts"}]
SHORTS_TEST_CASES = [(i, SHORTS_OUT) for i in SHORTS_URL_IN]

# channel id parsing
CHANNEL_URL_IN = [
    "UCBa659QWEk1AI4Tg--mrJ2A",
    "@TomScottGo",
    "https://www.youtube.com/channel/UCBa659QWEk1AI4Tg--mrJ2A",
    "https://www.youtube.com/@TomScottGo",
]
CHANNEL_OUT = [
    {
        "type": "channel",
        "url": "UCBa659QWEk1AI4Tg--mrJ2A",
        "vid_type": "unknown",
    }
]
CHANNEL_TEST_CASES = [(i, CHANNEL_OUT) for i in CHANNEL_URL_IN]

# channel vid type parsing
CHANNEL_VID_TYPES = [
    (
        "https://www.youtube.com/@IBRACORP/videos",
        [
            {
                "type": "channel",
                "url": "UC7aW7chIafJG6ECYAd3N5uQ",
                "vid_type": "videos",
            }
        ],
    ),
    (
        "https://www.youtube.com/@IBRACORP/shorts",
        [
            {
                "type": "channel",
                "url": "UC7aW7chIafJG6ECYAd3N5uQ",
                "vid_type": "shorts",
            }
        ],
    ),
    (
        "https://www.youtube.com/@IBRACORP/streams",
        [
            {
                "type": "channel",
                "url": "UC7aW7chIafJG6ECYAd3N5uQ",
                "vid_type": "streams",
            }
        ],
    ),
]

# playlist id parsing
PLAYLIST_URL_IN = [
    "PL96C35uN7xGJu6skU4TBYrIWxggkZBrF5",
    "https://www.youtube.com/playlist?list=PL96C35uN7xGJu6skU4TBYrIWxggkZBrF5",
    "https://www.youtube.com/playlist?list=PLE6-bhDW8GWg",
]
PLAYLIST_OUT = [
    {
        "type": "playlist",
        "url": "PL96C35uN7xGJu6skU4TBYrIWxggkZBrF5",
        "vid_type": "unknown",
    },
    {
        "type": "playlist",
        "url": "PL96C35uN7xGJu6skU4TBYrIWxggkZBrF5",
        "vid_type": "unknown",
    },
    {
        "type": "playlist",
        "url": "PLE6-bhDW8GWg",
        "vid_type": "unknown",
    },
]
PLAYLIST_TEST_CASES = [
    (i, [PLAYLIST_OUT[idx]]) for idx, i in enumerate(PLAYLIST_URL_IN)
]

# personal playlists
EXPECTED_WL = [{"type": "playlist", "url": "WL", "vid_type": "unknown"}]
EXPECTED_LL = [{"type": "playlist", "url": "LL", "vid_type": "unknown"}]
PERSONAL_PLAYLISTS_TEST_CASES = [
    ("WL", EXPECTED_WL),
    ("https://www.youtube.com/playlist?list=WL", EXPECTED_WL),
    ("LL", EXPECTED_LL),
    ("https://www.youtube.com/playlist?list=LL", EXPECTED_LL),
]

# collect tests expected to pass
PASSTING_TESTS = []
PASSTING_TESTS.extend(VIDEO_TEST_CASES)
PASSTING_TESTS.extend(SHORTS_TEST_CASES)
PASSTING_TESTS.extend(CHANNEL_TEST_CASES)
PASSTING_TESTS.extend(CHANNEL_VID_TYPES)
PASSTING_TESTS.extend(PLAYLIST_TEST_CASES)
PASSTING_TESTS.extend(PERSONAL_PLAYLISTS_TEST_CASES)


@pytest.mark.parametrize("url_str, expected_result", PASSTING_TESTS)
def test_passing_parse(url_str, expected_result):
    """test parser"""
    parser = Parser(url_str, use_cache=False)
    parsed = parser.parse()
    assert parsed == expected_result


INVALID_IDS_ERRORS = [
    "aaaaa",
    "https://www.youtube.com/playlist?list=AAAA",
    "https://www.youtube.com/channel/UC9-y-6csu5WGm29I7Jiwpn",
    "https://www.youtube.com/watch?v=CK3_zarXkw",
]


@pytest.mark.parametrize("invalid_value", INVALID_IDS_ERRORS)
def test_invalid_ids(invalid_value):
    """test for invalid IDs"""
    with pytest.raises(ValueError, match="not a valid id_str"):
        parser = Parser(invalid_value, use_cache=False)
        parser.parse()


INVALID_DOMAINS = [
    "https://vimeo.com/32001208",
    "https://peertube.tv/w/8RiJE2j2nw569FVgPNjDt7",
]


@pytest.mark.parametrize("invalid_value", INVALID_DOMAINS)
def test_invalid_domains(invalid_value):
    """raise error on none YT domains"""
    parser = Parser(invalid_value, use_cache=False)
    with pytest.raises(ValueError, match="invalid domain"):
        parser.parse()


@pytest.mark.parametrize(
    "url, expected_type",
    [
        ("https://youtu.be/UCBa659QWEk1AI4Tg--mrJ2A", "video"),
        (
            "https://www.youtube.com/watch?v=PL96C35uN7xGJu6skU4TBYrIWx",
            "video",
        ),
        ("https://www.youtube.com/channel/7DKv5H5Frt0", "channel"),
    ],
)
def test_unexpected_type(url, expected_type):
    """an id of another type where one type is expected"""
    with pytest.raises(
        ValueError, match=f"not of expected type {expected_type}"
    ):
        Parser(url, use_cache=False).parse()


def test_multiple_and_tab_detection():
    """several items, vid_type from the last path part"""
    parsed = Parser(
        "7DKv5H5Frt0\nhttps://www.youtube.com/@OldName/streams",
        use_cache=False,
    ).parse()
    assert parsed == [
        {"type": "video", "url": "7DKv5H5Frt0", "vid_type": "unknown"},
        {"type": "channel", "url": "UColdname", "vid_type": "streams"},
    ]


def test_extract_channel_name_failures():
    """unknown handle and response without any id"""
    with pytest.raises(ValueError, match="failed to retrieve URL"):
        Parser("@NoSuchHandle", use_cache=False).parse()
    with pytest.raises(ValueError):
        Parser("@Empty", use_cache=False).parse()


@pytest.mark.usefixtures("redis_clean")
def test_handle_lookup_is_cached(fake_yt, redis_clean):
    """resolved handles are cached in redis for a week"""
    expected = [
        {
            "type": "channel",
            "url": "UCBa659QWEk1AI4Tg--mrJ2A",
            "vid_type": "unknown",
        }
    ]
    assert Parser("@TomScottGo").parse() == expected
    assert Parser("https://www.youtube.com/@tomscottgo/").parse() == expected
    assert fake_yt.calls == ["https://www.youtube.com/@TomScottGo"]

    key = "channel:handlesearch:@tomscottgo"
    assert RedisArchivist().get_message_dict(key) == {
        "channel_id": "UCBa659QWEk1AI4Tg--mrJ2A",
        "handle": "@TomScottGo",
    }
    ttl = redis_clean.ttl(f"{RedisArchivist.NAME_SPACE}{key}")
    assert 3600 * 24 * 6 < ttl <= 3600 * 24 * 7


@pytest.mark.usefixtures("redis_clean")
def test_redirect_is_not_cached(fake_yt):
    """old channel name redirects resolve, but are not cached"""
    assert Parser("@OldName").parse()[0]["url"] == "UColdname"
    assert Parser("@OldName").parse()[0]["url"] == "UColdname"
    assert len(fake_yt.calls) == 2
    assert RedisArchivist().list_keys("channel:handlesearch") == []


@pytest.mark.usefixtures("redis_clean")
def test_cache_only_for_handles():
    """no cache lookups or writes for urls without a handle"""
    url = "https://www.youtube.com/c/legacy"
    assert Parser._get_cached(url) is None
    Parser._set_cache(url, "UCx")
    assert RedisArchivist().list_keys("channel:handlesearch") == []

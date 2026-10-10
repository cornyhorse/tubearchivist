"""tests for the youtube item base class and pagination"""

import pytest
from channel.models import Channel
from common.src import index_generic
from common.src.index_generic import Pagination, YouTubeItem
from common.tests.docs import channel_doc
from django.test import RequestFactory
from user.src.user_config import UserConfig

CHANNEL_ID = "UCchannel000000000000001"


class ChannelItem(YouTubeItem):
    """minimal subclass on the channel model"""

    model = Channel
    yt_base = "https://www.youtube.com/channel/"


@pytest.fixture(name="fake_yt")
def fixture_fake_yt(monkeypatch):
    """record YtWrap requests instead of calling youtube"""
    calls = []

    class FakeYtWrap:
        """stand in for YtWrap"""

        def __init__(self, obs_request, config=False):
            self.obs_request = obs_request
            self.config = config

        def extract(self, url):
            """record request, return canned response"""
            calls.append((url, self.obs_request, self.config))
            return {"id": url}, None

    monkeypatch.setattr(index_generic, "YtWrap", FakeYtWrap)
    return calls


def test_build_url_and_get_from_youtube(app_config, fake_yt):
    del app_config
    item = ChannelItem(CHANNEL_ID)
    assert item.build_yt_url() == f"https://www.youtube.com/channel/{CHANNEL_ID}"
    assert item.youtube_meta is False and item.json_data is False

    item.get_from_youtube()

    url, obs, config = fake_yt[0]
    assert url == item.build_yt_url()
    assert obs == {"skip_download": True, "noplaylist": True}
    assert config == item.config
    assert item.youtube_meta == {"id": url}
    assert item.error is None


def test_get_from_youtube_lang_and_overwrite(app_config, fake_yt):
    app_config.update_config({"downloads": {"extractor_lang": "en, de"}})
    item = ChannelItem(CHANNEL_ID)

    item.get_from_youtube(obs_overwrite={"noplaylist": False})

    _, obs, _ = fake_yt[0]
    assert obs == {
        "skip_download": True,
        "noplaylist": False,
        "extractor_args": {"youtube": {"lang": ["en", "de"]}},
    }
    # class level defaults untouched
    assert ChannelItem.yt_obs == {"skip_download": True, "noplaylist": True}


def test_db_round_trip(app_config):
    del app_config
    item = ChannelItem(CHANNEL_ID)
    item.get_from_db()
    assert item.json_data is None

    item.json_data = channel_doc()
    item.save_to_db()
    item.get_from_db()
    assert item.json_data == channel_doc()

    item.deactivate()
    assert Channel.objects.get().active is False

    item.delete_from_db()
    assert not Channel.objects.exists()


def make_pagination(user, query="", page_size=None) -> Pagination:
    """pagination for a GET request with query string"""
    if page_size:
        UserConfig(user.id).set_value("page_size", page_size)
    request = RequestFactory().get(f"/api/video/?{query}")
    request.user = user
    return Pagination(request)


@pytest.mark.django_db
def test_pagination_first_page(account):
    for page in (0, 1, -2):
        pagination = make_pagination(account, f"page={page}")
        assert pagination.pagination == {
            "page_size": 25,
            "page_from": 0,
            "prev_pages": None,
            "current_page": page,
            "max_hits": False,
            "params": "",
        }


@pytest.mark.django_db
def test_pagination_later_page_keeps_params(account):
    pagination = make_pagination(account, "page=8&sort=views", page_size=10)

    assert pagination.pagination == {
        "page_size": 10,
        "page_from": 70,
        "prev_pages": [3, 4, 5, 6, 7],
        "current_page": 8,
        "max_hits": False,
        "params": "sort=views",
    }
    assert make_pagination(account, "page=3").pagination["prev_pages"] == [2]


@pytest.mark.django_db
def test_pagination_validate(account):
    pagination = make_pagination(account, "page=2", page_size=10)
    pagination.validate(95)
    assert pagination.pagination["last_page"] == 10
    assert pagination.pagination["next_pages"] == [3, 4, 5, 6, 7]
    assert pagination.pagination["total_hits"] == 95

    last = make_pagination(account, "page=10")
    last.validate(95)
    assert last.pagination["last_page"] is False
    assert last.pagination["next_pages"] == []

    assert make_pagination(account).pagination["current_page"] == 0
    single = make_pagination(account)
    single.validate(5)
    assert single.pagination["last_page"] is False
    assert single.pagination["next_pages"] == []

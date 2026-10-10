"""tests for the members.tubearchivist.com client, requests mocked"""

import pytest
from appsettings.src import membership as membership_module
from appsettings.src.membership import Membership
from channel.models import Channel
from common.src.ta_redis import RedisArchivist
from common.tests.docs import channel_doc

pytestmark = pytest.mark.django_db


@pytest.fixture(name="requests_log")
def fixture_requests_log(monkeypatch):
    """capture requests.get/post calls"""
    calls = []

    def fake(method):
        def call(url, **kwargs):
            calls.append((method, url, kwargs))
            return f"{method} response"

        return call

    monkeypatch.setattr(membership_module.requests, "get", fake("get"))
    monkeypatch.setattr(membership_module.requests, "post", fake("post"))
    return calls


@pytest.fixture(name="token")
def fixture_token(redis_clean):
    """stored membership token"""
    # pylint: disable=unused-argument
    RedisArchivist().set_message(Membership.REDIS_KEY, {"token": "abc"})


def test_profile_without_token(app_config, redis_clean, requests_log):
    # pylint: disable=unused-argument
    with pytest.raises(ValueError, match="expected MB_API_KEY"):
        Membership().get_profile()
    assert not requests_log


def test_get_profile(app_config, token, requests_log):
    # pylint: disable=unused-argument
    assert Membership().get_profile() == "get response"

    method, url, kwargs = requests_log[0]
    assert method == "get"
    assert url == f"{Membership.BASE_URL}/api/profile/me/"
    assert kwargs == {"headers": {"Authorization": "Token abc"}, "timeout": 30}


def test_sync_subs(app_config, token, requests_log):
    # pylint: disable=unused-argument
    app_config.update_config({"subscriptions": {"live_channel_size": 0}})
    Channel.save_doc(channel_doc(channel_id="UCa", channel_name="a"))
    Channel.save_doc(
        channel_doc(
            channel_id="UCb",
            channel_name="b",
            channel_overwrites={
                "subscriptions_channel_size": 0,
                "subscriptions_live_channel_size": 0,
                "subscriptions_shorts_channel_size": 0,
            },
        )
    )
    Channel.save_doc(
        channel_doc(
            channel_id="UCc", channel_name="c", channel_subscribed=False
        )
    )

    assert Membership().sync_subs() == "post response"

    method, url, kwargs = requests_log[0]
    assert method == "post"
    assert url.endswith("/api/profile/subscription/?delete=true")
    assert kwargs["headers"] == {"Authorization": "Token abc"}
    assert kwargs["json"] == [
        {
            "channel_id": "UCa",
            "notify_videos": True,
            "notify_streams": False,
            "notify_shorts": True,
        },
        {
            "channel_id": "UCb",
            "notify_videos": False,
            "notify_streams": False,
            "notify_shorts": False,
        },
    ]

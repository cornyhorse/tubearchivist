"""tests for API base classes and common serializers"""

import pytest
from channel.models import Channel
from common.serializers import ValidateUnknownFieldsMixin
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc
from common.views_base import AdminWriteOnly, ApiBaseView
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rest_framework import serializers
from rest_framework.request import Request
from user.src.user_config import UserConfig

CACHE = EnvironmentSettings().get_cache_root()


def request_as(user, method="get", query=""):
    """DRF request of user"""
    django_request = getattr(RequestFactory(), method)(f"/api/x/?{query}")
    django_request.user = user
    request = Request(django_request)
    request.user = user
    return request


@pytest.mark.django_db
def test_admin_write_only(account, admin_account):
    permission = AdminWriteOnly()
    view = ApiBaseView()

    for method, user, allowed in [
        ("get", AnonymousUser(), False),
        ("get", account, True),
        ("head", account, True),
        ("post", account, False),
        ("delete", account, False),
        ("post", admin_account, True),
    ]:
        request = request_as(user, method)
        assert permission.has_permission(request, view) is allowed


def test_get_document():
    view = ApiBaseView()
    view.get_document("ta_channel", None)
    assert view.response == {}
    assert view.status_code == 404

    view.get_document("ta_channel", channel_doc())
    assert view.status_code == 200
    assert view.response["channel_id"] == "UCchannel000000000000001"
    assert view.response["channel_thumb_url"].startswith(f"{CACHE}/")


@pytest.fixture(name="channels")
def fixture_channels(db):  # pylint: disable=unused-argument
    """five channels"""
    for idx in range(5):
        Channel.save_doc(
            channel_doc(
                channel_id=f"UCchannel00000000000000{idx:02d}",
                channel_name=f"Channel {idx}",
            )
        )
    return Channel.objects.order_by("channel_id")


@pytest.mark.django_db
def test_get_document_list_paginated(account, channels):
    UserConfig(account.id).set_value("page_size", 2)
    view = ApiBaseView()

    view.get_document_list(
        request_as(account, query="page=2"), "ta_channel", channels
    )

    assert view.status_code == 200
    names = [doc["channel_name"] for doc in view.response["data"]]
    assert names == ["Channel 2", "Channel 3"]
    assert view.response["paginate"]["total_hits"] == 5
    assert view.response["paginate"]["last_page"] == 3
    assert view.response["paginate"]["prev_pages"] is not None


@pytest.mark.django_db
def test_get_document_list_unpaginated_custom_doc(account, channels):
    view = ApiBaseView()
    view.get_document_list(
        request_as(account),
        "ta_channel",
        channels[:2],
        to_doc=lambda obj: {**obj.to_doc(), "channel_name": "custom"},
        pagination=False,
    )

    assert view.status_code == 200
    assert [d["channel_name"] for d in view.response["data"]] == [
        "custom",
        "custom",
    ]
    assert "paginate" not in view.response


@pytest.mark.django_db
def test_get_document_list_empty(account):
    view = ApiBaseView()
    view.get_document_list(
        request_as(account), "ta_channel", Channel.objects.all()
    )

    assert view.status_code == 404
    assert view.response["data"] == []
    assert view.response["paginate"]["total_hits"] == 0


class StrictSerializer(ValidateUnknownFieldsMixin, serializers.Serializer):
    """serializer rejecting unknown keys"""

    name = serializers.CharField()


def test_validate_unknown_fields():
    valid = StrictSerializer(data={"name": "x"})
    assert valid.is_valid()

    invalid = StrictSerializer(data={"name": "x", "extra": 1})
    assert not invalid.is_valid()
    assert invalid.errors == {"error": "Unknown fields: extra"}

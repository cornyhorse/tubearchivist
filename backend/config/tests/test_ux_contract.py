"""
the endpoints of docs/ux-api-contract.md: routed, admin only where the
contract says so, and 501 until the PR that builds them
"""

from pathlib import Path

import pytest
from django.core.management import call_command
from download.serializers_found import AddSerializer, FoundActionSerializer
from download.serializers_rules import EffectiveRuleQuerySerializer
from mediaserver.serializers import NotifyQuerySerializer

# pylint: disable=unused-import
from task.tests.helpers import (  # noqa: F401
    fixture_admin_client,
    fixture_no_broker,
    fixture_user_client,
)

pytestmark = pytest.mark.django_db

SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "ux-api-schema.yaml"

# (method, url, open to any signed in user)
NOT_BUILT = [
    ("get", "/api/rules/", False),
    ("get", "/api/rules/presets/", True),
    ("get", "/api/rules/default/", False),
    ("put", "/api/rules/default/", False),
    ("delete", "/api/rules/default/", False),
    ("post", "/api/rules/preview/", False),
    ("get", "/api/rules/effective/", False),
    ("get", "/api/rules/videos/", False),
    ("put", "/api/rules/videos/vid00000001/", False),
    ("delete", "/api/rules/videos/vid00000001/", False),
    ("get", "/api/rules/channel/UC1/", False),
    ("put", "/api/rules/channel/UC1/", False),
    ("delete", "/api/rules/channel/UC1/", False),
    ("get", "/api/numbering/schemes/", True),
    ("get", "/api/numbering/channel/UC1/", False),
    ("put", "/api/numbering/channel/UC1/", False),
    ("post", "/api/numbering/channel/UC1/preview/", False),
    ("get", "/api/numbering/video/vid00000001/", False),
    ("put", "/api/numbering/video/vid00000001/", False),
    ("delete", "/api/numbering/video/vid00000001/", False),
    ("get", "/api/mediaserver/", False),
    ("put", "/api/mediaserver/", False),
    ("post", "/api/mediaserver/test/", False),
    ("get", "/api/mediaserver/libraries/", False),
    ("post", "/api/mediaserver/notify/", False),
    ("post", "/api/mediaserver/refresh/", False),
]


@pytest.mark.parametrize("method,url,open_to_users", NOT_BUILT)
def test_not_built_yet(admin_client, user_client, method, url, open_to_users):
    response = getattr(admin_client, method)(url, {}, format="json")
    assert response.status_code == 501
    assert response.json()["error"].startswith("not built yet: ")

    as_user = getattr(user_client, method)(url, {}, format="json")
    assert as_user.status_code == (501 if open_to_users else 403)


def test_schema_is_current(tmp_path):
    """docs/ux-api-schema.yaml is regenerated with every API change"""
    generated = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(generated))
    assert generated.read_text() == SCHEMA.read_text(), (
        "API changed, run: python backend/manage.py spectacular "
        "--file docs/ux-api-schema.yaml"
    )


@pytest.mark.parametrize(
    "serializer,data,valid",
    [
        (EffectiveRuleQuerySerializer, {"video": "v1"}, True),
        (EffectiveRuleQuerySerializer, {}, False),
        (EffectiveRuleQuerySerializer, {"video": "v", "channel": "c"}, False),
        (FoundActionSerializer, {"action": "ignore", "ids": ["v1"]}, True),
        (FoundActionSerializer, {"action": "ignore", "filter": {}}, True),
        (FoundActionSerializer, {"action": "ignore"}, False),
        (
            FoundActionSerializer,
            {"action": "ignore", "ids": ["v1"], "filter": {}},
            False,
        ),
        (AddSerializer, {"urls": "x", "mode": "once"}, True),
        (AddSerializer, {"urls": "x", "mode": "newest_n"}, False),
        (
            AddSerializer,
            {"urls": "x", "mode": "newest_n", "newest_n": 3},
            True,
        ),
        (NotifyQuerySerializer, {"paths": ["/a"]}, True),
        (NotifyQuerySerializer, {"all": True}, True),
        (NotifyQuerySerializer, {}, False),
        (NotifyQuerySerializer, {"paths": ["/a"], "all": True}, False),
    ],
)
def test_contract_validation(serializer, data, valid):
    assert serializer(data=data).is_valid() is valid

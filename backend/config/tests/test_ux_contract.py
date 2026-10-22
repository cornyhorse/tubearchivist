"""the schema of docs/ux-api-contract.md and its validation rules"""

from pathlib import Path

import pytest
from django.core.management import call_command
from download.serializers_found import AddSerializer, FoundActionSerializer
from download.serializers_rules import EffectiveRuleQuerySerializer
from mediaserver.serializers import NotifyQuerySerializer

pytestmark = pytest.mark.django_db

SCHEMA = Path(__file__).resolve().parents[3] / "docs" / "ux-api-schema.yaml"


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

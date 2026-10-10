"""tests for download rules"""

from datetime import date

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc
from download.models import DownloadRule
from download.serializers import DownloadRuleSerializer
from download.src.rules import RuleSet, failed_reason, get_rule, set_rule

pytestmark = pytest.mark.django_db

CHANNEL_A = "UCchannelA"
CHANNEL_B = "UCchannelB"
CHANNEL_C = "UCchannelC"


def entry(**overrides) -> dict:
    """queue entry as PendingList builds it"""
    doc = {
        "channel_id": CHANNEL_A,
        "title": "A regular video",
        "vid_type": "videos",
        "duration_seconds": 600,
        "published": 1700000000,  # 2023-11-14
    }
    doc.update(overrides)
    return doc


def make_channel(channel_id):
    """index a channel"""
    return Channel.save_doc(channel_doc(channel_id=channel_id))


@pytest.mark.parametrize(
    "rule_kwargs, overrides, expected",
    [
        ({"min_duration": 60}, {"duration_seconds": 30}, "shorter than 60s"),
        ({"max_duration": 60}, {"duration_seconds": 61}, "longer than 60s"),
        ({"min_duration": 60}, {"duration_seconds": None}, None),
        ({"min_duration": 60, "max_duration": 900}, {}, None),
        ({"vid_types": ["shorts"]}, {}, "type videos not in shorts"),
        ({"vid_types": ["videos"]}, {}, None),
        ({"title_include": "^live"}, {}, "title doesn't match /^live/"),
        ({"title_include": "REGULAR"}, {}, None),
        ({"title_exclude": "regular"}, {}, "title matches excluded"),
        ({"title_exclude": "trailer"}, {"title": None}, None),
        (
            {"published_after": date(2024, 1, 1)},
            {},
            "published 2023-11-14 before 2024-01-01",
        ),
        ({"published_after": date(2023, 1, 1)}, {}, None),
        ({"published_after": date(2024, 1, 1)}, {"published": None}, None),
    ],
)
def test_failed_reason(rule_kwargs, overrides, expected):
    """each condition, unknown values pass"""
    rule = DownloadRule(**rule_kwargs)
    reason = failed_reason(rule, entry(**overrides))
    if expected is None:
        assert reason is None
    else:
        assert reason.startswith(expected)


def test_ruleset_default_channel_and_disabled():
    """channel rule beats default, disabled channel rule switches it off"""
    make_channel(CHANNEL_A)
    make_channel(CHANNEL_B)
    DownloadRule.objects.create(channel=None, min_duration=60)
    DownloadRule.objects.create(
        channel_id=CHANNEL_A, max_duration=100, action="skip"
    )
    DownloadRule.objects.create(
        channel_id=CHANNEL_B, enabled=False, min_duration=10000
    )

    rules = RuleSet()

    assert rules.check(entry()) == ("skip", "longer than 100s (600s)")
    assert rules.check(entry(channel_id=CHANNEL_B, duration_seconds=1)) is None
    assert rules.check(entry(channel_id=CHANNEL_C, duration_seconds=1)) == (
        "ignore",
        "shorter than 60s (1s)",
    )
    assert rules.check(entry(channel_id=CHANNEL_C)) is None


def test_ruleset_without_rules():
    """no rules, everything passes"""
    assert RuleSet().check(entry()) is None


def test_get_set_rule():
    """create, replace and delete a channel rule and the default"""
    make_channel(CHANNEL_A)
    assert get_rule(CHANNEL_A) is None

    rule = set_rule(CHANNEL_A, {"min_duration": 60, "vid_types": ["videos"]})
    assert rule["min_duration"] == 60
    assert rule["vid_types"] == ["videos"]
    assert rule["enabled"] is True

    rule = set_rule(CHANNEL_A, {"min_duration": 120})
    assert rule["min_duration"] == 120
    assert DownloadRule.objects.count() == 1

    default = set_rule(None, {"action": "skip"})
    assert default["action"] == "skip"
    assert get_rule(None)["action"] == "skip"

    assert set_rule(CHANNEL_A, None) is None
    assert get_rule(CHANNEL_A) is None
    assert get_rule(None) is not None


@pytest.mark.parametrize(
    "data, valid",
    [
        ({"title_include": "ok.*"}, True),
        ({"title_include": "("}, False),
        ({"title_exclude": "["}, False),
        ({"title_exclude": "trailer|teaser"}, True),
        ({"min_duration": 100, "max_duration": 10}, False),
        ({"min_duration": 10, "max_duration": 100}, True),
        ({"min_duration": 10}, True),
    ],
)
def test_rule_serializer(data, valid):
    """regex must compile, min not above max"""
    serializer = DownloadRuleSerializer(data=data)
    assert serializer.is_valid() is valid

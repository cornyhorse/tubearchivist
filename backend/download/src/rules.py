"""
Functionality:
- decide which videos of a channel to download, from DownloadRule
- pure evaluation: a queue entry in, the reason it fails out
"""

import re
from datetime import date, datetime, timezone

from download.models import DownloadRule


class RuleSet:
    """effective rules for all channels, loaded once per extraction"""

    def __init__(self):
        rules = list(DownloadRule.objects.filter(enabled=True))
        self.default = next((r for r in rules if r.channel_id is None), None)
        self.by_channel = {r.channel_id: r for r in rules if r.channel_id}
        # a disabled channel rule switches the default off for that channel
        self.disabled = set(
            DownloadRule.objects.filter(
                enabled=False, channel__isnull=False
            ).values_list("channel_id", flat=True)
        )

    def for_channel(self, channel_id: str) -> DownloadRule | None:
        """the rule that applies to a channel, None for download everything"""
        if channel_id in self.disabled:
            return None
        return self.by_channel.get(channel_id, self.default)

    def check(self, entry: dict) -> tuple[str, str, str] | None:
        """
        (action, reason, reason_key) if entry fails its channel's rule,
        else None
        """
        return check_rule(self.for_channel(entry["channel_id"]), entry)


def check_rule(
    rule: DownloadRule | None, entry: dict
) -> tuple[str, str, str] | None:
    """(action, reason, reason_key) if entry fails rule, else None"""
    if not rule or not rule.enabled:
        return None
    failed = failed_check(rule, entry)
    if not failed:
        return None
    key, reason = failed
    return rule.action, reason, key


def _published_date(entry: dict) -> date | None:
    published = entry.get("published")
    if published is None:
        return None
    return datetime.fromtimestamp(int(published), tz=timezone.utc).date()


def failed_reason(rule: DownloadRule, entry: dict) -> str | None:
    """why entry fails rule, None if it passes; unknown values pass"""
    failed = failed_check(rule, entry)
    return failed[1] if failed else None


def failed_check(rule: DownloadRule, entry: dict) -> tuple[str, str] | None:
    """(reason_key, reason) if entry fails rule; unknown values pass"""
    seconds = entry.get("duration_seconds")
    if seconds is not None:
        if rule.min_duration is not None and seconds < rule.min_duration:
            return (
                "too_short",
                f"shorter than {rule.min_duration}s ({seconds}s)",
            )
        if rule.max_duration is not None and seconds > rule.max_duration:
            return "too_long", f"longer than {rule.max_duration}s ({seconds}s)"

    vid_type = entry.get("vid_type")
    if rule.vid_types and vid_type not in rule.vid_types:
        return (
            "vid_type",
            f"type {vid_type} not in {', '.join(rule.vid_types)}",
        )

    title = entry.get("title") or ""
    if rule.title_include and not re.search(rule.title_include, title, re.I):
        return "title_include", f"title doesn't match /{rule.title_include}/"
    if rule.title_exclude and re.search(rule.title_exclude, title, re.I):
        return (
            "title_exclude",
            f"title matches excluded /{rule.title_exclude}/",
        )

    published = _published_date(entry)
    if rule.published_after and published and published < rule.published_after:
        return (
            "published_before",
            f"published {published} before {rule.published_after}",
        )

    return None


RULE_FIELDS = [
    "enabled",
    "min_duration",
    "max_duration",
    "vid_types",
    "title_include",
    "title_exclude",
    "published_after",
    "action",
]


def get_rule(channel_id: str | None) -> dict | None:
    """rule of a channel (None for the default) as a dict"""
    rule = DownloadRule.objects.filter(channel_id=channel_id).first()
    if not rule:
        return None
    return {key: getattr(rule, key) for key in RULE_FIELDS}


def set_rule(channel_id: str | None, data: dict | None) -> dict | None:
    """create, replace or (with None) delete a rule"""
    if data is None:
        DownloadRule.objects.filter(channel_id=channel_id).delete()
        return None
    DownloadRule.objects.update_or_create(
        channel_id=channel_id,
        defaults={key: data.get(key) for key in RULE_FIELDS if key in data},
    )
    return get_rule(channel_id)


def _duration_label(seconds: int) -> str:
    """600 -> '10 min', 3600 -> '1 h', 5400 -> '1 h 30 min', 45 -> '45 s'"""
    if seconds < 60:
        return f"{seconds} s"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if not hours:
        return f"{minutes} min"
    return f"{hours} h" + (f" {minutes} min" if minutes else "")


TYPE_LABELS = {
    "videos": "videos",
    "streams": "livestreams",
    "shorts": "Shorts",
}


def _rule_parts(rule: DownloadRule) -> list[str]:
    parts = [
        f"No {label}"
        for vid_type, label in TYPE_LABELS.items()
        if rule.vid_types and vid_type not in rule.vid_types
    ]
    conditions = [
        (rule.min_duration, lambda v: f">{_duration_label(v)}"),
        (rule.max_duration, lambda v: f"<{_duration_label(v)}"),
        (rule.title_include, lambda v: f"title has /{v}/"),
        (rule.title_exclude, lambda v: f"title not /{v}/"),
        (rule.published_after, lambda v: f"after {v.isoformat()}"),
    ]
    parts.extend(
        label(value) for value, label in conditions if value not in (None, "")
    )
    return parts


def rule_summary(rule: DownloadRule | None) -> str:
    """badge text: 'No Shorts · >10 min'"""
    if rule is None:
        return "Everything"
    if not rule.enabled:
        return "Off"
    parts = _rule_parts(rule)
    if not parts:
        return "Everything"
    summary = " · ".join(parts)
    if rule.action == "skip":
        summary += " (others not added)"
    return summary


def reason_label(key: str | None, rule: DownloadRule | None, entry: dict):
    """short label to group skipped videos: 'Shorts', '>1 h'"""
    if key == "vid_type":
        return TYPE_LABELS.get(entry.get("vid_type"), "type").capitalize()
    if key == "too_long" and rule and rule.max_duration is not None:
        return f">{_duration_label(rule.max_duration)}"
    if key == "too_short" and rule and rule.min_duration is not None:
        return f"<{_duration_label(rule.min_duration)}"
    labels = {
        "title_include": "Title doesn't match",
        "title_exclude": "Title excluded",
        "published_before": "Too old",
        "always_ignore": "Always ignore",
    }
    return labels.get(key, "Other")


def rule_ref(rule: DownloadRule | None) -> dict | None:
    """which rule decided, for the badge link"""
    if rule is None:
        return None
    if rule.channel_id:
        scope, target = "channel", rule.channel_id
    else:
        scope, target = "default", None
    return {"scope": scope, "target_id": target, "summary": rule_summary(rule)}

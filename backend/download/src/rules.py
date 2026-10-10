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

    def check(self, entry: dict) -> tuple[str, str] | None:
        """(action, reason) if entry fails its channel's rule, else None"""
        rule = self.for_channel(entry["channel_id"])
        if not rule:
            return None
        reason = failed_reason(rule, entry)
        if not reason:
            return None
        return rule.action, reason


def _published_date(entry: dict) -> date | None:
    published = entry.get("published")
    if published is None:
        return None
    return datetime.fromtimestamp(int(published), tz=timezone.utc).date()


def failed_reason(rule: DownloadRule, entry: dict) -> str | None:
    """why entry fails rule, None if it passes; unknown values pass"""
    seconds = entry.get("duration_seconds")
    if seconds is not None:
        if rule.min_duration is not None and seconds < rule.min_duration:
            return f"shorter than {rule.min_duration}s ({seconds}s)"
        if rule.max_duration is not None and seconds > rule.max_duration:
            return f"longer than {rule.max_duration}s ({seconds}s)"

    vid_type = entry.get("vid_type")
    if rule.vid_types and vid_type not in rule.vid_types:
        return f"type {vid_type} not in {', '.join(rule.vid_types)}"

    title = entry.get("title") or ""
    if rule.title_include and not re.search(rule.title_include, title, re.I):
        return f"title doesn't match /{rule.title_include}/"
    if rule.title_exclude and re.search(rule.title_exclude, title, re.I):
        return f"title matches excluded /{rule.title_exclude}/"

    published = _published_date(entry)
    if rule.published_after and published and published < rule.published_after:
        return f"published {published} before {rule.published_after}"

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

"""
Functionality:
- the channel table: search, filters, sort, and per channel the counts,
  latest upload, rule and numbering, all annotated in one query
"""

from channel.models import Channel
from django.db.models import (
    Count,
    Exists,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce, Lower
from download.models import Download, DownloadRule, DownloadStatus
from download.src.rules import RULE_FIELDS, rule_summary
from emby.src.tags import canonical_tag
from video.models import Video

SORTS = {
    "name": Lower("name"),
    "subscribed": F("subscribed"),
    "downloaded": F("count_downloaded"),
    "queued": F("count_queued"),
    "ignored": F("count_ignored"),
    "latest_upload": F("latest_upload"),
    "last_checked": F("last_refresh"),
}


def _count(queryset) -> Coalesce:
    """count of queryset rows of the outer channel"""
    rows = (
        queryset.filter(channel_id=OuterRef("channel_id"))
        .order_by()
        .values("channel_id")
        .annotate(n=Count("pk"))
        .values("n")[:1]
    )
    return Coalesce(Subquery(rows, output_field=IntegerField()), Value(0))


def annotated() -> QuerySet:
    """channels with counts, latest upload and has_rule"""
    pending = Download.objects.filter(status=DownloadStatus.PENDING)
    latest = (
        Video.objects.filter(channel_id=OuterRef("channel_id"))
        .order_by()
        .values("channel_id")
        .annotate(latest=Max("published"))
        .values("latest")[:1]
    )
    return Channel.objects.select_related("download_rule").annotate(
        count_downloaded=_count(Video.objects.all()),
        count_queued=_count(pending.filter(message__isnull=True)),
        count_errors=_count(pending.filter(message__isnull=False)),
        count_ignored=_count(
            Download.objects.filter(status=DownloadStatus.IGNORE)
        ),
        latest_upload=Subquery(latest),
        has_rule=Exists(
            DownloadRule.objects.filter(channel_id=OuterRef("channel_id"))
        ),
    )


def channel_list(filters: dict) -> QuerySet:
    """the channel table query"""
    queryset = annotated()
    if filters.get("filter") is not None:
        queryset = queryset.filter(
            subscribed=filters["filter"] == "subscribed"
        )
    if query := filters.get("q"):
        handle = query.lstrip("@")
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(name__icontains=handle)
            | Q(channel_id__icontains=query)
        )
    toggles = {
        "has_queued": Q(count_queued__gt=0),
        "has_errors": Q(count_errors__gt=0),
        "has_rule": Q(has_rule=True),
        "auto_download": Q(auto_download=True),
    }
    for key, condition in toggles.items():
        if filters.get(key) is not None:
            queryset = queryset.filter(
                condition if filters[key] else ~condition
            )
    if filters.get("emby_tag"):
        queryset = queryset.filter(
            emby_tags__contains=[canonical_tag(filters["emby_tag"])]
        )
    sort = SORTS[filters.get("sort", "name")]
    if filters.get("order", "asc") == "desc":
        order = sort.desc(nulls_last=True)
    else:
        order = sort.asc(nulls_last=True)
    return queryset.order_by(order, Lower("name"), "channel_id")


def table_doc(channel: Channel) -> dict:
    """a channel document plus what the table shows"""
    rule = getattr(channel, "download_rule", None)
    return {
        **channel.to_doc(),
        "download_rule": (
            {key: getattr(rule, key) for key in RULE_FIELDS} if rule else None
        ),
        # badge text of the channel's own rule, null: uses the default
        "rule_summary": rule_summary(rule) if rule else None,
        "counts": {
            "downloaded": channel.count_downloaded,
            "queued": channel.count_queued,
            "ignored": channel.count_ignored,
            "errors": channel.count_errors,
        },
        "latest_upload": channel.latest_upload,
    }

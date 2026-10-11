"""
Functionality:
- the Rules page: every rule with its target, presets, preview
- per-video overrides: 'download anyway' and 'always ignore'
- shared by the REST views and the MCP server
"""

from channel.models import Channel
from download.models import (
    Download,
    DownloadRule,
    DownloadStatus,
    VideoOverride,
)
from download.src.rules import (
    RULE_FIELDS,
    RuleSet,
    get_rule,
    reason_label,
    rule_summary,
    set_rule,
)
from jobs.models import CreatedBy, Priority
from jobs.src.engine import cancel_waiting, enqueue, pump
from playlist.models import Playlist, PlaylistEntry
from video.models import Video

PRESETS = [
    {
        "key": "no_shorts",
        "label": "No Shorts",
        "description": "Only regular videos and livestreams.",
        "patch": {"vid_types": ["videos", "streams"]},
        "needs_value": None,
        "value_field": None,
    },
    {
        "key": "no_livestreams",
        "label": "No livestreams",
        "description": "Only regular videos and Shorts.",
        "patch": {"vid_types": ["videos", "shorts"]},
        "needs_value": None,
        "value_field": None,
    },
    {
        "key": "skip_over_1h",
        "label": "Skip over 1 hour",
        "description": "Videos longer than an hour are skipped.",
        "patch": {"max_duration": 3600},
        "needs_value": None,
        "value_field": None,
    },
    {
        "key": "only_over_1h",
        "label": "Only over 1 hour",
        "description": "Only videos longer than an hour download.",
        "patch": {"min_duration": 3600},
        "needs_value": None,
        "value_field": None,
    },
    {
        "key": "only_after_date",
        "label": "Only after date…",
        "description": "Only videos published on or after a date.",
        "patch": {},
        "needs_value": "date",
        "value_field": "published_after",
    },
    {
        "key": "title_contains",
        "label": "Title contains…",
        "description": "Only videos whose title matches (regex).",
        "patch": {},
        "needs_value": "text",
        "value_field": "title_include",
    },
    {
        "key": "title_excludes",
        "label": "Title doesn't contain…",
        "description": "Skip videos whose title matches (regex).",
        "patch": {},
        "needs_value": "text",
        "value_field": "title_exclude",
    },
]
MODELS = {"channel": Channel, "playlist": Playlist}


def _target_kwargs(scope: str, target_id: str | None) -> dict:
    return {
        "channel_id": target_id if scope == "channel" else None,
        "playlist_id": target_id if scope == "playlist" else None,
    }


def target_exists(scope: str, target_id: str) -> bool:
    """the channel or playlist is in the archive"""
    return MODELS[scope].objects.filter(pk=target_id).exists()


def rule_dict(data: dict | None) -> dict | None:
    """a rule as the API returns it: body plus summary"""
    if data is None:
        return None
    return {**data, "summary": rule_summary(DownloadRule(**data))}


def get_target_rule(scope: str, target_id: str | None) -> dict | None:
    """the target's own rule, None if it has none"""
    return rule_dict(get_rule(**_target_kwargs(scope, target_id)))


def set_target_rule(
    scope: str, target_id: str | None, data: dict | None
) -> dict | None:
    """create, replace, or with None delete"""
    kwargs = _target_kwargs(scope, target_id)
    return rule_dict(
        set_rule(kwargs["channel_id"], data, kwargs["playlist_id"])
    )


def rule_rows() -> list[dict]:
    """default first, then channels and playlists by name"""
    rows = []
    for rule in DownloadRule.objects.select_related("channel", "playlist"):
        data = rule_dict({key: getattr(rule, key) for key in RULE_FIELDS})
        if rule.channel_id:
            row = ("channel", rule.channel_id, rule.channel.name)
            subscribed = rule.channel.subscribed
        elif rule.playlist_id:
            row = ("playlist", rule.playlist_id, rule.playlist.name)
            subscribed = rule.playlist.subscribed
        else:
            row, subscribed = ("default", None, None), None
        rows.append(
            {
                "scope": row[0],
                "target_id": row[1],
                "target_title": row[2],
                "target_subscribed": subscribed,
                "rule": data,
            }
        )
    order = {"default": 0, "channel": 1, "playlist": 2}
    rows.sort(key=lambda r: (order[r["scope"]], r["target_title"] or ""))
    return rows


def _entries(scope: str, target_id: str | None, limit: int) -> list[dict]:
    """the last known videos of a target, found and downloaded, newest first"""
    found = Download.objects.all()
    videos = Video.objects.all()
    if scope == "channel":
        found = found.filter(channel_id=target_id)
        videos = videos.filter(channel_id=target_id)
    elif scope == "playlist":
        ids = PlaylistEntry.objects.filter(playlist_id=target_id).values(
            "youtube_id"
        )
        found = found.filter(youtube_id__in=ids)
        videos = videos.filter(youtube_id__in=ids)
    entries = [
        {
            "youtube_id": i.youtube_id,
            "title": i.title,
            "channel_id": i.channel_id,
            "vid_type": i.vid_type,
            "duration_seconds": i.duration_seconds,
            "published": i.published,
        }
        for i in found.order_by("-published")[:limit]
    ] + [
        {
            "youtube_id": i.youtube_id,
            "title": i.title,
            "channel_id": i.channel_id,
            "vid_type": i.vid_type,
            "duration_seconds": i.duration,
            "published": i.published,
        }
        for i in videos.order_by("-published")[:limit]
    ]
    entries.sort(
        key=lambda e: (e["published"] is not None, e["published"]),
        reverse=True,
    )
    return entries[:limit]


def preview_rule(
    scope: str,
    target_id: str | None,
    draft: dict | None | bool = False,
    limit: int = 50,
) -> dict:
    """
    run a rule against the last known videos of the target
    draft: rule fields, None for 'no rule', False for the stored rule
    """
    rules = RuleSet()
    if draft is False:
        stored = get_rule(**_target_kwargs(scope, target_id))
        rule = DownloadRule(**stored) if stored else None
    else:
        rule = DownloadRule(**draft) if draft else None
    playlist_id = target_id if scope == "playlist" else None
    ref = None
    if rule:
        ref = {"scope": scope, "target_id": target_id}
        ref["summary"] = rule_summary(rule)

    videos, reasons = [], {}
    for entry in _entries(scope, target_id, limit):
        rule_entry = {
            **entry,
            "published": (
                entry["published"].timestamp() if entry["published"] else None
            ),
        }
        verdict = rules.verdict(rule_entry, playlist_id, rule, ref)
        if not verdict["allowed"]:
            label = reason_label(verdict["reason_key"], rule, entry)
            group = (verdict["reason_key"], label)
            reasons[group] = reasons.get(group, 0) + 1
        videos.append({**entry, "verdict": verdict})

    skipped = sum(reasons.values())
    return {
        "total": len(videos),
        "download": len(videos) - skipped,
        "skipped": skipped,
        "reasons": [
            {"key": key, "label": label, "count": count}
            for (key, label), count in sorted(
                reasons.items(), key=lambda i: -i[1]
            )
        ],
        "videos": videos,
    }


def _source(rule: DownloadRule | None) -> tuple[dict | None, dict | None]:
    if rule is None:
        return None, None
    data = {key: getattr(rule, key) for key in RULE_FIELDS}
    if rule.channel_id:
        scope, target = "channel", rule.channel_id
    elif rule.playlist_id:
        scope, target = "playlist", rule.playlist_id
    else:
        scope, target = "default", None
    ref = {"scope": scope, "target_id": target, "summary": rule_summary(rule)}
    return ref, rule_dict(data)


def effective_rule(
    video: str | None = None,
    channel: str | None = None,
    playlist: str | None = None,
) -> dict:
    """which rule applies to a target, and where it comes from"""
    rules = RuleSet()
    override = None
    if video:
        override = rules.overrides.get(video)
        item = (
            Download.objects.filter(pk=video)
            .values("channel_id", "found_playlist_id")
            .first()
        ) or {
            "channel_id": Video.objects.filter(pk=video)
            .values_list("channel_id", flat=True)
            .first(),
            "found_playlist_id": None,
        }
        rule = rules.for_entry(item["channel_id"], item["found_playlist_id"])
    elif playlist:
        rule = rules.for_entry(None, playlist)
    else:
        rule = rules.for_channel(channel)
    source, data = _source(rule)
    return {"source": source, "rule": data, "override": override}


def override_dict(item: VideoOverride) -> dict:
    """an override with what TA knows about the video"""
    known = (
        Download.objects.filter(pk=item.youtube_id)
        .values("title", "channel_id")
        .first()
        or Video.objects.filter(pk=item.youtube_id)
        .values("title", "channel_id")
        .first()
        or {"title": None, "channel_id": None}
    )
    return {
        "youtube_id": item.youtube_id,
        "override": item.override,
        "note": item.note,
        "title": known["title"],
        "channel_id": known["channel_id"],
        "created_at": item.created_at,
    }


def list_overrides() -> list[dict]:
    """newest first"""
    return [
        override_dict(i) for i in VideoOverride.objects.order_by("-created_at")
    ]


def set_override(
    youtube_ids: list[str],
    override: str,
    note: str = "",
    created_by: str = CreatedBy.USER,
) -> list[int]:
    """
    store overrides and act on found videos: 'download' un-ignores them and
    queues high priority downloads, 'ignore' ignores them and cancels their
    queued downloads; returns the job ids
    """
    for youtube_id in youtube_ids:
        VideoOverride.objects.update_or_create(
            youtube_id=youtube_id,
            defaults={"override": override, "note": note},
        )
    found = Download.objects.filter(youtube_id__in=youtube_ids)
    if override == "ignore":
        cancel_waiting("download_video", youtube_ids)
        found.update(
            status=DownloadStatus.IGNORE,
            ignore_reason="always ignore, set by you",
            ignore_reason_key="always_ignore",
        )
        return []

    found.update(
        status=DownloadStatus.PENDING,
        ignore_reason=None,
        ignore_reason_key=None,
        message=None,
    )
    job_ids = []
    for youtube_id, title in found.values_list("youtube_id", "title"):
        job, _ = enqueue(
            "download_video",
            youtube_id,
            target_title=title,
            priority=Priority.HIGH,
            created_by=created_by,
            dispatch=False,
        )
        job_ids.append(job.pk)
    pump()
    return job_ids


def delete_override(youtube_id: str) -> bool:
    """rules decide again; False if there was none"""
    deleted, _ = VideoOverride.objects.filter(pk=youtube_id).delete()
    return bool(deleted)

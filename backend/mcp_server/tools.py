"""
Functionality:
- the MCP tools: name, description, input schema, annotations, handler
- every handler calls the same service functions as the REST API, so the
  UI, REST and MCP behave the same
"""

from datetime import timedelta

from channel.models import Channel, EmbyShow
from common.src.searching import SearchForm
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from download.models import Download, DownloadRule
from download.serializers import DownloadRuleSerializer
from download.src.add import add, preview
from download.src.auto_download import set_auto_download
from download.src.found import found_action, found_dicts, found_queryset
from download.src.rules_api import (
    PRESETS,
    delete_override,
    set_override,
    set_target_rule,
    target_exists,
)
from emby.src.numbering_api import preview_scheme, set_scheme, video_numbers
from emby.src.tags import clean_tags, set_tags
from jobs.models import ACTIVE, CreatedBy, Job, JobStatus, Priority
from jobs.src.engine import ActionNotAllowed, active_jobs, apply_action
from jobs.src.handlers import request_emby_write
from jobs.src.present import filter_jobs, jobs_dicts, summary
from jobs.src.schedules import schedule_dicts
from mediaserver.src.jobs import manual_notify
from video.models import Video

AGENT = CreatedBy.AGENT
SCHEMES = [
    "date",
    "year_frozen",
    "absolute_frozen",
    "playlist_frozen",
    "playlist_position",
]
PRESET_NAMES = [p["key"] for p in PRESETS if not p["needs_value"]]


class ToolError(Exception):
    """a problem the agent can fix; the message says how"""


RULE_SCHEMA = {
    "type": "object",
    "description": (
        "Download rule; empty conditions match everything. Durations are "
        'seconds. Example: {"min_duration": 3600} = only over an hour.'
    ),
    "properties": {
        "min_duration": {"type": ["integer", "null"], "minimum": 0},
        "max_duration": {"type": ["integer", "null"], "minimum": 0},
        "vid_types": {
            "type": "array",
            "items": {"enum": ["videos", "streams", "shorts"]},
            "description": "allowed types, empty = all",
        },
        "title_include": {"type": "string", "description": "regex"},
        "title_exclude": {"type": "string", "description": "regex"},
        "published_after": {
            "type": ["string", "null"],
            "format": "date",
        },
        "action": {
            "enum": ["ignore", "skip"],
            "description": "ignore: keep as ignored (default); skip: drop",
        },
        "enabled": {"type": "boolean"},
    },
    "additionalProperties": False,
}
PRESETS_SCHEMA = {
    "type": "array",
    "items": {"enum": PRESET_NAMES},
    "description": "one-click rules merged into the rule, e.g. no_shorts",
}
IDS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 1,
    "maxItems": 500,
}


def _rule(args: dict, required: bool = False) -> dict | None | bool:
    """rule + presets -> validated rule fields, None for no rule, False"""
    if "rule" not in args and not args.get("presets"):
        if required:
            raise ToolError("give rule (object or null) or presets")
        return False
    if args.get("rule") is None and "rule" in args and not args.get("presets"):
        return None
    data = {}
    for name in args.get("presets") or []:
        preset = next((p for p in PRESETS if p["key"] == name), None)
        if not preset or preset["needs_value"]:
            raise ToolError(f"unknown preset {name}; use {PRESET_NAMES}")
        data.update(preset["patch"])
    data.update(args.get("rule") or {})
    serializer = DownloadRuleSerializer(data=data)
    if not serializer.is_valid():
        raise ToolError(f"invalid rule: {serializer.errors}")
    return dict(serializer.validated_data)


def _ids(args: dict) -> list[str]:
    ids = args.get("ids")
    if not ids or not isinstance(ids, list):
        raise ToolError("give ids: a list of YouTube video ids")
    return [str(i) for i in ids]


def preview_url(args: dict) -> dict:
    """what links are and what the rules would do"""
    if not args.get("url"):
        raise ToolError("give url: one or more links, space separated")
    rule = _rule(args)
    if isinstance(rule, dict):
        rule = DownloadRule(**rule)
    return preview(args["url"], rule=rule, newest_n=args.get("newest_n"))


def add_url(args: dict) -> dict:
    """queue scoped scans; downloads follow as videos are found"""
    mode = args.get("mode", "once")
    if mode not in ("subscribe", "once", "newest_n"):
        raise ToolError("mode is subscribe, once or newest_n")
    if mode == "newest_n" and not args.get("newest_n"):
        raise ToolError("mode newest_n needs newest_n, e.g. 10")
    if args.get("numbering") and args["numbering"] not in SCHEMES:
        raise ToolError(f"numbering is one of {SCHEMES}")
    if not args.get("url"):
        raise ToolError("give url: one or more links, space separated")
    jobs, errors = add(
        args["url"],
        mode,
        newest_n=args.get("newest_n"),
        rule=_rule(args),
        save_rule=args.get("save_rule"),
        numbering=args.get("numbering"),
        download_now=args.get("download_now", True),
        priority=Priority.HIGH,
        created_by=AGENT,
        auto_download=args.get("auto_download"),
    )
    return {"jobs": jobs_dicts(jobs), "errors": errors}


def _auto_download(args: dict) -> bool | None:
    """the auto_download argument, None if not given"""
    value = args.get("auto_download")
    if value is not None and args.get("scope") not in ("channel", "playlist"):
        raise ToolError("auto_download is set per channel or playlist")
    return value


def set_rule(args: dict) -> dict:
    """
    store a rule on the default, a channel or a playlist, or a video;
    auto_download on a channel or playlist
    """
    scope, target_id = args.get("scope"), args.get("target_id")
    auto_download = _auto_download(args)
    if scope == "video":
        if not target_id:
            raise ToolError("give target_id: the YouTube video id")
        override = args.get("override")
        if override is None:
            return {"deleted": delete_override(target_id)}
        if override not in ("download", "ignore"):
            raise ToolError("override is download, ignore or null")
        jobs = set_override([target_id], override, created_by=AGENT)
        return {"override": override, "job_ids": jobs}
    if scope not in ("default", "channel", "playlist"):
        raise ToolError("scope is default, channel, playlist or video")
    if scope != "default" and not (
        target_id and target_exists(scope, target_id)
    ):
        raise ToolError(f"{scope} {target_id} is not in the archive")
    rule = _rule(args, required=auto_download is None)
    result = {}
    if rule is not False:
        result["rule"] = set_target_rule(scope, target_id, rule)
    if auto_download is not None:
        set_auto_download(scope, [target_id], auto_download)
        result["auto_download"] = auto_download
    return result


def set_numbering(args: dict) -> dict:
    """change the Emby numbering of a channel or playlist"""
    kind, series_id = args.get("kind"), args.get("id")
    if kind not in ("channel", "playlist") or not series_id:
        raise ToolError("give kind (channel or playlist) and id")
    if args.get("scheme") not in SCHEMES:
        raise ToolError(f"scheme is one of {SCHEMES}")
    try:
        if args.get("dry_run"):
            return preview_scheme(kind, series_id, args["scheme"])
        return set_scheme(kind, series_id, args["scheme"], AGENT)
    except ValueError as err:
        raise ToolError(str(err)) from err
    except ObjectDoesNotExist as err:
        raise ToolError(f"{kind} {series_id} is not in the archive") from err


def set_emby_tags(args: dict) -> dict:
    """set the Emby access tags of a channel or playlist"""
    kind, series_id = args.get("kind"), args.get("id")
    tags = args.get("tags")
    if kind not in ("channel", "playlist") or not series_id:
        raise ToolError("give kind (channel or playlist) and id")
    if not isinstance(tags, list):
        raise ToolError("give tags: a list of tag names, [] to hide it")
    if not set_tags(kind, [series_id], tags):
        raise ToolError(f"{kind} {series_id} is not in the archive")
    request_emby_write(created_by=AGENT)
    return {"kind": kind, "id": series_id, "emby_tags": clean_tags(tags)}


def set_emby_show(args: dict) -> dict:
    """whether a channel has its own show in Emby"""
    channel_id, show = args.get("id"), args.get("show")
    if show not in EmbyShow.values:
        raise ToolError(f"show is one of {EmbyShow.values}")
    if not Channel.objects.filter(pk=channel_id).update(emby_show=show):
        raise ToolError(f"channel {channel_id} is not in the archive")
    request_emby_write(created_by=AGENT)
    return {"id": channel_id, "emby_show": show}


def list_queue(args: dict) -> dict:
    """jobs, active by default"""
    filters = {key: args.get(key) for key in ["lane", "type", "status"]}
    filters["status"] = filters["status"] or "active"
    limit = min(int(args.get("limit", 50)), 200)
    try:
        queryset = filter_jobs(filters)
    except ValueError as err:
        raise ToolError(str(err)) from err
    jobs = list(queryset[:limit])
    return {
        "jobs": jobs_dicts(jobs),
        "total": queryset.count(),
        "has_more": queryset.count() > limit,
    }


def list_found(args: dict) -> dict:
    """found videos with verdicts"""
    filters = {
        "tab": args.get("tab", "waiting"),
        "q": args.get("q"),
        "channel": args.get("channel"),
        "sort": "-found",
        "downloaded_days": 7,
    }
    if filters["tab"] not in ("waiting", "ignored", "failed", "downloaded"):
        raise ToolError("tab is waiting, ignored, failed or downloaded")
    limit = min(int(args.get("limit", 50)), 200)
    queryset = found_queryset(filters)
    return {
        "videos": found_dicts(list(queryset[:limit])),
        "total": queryset.count(),
        "has_more": queryset.count() > limit,
    }


def download(args: dict) -> dict:
    """download found videos now, ignored ones included"""
    action = "download_now" if args.get("now", True) else "download"
    return found_action(action, ids=_ids(args), created_by=AGENT)


def ignore(args: dict) -> dict:
    """ignore found videos; always=true also ignores them in future"""
    action = "always_ignore" if args.get("always") else "ignore"
    return found_action(action, ids=_ids(args), created_by=AGENT)


def _job_ids(ids: list) -> list[int]:
    """job ids as given, or the download job of a video id"""
    numeric = [int(i) for i in ids if str(i).isdigit()]
    videos = [str(i) for i in ids if not str(i).isdigit()]
    found = active_jobs("download_video", videos)
    return numeric + [job.pk for job in found.values()]


def move_to_top(args: dict) -> dict:
    """run these first in their lane"""
    ids = args.get("ids")
    if not ids:
        raise ToolError("give ids: job ids or YouTube video ids")
    moved, skipped = [], {}
    for job_id in _job_ids(ids):
        try:
            moved.append(apply_action(job_id, "move_to_top").pk)
        except (ActionNotAllowed, Job.DoesNotExist) as err:
            skipped[str(job_id)] = str(err) or "not found"
    return {"moved": moved, "skipped": skipped}


def activity(args: dict) -> dict:
    """what runs, what's next, what's scheduled, what failed"""
    del args
    day_ago = timezone.now() - timedelta(days=1)
    running = filter_jobs({"status": "running"})
    queued = filter_jobs({"status": "queued"})
    failed = Job.objects.filter(
        status=JobStatus.FAILED, finished_at__gte=day_ago
    ).order_by("-finished_at")
    return {
        "summary": summary(),
        "running": jobs_dicts(list(running)),
        "next": jobs_dicts(list(queued[:10])),
        "scheduled": [
            {k: s[k] for k in ["name", "label", "enabled", "next_run"]}
            for s in schedule_dicts()
        ],
        "recent_errors": [
            {
                "id": job.pk,
                "type": job.type,
                "target": job.target_title or job.target_id,
                "error": job.error,
                "finished_at": job.finished_at,
            }
            for job in failed[:10]
        ],
    }


def stop_task(args: dict) -> dict:
    """stop a running job, or cancel a waiting one"""
    job = Job.objects.filter(pk=args.get("id")).first()
    if not job:
        raise ToolError("no job with that id; see list_queue")
    action = "stop" if job.status == JobStatus.RUNNING else "cancel"
    if job.status not in ACTIVE:
        raise ToolError(f"job {job.pk} already ended: {job.status}")
    return jobs_dicts([apply_action(job.pk, action)])[0]


def search_library(args: dict) -> dict:
    """search downloaded videos, channels and playlists"""
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("give query")
    limit = min(int(args.get("limit", 20)), 100)
    results = SearchForm().multi_search(query)["results"]
    return {
        "videos": [
            {
                "youtube_id": v["youtube_id"],
                "title": v["title"],
                "channel": v["channel"]["channel_name"],
                "published": v.get("published"),
                "duration": v.get("player", {}).get("duration_str"),
            }
            for v in results["video_results"][:limit]
        ],
        "channels": [
            {
                "channel_id": c["channel_id"],
                "name": c["channel_name"],
                "subscribed": c["channel_subscribed"],
            }
            for c in results["channel_results"][:limit]
        ],
        "playlists": [
            {"playlist_id": p["playlist_id"], "name": p["playlist_name"]}
            for p in results["playlist_results"][:limit]
        ],
    }


def get_video(args: dict) -> dict:
    """a downloaded video, or a found one"""
    youtube_id = args.get("youtube_id")
    doc = Video.load_doc(youtube_id) if youtube_id else None
    if doc:
        return {
            "state": "downloaded",
            "youtube_id": youtube_id,
            "title": doc["title"],
            "channel": doc["channel"]["channel_name"],
            "channel_id": doc["channel"]["channel_id"],
            "published": doc.get("published"),
            "duration": doc.get("player", {}).get("duration_str"),
            "vid_type": doc.get("vid_type"),
            "media_url": doc.get("media_url"),
            "watched": doc.get("player", {}).get("watched"),
            "numbering": video_numbers(youtube_id),
        }
    found = found_dicts(list(Download.objects.filter(pk=youtube_id or "")))
    if not found:
        raise ToolError(f"{youtube_id} is neither downloaded nor found")
    return found[0]


def notify_media_server(args: dict) -> dict:
    """tell Emby/Jellyfin about changed paths, or refresh everything"""
    try:
        job = manual_notify(args.get("paths"), bool(args.get("all")))
    except LookupError as err:
        raise ToolError("no media server configured in TA") from err
    except ValueError as err:
        raise ToolError(f"{err}; give paths under TA_EMBY_ROOT") from err
    return jobs_dicts([job])[0]


def _tool(name, title, description, properties, handler, **hints):
    required = hints.pop("required", [])
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "title": title,
            "readOnlyHint": hints.get("read_only", False),
            "destructiveHint": hints.get("destructive", False),
            "idempotentHint": hints.get("idempotent", False),
            "openWorldHint": hints.get("open_world", False),
        },
        "handler": handler,
    }


TOOLS = [
    _tool(
        "preview_url",
        "Preview links",
        "What pasted YouTube links are (video, channel, playlist) and what "
        "the download rules would do with their videos: counts, skipped "
        "reasons and a sample. Stores nothing. Pass rule/presets to try a "
        "rule before adding.",
        {
            "url": {"type": "string", "description": "links or ids"},
            "rule": RULE_SCHEMA,
            "presets": PRESETS_SCHEMA,
            "newest_n": {"type": "integer", "minimum": 1},
        },
        preview_url,
        required=["url"],
        read_only=True,
        open_world=True,
    ),
    _tool(
        "add_url",
        "Add links",
        "Add YouTube links: subscribe (keep checking), once (all videos "
        "now) or newest_n. Queues one high priority check per link; allowed "
        "videos start downloading as they are found. rule applies to this "
        "add only unless save_rule (default for subscribe). Follow progress "
        "with activity.",
        {
            "url": {"type": "string", "description": "links or ids"},
            "mode": {"enum": ["subscribe", "once", "newest_n"]},
            "newest_n": {"type": "integer", "minimum": 1},
            "rule": RULE_SCHEMA,
            "presets": PRESETS_SCHEMA,
            "save_rule": {"type": "boolean"},
            "numbering": {"enum": SCHEMES},
            "download_now": {"type": "boolean", "default": True},
            "auto_download": {
                "type": "boolean",
                "description": (
                    "store on the channels/playlists: later checks download "
                    "what the rules allow. Omit to leave it; a new "
                    "subscription starts off (unless the default says on)"
                ),
            },
        },
        add_url,
        required=["url"],
        open_world=True,
    ),
    _tool(
        "set_rule",
        "Set download rule",
        "Store the download rule of the default, a channel or a playlist "
        "(rule null deletes it), or a per-video override with scope video: "
        "override download (download anyway), ignore (always ignore) or "
        "null (rules decide again). auto_download (channel or playlist "
        "only) switches auto download: true makes its checks download what "
        "the rule allows; false (the default) leaves found videos waiting. "
        "Give rule/presets, auto_download, or both.",
        {
            "scope": {"enum": ["default", "channel", "playlist", "video"]},
            "target_id": {"type": "string"},
            "rule": {"anyOf": [RULE_SCHEMA, {"type": "null"}]},
            "presets": PRESETS_SCHEMA,
            "override": {"enum": ["download", "ignore", None]},
            "auto_download": {"type": "boolean"},
        },
        set_rule,
        required=["scope"],
        idempotent=True,
    ),
    _tool(
        "set_numbering",
        "Set Emby numbering",
        "Change how a channel or playlist is numbered in Emby. dry_run "
        "returns how many episodes would be renumbered without changing "
        "anything; check it first.",
        {
            "kind": {"enum": ["channel", "playlist"]},
            "id": {"type": "string"},
            "scheme": {"enum": SCHEMES},
            "dry_run": {"type": "boolean", "default": False},
        },
        set_numbering,
        required=["kind", "id", "scheme"],
        idempotent=True,
    ),
    _tool(
        "set_emby_tags",
        "Set Emby access tags",
        "Set the Emby tags of a channel or playlist, written to the show's "
        "NFO. Emby users limited to tagged items (David) see a show only "
        "if it carries one of their tags; [] hides it from them. Replaces "
        "the tags. A playlist's tags also name the Emby users who get it.",
        {
            "kind": {"enum": ["channel", "playlist"]},
            "id": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        set_emby_tags,
        required=["kind", "id", "tags"],
        idempotent=True,
    ),
    _tool(
        "set_emby_show",
        "Set a channel's Emby show",
        "Whether a channel has its own show in Emby. auto (default): only "
        "subscribed channels; always; never: its videos are in the single "
        "Other videos show, reached through playlists.",
        {
            "id": {"type": "string", "description": "channel id"},
            "show": {"enum": EmbyShow.values},
        },
        set_emby_show,
        required=["id", "show"],
        idempotent=True,
    ),
    _tool(
        "list_queue",
        "List jobs",
        "Jobs of the work queue: status active (default), history, or a "
        "comma list of queued, running, paused, done, failed, cancelled.",
        {
            "status": {"type": "string"},
            "lane": {
                "enum": ["scan", "download", "postprocess", "maintenance"]
            },
            "type": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        list_queue,
        read_only=True,
    ),
    _tool(
        "list_found",
        "List found videos",
        "Videos TA found but hasn't downloaded, with the rule verdict and "
        "download state. Use their youtube_id with download or ignore.",
        {
            "tab": {"enum": ["waiting", "ignored", "failed", "downloaded"]},
            "q": {"type": "string", "description": "search title/channel"},
            "channel": {"type": "string", "description": "channel id"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        list_found,
        read_only=True,
    ),
    _tool(
        "download",
        "Download videos",
        "Download found videos by YouTube id, ignored ones included. now "
        "(default) puts them ahead of scheduled work.",
        {"ids": IDS_SCHEMA, "now": {"type": "boolean", "default": True}},
        download,
        required=["ids"],
    ),
    _tool(
        "ignore",
        "Ignore videos",
        "Ignore found videos and cancel their queued downloads. always "
        "also ignores them in future checks.",
        {"ids": IDS_SCHEMA, "always": {"type": "boolean", "default": False}},
        ignore,
        required=["ids"],
        idempotent=True,
    ),
    _tool(
        "move_to_top",
        "Move to top",
        "Run these next in their lane: job ids, or YouTube ids of videos "
        "with a queued download.",
        {"ids": {"type": "array", "items": {"type": ["string", "integer"]}}},
        move_to_top,
        required=["ids"],
    ),
    _tool(
        "activity",
        "Activity",
        "What is running (with progress), what is next, when scheduled "
        "checks run, and errors of the last 24 hours.",
        {},
        activity,
        read_only=True,
    ),
    _tool(
        "stop_task",
        "Stop job",
        "Stop a running job or cancel a waiting one, by job id.",
        {"id": {"type": "integer"}},
        stop_task,
        required=["id"],
        destructive=True,
    ),
    _tool(
        "search_library",
        "Search library",
        "Search downloaded videos, channels and playlists.",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        search_library,
        required=["query"],
        read_only=True,
    ),
    _tool(
        "get_video",
        "Get video",
        "One video by YouTube id: downloaded (with Emby numbering) or found "
        "(with verdict and download state).",
        {"youtube_id": {"type": "string"}},
        get_video,
        required=["youtube_id"],
        read_only=True,
    ),
    _tool(
        "notify_media_server",
        "Notify media server",
        "Tell Emby/Jellyfin that paths under TA's Emby root changed, or "
        "all=true for a full library refresh.",
        {
            "paths": {"type": "array", "items": {"type": "string"}},
            "all": {"type": "boolean"},
        },
        notify_media_server,
        idempotent=True,
        open_world=True,
    ),
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}


def describe() -> list[dict]:
    """tools/list result entries"""
    return [
        {k: v for k, v in tool.items() if k != "handler"} for tool in TOOLS
    ]

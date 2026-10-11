"""
Functionality:
- preview pasted links: what they are and what the rules would do
- add pasted links: one high priority scan job per link, scoped to it
- shared by the REST views and the MCP server
"""

from datetime import datetime, timezone

from appsettings.src.config import AppConfig
from channel.models import Channel
from channel.src.index import YoutubeChannel
from channel.src.remote_query import get_last_channel_videos
from common.src.ta_redis import RedisArchivist
from common.src.urlparser import Parser
from download.models import Download, DownloadRule
from download.serializers import DownloadRuleSerializer
from download.src.rules import RuleSet, reason_label, rule_ref, rule_summary
from jobs.models import CreatedBy, Job, Priority
from jobs.src.engine import enqueue
from playlist.models import Playlist
from playlist.src.index import YoutubePlaylist
from video.models import Video
from video.src.index import YoutubeVideo

# videos listed per channel tab for a preview
PREVIEW_LIMIT = 500
SAMPLE_SIZE = 50
CACHE_SECONDS = 600
JOB_TYPES = {
    "video": "fetch_video",
    "channel": "check_channel",
    "playlist": "check_playlist",
}


def split_links(text: str) -> list[str]:
    """links or ids, separated by whitespace or commas"""
    return [i for i in text.replace(",", " ").split() if i]


def parse_links(text: str) -> tuple[list[tuple[str, dict]], list[dict]]:
    """([(link, parsed)], [{url, error}]); one bad link fails alone"""
    parsed, errors = [], []
    for link in split_links(text):
        try:
            parsed.extend((link, item) for item in Parser(link).parse())
        except ValueError as err:
            errors.append({"url": link, "error": str(err) or "not a link"})
    return parsed, errors


def _cached(key: str, fetch) -> dict:
    """listing from redis, or fetched and cached for 10 minutes"""
    redis = RedisArchivist()
    cached = redis.get_message_dict(f"add:preview:{key}")
    if cached:
        return cached
    listing = fetch()
    redis.set_message(f"add:preview:{key}", listing, expire=CACHE_SECONDS)
    return listing


def _entry(item: dict, channel_id: str, default_type: str) -> dict:
    """flat yt-dlp entry -> what rules look at"""
    return {
        "youtube_id": item["id"],
        "title": item.get("title") or "",
        "channel_id": item.get("channel_id") or channel_id,
        "vid_type": item.get("vid_type") or default_type,
        "duration_seconds": (
            int(item["duration"])
            if isinstance(item.get("duration"), (int, float))
            else None
        ),
        "published": item.get("timestamp") or item.get("release_timestamp"),
    }


def _fetch_channel(channel_id: str) -> dict:
    channel = YoutubeChannel(channel_id)
    channel.get_from_youtube()
    if not channel.youtube_meta:
        raise LookupError(channel.error or "channel not found on YouTube")
    meta = channel.youtube_meta
    thumbs = [t for t in meta.get("thumbnails", []) if t.get("width")]
    square = [t["url"] for t in thumbs if t.get("width") == t.get("height")]
    config = AppConfig().config
    videos = get_last_channel_videos(channel_id, config, limit=PREVIEW_LIMIT)
    return {
        "title": meta.get("uploader") or meta.get("channel") or channel_id,
        "channel_id": channel_id,
        "channel_name": meta.get("uploader") or channel_id,
        "thumb_url": square[-1] if square else None,
        "entries": [_entry(v, channel_id, "videos") for v in videos],
    }


def _fetch_playlist(playlist_id: str) -> dict:
    playlist = YoutubePlaylist(playlist_id)
    playlist.get_from_youtube()
    if not playlist.youtube_meta:
        raise LookupError(playlist.error or "playlist not found on YouTube")
    meta = playlist.youtube_meta
    channel_id = meta.get("channel_id")
    thumbs = meta.get("thumbnails") or [{}]
    return {
        "title": meta.get("title") or playlist_id,
        "channel_id": channel_id,
        "channel_name": meta.get("channel"),
        "thumb_url": thumbs[-1].get("url"),
        "entries": [
            _entry(v, channel_id, "unknown") for v in meta.get("entries", [])
        ],
    }


def _fetch_video(youtube_id: str) -> dict:
    video = YoutubeVideo(youtube_id)
    video.get_from_youtube()
    if not video.youtube_meta:
        raise LookupError(video.error or "video not found on YouTube")
    meta = video.youtube_meta
    entry = _entry(meta, meta.get("channel_id"), "videos")
    return {
        "title": meta.get("title") or youtube_id,
        "channel_id": meta.get("channel_id"),
        "channel_name": meta.get("channel"),
        "thumb_url": meta.get("thumbnail"),
        "entries": [entry],
    }


FETCH = {
    "channel": _fetch_channel,
    "playlist": _fetch_playlist,
    "video": _fetch_video,
}


def _known(ids: list[str]) -> dict[str, str]:
    """youtube_id -> downloaded, waiting or ignored"""
    known = {
        youtube_id: "ignored" if status == "ignore" else "waiting"
        for youtube_id, status in Download.objects.filter(
            youtube_id__in=ids
        ).values_list("youtube_id", "status")
    }
    known.update(
        {
            i: "downloaded"
            for i in Video.objects.filter(youtube_id__in=ids).values_list(
                "youtube_id", flat=True
            )
        }
    )
    return known


def _published(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _numbering(kind: str, item_id: str, channel_id: str | None) -> dict:
    if kind == "playlist":
        model, pk, source = Playlist, item_id, "playlist"
    else:
        model, pk, source = Channel, channel_id, "channel"
    scheme = (
        model.objects.filter(pk=pk)
        .values_list("emby_numbering", flat=True)
        .first()
    )
    if scheme:
        return {"scheme": scheme, "source": source}
    default = "playlist_frozen" if kind == "playlist" else "date"
    return {"scheme": default, "source": "default"}


def _subscribed(kind: str, item_id: str, channel_id: str | None) -> bool:
    if kind == "playlist":
        return Playlist.objects.filter(pk=item_id, subscribed=True).exists()
    return Channel.objects.filter(pk=channel_id, subscribed=True).exists()


def preview_item(
    kind: str,
    item_id: str,
    rule: DownloadRule | None | bool = False,
    newest_n: int | None = None,
) -> dict:
    """
    what an item is and what the rules would do; rule False uses the
    stored rules, None no rule, a DownloadRule the draft; LookupError
    """
    listing = _cached(f"{kind}:{item_id}", lambda: FETCH[kind](item_id))
    entries = listing["entries"]
    if newest_n:
        entries = entries[:newest_n]
    channel_id = listing["channel_id"]
    rules = RuleSet()
    playlist_id = item_id if kind == "playlist" else None
    if rule is False:
        ref = rule_ref(rules.for_entry(channel_id, playlist_id))
    elif rule is None:
        ref = None
    else:
        ref = {
            "scope": "playlist" if playlist_id else "channel",
            "target_id": playlist_id or channel_id,
            "summary": rule_summary(rule),
        }

    known = _known([e["youtube_id"] for e in entries])
    sample, reasons = [], {}
    counts = {"download": 0, "skipped": 0, "already_have": 0}
    for entry in entries:
        verdict = rules.verdict(
            entry, playlist_id, rule, ref if rule is not False else False
        )
        state = known.get(entry["youtube_id"])
        if state:
            counts["already_have"] += 1
        elif verdict["allowed"]:
            counts["download"] += 1
        else:
            counts["skipped"] += 1
            used = rule
            if rule is False:
                used = rules.for_entry(entry["channel_id"], playlist_id)
            label = reason_label(verdict["reason_key"], used, entry)
            group = (verdict["reason_key"], label)
            reasons[group] = reasons.get(group, 0) + 1
        if len(sample) < SAMPLE_SIZE:
            sample.append(
                {
                    "youtube_id": entry["youtube_id"],
                    "title": entry["title"],
                    "vid_type": entry["vid_type"],
                    "duration_seconds": entry["duration_seconds"],
                    "published": _published(entry["published"]),
                    "known": state,
                    "verdict": verdict,
                }
            )

    known_counts = {"downloaded": 0, "waiting": 0, "ignored": 0}
    for state in known.values():
        known_counts[state] += 1
    return {
        "kind": kind,
        "id": item_id,
        "title": listing["title"],
        "channel_id": channel_id,
        "channel_name": listing["channel_name"],
        "thumb_url": listing["thumb_url"],
        "video_count": len(listing["entries"]),
        "subscribed": _subscribed(kind, item_id, channel_id),
        "known": known_counts,
        "rule": ref,
        "numbering": _numbering(kind, item_id, channel_id),
        "verdict": {
            "total": len(entries),
            **counts,
            "reasons": [
                {"key": key, "label": label, "count": count}
                for (key, label), count in sorted(
                    reasons.items(), key=lambda i: -i[1]
                )
            ],
        },
        "sample": sample,
    }


def preview(
    text: str,
    rule: DownloadRule | None | bool = False,
    newest_n: int | None = None,
) -> dict:
    """preview every pasted link"""
    parsed, errors = parse_links(text)
    items = []
    for link, item in parsed:
        try:
            result = preview_item(item["type"], item["url"], rule, newest_n)
        except LookupError as err:
            errors.append({"url": link, "error": str(err)})
            continue
        items.append({"url": link, **result})
    return {"items": items, "errors": errors}


def _rule_data(rule: dict | None) -> dict | None:
    """validated rule -> JSON for job params"""
    if rule is None:
        return None
    return dict(DownloadRuleSerializer(rule).data)


def add(
    text: str,
    mode: str,
    *,
    newest_n: int | None = None,
    rule: dict | None | bool = False,
    save_rule: bool | None = None,
    numbering: str | None = None,
    download_now: bool = True,
    priority: int = Priority.HIGH,
    created_by: str = CreatedBy.USER,
) -> tuple[list[Job], list[dict]]:
    """
    queue one scan job per link, returns (jobs, errors)
    rule: validated rule dict, None for 'no rule', False to keep the rules
    """
    parsed, errors = parse_links(text)
    if save_rule is None:
        save_rule = mode == "subscribe"
    jobs = []
    for _, item in parsed:
        kind, item_id = item["type"], item["url"]
        params: dict = {"download_now": download_now}
        if mode == "subscribe":
            params["subscribe"] = True
        if mode == "newest_n":
            params["newest_n"] = newest_n
        if mode == "once" and kind == "channel":
            entry = {"type": "channel", "url": item_id}
            params["entries"] = [{**entry, "vid_type": "unknown"}]
        if kind == "video":
            vid_type = getattr(item["vid_type"], "value", item["vid_type"])
            params["vid_type"] = str(vid_type)
        if rule is not False:
            stored = save_rule and kind != "video"
            params["save_rule" if stored else "rule"] = _rule_data(rule)
        if numbering and kind != "video":
            params["numbering"] = numbering
        job, _ = enqueue(
            JOB_TYPES[kind],
            item_id,
            params=params,
            priority=priority,
            created_by=created_by,
        )
        jobs.append(job)
    return jobs, errors

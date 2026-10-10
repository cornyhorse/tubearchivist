"""aggregations for the stats page, from the database"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from channel.models import Channel as ChannelModel
from common.src.env_settings import EnvironmentSettings
from common.src.helper import get_duration_str
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDay
from download.models import Download as DownloadModel
from playlist.models import Playlist as PlaylistModel
from video.models import Video as VideoModel


def _totals(queryset) -> dict:
    """doc_count, media_size, duration of a video queryset"""
    totals = queryset.aggregate(
        doc_count=Count("youtube_id"),
        media_size=Sum("media_size", default=0),
        duration=Sum("duration", default=0),
    )
    totals["duration_str"] = get_duration_str(totals["duration"])
    return totals


class Video:
    """get video stats"""

    def process(self):
        """totals, by vid_type and by active"""
        videos = VideoModel.objects.all()
        response = _totals(videos)
        for vid_type in (
            videos.order_by().values_list("vid_type", flat=True).distinct()
        ):
            response[f"type_{vid_type}"] = _totals(
                videos.filter(vid_type=vid_type)
            )
        for active in (
            videos.order_by().values_list("active", flat=True).distinct()
        ):
            key = f"active_{str(active).lower()}"
            response[key] = _totals(videos.filter(active=active))

        return response


def _active_subscribed(queryset) -> dict:
    """doc_count plus active_/subscribed_ true/false counts"""
    counts = queryset.aggregate(
        total=Count("pk"),
        active_true=Count("pk", filter=Q(active=True)),
        active_false=Count("pk", filter=Q(active=False)),
        subscribed_true=Count("pk", filter=Q(subscribed=True)),
        subscribed_false=Count("pk", filter=Q(subscribed=False)),
    )
    response = {"doc_count": counts.pop("total")}
    # like ES terms buckets, empty buckets are left out
    response.update({k: v for k, v in counts.items() if v})
    return response


class Channel:
    """get channel stats"""

    def process(self):
        """process aggregation"""
        return _active_subscribed(ChannelModel.objects.all())


class Playlist:
    """get playlist stats"""

    def process(self):
        """process aggregation"""
        return _active_subscribed(PlaylistModel.objects.all())


class Download:
    """get downloads queue stats"""

    def process(self):
        """counts by status, and pending by vid_type"""
        queue = DownloadModel.objects.order_by()
        response = {
            row["status"]: row["count"]
            for row in queue.values("status").annotate(count=Count("pk"))
        }
        pending = queue.filter(status="pending").values("vid_type")
        for row in pending.annotate(count=Count("pk")):
            response[f"pending_{row['vid_type']}"] = row["count"]

        return response


class WatchProgress:
    """get watch progress"""

    def process(self):
        """totals, and per watched state with share of total duration"""
        videos = VideoModel.objects.all()
        total = _totals(videos)
        all_duration = total["duration"]
        response = {
            "total": {
                "duration": all_duration,
                "duration_str": total["duration_str"],
                "items": total["doc_count"],
            }
        }
        for watched, key in ((False, "unwatched"), (True, "watched")):
            bucket = _totals(videos.filter(watched=watched))
            if not bucket["doc_count"]:
                continue
            response[key] = {
                "duration": bucket["duration"],
                "duration_str": bucket["duration_str"],
                "progress": (
                    bucket["duration"] / all_duration if all_duration else 0
                ),
                "items": bucket["doc_count"],
            }

        return response


class DownloadHist:
    """get downloads histogram last week"""

    def process(self):
        """per day in TZ since 7 days ago, newest first, gaps filled"""
        tz = ZoneInfo(EnvironmentSettings.TZ)
        today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        since = today - timedelta(days=7)
        rows = (
            VideoModel.objects.filter(date_downloaded__gte=since)
            .annotate(day=TruncDay("date_downloaded", tzinfo=tz))
            .values("day")
            .annotate(count=Count("youtube_id"), media_size=Sum("media_size"))
            .order_by("-day")
        )
        by_day = {row["day"].date(): row for row in rows}
        if not by_day:
            return []

        response = []
        day = max(by_day)
        while day >= min(by_day):
            row = by_day.get(day)
            response.append(
                {
                    "date": day.isoformat(),
                    "count": row["count"] if row else 0,
                    "media_size": row["media_size"] if row else 0,
                }
            )
            day -= timedelta(days=1)

        return response


class BiggestChannel:
    """get the 10 biggest channels by order"""

    order_choices = ["doc_count", "duration", "media_size"]

    def __init__(self, order):
        self.order = order

    def process(self):
        """process aggregation, order_by validated in the view"""
        rows = (
            VideoModel.objects.values("channel__name", "channel_id")
            .annotate(
                doc_count=Count("youtube_id"),
                duration=Sum("duration"),
                media_size=Sum("media_size"),
            )
            .order_by(f"-{self.order}", "channel_id")[:10]
        )

        return [
            {
                "id": row["channel_id"],
                "name": row["channel__name"].title(),
                "doc_count": row["doc_count"],
                "duration": row["duration"],
                "duration_str": get_duration_str(row["duration"]),
                "media_size": row["media_size"],
            }
            for row in rows
        ]

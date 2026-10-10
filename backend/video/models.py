"""video, subtitle and comment models"""

from channel.models import Channel
from common.src.db import WeightedSearchVector, from_epoch, to_epoch
from common.src.helper import get_duration_str
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import connection, models
from video.src.constants import VideoTypeEnum


class VideoQuerySet(models.QuerySet):
    """video queries"""

    def similar_to(self, youtube_id: str, limit: int = 6) -> list["Video"]:
        """
        videos sharing title or tag terms with youtube_id, best match first;
        replaces ES more_like_this on tags and title
        """
        sql = """
            WITH terms AS (
                SELECT to_tsquery(
                    'simple',
                    string_agg(quote_literal(lexeme), ' | ')
                ) AS query
                FROM video_video,
                    unnest(tsvector_to_array(ts_filter(search, '{a,b}')))
                        AS lexeme
                WHERE youtube_id = %s
            )
            SELECT v.youtube_id
            FROM video_video v, terms
            WHERE terms.query IS NOT NULL
              AND v.youtube_id <> %s
              AND v.search @@ terms.query
            ORDER BY ts_rank(v.search, terms.query) DESC, v.youtube_id
            LIMIT %s
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [youtube_id, youtube_id, limit])
            ids = [row[0] for row in cursor.fetchall()]

        found = self.select_related("channel").in_bulk(ids)
        return [found[i] for i in ids]


class Video(models.Model):
    """a downloaded video"""

    youtube_id = models.CharField(primary_key=True, max_length=32)
    channel = models.ForeignKey(
        Channel, on_delete=models.PROTECT, related_name="videos"
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    category = ArrayField(models.TextField(), default=list, blank=True)
    tags = ArrayField(models.TextField(), default=list, blank=True)
    vid_type = models.CharField(
        max_length=16,
        choices=[(i.value, i.value) for i in VideoTypeEnum],
        default=VideoTypeEnum.VIDEOS.value,
    )
    active = models.BooleanField(default=True)

    published = models.DateTimeField(null=True, blank=True)
    date_downloaded = models.DateTimeField()
    last_refresh = models.DateTimeField(null=True, blank=True)

    media_url = models.TextField()
    media_size = models.BigIntegerField(default=0)
    duration = models.IntegerField(default=0)
    vid_thumb_url = models.TextField(blank=True, default="")

    # first video stream, denormalized from streams for sort and filter
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)

    watched = models.BooleanField(default=False)
    watched_date = models.DateTimeField(null=True, blank=True)

    view_count = models.BigIntegerField(null=True, blank=True)
    like_count = models.BigIntegerField(null=True, blank=True)
    dislike_count = models.BigIntegerField(null=True, blank=True)
    average_rating = models.FloatField(null=True, blank=True)
    comment_count = models.IntegerField(null=True, blank=True)

    # semi structured, read whole, never queried by field
    streams = models.JSONField(default=list, blank=True)
    subtitles = models.JSONField(default=list, blank=True)
    sponsorblock = models.JSONField(null=True, blank=True)

    objects = VideoQuerySet.as_manager()

    search = models.GeneratedField(
        expression=WeightedSearchVector(
            ("title", "A", False),
            ("tags", "B", True),
            ("category", "C", True),
            ("description", "D", False),
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["published"]),
            models.Index(fields=["date_downloaded"]),
            models.Index(fields=["channel", "published"]),
            models.Index(fields=["vid_type"]),
            GinIndex(fields=["search"], name="video_search_gin"),
            GinIndex(
                name="video_title_trgm",
                fields=["title"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.youtube_id} ({self.title})"

    @staticmethod
    def _first_video_stream(streams: list) -> dict:
        for stream in streams or []:
            if stream.get("type") == "video":
                return stream
        return {}

    @classmethod
    def fields_from_doc(cls, doc: dict) -> dict:
        """map legacy ta_video document to model fields, minus channel"""
        player = doc.get("player") or {}
        stats = doc.get("stats") or {}
        streams = doc.get("streams") or []
        video_stream = cls._first_video_stream(streams)
        return {
            "youtube_id": doc["youtube_id"],
            "title": doc.get("title") or "",
            "description": doc.get("description"),
            "category": list(doc.get("category") or []),
            "tags": list(doc.get("tags") or []),
            "vid_type": doc.get("vid_type") or VideoTypeEnum.VIDEOS.value,
            "active": bool(doc.get("active", True)),
            "published": from_epoch(doc.get("published")),
            "date_downloaded": from_epoch(doc.get("date_downloaded")),
            "last_refresh": from_epoch(doc.get("vid_last_refresh")),
            "media_url": doc.get("media_url") or "",
            "media_size": doc.get("media_size") or 0,
            "duration": player.get("duration") or 0,
            "vid_thumb_url": doc.get("vid_thumb_url") or "",
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "watched": bool(player.get("watched", False)),
            "watched_date": from_epoch(player.get("watched_date")),
            "view_count": stats.get("view_count"),
            "like_count": stats.get("like_count"),
            "dislike_count": stats.get("dislike_count"),
            "average_rating": stats.get("average_rating"),
            "comment_count": doc.get("comment_count"),
            "streams": streams,
            "subtitles": doc.get("subtitles") or [],
            "sponsorblock": doc.get("sponsorblock"),
        }

    @classmethod
    def save_doc(cls, doc: dict) -> "Video":
        """create or replace from legacy document, channel must exist"""
        fields = cls.fields_from_doc(doc)
        youtube_id = fields.pop("youtube_id")
        fields["channel_id"] = doc["channel"]["channel_id"]
        video, _ = cls.objects.update_or_create(
            youtube_id=youtube_id, defaults=fields
        )
        return video

    @classmethod
    def load_doc(cls, youtube_id: str) -> dict | None:
        """legacy document of a video, None if not indexed"""
        video = (
            cls.objects.select_related("channel")
            .filter(youtube_id=youtube_id)
            .first()
        )
        return video.to_doc() if video else None

    @classmethod
    def from_doc(cls, doc: dict) -> "Video":
        """build unsaved instance from legacy document"""
        channel_id = (doc.get("channel") or {}).get("channel_id")
        return cls(channel_id=channel_id, **cls.fields_from_doc(doc))

    def to_doc(self, playlists: list[str] | None = None) -> dict:
        """
        legacy ta_video document shape, as the API returns it
        playlists: ids of playlists containing this video, queried if None
        """
        if playlists is None:
            from playlist.models import (  # pylint: disable=import-outside-toplevel
                PlaylistEntry,
            )

            playlists = sorted(
                set(
                    PlaylistEntry.objects.filter(
                        youtube_id=self.youtube_id
                    ).values_list("playlist_id", flat=True)
                )
            )

        player = {
            "duration": self.duration,
            "duration_str": get_duration_str(self.duration),
            "watched": self.watched,
        }
        if self.watched_date:
            player["watched_date"] = to_epoch(self.watched_date)

        doc = {
            "youtube_id": self.youtube_id,
            "title": self.title,
            "description": self.description,
            "category": list(self.category),
            "tags": list(self.tags),
            "vid_type": self.vid_type,
            "active": self.active,
            "published": to_epoch(self.published),
            "date_downloaded": to_epoch(self.date_downloaded),
            "vid_last_refresh": to_epoch(self.last_refresh),
            "media_url": self.media_url,
            "media_size": self.media_size,
            "vid_thumb_url": self.vid_thumb_url,
            "player": player,
            "stats": {
                "view_count": self.view_count,
                "like_count": self.like_count,
                "dislike_count": self.dislike_count,
                "average_rating": self.average_rating,
            },
            "streams": self.streams,
            "channel": self.channel.to_doc(),
        }
        if self.subtitles:
            doc["subtitles"] = self.subtitles
        if self.sponsorblock is not None:
            doc["sponsorblock"] = self.sponsorblock
        if self.comment_count is not None:
            doc["comment_count"] = self.comment_count
        if playlists:
            doc["playlist"] = playlists

        return doc


class SubtitleLine(models.Model):
    """one indexed subtitle line, for full text search"""

    fragment_id = models.CharField(max_length=128, unique=True)
    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="subtitle_lines"
    )
    lang = models.CharField(max_length=16)
    source = models.CharField(max_length=16)
    index = models.IntegerField()
    start = models.CharField(max_length=16)
    end = models.CharField(max_length=16)
    line = models.TextField()
    last_refresh = models.DateTimeField(null=True, blank=True)

    search = models.GeneratedField(
        expression=WeightedSearchVector(("line", "A", False)),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["video", "lang", "index"]),
            GinIndex(fields=["search"], name="subtitle_search_gin"),
        ]

    @classmethod
    def fields_from_doc(cls, doc: dict) -> dict:
        """map legacy ta_subtitle document to model fields"""
        return {
            "fragment_id": doc["subtitle_fragment_id"],
            "video_id": doc["youtube_id"],
            "lang": doc.get("subtitle_lang") or "",
            "source": doc.get("subtitle_source") or "",
            "index": doc.get("subtitle_index") or 0,
            "start": doc.get("subtitle_start") or "",
            "end": doc.get("subtitle_end") or "",
            "line": doc.get("subtitle_line") or "",
            "last_refresh": from_epoch(doc.get("subtitle_last_refresh")),
        }

    def to_doc(self) -> dict:
        """legacy ta_subtitle document shape"""
        return {
            "subtitle_fragment_id": self.fragment_id,
            "youtube_id": self.video_id,
            "title": self.video.title,
            "subtitle_channel": self.video.channel.name,
            "subtitle_channel_id": self.video.channel_id,
            "subtitle_lang": self.lang,
            "subtitle_source": self.source,
            "subtitle_index": self.index,
            "subtitle_start": self.start,
            "subtitle_end": self.end,
            "subtitle_line": self.line,
            "subtitle_last_refresh": to_epoch(self.last_refresh),
        }


class Comment(models.Model):
    """one youtube comment of a video, threads via parent"""

    video = models.ForeignKey(
        Video, on_delete=models.CASCADE, related_name="comments"
    )
    position = models.IntegerField()
    comment_id = models.CharField(max_length=128)
    parent = models.CharField(max_length=128)
    author = models.TextField(blank=True, default="")
    author_id = models.CharField(max_length=64, blank=True, default="")
    author_is_uploader = models.BooleanField(default=False)
    author_thumbnail = models.TextField(blank=True, default="")
    text = models.TextField(blank=True, default="")
    time_text = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(null=True, blank=True)
    like_count = models.IntegerField(null=True, blank=True)
    is_favorited = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["video", "comment_id"], name="comment_unique_id"
            ),
        ]
        ordering = ["video", "position"]

    @classmethod
    def fields_from_doc(cls, comment: dict, position: int) -> dict:
        """map one entry of legacy comment_comments to model fields"""
        return {
            "position": position,
            "comment_id": comment["comment_id"],
            "parent": comment.get("comment_parent") or "root",
            "author": comment.get("comment_author") or "",
            "author_id": comment.get("comment_author_id") or "",
            "author_is_uploader": bool(
                comment.get("comment_author_is_uploader")
            ),
            "author_thumbnail": comment.get("comment_author_thumbnail") or "",
            "text": comment.get("comment_text") or "",
            "time_text": comment.get("comment_time_text") or "",
            "timestamp": from_epoch(comment.get("comment_timestamp")),
            "like_count": comment.get("comment_likecount"),
            "is_favorited": bool(comment.get("comment_is_favorited")),
        }

    def to_doc(self) -> dict:
        """legacy comment_comments entry"""
        return {
            "comment_id": self.comment_id,
            "comment_parent": self.parent,
            "comment_author": self.author,
            "comment_author_id": self.author_id,
            "comment_author_is_uploader": self.author_is_uploader,
            "comment_author_thumbnail": self.author_thumbnail,
            "comment_text": self.text,
            "comment_time_text": self.time_text,
            "comment_timestamp": to_epoch(self.timestamp),
            "comment_likecount": self.like_count,
            "comment_is_favorited": self.is_favorited,
        }


class CommentRefresh(models.Model):
    """when the comments of a video were last fetched"""

    video = models.OneToOneField(
        Video,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="comment_refresh",
    )
    last_refresh = models.DateTimeField()

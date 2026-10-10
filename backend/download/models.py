"""download queue model"""

from common.src.db import from_epoch, to_epoch
from django.db import models, transaction
from video.src.constants import VideoTypeEnum


class DownloadStatus(models.TextChoices):
    """state of an item in the download queue"""

    PENDING = "pending"
    IGNORE = "ignore"


class DownloadQuerySet(models.QuerySet):
    """download queue queries"""

    def matching(
        self,
        status: str | None = None,
        channel_id: str | None = None,
        vid_type: str | None = None,
        error: bool | None = None,
    ):
        """filter used by the bulk queue actions and the queue view"""
        queryset = self
        if status:
            queryset = queryset.filter(status=status)
        if channel_id:
            queryset = queryset.filter(channel_id=channel_id)
        if vid_type:
            queryset = queryset.filter(vid_type=vid_type)
        if error is True:
            queryset = queryset.exclude(message__isnull=True)
        elif error is False:
            queryset = queryset.filter(message__isnull=True)
        return queryset

    def queue_order(self):
        """order the downloader works in: auto start first, then oldest"""
        return self.order_by("-auto_start", "timestamp", "youtube_id")

    def next_pending(self, auto_only: bool = False) -> "Download | None":
        """
        lock and return the next item to download, or None
        call inside transaction.atomic(); skips items locked by another worker
        """
        queryset = self.filter(
            status=DownloadStatus.PENDING, message__isnull=True
        )
        if auto_only:
            queryset = queryset.filter(auto_start=True)

        return (
            queryset.queue_order()
            .select_for_update(skip_locked=True)
            .first()
        )

    def set_status(self, new_status: str) -> int:
        """bulk status change, 'priority' and 'clear_error' are actions"""
        if new_status == "priority":
            return self.update(
                status=DownloadStatus.PENDING, auto_start=True, message=None
            )
        if new_status == "clear_error":
            return self.update(message=None)
        if new_status not in DownloadStatus.values:
            raise ValueError(f"invalid status: {new_status}")
        return self.update(status=new_status)

    def reset_auto_start(self) -> int:
        """clear auto_start on all items, after a download run"""
        return self.filter(auto_start=True).update(auto_start=False)


class Download(models.Model):
    """an item in the download queue, or an ignored video"""

    youtube_id = models.CharField(primary_key=True, max_length=32)
    title = models.TextField(blank=True, default="")
    # the channel may not be indexed yet, so no foreign key
    channel_id = models.CharField(max_length=64)
    channel_name = models.TextField(blank=True, default="")
    vid_type = models.CharField(
        max_length=16,
        choices=[(i.value, i.value) for i in VideoTypeEnum],
        default=VideoTypeEnum.VIDEOS.value,
    )
    duration = models.CharField(max_length=32, blank=True, default="")
    published = models.DateTimeField(null=True, blank=True)
    timestamp = models.DateTimeField()
    status = models.CharField(
        max_length=16,
        choices=DownloadStatus.choices,
        default=DownloadStatus.PENDING,
    )
    auto_start = models.BooleanField(default=False)
    message = models.TextField(null=True, blank=True)
    vid_thumb_url = models.TextField(blank=True, default="")

    objects = DownloadQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(
                fields=["status", "-auto_start", "timestamp"],
                name="download_queue_order",
            ),
            models.Index(fields=["channel_id"]),
        ]

    def __str__(self):
        return f"{self.youtube_id} ({self.status})"

    @classmethod
    def fields_from_doc(cls, doc: dict) -> dict:
        """map legacy ta_download document to model fields"""
        return {
            "youtube_id": doc["youtube_id"],
            "title": doc.get("title") or "",
            "channel_id": doc.get("channel_id") or "",
            "channel_name": doc.get("channel_name") or "",
            "vid_type": doc.get("vid_type") or VideoTypeEnum.VIDEOS.value,
            "duration": doc.get("duration") or "",
            "published": from_epoch(doc.get("published")),
            "timestamp": from_epoch(doc.get("timestamp")),
            "status": doc.get("status") or DownloadStatus.PENDING,
            "auto_start": bool(doc.get("auto_start", False)),
            "message": doc.get("message"),
            "vid_thumb_url": doc.get("vid_thumb_url") or "",
        }

    @classmethod
    def from_doc(cls, doc: dict) -> "Download":
        """build unsaved instance from legacy document"""
        return cls(**cls.fields_from_doc(doc))

    @classmethod
    def upsert_doc(cls, doc: dict) -> "Download":
        """create or replace from legacy document"""
        fields = cls.fields_from_doc(doc)
        youtube_id = fields.pop("youtube_id")
        with transaction.atomic():
            item, _ = cls.objects.update_or_create(
                youtube_id=youtube_id, defaults=fields
            )
        return item

    @property
    def channel_indexed(self) -> bool:
        """the channel of this item is in the archive"""
        from channel.models import (  # pylint: disable=import-outside-toplevel
            Channel,
        )

        return Channel.objects.filter(channel_id=self.channel_id).exists()

    def to_doc(self, channel_indexed: bool | None = None) -> dict:
        """legacy ta_download document shape, as the API returns it"""
        if channel_indexed is None:
            channel_indexed = self.channel_indexed

        return {
            "youtube_id": self.youtube_id,
            "title": self.title,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "channel_indexed": channel_indexed,
            "vid_type": self.vid_type,
            "duration": self.duration,
            "published": to_epoch(self.published),
            "timestamp": to_epoch(self.timestamp),
            "status": self.status,
            "auto_start": self.auto_start,
            "message": self.message,
            "vid_thumb_url": self.vid_thumb_url,
        }

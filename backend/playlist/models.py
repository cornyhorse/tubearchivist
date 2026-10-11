"""playlist models"""

from channel.models import Channel, EmbyNumbering
from common.src.db import WeightedSearchVector, from_epoch, to_epoch
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models, transaction


class Playlist(models.Model):
    """a youtube playlist or a custom playlist"""

    playlist_id = models.CharField(primary_key=True, max_length=64)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    # uploader of the playlist; the channel may not be indexed
    channel_id = models.CharField(max_length=64, blank=True, default="")
    channel_name = models.TextField(blank=True, default="")
    active = models.BooleanField(default=True)
    subscribed = models.BooleanField(default=False)
    last_refresh = models.DateTimeField(null=True, blank=True)
    sort_order = models.CharField(max_length=16, default="top")
    thumbnail = models.TextField(blank=True, default="")
    playlist_type = models.CharField(max_length=16, default="regular")
    emby_numbering = models.CharField(
        max_length=32,
        choices=EmbyNumbering.choices,
        default=EmbyNumbering.PLAYLIST_POSITION,
    )

    search = models.GeneratedField(
        expression=WeightedSearchVector(
            ("name", "A", False),
            ("channel_name", "B", False),
            ("description", "C", False),
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["channel_id"]),
            GinIndex(fields=["search"], name="playlist_search_gin"),
            GinIndex(
                name="playlist_name_trgm",
                fields=["name"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.playlist_id} ({self.name})"

    @property
    def channel(self) -> Channel | None:
        """indexed channel of uploader, if any"""
        return Channel.objects.filter(channel_id=self.channel_id).first()

    @classmethod
    def fields_from_doc(cls, doc: dict) -> dict:
        """map legacy ta_playlist document to model fields, minus entries"""
        return {
            "playlist_id": doc["playlist_id"],
            "name": doc.get("playlist_name") or "",
            "description": doc.get("playlist_description") or None,
            "channel_id": doc.get("playlist_channel_id") or "",
            "channel_name": doc.get("playlist_channel") or "",
            "active": bool(doc.get("playlist_active", True)),
            "subscribed": bool(doc.get("playlist_subscribed", False)),
            "last_refresh": from_epoch(doc.get("playlist_last_refresh")),
            "sort_order": doc.get("playlist_sort_order") or "top",
            "thumbnail": doc.get("playlist_thumbnail") or "",
            "playlist_type": doc.get("playlist_type") or "regular",
            **(
                {"emby_numbering": doc["emby_numbering"]}
                if doc.get("emby_numbering")
                else {}
            ),
        }

    @classmethod
    def save_doc(cls, doc: dict) -> "Playlist":
        """create or replace playlist and its entries from legacy document"""
        fields = cls.fields_from_doc(doc)
        playlist_id = fields.pop("playlist_id")
        with transaction.atomic():
            playlist, _ = cls.objects.update_or_create(
                playlist_id=playlist_id, defaults=fields
            )
            playlist.entries.all().delete()
            PlaylistEntry.objects.bulk_create(cls.entries_from_doc(doc))
        return playlist

    @classmethod
    def load_doc(cls, playlist_id: str) -> dict | None:
        """legacy document of a playlist, None if not indexed"""
        playlist = cls.objects.filter(playlist_id=playlist_id).first()
        return playlist.to_doc() if playlist else None

    @staticmethod
    def entries_from_doc(doc: dict) -> list["PlaylistEntry"]:
        """unsaved entries from legacy playlist_entries"""
        return [
            PlaylistEntry(
                playlist_id=doc["playlist_id"],
                idx=entry["idx"],
                youtube_id=entry["youtube_id"],
                title=entry.get("title"),
                uploader=entry.get("uploader"),
            )
            for entry in doc.get("playlist_entries") or []
        ]

    def to_doc(self, with_entries: bool = True) -> dict:
        """
        legacy ta_playlist document shape, as the API returns it
        with_entries: False leaves out playlist_entries, saving the query
        """
        doc = {
            "playlist_id": self.playlist_id,
            "playlist_name": self.name,
            "playlist_description": self.description,
            "playlist_channel_id": self.channel_id,
            "playlist_channel": self.channel_name,
            "playlist_active": self.active,
            "playlist_subscribed": self.subscribed,
            "playlist_last_refresh": to_epoch(self.last_refresh),
            "playlist_sort_order": self.sort_order,
            "playlist_thumbnail": self.thumbnail,
            "playlist_type": self.playlist_type,
            "emby_numbering": self.emby_numbering,
        }
        if with_entries:
            entries = self.entries.with_downloaded().order_by("idx")
            doc["playlist_entries"] = [entry.to_doc() for entry in entries]

        return doc


class PlaylistEntryQuerySet(models.QuerySet):
    """playlist entry queries"""

    def with_downloaded(self):
        """annotate downloaded: the video exists in the archive"""
        from video.models import (  # pylint: disable=import-outside-toplevel
            Video,
        )

        return self.annotate(
            downloaded=models.Exists(
                Video.objects.filter(youtube_id=models.OuterRef("youtube_id"))
            )
        )


class PlaylistEntry(models.Model):
    """
    one position in a playlist; the video may not be downloaded,
    so youtube_id is not a foreign key
    """

    playlist = models.ForeignKey(
        Playlist, on_delete=models.CASCADE, related_name="entries"
    )
    idx = models.IntegerField()
    youtube_id = models.CharField(max_length=32, db_index=True)
    title = models.TextField(null=True, blank=True)
    uploader = models.TextField(null=True, blank=True)

    objects = PlaylistEntryQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["playlist", "idx"], name="playlist_entry_unique_idx"
            ),
        ]
        ordering = ["playlist", "idx"]

    def to_doc(self) -> dict:
        """legacy playlist_entries item, needs with_downloaded()"""
        return {
            "youtube_id": self.youtube_id,
            "idx": self.idx,
            "title": self.title,
            "uploader": self.uploader,
            "downloaded": bool(getattr(self, "downloaded", False)),
        }

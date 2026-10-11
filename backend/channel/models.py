"""channel models"""

from common.src.db import WeightedSearchVector, from_epoch, to_epoch
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

# channel_overwrites keys, stored as nullable columns: null = use global
OVERWRITE_FIELDS = [
    "download_format",
    "autodelete_days",
    "index_playlists",
    "integrate_sponsorblock",
    "subscriptions_channel_size",
    "subscriptions_live_channel_size",
    "subscriptions_shorts_channel_size",
]


class EmbyNumbering(models.TextChoices):
    """how the Emby writer numbers episodes of a series"""

    DATE = "date", "Date: season = year, episode = MMDD*100+n"
    YEAR_FROZEN = "year_frozen", "Year: season = year, frozen order"
    ABSOLUTE_FROZEN = "absolute_frozen", "Absolute: season 1, frozen order"
    PLAYLIST_FROZEN = "playlist_frozen", "Playlist order, frozen"
    PLAYLIST_POSITION = "playlist_position", "Playlist position (renumbers)"


class Channel(models.Model):
    """a youtube channel, indexed or subscribed"""

    channel_id = models.CharField(primary_key=True, max_length=64)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    active = models.BooleanField(default=True)
    subscribed = models.BooleanField(default=False)
    subs = models.BigIntegerField(null=True, blank=True)
    last_refresh = models.DateTimeField(null=True, blank=True)
    thumb_url = models.TextField(blank=True, default="")
    banner_url = models.TextField(blank=True, default="")
    tvart_url = models.TextField(blank=True, default="")
    tags = ArrayField(models.TextField(), default=list, blank=True)
    tabs = ArrayField(models.TextField(), default=list, blank=True)

    # per channel overwrites of the global download settings
    download_format = models.TextField(null=True, blank=True)
    autodelete_days = models.IntegerField(null=True, blank=True)
    index_playlists = models.BooleanField(null=True, blank=True)
    integrate_sponsorblock = models.BooleanField(null=True, blank=True)
    subscriptions_channel_size = models.IntegerField(null=True, blank=True)
    subscriptions_live_channel_size = models.IntegerField(
        null=True, blank=True
    )
    subscriptions_shorts_channel_size = models.IntegerField(
        null=True, blank=True
    )

    emby_numbering = models.CharField(
        max_length=32,
        choices=EmbyNumbering.choices,
        default=EmbyNumbering.DATE,
    )

    search = models.GeneratedField(
        expression=WeightedSearchVector(
            ("name", "A", False),
            ("tags", "B", True),
            ("description", "C", False),
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        indexes = [
            GinIndex(fields=["search"], name="channel_search_gin"),
            GinIndex(
                name="channel_name_trgm",
                fields=["name"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.channel_id} ({self.name})"

    @property
    def overwrites(self) -> dict:
        """set overwrites as dict, like the legacy channel_overwrites"""
        return {
            key: getattr(self, key)
            for key in OVERWRITE_FIELDS
            if getattr(self, key) is not None
        }

    @classmethod
    def fields_from_doc(cls, doc: dict) -> dict:
        """map legacy ta_channel document to model fields"""
        overwrites = doc.get("channel_overwrites") or {}
        fields = {
            "channel_id": doc["channel_id"],
            "name": doc.get("channel_name") or "",
            "description": doc.get("channel_description"),
            "active": bool(doc.get("channel_active", True)),
            "subscribed": bool(doc.get("channel_subscribed", False)),
            "subs": doc.get("channel_subs"),
            "last_refresh": from_epoch(doc.get("channel_last_refresh")),
            "thumb_url": doc.get("channel_thumb_url") or "",
            "banner_url": doc.get("channel_banner_url") or "",
            "tvart_url": doc.get("channel_tvart_url") or "",
            "tags": list(doc.get("channel_tags") or []),
            "tabs": list(doc.get("channel_tabs") or []),
        }
        for key in OVERWRITE_FIELDS:
            fields[key] = overwrites.get(key)
        # fork setting: only in docs written by the fork, ES docs keep default
        if doc.get("emby_numbering"):
            fields["emby_numbering"] = doc["emby_numbering"]

        return fields

    @classmethod
    def save_doc(cls, doc: dict) -> "Channel":
        """create or replace from legacy document"""
        fields = cls.fields_from_doc(doc)
        channel_id = fields.pop("channel_id")
        channel, _ = cls.objects.update_or_create(
            channel_id=channel_id, defaults=fields
        )
        return channel

    @classmethod
    def load_doc(cls, channel_id: str) -> dict | None:
        """legacy document of a channel, None if not indexed"""
        channel = cls.objects.filter(channel_id=channel_id).first()
        return channel.to_doc() if channel else None

    @classmethod
    def from_doc(cls, doc: dict) -> "Channel":
        """build unsaved instance from legacy document"""
        return cls(**cls.fields_from_doc(doc))

    def to_doc(self) -> dict:
        """legacy ta_channel document shape, as the API returns it"""
        doc = {
            "channel_id": self.channel_id,
            "channel_name": self.name,
            "channel_description": self.description,
            "channel_active": self.active,
            "channel_subscribed": self.subscribed,
            "channel_subs": self.subs,
            "channel_last_refresh": to_epoch(self.last_refresh),
            "channel_thumb_url": self.thumb_url,
            "channel_banner_url": self.banner_url,
            "channel_tvart_url": self.tvart_url,
            "channel_tags": list(self.tags),
            "channel_tabs": list(self.tabs),
            "emby_numbering": self.emby_numbering,
        }
        if overwrites := self.overwrites:
            doc["channel_overwrites"] = overwrites

        return doc

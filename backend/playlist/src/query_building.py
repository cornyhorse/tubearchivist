"""build the playlist list queryset"""

from django.db.models import QuerySet
from django.db.models.functions import Lower
from emby.src.tags import canonical_tag
from playlist.models import Playlist
from playlist.src.constants import PlaylistTypesEnum


class QueryBuilder:
    """contain functionality"""

    def __init__(self, **kwargs):
        self.request_params = kwargs

    def build_queryset(self) -> QuerySet:
        """filtered playlists sorted by name"""
        queryset = Playlist.objects.all()
        channel = self.request_params.get("channel")
        if channel:
            queryset = queryset.filter(channel_id=channel)

        subscribed = self.request_params.get("subscribed")
        if subscribed is not None:
            queryset = queryset.filter(subscribed=subscribed)

        auto_download = self.request_params.get("auto_download")
        if auto_download is not None:
            queryset = queryset.filter(auto_download=auto_download)

        emby_tag = self.request_params.get("emby_tag")
        if emby_tag:
            queryset = queryset.filter(
                emby_tags__contains=[canonical_tag(emby_tag)]
            )

        playlist_type = self.request_params.get("type")
        if playlist_type:
            queryset = queryset.filter(
                playlist_type=self.parse_type(playlist_type)
            )

        return queryset.order_by(Lower("name"), "playlist_id")

    @staticmethod
    def parse_type(playlist_type: str) -> str:
        """parse playlist type"""
        if not hasattr(PlaylistTypesEnum, playlist_type.upper()):
            raise ValueError(f"'{playlist_type}' not in PlaylistTypesEnum")

        return getattr(PlaylistTypesEnum, playlist_type.upper()).value

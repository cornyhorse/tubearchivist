"""build the video list queryset from request parameters"""

from common.src.ta_redis import RedisArchivist
from django.db.models import F, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce
from playlist.models import Playlist, PlaylistEntry
from video.models import Video
from video.src.constants import OrderEnum, SortEnum, VideoTypeEnum

# sort enum value -> model field
SORT_FIELDS = {
    SortEnum.PUBLISHED: "published",
    SortEnum.DOWNLOADED: "date_downloaded",
    SortEnum.VIEWS: "view_count",
    SortEnum.LIKES: "like_count",
    SortEnum.DURATION: "duration",
    SortEnum.MEDIASIZE: "media_size",
    SortEnum.WIDTH: "width",
    SortEnum.HEIGHT: "height",
}


class QueryBuilder:
    """contain functionality"""

    WATCH_OPTIONS = ["watched", "unwatched", "continue"]

    def __init__(self, user_id: int, **kwargs):
        self.user_id = user_id
        self.request_params = kwargs

    def build_queryset(self) -> QuerySet | None:
        """filtered and sorted videos, None if nothing can match"""
        queryset = Video.objects.select_related("channel")
        channel = self.request_params.get("channel")
        if channel:
            queryset = queryset.filter(channel_id=channel)

        playlist = self.request_params.get("playlist")
        if playlist:
            entries = PlaylistEntry.objects.filter(playlist_id=playlist)
            queryset = queryset.filter(
                youtube_id__in=entries.values("youtube_id")
            )

        watch = self.request_params.get("watch")
        if watch is not None:
            watch_filter = self.parse_watch(watch)
            if watch_filter is None:
                return None
            queryset = queryset.filter(watch_filter)

        video_type = self.request_params.get("type")
        if video_type:
            queryset = queryset.filter(vid_type=self.parse_type(video_type))

        height = self.request_params.get("height")
        if height:
            queryset = queryset.filter(height=int(height))

        query = self.request_params.get("q")
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(channel__name__icontains=query)
            )

        return queryset.order_by(*self.parse_sort(), "youtube_id")

    def parse_watch(self, watch: str) -> Q | None:
        """filter for watch state, None if continue has nothing"""
        if watch not in self.WATCH_OPTIONS:
            raise ValueError(f"'{watch}' not in {self.WATCH_OPTIONS}")

        if watch == "continue":
            return self._build_continue()

        return Q(watched=watch == "watched")

    def _build_continue(self) -> Q | None:
        results = RedisArchivist().list_items(f"{self.user_id}:progress:")
        if not results:
            return None

        ids = [i.get("youtube_id") for i in results if not i.get("watched")]
        if not ids:
            return None

        return Q(youtube_id__in=ids)

    @staticmethod
    def parse_type(video_type: str) -> str:
        """parse video type"""
        if not hasattr(VideoTypeEnum, video_type.upper()):
            raise ValueError(f"'{video_type}' not in VideoTypeEnum")

        return getattr(VideoTypeEnum, video_type.upper()).value

    def parse_sort(self) -> list:
        """order_by arguments"""
        playlist = self.request_params.get("playlist")
        if playlist:
            # sort by position in playlist
            return [self._get_playlist_sort(playlist_id=playlist)]

        sort = self.request_params.get("sort")
        if not sort:
            return []

        if not hasattr(SortEnum, sort.upper()):
            raise ValueError(f"'{sort}' not in SortEnum")

        field = SORT_FIELDS[getattr(SortEnum, sort.upper())]
        order = self.request_params.get("order", "desc")
        if not hasattr(OrderEnum, order.upper()):
            raise ValueError(f"'{order}' not in OrderEnum")

        # missing values sort last either way, like ES
        if getattr(OrderEnum, order.upper()) == OrderEnum.ASC:
            return [F(field).asc(nulls_last=True)]
        return [F(field).desc(nulls_last=True)]

    @staticmethod
    def _get_playlist_sort(playlist_id: str):
        """order expression: position of the video in the playlist"""
        if not Playlist.objects.filter(playlist_id=playlist_id).exists():
            raise ValueError(f"playlist {playlist_id} not found")

        position = PlaylistEntry.objects.filter(
            playlist_id=playlist_id, youtube_id=OuterRef("youtube_id")
        ).values("idx")[:1]
        return Coalesce(Subquery(position), Value(100000)).asc()

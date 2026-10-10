"""
functionality:
- handle watched state for videos, channels and playlists
"""

from datetime import datetime, timezone

from common.src.ta_redis import RedisArchivist
from common.src.urlparser import Parser
from playlist.models import PlaylistEntry
from video.models import Video


class WatchState:
    """handle watched checkbox for videos and channels"""

    def __init__(self, youtube_id: str, is_watched: bool, user_id: int):
        self.youtube_id = youtube_id
        self.is_watched = is_watched
        self.user_id = user_id
        self.now = datetime.now(timezone.utc)

    def change(self):
        """change watched state of item(s)"""
        print(f"{self.youtube_id}: change watched state to {self.is_watched}")
        url_type = self._dedect_type()
        if url_type == "video":
            self.change_vid_state()
            return

        if url_type == "channel":
            videos = Video.objects.filter(channel_id=self.youtube_id)
        else:
            entries = PlaylistEntry.objects.filter(playlist_id=self.youtube_id)
            videos = Video.objects.filter(
                youtube_id__in=entries.values("youtube_id")
            )

        self._reset_list(list(videos.values_list("youtube_id", flat=True)))
        videos.exclude(watched=self.is_watched).update(
            watched=self.is_watched, watched_date=self._watched_date()
        )

    def _watched_date(self):
        return self.now if self.is_watched else None

    def _dedect_type(self):
        """find youtube id type"""
        url_process = Parser(self.youtube_id).parse()
        url_type = url_process[0]["type"]
        return url_type

    def change_vid_state(self):
        """change watched state of video"""
        updated = Video.objects.filter(youtube_id=self.youtube_id).update(
            watched=self.is_watched, watched_date=self._watched_date()
        )
        key = f"{self.user_id}:progress:{self.youtube_id}"
        RedisArchivist().del_message(key)
        if not updated:
            raise ValueError("failed to mark video as watched")

    def _reset_list(self, video_ids: list[str]):
        """reset progress positions of video ids"""
        redis_con = RedisArchivist()
        all_ids = redis_con.list_keys(f"{self.user_id}:progress")
        for progress_id in all_ids:
            video_id = progress_id.split(":")[-1]
            if video_id in video_ids:
                redis_con.del_message(progress_id)

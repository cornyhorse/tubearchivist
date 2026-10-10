"""build channel nav"""

from download.models import Download
from playlist.models import Playlist
from video.models import Video


class ChannelNav:
    """get all nav items"""

    def __init__(self, channel_id):
        self.channel_id = channel_id

    def get_nav(self):
        """build nav items"""
        queue = Download.objects.filter(channel_id=self.channel_id)
        nav = {
            "has_pending": queue.filter(status="pending").exists(),
            "has_ignored": queue.filter(status="ignore").exists(),
            "has_playlists": Playlist.objects.filter(
                channel_id=self.channel_id
            ).exists(),
        }
        vid_types = set(
            Video.objects.filter(channel_id=self.channel_id)
            .values_list("vid_type", flat=True)
            .distinct()
        )
        for vid_type in ("videos", "streams", "shorts"):
            nav[f"has_{vid_type}"] = vid_type in vid_types

        return nav

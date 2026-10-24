"""
functionality:
- get metadata from youtube for a channel
- index and update in the database
"""

import os
from datetime import datetime

from appsettings.src.config import auto_download_default
from channel.models import OVERWRITE_FIELDS, Channel
from channel.src.remote_query import get_last_channel_videos
from common.src.env_settings import EnvironmentSettings
from common.src.helper import rand_sleep
from common.src.index_generic import YouTubeItem
from download.src.thumbnails import ThumbManager
from download.src.yt_dlp_base import YtWrap
from video.src.archive import is_archive_id
from video.src.constants import VideoTypeEnum


class YoutubeChannel(YouTubeItem):
    """represents a single youtube channel"""

    model = Channel
    yt_base = "https://www.youtube.com/channel/"
    yt_obs = {
        "playlist_items": "0,0",
        "skip_download": True,
    }

    def __init__(self, youtube_id, task=False):
        super().__init__(youtube_id)
        self.all_playlists = False
        self.task = task

    def build_json(self, upload=False, fallback=False):
        """get from the database or from youtube"""
        self.get_from_db()
        if self.json_data:
            return

        self.get_from_youtube()
        if not self.youtube_meta and fallback:
            self._video_fallback(fallback)
        else:
            if not self.youtube_meta:
                message = f"{self.youtube_id}: Failed to get metadata"
                raise ValueError(message)

            self.process_youtube_meta()
            self.get_channel_art()

        if upload:
            self.save_to_db()

    def process_youtube_meta(self):
        """extract relevant fields"""
        self.youtube_meta["thumbnails"].reverse()
        channel_name = self.youtube_meta["uploader"] or self.youtube_meta["id"]
        description = self.youtube_meta.get("description") or None
        self.json_data = {
            "channel_active": True,
            "channel_description": description,
            "channel_id": self.youtube_id,
            "channel_last_refresh": int(datetime.now().timestamp()),
            "channel_name": channel_name,
            "channel_subs": self.youtube_meta.get("channel_follower_count")
            or 0,
            "channel_subscribed": False,
            "channel_tags": self.youtube_meta.get("tags", []),
            "channel_tabs": self.get_channel_tabs(),
        }

        self._get_thumb_art()
        self._get_tv_art()
        self._get_banner_art()

    def _get_thumb_art(self) -> None:
        """extract thumb art"""
        for i in self.youtube_meta["thumbnails"]:
            if not i.get("width"):
                continue
            if i.get("width") == i.get("height"):
                self.json_data["channel_thumb_url"] = i["url"]
                return

    def _get_tv_art(self) -> None:
        """extract tv artwork"""
        for i in self.youtube_meta["thumbnails"]:
            if i.get("id") == "banner_uncropped":
                self.json_data["channel_tvart_url"] = i["url"]
                return
        for i in self.youtube_meta["thumbnails"]:
            if not i.get("width"):
                continue
            if i["width"] // i["height"] < 2 and not i["width"] == i["height"]:
                self.json_data["channel_tvart_url"] = i["url"]
                return

        return

    def _get_banner_art(self) -> None:
        """extract banner artwork"""
        for i in self.youtube_meta["thumbnails"]:
            if not i.get("width"):
                continue
            if i["width"] // i["height"] > 5:
                self.json_data["channel_banner_url"] = i["url"]
                return

    def get_channel_tabs(self) -> list[str]:
        """get channel tabs"""
        tabs = VideoTypeEnum.values_known()
        config_cp = self.config.copy()
        tabs = []
        for query_filter in VideoTypeEnum:
            if query_filter == VideoTypeEnum.UNKNOWN:
                continue

            videos = get_last_channel_videos(
                channel_id=self.youtube_id,
                config=config_cp,
                limit=1,
                query_filter=query_filter,
            )
            if videos:
                tabs.append(query_filter.value)

        return tabs

    def _video_fallback(self, fallback):
        """use video metadata as fallback"""
        print(f"{self.youtube_id}: fallback to video metadata")
        self.json_data = {
            "channel_active": False,
            "channel_last_refresh": int(datetime.now().timestamp()),
            "channel_subs": fallback.get("channel_follower_count") or 0,
            "channel_name": fallback["uploader"],
            "channel_id": self.youtube_id,
            "channel_subscribed": False,
            "channel_tags": [],
        }

    def get_channel_art(self):
        """download channel art for new channels"""
        urls = (
            self.json_data.get("channel_thumb_url"),
            self.json_data.get("channel_banner_url"),
            self.json_data.get("channel_tvart_url"),
        )
        ThumbManager(self.youtube_id, item_type="channel").download(urls)

    def change_subscribe(self, new_subscribe_state: bool):
        """
        change subscribe status; a new subscription gets the default
        auto_download (subscriptions.auto_download_default, off unless set)
        """
        if new_subscribe_state and is_archive_id(self.youtube_id):
            raise ValueError("an archived channel can't be subscribed")

        was_subscribed = Channel.objects.filter(
            channel_id=self.youtube_id, subscribed=True
        ).exists()
        if not self.json_data:
            self.build_json()

        self.json_data["channel_subscribed"] = new_subscribe_state
        if new_subscribe_state and not was_subscribed:
            self.json_data["auto_download"] = auto_download_default()
        self.save_to_db()
        return self.json_data

    def delete_channel(self):
        """delete channel and all videos"""
        print(f"{self.youtube_id}: delete channel")
        self.get_from_db()
        if not self.json_data:
            raise FileNotFoundError

        ChannelDelete(json_data=self.json_data).delete()

    def index_channel_playlists(self):
        """add all playlists of channel to index"""
        print(f"{self.youtube_id}: index all playlists")
        self.get_from_db()
        channel_name = self.json_data["channel_name"]
        if self.task:
            self.task.send_progress([f"{channel_name}: Looking for Playlists"])
        self.get_all_playlists()
        if not self.all_playlists:
            print(f"{self.youtube_id}: no playlists found.")
            return

        total = len(self.all_playlists)
        for idx, playlist in enumerate(self.all_playlists):
            if self.task:
                self._notify_single_playlist(idx, total)

            self._index_single_playlist(playlist)
            print("add playlist: " + playlist[1])
            rand_sleep(self.config)

    def get_all_playlists(self):
        """get all playlists owned by this channel"""
        url = (
            f"https://www.youtube.com/channel/{self.youtube_id}"
            + "/playlists?view=1&sort=dd&shelf_id=0"
        )
        obs = {"skip_download": True, "extract_flat": True}
        playlists, _ = YtWrap(obs, self.config).extract(url)
        if not playlists:
            self.all_playlists = []
            return

        all_entries = [(i["id"], i["title"]) for i in playlists["entries"]]
        self.all_playlists = all_entries

    def _notify_single_playlist(self, idx, total):
        """send notification"""
        channel_name = self.json_data["channel_name"]
        message = [
            f"{channel_name}: Scanning channel for playlists",
            f"Progress: {idx + 1}/{total}",
        ]
        self.task.send_progress(message, progress=(idx + 1) / total)

    def _index_single_playlist(self, playlist):
        """add single playlist if needed"""
        from playlist.src.index import YoutubePlaylist

        try:
            playlist = YoutubePlaylist(playlist[0])
            playlist.update_playlist(skip_on_empty=True)
        except ValueError as err:
            message = [
                f"{self.youtube_id}: skip failed playlist import",
                str(err),
            ]
            print(message)
            if self.task:
                self.task.send_progress(message)

    def get_channel_videos(self) -> list[dict]:
        """youtube_id and vid_type of all videos of the channel"""
        # pylint: disable=import-outside-toplevel
        from video.models import Video

        return list(
            Video.objects.filter(channel_id=self.youtube_id)
            .order_by("youtube_id")
            .values("youtube_id", "vid_type")
        )

    def get_overwrites(self) -> dict:
        """get all per channel overwrites"""
        return self.json_data.get("channel_overwrites", {})

    def set_overwrites(self, overwrites):
        """set per channel overwrites"""
        valid_keys = OVERWRITE_FIELDS

        to_write = self.json_data.get("channel_overwrites", {})
        for key, value in overwrites.items():
            if key not in valid_keys:
                raise ValueError(f"invalid overwrite key: {key}")

            if value is None and key in to_write:
                to_write.pop(key)
                continue

            to_write.update({key: value})

        self.json_data["channel_overwrites"] = to_write


class ChannelDelete(YouTubeItem):
    """delete and cleanup"""

    model = Channel

    def __init__(self, json_data):
        super().__init__(youtube_id=json_data["channel_id"])
        self.json_data = json_data

    def delete(self):
        """delete channel and all videos"""
        folder_path = self._get_folder_path()
        print(f"{self.youtube_id}: delete all media files")
        try:
            all_videos = os.listdir(folder_path)
            for video in all_videos:
                video_path = os.path.join(folder_path, video)
                os.remove(video_path)
            os.rmdir(folder_path)
        except FileNotFoundError:
            print(f"no videos found for {folder_path}")

        print(f"{self.youtube_id}: delete indexed playlists")
        self._delete_playlists()
        print(f"{self.youtube_id}: delete indexed videos")
        self._delete_videos()
        self.delete_from_db()

    def _get_folder_path(self):
        """get folder where media files get stored"""
        folder_path = os.path.join(
            EnvironmentSettings.MEDIA_DIR,
            self.json_data["channel_id"],
        )
        return folder_path

    def _delete_videos(self):
        """delete all videos, their comments and subtitles cascade"""
        # pylint: disable=import-outside-toplevel
        from video.models import Video

        Video.objects.filter(channel_id=self.youtube_id).delete()

    def _delete_playlists(self):
        """delete all indexed playlists of the channel"""
        from playlist.src.index import YoutubePlaylist

        all_playlists = self._get_indexed_playlists()
        for playlist in all_playlists:
            YoutubePlaylist(playlist["playlist_id"]).delete_metadata()

    def _get_indexed_playlists(self) -> list[dict]:
        """playlist ids of the channel's indexed playlists"""
        # pylint: disable=import-outside-toplevel
        from playlist.models import Playlist

        return list(
            Playlist.objects.filter(channel_id=self.youtube_id).values(
                "playlist_id"
            )
        )


def channel_overwrites(channel_id, overwrites):
    """collection to overwrite settings per channel"""
    channel = YoutubeChannel(channel_id)
    channel.build_json()
    channel.set_overwrites(overwrites)
    channel.save_to_db()

    return channel.json_data

"""
functionality:
- periodically refresh documents
- refresh indexed items from youtube
"""

import json
import os
from datetime import datetime, timezone

from django.db import transaction
from typing import Literal, TypedDict

from appsettings.src.config import AppConfig
from channel.src.index import YoutubeChannel
from channel.src.remote_query import get_last_channel_videos
from common.src.env_settings import EnvironmentSettings
from common.src.helper import rand_sleep
from common.src.ta_redis import RedisArchivist, RedisQueue
from download.src.thumbnails import ThumbManager
from download.src.yt_dlp_base import CookieHandler
from playlist.src.index import YoutubePlaylist
from task.models import CustomPeriodicTask
from video.src.comments import Comments
from video.src.index import YoutubeVideo


class ReindexConfigType(TypedDict):
    """represents config type"""

    index_name: str
    queue_name: str
    active_key: str
    refresh_key: str


class ReindexBase:
    """base config class for reindex task"""

    REINDEX_CONFIG: dict[str, ReindexConfigType] = {
        "video": {
            "index_name": "ta_video",
            "queue_name": "reindex:ta_video",
            "active_key": "active",
            "refresh_key": "vid_last_refresh",
        },
        "channel": {
            "index_name": "ta_channel",
            "queue_name": "reindex:ta_channel",
            "active_key": "channel_active",
            "refresh_key": "channel_last_refresh",
        },
        "playlist": {
            "index_name": "ta_playlist",
            "queue_name": "reindex:ta_playlist",
            "active_key": "playlist_active",
            "refresh_key": "playlist_last_refresh",
        },
    }

    MULTIPLY = 1.2
    DAYS3 = 60 * 60 * 24 * 3

    def __init__(self):
        self.config = AppConfig().config
        self.now = int(datetime.now().timestamp())

    def populate(self, all_ids, reindex_config: ReindexConfigType):
        """add all to reindex ids to redis queue"""
        if not all_ids:
            return

        RedisQueue(queue_name=reindex_config["queue_name"]).add_list(all_ids)


class ReindexPopulate(ReindexBase):
    """add outdated and recent documents to reindex queue"""

    INTERVAL_DEFAIULT: int = 90

    def __init__(self):
        super().__init__()
        self.interval = self.INTERVAL_DEFAIULT

    def get_interval(self) -> None:
        """get reindex days interval from task"""
        try:
            task = CustomPeriodicTask.objects.get(name="check_reindex")
        except CustomPeriodicTask.DoesNotExist:
            return

        task_config = task.task_config
        if task_config.get("days"):
            self.interval = task_config.get("days")

    @staticmethod
    def _model(reindex_config: ReindexConfigType):
        # pylint: disable=import-outside-toplevel
        from channel.models import Channel
        from playlist.models import Playlist
        from video.models import Video

        return {
            "ta_video": Video,
            "ta_channel": Channel,
            "ta_playlist": Playlist,
        }[reindex_config["index_name"]]

    def add_recent(self) -> None:
        """add videos published in the last 3 days to refresh"""
        # pylint: disable=import-outside-toplevel
        from video.models import Video

        since = datetime.fromtimestamp(self.now - self.DAYS3, tz=timezone.utc)
        all_ids = list(
            Video.objects.filter(active=True, published__gte=since)
            .order_by("-published")
            .values_list("youtube_id", flat=True)
        )
        reindex_config: ReindexConfigType = self.REINDEX_CONFIG["video"]
        self.populate(all_ids, reindex_config)

    def add_outdated(self) -> None:
        """add outdated documents"""
        for reindex_config in self.REINDEX_CONFIG.values():
            total_hits = self._get_total_hits(reindex_config)
            daily_should = self._get_daily_should(total_hits)
            all_ids = self._get_outdated_ids(reindex_config, daily_should)
            self.populate(all_ids, reindex_config)

    def _get_total_hits(self, reindex_config: ReindexConfigType) -> int:
        """count active items"""
        return self._model(reindex_config).objects.filter(active=True).count()

    def _get_daily_should(self, total_hits: int) -> int:
        """calc how many should reindex daily"""
        return int((total_hits // self.interval + 1) * self.MULTIPLY)

    def _get_outdated_ids(
        self, reindex_config: ReindexConfigType, daily_should: int
    ) -> list[str]:
        """the daily_should active items refreshed longest ago"""
        cutoff = datetime.fromtimestamp(
            self.now - self.interval * 24 * 60 * 60, tz=timezone.utc
        )
        model = self._model(reindex_config)
        return list(
            model.objects.filter(active=True, last_refresh__lte=cutoff)
            .order_by("last_refresh", "pk")
            .values_list("pk", flat=True)[:daily_should]
        )


class ReindexManual(ReindexBase):
    """
    manually add ids to reindex queue from API
    data_example = {
        "video": ["video1", "video2", "video3"],
        "channel": ["channel1", "channel2", "channel3"],
        "playlist": ["playlist1", "playlist2"],
    }
    extract_videos to also reindex all videos of channel/playlist
    """

    def __init__(self, extract_videos=False):
        super().__init__()
        self.extract_videos = extract_videos
        self.data = False

    def extract_data(self, data) -> None:
        """process data"""
        self.data = data
        for key, values in self.data.items():
            reindex_config = self.REINDEX_CONFIG.get(key)
            if not reindex_config:
                print(f"reindex type {key} not valid")
                raise ValueError

            self.process_index(reindex_config, values)

    def process_index(
        self, index_config: ReindexConfigType, values: list[str]
    ) -> None:
        """process values per index"""
        index_name = index_config["index_name"]
        if index_name == "ta_video":
            self._add_videos(values)
        elif index_name == "ta_channel":
            self._add_channels(values)
        elif index_name == "ta_playlist":
            self._add_playlists(values)

    def _add_videos(self, values: list[str]) -> None:
        """add list of videos to reindex queue"""
        if not values:
            return

        queue_name = self.REINDEX_CONFIG["video"]["queue_name"]
        RedisQueue(queue_name).add_list(values)

    def _add_channels(self, values: list[str]) -> None:
        """add list of channels to reindex queue"""
        queue_name = self.REINDEX_CONFIG["channel"]["queue_name"]
        RedisQueue(queue_name).add_list(values)

        if self.extract_videos:
            for channel_id in values:
                all_videos = self._get_channel_videos(channel_id)
                self._add_videos(all_videos)

    def _add_playlists(self, values: list[str]) -> None:
        """add list of playlists to reindex queue"""
        queue_name = self.REINDEX_CONFIG["playlist"]["queue_name"]
        RedisQueue(queue_name).add_list(values)

        if self.extract_videos:
            for playlist_id in values:
                all_videos = self._get_playlist_videos(playlist_id)
                self._add_videos(all_videos)

    @staticmethod
    def _get_channel_videos(channel_id: str) -> list[str]:
        """get all videos from channel"""
        # pylint: disable=import-outside-toplevel
        from video.models import Video

        return list(
            Video.objects.filter(channel_id=channel_id).values_list(
                "youtube_id", flat=True
            )
        )

    @staticmethod
    def _get_playlist_videos(playlist_id: str) -> list[str]:
        """get all downloaded videos of playlist"""
        # pylint: disable=import-outside-toplevel
        from playlist.models import PlaylistEntry
        from video.models import Video

        entries = PlaylistEntry.objects.filter(playlist_id=playlist_id)
        return list(
            Video.objects.filter(
                youtube_id__in=entries.values("youtube_id")
            ).values_list("youtube_id", flat=True)
        )


class Reindex(ReindexBase):
    """reindex all documents from redis queue"""

    def __init__(self, task=False):
        super().__init__()
        self.task = task
        self.processed = {
            "videos": 0,
            "channels": 0,
            "playlists": 0,
        }

    def reindex_all(self) -> None:
        """reindex all in queue"""
        if not self.cookie_is_valid():
            print("[reindex] cookie invalid, exiting...")
            return

        for name, index_config in self.REINDEX_CONFIG.items():
            if not RedisQueue(index_config["queue_name"]).length():
                continue

            self.reindex_type(name, index_config)

    def reindex_type(self, name: str, index_config: ReindexConfigType) -> None:
        """reindex all of a single index"""
        queue = RedisQueue(index_config["queue_name"])
        while True:
            total = queue.max_score()
            youtube_id, idx = queue.get_next()
            if not youtube_id or not idx or not total:
                break

            if self.task:
                self._notify(name, total, idx)

            self._mark_active(youtube_id, queue_name=queue.key)
            index_name = index_config["index_name"]
            if index_name == "ta_video":
                video = self.reindex_single_video(youtube_id)
                if video:
                    self._reindex_video_related(video)

            elif index_name == "ta_channel":
                self._reindex_single_channel(channel_id=youtube_id)
            elif index_name == "ta_playlist":
                self._reindex_single_playlist(playlist_id=youtube_id)

            self._clear_active(queue_name=queue.key)

            rand_sleep(self.config)

    def _notify(self, name: str, total: int, idx: int) -> None:
        """send notification back to task"""
        message = [f"Reindexing {name.title()}s {idx}/{total}"]
        progress = idx / total
        self.task.send_progress(message, progress=progress)

    def _mark_active(self, youtube_id: str, queue_name: str) -> None:
        """mark current video as active for RedisQueue.get_position"""
        RedisArchivist().set_message(
            key=f"{queue_name}:active", message=youtube_id, expire=300
        )

    def _clear_active(self, queue_name: str) -> None:
        """clear from active key"""
        RedisArchivist().del_message(key=f"{queue_name}:active")

    def reindex_single_video(
        self, youtube_id: str, from_download=False
    ) -> YoutubeVideo | None:
        """refresh data for single video"""
        video = YoutubeVideo(youtube_id)

        # read current state
        video.get_from_db()
        if not video.json_data:
            return None

        stored = video.json_data.copy()

        media_url: str | bool
        if from_download:
            # use cache path for reindex media file
            media_url = False
        else:
            # use archive path for reindex media file
            media_url = os.path.join(
                EnvironmentSettings.MEDIA_DIR, stored["media_url"]
            )

        video.build_json(media_path=media_url)
        if not video.youtube_meta:
            video.deactivate()
            return None

        video.delete_subtitles(subtitles=stored.get("subtitles"))
        video.check_subtitles()

        # add back
        video.json_data["player"] = stored.get("player")
        video.json_data["date_downloaded"] = stored.get("date_downloaded")
        video.json_data["vid_type"] = stored.get("vid_type")
        video.json_data["channel"] = stored.get("channel")
        if stored.get("playlist"):
            video.json_data["playlist"] = stored.get("playlist")

        video.save_to_db()
        self.processed["videos"] += 1

        return video

    def _reindex_video_related(self, video: YoutubeVideo) -> None:
        """reindex video related metadata and fields"""
        thumb_handler = ThumbManager(video.youtube_id)
        thumb_handler.delete_video_thumb()
        thumb_handler.download_video_thumb(video.json_data["vid_thumb_url"])

        Comments(video.youtube_id, config=self.config).reindex_comments()
        video.get_from_db()
        video.embed_metadata()

    def _reindex_single_channel(self, channel_id: str) -> None:
        """refresh channel data and sync to videos"""
        # read current state
        channel = YoutubeChannel(channel_id)
        channel.get_from_db()
        if not channel.json_data:
            return

        stored = channel.json_data.copy()

        # get new
        channel.get_from_youtube()
        if not channel.youtube_meta:
            channel.deactivate()
            return

        channel.process_youtube_meta()
        channel.get_channel_art()

        # add back
        channel.json_data["channel_subscribed"] = stored["channel_subscribed"]
        overwrites = stored.get("channel_overwrites")
        if overwrites:
            channel.json_data["channel_overwrites"] = overwrites

        channel.save_to_db()
        ChannelFullScan(channel_id, self.config).scan()
        self.processed["channels"] += 1

    def _reindex_single_playlist(self, playlist_id: str) -> None:
        """refresh playlist data"""
        playlist = YoutubePlaylist(playlist_id)
        playlist.get_from_db()
        if (
            not playlist.json_data
            or playlist.json_data["playlist_type"] == "custom"
        ):
            return

        is_active = playlist.update_playlist()
        if not is_active:
            playlist.deactivate()
            return

        self.processed["playlists"] += 1

    def cookie_is_valid(self) -> bool:
        """return true if cookie is enabled and valid"""
        if not self.config["downloads"]["cookie_import"]:
            # is not activated, continue reindex
            return True

        valid = CookieHandler(self.config).validate()
        return valid

    def build_message(self) -> str:
        """build progress message"""
        message = ""
        for key, value in self.processed.items():
            if value:
                message = message + f"{value} {key}, "

        if message:
            message = f"reindexed {message.rstrip(', ')}"

        return message


ReindexStateType = Literal[
    "in_queue", "not_in_queue", "running", "empty", "processing"
]
RequestType = Literal["video", "channel", "playlist"]
QueueType = RequestType | Literal["all"]


class ReindexProgressType(TypedDict):
    """response type"""

    id: str | None
    total_queued: int
    queue_type: QueueType
    queue_position: int | None
    state: ReindexStateType


class ReindexProgress(ReindexBase):
    """
    get progress of reindex task
    request_type: key of self.REINDEX_CONFIG
    request_id: id of request_type
    """

    def __init__(
        self,
        request_type: RequestType | None = None,
        request_id: str | None = None,
    ):
        super().__init__()
        self.request_type = request_type
        self.request_id = request_id

    def get_progress(self) -> ReindexProgressType:
        """get progress from task"""
        queue_name, request_type = self._get_queue_name()
        total = self._get_total_in_queue(queue_name)
        queue_position, state = self._get_state(total, queue_name)

        progress: ReindexProgressType = {
            "id": self.request_id or None,
            "total_queued": total,
            "queue_type": request_type,
            "queue_position": queue_position,
            "state": state,
        }

        return progress

    def _get_queue_name(self) -> tuple[str, QueueType]:
        """return queue_name, queue_type, raise exception on error"""
        if self.request_type is None:
            return "all", "all"

        reindex_config = self.REINDEX_CONFIG.get(self.request_type)
        if not reindex_config:
            print(f"reindex_config not found: {self.request_type}")
            raise ValueError

        return reindex_config["queue_name"], self.request_type

    def _get_total_in_queue(self, queue_name):
        """get all items in queue"""
        total = 0
        if queue_name == "all":
            queues = [i["queue_name"] for i in self.REINDEX_CONFIG.values()]
            for queue in queues:
                total += len(RedisQueue(queue).get_all())
        else:
            total += len(RedisQueue(queue_name).get_all())

        return total

    def _get_state(
        self, total, queue_name
    ) -> tuple[int | None, ReindexStateType]:
        """get state based on request_id"""
        if not self.request_id:
            return (None, "running") if total else (None, "empty")

        active_id = RedisArchivist().get_message_str(f"{queue_name}:active")
        if active_id == self.request_id:
            return None, "processing"

        rank = RedisQueue(queue_name).get_rank(self.request_id)
        if rank is not None:
            return rank, "in_queue"

        return None, "not_in_queue"


class ChannelFullScan:
    """full scan of channel to fix vid_type mismatch"""

    def __init__(self, channel_id, config):
        self.channel_id = channel_id
        self.config = config
        self.to_update = False

    def scan(self) -> None:
        """match local with remote"""
        print(f"{self.channel_id}: start full scan")
        all_local_videos = self._get_all_local()
        all_remote_videos = get_last_channel_videos(
            self.channel_id, self.config, limit=None
        )

        self.to_update = []
        for video in all_local_videos:
            video_id = video["youtube_id"]
            remote_match = [
                i for i in all_remote_videos if i["id"] == video_id
            ]
            if not remote_match:
                print(f"{video_id}: no remote match found")
                continue

            expected_type = remote_match[0]["vid_type"]
            if video["vid_type"] != expected_type:
                self.to_update.append(
                    {
                        "video_id": video_id,
                        "vid_type": expected_type,
                    }
                )

        self.update()

    def _get_all_local(self):
        """get all local indexed channel_videos"""
        channel = YoutubeChannel(self.channel_id)
        all_local_videos = channel.get_channel_videos()

        return all_local_videos

    def update(self):
        """build bulk query for updates"""
        if not self.to_update:
            print(f"{self.channel_id}: nothing to update")
            return

        print(f"{self.channel_id}: fixing {len(self.to_update)} videos")
        # pylint: disable=import-outside-toplevel
        from video.models import Video

        with transaction.atomic():
            for video in self.to_update:
                Video.objects.filter(youtube_id=video["video_id"]).update(
                    vid_type=video["vid_type"]
                )

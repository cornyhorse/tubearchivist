"""
functionality:
- handle yt_dlp
- build options and post processor
- download video files
- move to archive
"""

import os
import shutil
from datetime import datetime, timedelta, timezone

from appsettings.src.config import AppConfig
from channel.src.index import YoutubeChannel
from common.src.env_settings import EnvironmentSettings
from common.src.helper import (
    get_channel_overwrites,
    get_playlists,
    ignore_filelist,
    rand_sleep,
)
from common.src.ta_redis import RedisQueue
from common.src.urlparser import ParsedURLType
from django.db import transaction
from download.models import Download
from download.src.queue import PendingList
from download.src.yt_dlp_base import YtWrap
from playlist.models import Playlist
from playlist.src.index import YoutubePlaylist
from video.models import Video
from video.src.comments import CommentList
from video.src.constants import VideoTypeEnum
from video.src.index import YoutubeVideo, index_new_video


class DownloaderBase:
    """base class for shared config"""

    CACHE_DIR = EnvironmentSettings.CACHE_DIR
    MEDIA_DIR = EnvironmentSettings.MEDIA_DIR
    CHANNEL_QUEUE = "download:channel"
    PLAYLIST_QUEUE = "download:playlist:full"
    PLAYLIST_QUICK = "download:playlist:quick"
    VIDEO_QUEUE = "download:video"

    def __init__(self, task=None):
        self.task = task
        self.config = AppConfig().config
        self.channel_overwrites = get_channel_overwrites()
        self.now = datetime.now(timezone.utc)


class VideoDownloader(DownloaderBase):
    """handle the video download functionality"""

    def __init__(self, task=False):
        super().__init__(task)
        self.obs = False
        self._build_obs()

    def run_queue(self, auto_only=False) -> tuple[int, int]:
        """setup download queue in redis loop until no more items"""
        downloaded = 0
        failed = 0
        while True:
            video_data = self._get_next(auto_only)
            if self.task.is_stopped() or not video_data:
                self._reset_auto()
                break

            if downloaded > 0:
                rand_sleep(self.config)

            youtube_id = video_data["youtube_id"]
            channel_id = video_data["channel_id"]
            print(f"{youtube_id}: Downloading video")
            self._notify(video_data, "Validate download format")

            success = self._dl_single_vid(youtube_id, channel_id)
            if not success:
                failed += 1
                continue

            self._notify(video_data, "Add video metadata to index", progress=1)
            video_type = VideoTypeEnum(video_data["vid_type"])
            vid_dict = index_new_video(youtube_id, video_type=video_type)
            RedisQueue(self.CHANNEL_QUEUE).add(channel_id)
            RedisQueue(self.VIDEO_QUEUE).add(youtube_id)

            self._notify(video_data, "Move downloaded file to archive")
            self.move_to_archive(vid_dict)
            self._delete_from_pending(youtube_id)
            downloaded += 1

        # post processing
        DownloadPostProcess(self.task).run()

        return downloaded, failed

    def _notify(self, video_data, message, progress=False):
        """send progress notification to task"""
        if not self.task:
            return

        typ = VideoTypeEnum(video_data["vid_type"]).value.rstrip("s").title()
        title = video_data.get("title")
        self.task.send_progress(
            [f"Processing {typ}: {title}", message], progress=progress
        )

    @staticmethod
    def _get_next(auto_only) -> dict | bool:
        """next item in queue as document, False if empty"""
        with transaction.atomic():
            item = Download.objects.next_pending(auto_only=auto_only)
            if not item:
                return False
            return item.to_doc()

    def _progress_hook(self, response):
        """process the progress_hooks from yt_dlp"""
        progress = False
        try:
            size = response.get("_total_bytes_str")
            if size.strip() == "N/A":
                size = response.get("_total_bytes_estimate_str", "N/A")

            percent = response["_percent_str"]
            progress = float(percent.strip("%")) / 100
            speed = response["_speed_str"]
            eta = response["_eta_str"]
            message = f"{percent} of {size} at {speed} - time left: {eta}"
        except KeyError:
            message = "processing"

        if self.task:
            title = response["info_dict"]["title"]
            self.task.send_progress([title, message], progress=progress)

    def _build_obs(self):
        """collection to build all obs passed to yt-dlp"""
        self._build_obs_basic()
        self._build_obs_user()
        self._build_obs_postprocessors()

    def _build_obs_basic(self):
        """initial obs"""
        self.obs = {
            "merge_output_format": "mp4",
            "outtmpl": (self.CACHE_DIR + "/download/%(id)s.mp4"),
            "progress_hooks": [self._progress_hook],
            "noprogress": True,
            "continuedl": True,
            "writethumbnail": False,
            "noplaylist": True,
            "color": "no_color",
        }

    def _build_obs_user(self):
        """build user customized options"""
        if self.config["downloads"]["format"]:
            self.obs["format"] = self.config["downloads"]["format"]
        if self.config["downloads"]["format_sort"]:
            format_sort = self.config["downloads"]["format_sort"]
            format_sort_list = [i.strip() for i in format_sort.split(",")]
            self.obs["format_sort"] = format_sort_list
        if self.config["downloads"]["limit_speed"]:
            self.obs["ratelimit"] = (
                self.config["downloads"]["limit_speed"] * 1024
            )

        throttle = self.config["downloads"]["throttledratelimit"]
        if throttle:
            self.obs["throttledratelimit"] = throttle * 1024

    def _build_obs_postprocessors(self):
        """add postprocessor to obs"""
        postprocessors = []

        if self.config["downloads"]["add_metadata"]:
            # full metadata is added in DownloadPostProcess
            postprocessors.append(
                {
                    "key": "FFmpegMetadata",
                    "add_chapters": True,
                }
            )

        self.obs["postprocessors"] = postprocessors

    def _set_overwrites(self, obs: dict, channel_id: str) -> None:
        """add overwrites to obs"""
        overwrites = self.channel_overwrites.get(channel_id)
        if overwrites and overwrites.get("download_format"):
            obs["format"] = overwrites.get("download_format")

    def _dl_single_vid(self, youtube_id: str, channel_id: str) -> bool:
        """download single video"""
        obs = self.obs.copy()
        self._set_overwrites(obs, channel_id)
        dl_cache = os.path.join(self.CACHE_DIR, "download")

        success, message = YtWrap(obs, self.config).download(youtube_id)
        if not success:
            self._handle_error(youtube_id, message)

        if self.obs["writethumbnail"]:
            # webp files don't get cleaned up automatically
            all_cached = ignore_filelist(os.listdir(dl_cache))
            to_clean = [i for i in all_cached if not i.endswith(".mp4")]
            for file_name in to_clean:
                file_path = os.path.join(dl_cache, file_name)
                os.remove(file_path)

        return success

    @staticmethod
    def _handle_error(youtube_id, message):
        """store error message"""
        Download.objects.filter(youtube_id=youtube_id).update(message=message)

    def move_to_archive(self, vid_dict):
        """move downloaded video from cache to archive"""
        host_uid = EnvironmentSettings.HOST_UID
        host_gid = EnvironmentSettings.HOST_GID
        # make folder
        folder = os.path.join(
            self.MEDIA_DIR, vid_dict["channel"]["channel_id"]
        )
        if not os.path.exists(folder):
            os.makedirs(folder)
            if host_uid and host_gid:
                os.chown(folder, host_uid, host_gid)
        # move media file
        media_file = vid_dict["youtube_id"] + ".mp4"
        old_path = os.path.join(self.CACHE_DIR, "download", media_file)
        new_path = os.path.join(self.MEDIA_DIR, vid_dict["media_url"])
        # move media file and fix permission
        shutil.move(old_path, new_path, copy_function=shutil.copyfile)
        if host_uid and host_gid:
            os.chown(new_path, host_uid, host_gid)

    @staticmethod
    def _delete_from_pending(youtube_id):
        """delete downloaded video from the queue if its there"""
        Download.objects.filter(youtube_id=youtube_id).delete()

    @staticmethod
    def _reset_auto():
        """reset autostart to defaults after queue stop"""
        updated = Download.objects.reset_auto_start()
        if updated:
            print(f"[download] reset auto start on {updated} videos.")


class DownloadPostProcess(DownloaderBase):
    """handle task to run after download queue finishes"""

    def run(self):
        """run all functions"""
        self.auto_delete_all()
        self.auto_delete_overwrites()
        self.refresh_playlist()
        self.match_videos()
        self.get_comments()
        self.embed_metadata()

        RedisQueue(self.VIDEO_QUEUE).clear()

    def auto_delete_all(self):
        """handle auto delete"""
        autodelete_days = self.config["downloads"]["autodelete_days"]
        if not autodelete_days:
            return

        print(f"auto delete older than {autodelete_days} days")
        cutoff = self.now - timedelta(days=autodelete_days)
        videos = Video.objects.filter(
            watched=True,
            watched_date__lte=cutoff,
            channel__autodelete_days__isnull=True,
        ).order_by("watched_date")
        self._auto_delete_watched(videos)

    def auto_delete_overwrites(self):
        """handle per channel auto delete from overwrites"""
        for channel_id, value in self.channel_overwrites.items():
            autodelete_days = value.get("autodelete_days")
            if autodelete_days is None:
                continue

            print(f"{channel_id}: delete older than {autodelete_days}d")
            cutoff = self.now - timedelta(days=autodelete_days)
            videos = Video.objects.filter(
                channel_id=channel_id, watched=True, watched_date__lte=cutoff
            ).order_by("-watched_date")
            self._auto_delete_watched(videos)

    @staticmethod
    def _auto_delete_watched(videos) -> None:
        """delete watched videos after x days"""
        to_delete = list(videos.values("youtube_id", "vid_type"))
        if not to_delete:
            return

        for video in to_delete:
            youtube_id = video["youtube_id"]
            print(f"{youtube_id}: auto delete video")
            YoutubeVideo(youtube_id).delete_media_file()

        print("add deleted to ignore list")

        parsed_ids: list[ParsedURLType] = []

        for video_item in to_delete:
            vid_type = getattr(VideoTypeEnum, video_item["vid_type"].upper())
            parsed_ids.append(
                {
                    "type": "video",
                    "url": video_item["youtube_id"],
                    "vid_type": vid_type,
                }
            )

        PendingList(youtube_ids=parsed_ids).parse_url_list(status="ignore")

    def refresh_playlist(self) -> None:
        """match videos with playlists"""
        self.add_playlists_to_refresh()

        queue = RedisQueue(self.PLAYLIST_QUEUE)
        while True:
            total = queue.max_score()
            playlist_id, idx = queue.get_next()
            if not playlist_id or not idx or not total:
                break

            try:
                playlist = YoutubePlaylist(playlist_id)
                playlist.update_playlist(skip_on_empty=True)
                if not playlist.json_data:
                    raise ValueError("no json data extracted for playlist")

            except ValueError as err:
                message = [
                    f"{playlist_id}: skip failed playlist import",
                    str(err),
                ]
                print(message)
                if self.task:
                    self.task.send_progress(message)

                continue

            if not self.task:
                continue

            channel_name = playlist.json_data["playlist_channel"]
            playlist_title = playlist.json_data["playlist_name"]
            message = [
                f"Post Processing Playlists for: {channel_name}",
                f"{playlist_title} [{idx}/{total}]",
            ]
            progress = idx / total
            self.task.send_progress(message, progress=progress)
            rand_sleep(self.config)

    def add_playlists_to_refresh(self) -> None:
        """add playlists to refresh"""
        if self.task:
            message = ["Post Processing Playlists", "Scanning for Playlists"]
            self.task.send_progress(message)

        self._add_playlist_sub()
        self._add_channel_playlists()
        self._add_video_playlists()

    def _add_playlist_sub(self):
        """add subscribed playlists to refresh"""
        playlists = get_playlists(subscribed_only=True, source=["playlist_id"])
        to_add = [i["playlist_id"] for i in playlists]
        RedisQueue(self.PLAYLIST_QUEUE).add_list(to_add)

    def _add_channel_playlists(self):
        """add playlists from channels to refresh"""
        queue = RedisQueue(self.CHANNEL_QUEUE)
        while True:
            channel_id, _ = queue.get_next()
            if not channel_id:
                break

            channel = YoutubeChannel(channel_id)
            channel.get_from_db()
            if not channel.json_data:
                print(f"{channel_id}: skip failed channel import")
                continue

            overwrites = channel.get_overwrites()
            if overwrites.get("index_playlists"):
                channel.get_all_playlists()
                to_add = [i[0] for i in channel.all_playlists]
                RedisQueue(self.PLAYLIST_QUEUE).add_list(to_add)

    def _add_video_playlists(self):
        """add other playlists for quick sync"""
        all_playlists = RedisQueue(self.PLAYLIST_QUEUE).get_all()
        video_ids = RedisQueue(self.VIDEO_QUEUE).get_all()
        to_add = list(
            Playlist.objects.filter(entries__youtube_id__in=video_ids)
            .exclude(playlist_id__in=all_playlists)
            .order_by("playlist_id")
            .values_list("playlist_id", flat=True)
            .distinct()
        )
        RedisQueue(self.PLAYLIST_QUICK).add_list(to_add)

    def match_videos(self) -> None:
        """scan rest of indexed playlists to match videos"""
        queue = RedisQueue(self.PLAYLIST_QUICK)
        while True:
            total = queue.max_score()
            playlist_id, idx = queue.get_next()
            if not playlist_id or not idx or not total:
                break

            playlist = YoutubePlaylist(playlist_id)
            playlist.get_from_db()
            if not playlist.json_data:
                print(f"{playlist_id}: skip failed playlist import")
                continue

            playlist.match_local()

            if not self.task:
                continue

            message = [
                "Post Processing Playlists.",
                f"Validate Playlists: - {idx}/{total}",
            ]
            progress = idx / total
            self.task.send_progress(message, progress=progress)

    def get_comments(self):
        """get comments from youtube"""
        video_queue = RedisQueue(self.VIDEO_QUEUE)
        comment_list = CommentList(task=self.task)
        comment_list.add(video_ids=video_queue.get_all())
        comment_list.index()

    def embed_metadata(self):
        """embed metadata in media file"""
        if not self.config["downloads"].get("add_metadata"):
            return

        queue = RedisQueue(self.VIDEO_QUEUE)
        total = queue.max_score()
        video_ids = queue.get_all()

        for idx, youtube_id in enumerate(video_ids):
            YoutubeVideo(youtube_id).embed_metadata()

            if not self.task:
                continue

            message = [
                "Post Processing Videos.",
                f"Embed metadata: - {idx}/{total}",
            ]
            progress = idx / total
            self.task.send_progress(message, progress=progress)

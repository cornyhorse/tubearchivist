"""
Functionality:
- scan the filesystem to delete or index
"""

import os

from appsettings.src.config import AppConfig
from common.src.env_settings import EnvironmentSettings
from common.src.helper import ignore_filelist, rand_sleep
from download.src.queue_interact import PendingInteract
from video.models import Video
from video.src.comments import Comments
from video.src.index import YoutubeVideo, index_new_video
from video.src.meta_embed import IndexFromEmbed


class Scanner:
    """scan index and filesystem"""

    VIDEOS: str = EnvironmentSettings.MEDIA_DIR

    def __init__(
        self,
        task=False,
        ignore_error: bool = False,
        prefer_local: bool = False,
    ) -> None:
        self.task = task
        self.ignore_error = ignore_error
        self.prefer_local = prefer_local
        self.config = None
        self.to_delete: set[tuple[str, str]] = set()
        self.to_index: set[tuple[str, str]] = set()

    def scan(self) -> None:
        """scan the filesystem"""
        downloaded = self._get_downloaded()
        indexed = self._get_indexed()
        self.to_index = downloaded - indexed
        self.to_delete = indexed - downloaded

    def _get_downloaded(self) -> set[tuple[str, str]]:
        """get downloaded ids"""
        if self.task:
            self.task.send_progress(["Scan your filesystem for videos."])

        downloaded: set = set()
        channels = ignore_filelist(os.listdir(self.VIDEOS))
        for channel in channels:
            folder = os.path.join(self.VIDEOS, channel)
            files = ignore_filelist(os.listdir(folder))
            downloaded.update(
                {
                    (i.split(".")[0], f"{channel}/{i}")
                    for i in files
                    if i.endswith(".mp4")
                }
            )

        return downloaded

    def _get_indexed(self) -> set[tuple[str, str]]:
        """get all indexed ids"""
        if self.task:
            self.task.send_progress(["Get all videos indexed."])

        return set(Video.objects.values_list("youtube_id", "media_url"))

    def apply(self) -> None:
        """apply all changes"""
        if not self.config:
            self.config = AppConfig().config

        self.delete()
        self.index()

    def delete(self) -> None:
        """delete videos from index"""
        if not self.to_delete:
            print("[scanner] nothing to delete")
            return

        if self.task:
            self.task.send_progress(
                [f"Remove {len(self.to_delete)} videos from index."]
            )

        for youtube_id, _ in self.to_delete:
            YoutubeVideo(youtube_id).delete_media_file()

    def index(self) -> None:
        """index new"""
        if not self.to_index:
            print("[scanner] nothing to index")
            return

        total = len(self.to_index)
        for idx, (youtube_id, media_url) in enumerate(self.to_index):
            self._notify(total, youtube_id, idx)

            file_path = os.path.join(self.VIDEOS, media_url)
            if self.prefer_local:
                # try index from embed
                success = self._index_from_embed(file_path, youtube_id)
                if success:
                    continue

            try:
                # try index from remote
                index_new_video(youtube_id)
                self._cleanup(youtube_id)
                Comments(youtube_id).build_json(upload=True)
                YoutubeVideo(youtube_id).embed_metadata()
                rand_sleep(self.config)
            except ValueError as err:
                # fallback from index from embed
                success = self._index_from_embed(file_path, youtube_id)
                if success:
                    continue

                if self.ignore_error:
                    self._notify_error(youtube_id)
                    rand_sleep(self.config)
                    continue

                raise ValueError from err

    def _index_from_embed(self, file_path: str, youtube_id: str) -> bool:
        """index from embedded metadata"""
        json_data = IndexFromEmbed(
            file_path, use_user_conf=True, config=self.config
        ).run_index()
        if json_data:
            self._cleanup(youtube_id)

        return bool(json_data)

    @staticmethod
    def _cleanup(youtube_id: str) -> None:
        """clean up from queue"""
        PendingInteract(youtube_id=youtube_id).delete_item()

    def _notify(self, total, youtube_id, idx):
        """send notification"""
        if not self.task:
            return

        self.task.send_progress(
            message_lines=[
                f"Index missing video {youtube_id}, {idx + 1}/{total}"
            ],
            progress=(idx + 1) / total,
        )

    def _notify_error(self, youtube_id):
        """notify error"""
        if not self.task:
            return

        message = f"[scanner] Failed to index {youtube_id}, no metadata"
        print(f"[scanner] {message}")
        self.task.send_progress(
            message_lines=[message, "Continue..."],
            level="error",
        )

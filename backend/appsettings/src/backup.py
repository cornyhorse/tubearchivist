"""
Functionality:
- Handle json zip file based backup
- create backup
- restore backup

The zip keeps the format ES backups always had (es_<index>-<date>-<n>.json
files of bulk action/source line pairs), so backups taken before the move
to Postgres still restore. Restoring converts the zip into the
storage.es_export layout and runs the verified importer.
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from common.src.env_settings import EnvironmentSettings
from common.src.helper import ignore_filelist
from django.db import transaction
from task.models import CustomPeriodicTask

INDEXES = [
    "config",
    "channel",
    "video",
    "playlist",
    "download",
    "comment",
    "subtitle",
    # fork only, ignored by stock TA
    "download_rule",
    "emby_episode",
]
RESTORE_INDEXES = frozenset(f"ta_{index}" for index in INDEXES)
CHUNK_SIZE = 500


def _document_sources() -> dict:
    """index name -> function yielding (_id, legacy document)"""
    # pylint: disable=import-outside-toplevel
    from appsettings.models import ConfigDocument
    from channel.models import Channel
    from download.models import Download, DownloadRule
    from emby.models import EpisodeNumber
    from playlist.models import Playlist
    from storage.importer import episode_to_doc, rule_to_doc
    from user.models import UserConfig
    from video.models import CommentRefresh, SubtitleLine
    from video.src.comments import Comments

    def config():
        for document in ConfigDocument.objects.order_by("key"):
            yield document.key, document.value
        for user_config in UserConfig.objects.order_by("user_id"):
            yield f"user_{user_config.user_id}", {"config": user_config.config}

    def downloads():
        queue = Download.objects.with_channel_indexed().order_by("pk")
        for item in queue.iterator(chunk_size=CHUNK_SIZE):
            yield item.pk, item.to_doc()

    def subtitles():
        lines = SubtitleLine.objects.select_related("video__channel")
        for line in lines.order_by("fragment_id").iterator(chunk_size=5000):
            yield line.fragment_id, line.to_doc()

    return {
        "config": config,
        "channel": lambda: (
            (c.pk, c.to_doc()) for c in Channel.objects.order_by("pk")
        ),
        "video": _video_documents,
        "playlist": lambda: (
            (p.pk, p.to_doc()) for p in Playlist.objects.order_by("pk")
        ),
        "download": downloads,
        "comment": lambda: (
            (r.video_id, Comments(r.video_id).get_comments())
            for r in CommentRefresh.objects.order_by("video_id")
        ),
        "subtitle": subtitles,
        "download_rule": lambda: (
            (str(r.pk), rule_to_doc(r))
            for r in DownloadRule.objects.order_by("pk")
        ),
        "emby_episode": lambda: (
            (str(n.pk), episode_to_doc(n))
            for n in EpisodeNumber.objects.order_by("pk")
        ),
    }


def _video_documents():
    """(_id, document) of every video, playlists loaded per chunk"""
    # pylint: disable=import-outside-toplevel
    from video.models import Video

    queryset = Video.objects.select_related("channel").order_by("pk")
    chunk: list = []
    for video in queryset.iterator(chunk_size=CHUNK_SIZE):
        chunk.append(video)
        if len(chunk) == CHUNK_SIZE:
            yield from _with_ids(Video.to_docs(chunk))
            chunk = []
    yield from _with_ids(Video.to_docs(chunk))


def _with_ids(docs: list[dict]):
    """(youtube_id, document) pairs"""
    return ((doc["youtube_id"], doc) for doc in docs)


def iter_documents(index: str):
    """yield (_id, legacy document) of every row behind index"""
    sources = _document_sources()
    if index not in sources:
        raise ValueError(f"unknown index: {index}")
    yield from sources[index]()


def wipe_data() -> None:
    """delete all TA data rows, children first"""
    # pylint: disable=import-outside-toplevel
    from appsettings.models import ConfigDocument
    from channel.models import Channel
    from download.models import Download, DownloadRule
    from emby.models import EpisodeNumber
    from playlist.models import Playlist
    from user.models import UserConfig
    from video.models import Comment, CommentRefresh, SubtitleLine, Video

    for model in (
        DownloadRule,
        EpisodeNumber,
        Comment,
        CommentRefresh,
        SubtitleLine,
        Playlist,
        Download,
        Video,
        Channel,
        ConfigDocument,
        UserConfig,
    ):
        model.objects.all().delete()


class DatabaseBackup:
    """dump the database to nd-json files for later import"""

    CACHE_DIR = EnvironmentSettings.CACHE_DIR
    BACKUP_DIR = os.path.join(CACHE_DIR, "backup")

    def __init__(self, reason=False, task=False) -> None:
        self.timestamp = datetime.now().strftime("%Y%m%d")
        self.reason = reason
        self.task = task

    def backup_all_indexes(self):
        """backup all indexes, add reason to init"""
        print("backup all indexes")
        if not self.reason:
            raise ValueError("missing backup reason in DatabaseBackup")

        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        if self.task:
            self.task.send_progress(["Scanning your index."])
        for index in INDEXES:
            print(f"backup: export in progress for {index}")
            self.backup_index(index)

        if self.task:
            self.task.send_progress(["Compress files to zip archive."])
        self.zip_it()
        if self.reason == "auto":
            self.rotate_backup()

    def backup_index(self, index_name: str) -> None:
        """write all documents of index_name as bulk ndjson chunks"""
        chunk: list[str] = []
        counter = 0
        for doc_id, document in iter_documents(index_name):
            action = {"index": {"_index": f"ta_{index_name}", "_id": doc_id}}
            chunk.append(json.dumps(action))
            chunk.append(json.dumps(document))
            if len(chunk) >= 2 * CHUNK_SIZE:
                self._write_chunk(index_name, counter, chunk)
                counter += 1
                chunk = []

        if chunk:
            self._write_chunk(index_name, counter, chunk)

    def _write_chunk(self, index_name: str, counter: int, lines: list[str]):
        file_name = f"es_{index_name}-{self.timestamp}-{counter}.json"
        file_path = os.path.join(self.BACKUP_DIR, file_name)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if self.task:
            self.task.send_progress([f"Processing {index_name.title()}s"])

    def zip_it(self):
        """pack it up into single zip file"""
        file_name = f"ta_backup-{self.timestamp}-{self.reason}.zip"

        to_backup = []
        for file in os.listdir(self.BACKUP_DIR):
            if file.endswith(".json"):
                to_backup.append(os.path.join(self.BACKUP_DIR, file))

        backup_file = os.path.join(self.BACKUP_DIR, file_name)

        comp = zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(backup_file, "w", compression=comp) as zip_f:
            for backup_file in to_backup:
                zip_f.write(backup_file, os.path.basename(backup_file))

        # cleanup
        for backup_file in to_backup:
            os.remove(backup_file)

    def get_all_backup_files(self):
        """build all available backup files for view"""
        if not os.path.isdir(self.BACKUP_DIR):
            return []

        all_backup_files = ignore_filelist(os.listdir(self.BACKUP_DIR))
        all_available_backups = [
            i
            for i in all_backup_files
            if i.startswith("ta_") and i.endswith(".zip")
        ]
        all_available_backups.sort(reverse=True)

        backup_dicts = []
        for filename in all_available_backups:
            data = self.build_backup_file_data(filename)
            backup_dicts.append(data)

        return backup_dicts

    def build_backup_file_data(self, filename):
        """build metadata of single backup file"""
        file_path = os.path.join(self.BACKUP_DIR, filename)
        if not os.path.exists(file_path):
            return False

        file_split = filename.split("-")
        if len(file_split) == 2:
            timestamp = file_split[1].removesuffix(".zip")
            reason = False
        elif len(file_split) == 3:
            timestamp = file_split[1]
            reason = file_split[2].removesuffix(".zip")
        else:
            raise ValueError

        data = {
            "filename": filename,
            "file_path": file_path,
            "file_size": os.path.getsize(file_path),
            "timestamp": timestamp,
            "reason": reason,
        }

        return data

    def restore(self, filename):
        """replace all data with the backup, verified, in one transaction"""
        # pylint: disable=import-outside-toplevel
        from storage.importer import EsImporter

        file_path = os.path.join(self.BACKUP_DIR, filename)
        export_dir = Path(tempfile.mkdtemp(prefix="ta-restore-"))
        try:
            self._notify("Unpack backup file.", 0.1)
            self.zip_to_export(file_path, export_dir)
            self._notify("Restore database from backup.", 0.5)
            with transaction.atomic():
                wipe_data()
                report = EsImporter(export_dir).run()
                if not report.ok:
                    raise ValueError(
                        "restore verification failed: "
                        + "; ".join(report.mismatches[:5])
                    )
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)

        self._notify("Restore completed.", 1)
        return report

    @staticmethod
    def zip_to_export(zip_path: str, export_dir: Path) -> dict[str, int]:
        """convert a backup zip into the storage.es_export layout"""
        counts: dict[str, int] = {}
        handles = {}
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_f:
                for name in sorted(zip_f.namelist()):
                    if not name.startswith("es_") or not name.endswith(
                        ".json"
                    ):
                        continue
                    lines = zip_f.read(name).decode("utf-8").splitlines()
                    lines = [line for line in lines if line.strip()]
                    for action_line, source_line in zip(
                        lines[::2], lines[1::2]
                    ):
                        action = json.loads(action_line)["index"]
                        index = re.sub(r"_v\d+$", "", action["_index"])
                        if index not in RESTORE_INDEXES:
                            raise ValueError(
                                f"unknown index in backup: {index!r}"
                            )
                        if index not in handles:
                            handles[index] = open(
                                export_dir / f"{index}.ndjson",
                                "w",
                                encoding="utf-8",
                            )
                            counts[index] = 0
                        hit = {
                            "_id": action["_id"],
                            "_source": json.loads(source_line),
                        }
                        handles[index].write(json.dumps(hit) + "\n")
                        counts[index] += 1
        finally:
            for handle in handles.values():
                handle.close()

        (export_dir / "manifest.json").write_text(json.dumps(counts))
        return counts

    def _notify(self, message: str, progress: float):
        """notify restore progress"""
        if self.task:
            self.task.send_progress(message_lines=[message], progress=progress)

    def rotate_backup(self):
        """delete old backups if needed"""
        try:
            task = CustomPeriodicTask.objects.get(name="run_backup")
        except CustomPeriodicTask.DoesNotExist:
            return

        rotate = task.task_config.get("rotate")
        if not rotate:
            return

        all_backup_files = self.get_all_backup_files()
        auto = [i for i in all_backup_files if i["reason"] == "auto"]

        if len(auto) <= rotate:
            print("no backup files to rotate")
            return

        all_to_delete = auto[rotate:]
        for to_delete in all_to_delete:
            self.delete_file(to_delete["filename"])

    def delete_file(self, filename):
        """delete backup file"""
        file_path = os.path.join(self.BACKUP_DIR, filename)
        if not os.path.exists(file_path):
            print(f"backup file not found: {filename}")
            return False

        print(f"remove old backup file: {file_path}")
        os.remove(file_path)

        return file_path

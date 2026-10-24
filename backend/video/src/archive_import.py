"""
Functionality:
- import a folder of media files as an archived channel, without any
  network access (docs/fork/archive-import.md)
- idempotent: a file whose id is already indexed is left alone; nothing is
  ever renamed, overwritten or deleted
- a file is placed in the media tree as <channel id>/<video id>.<ext>, by
  hardlink, copy or move, so the scanner, the player and the Emby writer
  treat it like any other video
"""

import errno
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from channel.models import Channel, EmbyShow, Source
from common.src.env_settings import EnvironmentSettings
from django.db import transaction
from download.src.thumbnails import ThumbManager
from PIL import Image
from video.models import Video
from video.src.archive import (
    IMAGE_EXTENSIONS,
    archive_channel_id,
    archive_video_id,
    find_media,
    find_thumbnail,
    read_nfo,
    read_sidecar,
)
from video.src.constants import VideoTypeEnum

MODES = ("auto", "hardlink", "copy", "move")
# channel art looked for in the folder: kind, ThumbManager type, file names
CHANNEL_ART = (
    ("thumb", "icon", ("poster", "folder", "cover", "logo")),
    ("banner", "banner", ("banner",)),
    ("tvart", "tvart", ("fanart", "background")),
)


class ArchiveImportError(ValueError):
    """the import can't start, the message is shown as is"""


@dataclass
class ImportReport:
    """what an import did"""

    channel_id: str = ""
    imported: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    stopped: bool = False

    def message(self) -> str:
        """one line summary"""
        text = (
            f"{self.channel_id}: {len(self.imported)} imported, "
            f"{len(self.existing)} already indexed, "
            f"{len(self.failed)} failed"
        )
        return text + (", stopped early" if self.stopped else "")


def place_file(source: Path, dest: Path, mode: str) -> str:
    """
    put source at dest, returns the way it was placed: hardlink, copy
    or move. An existing dest of the same size is a finished earlier
    attempt and is kept; one of another size is an error.
    """
    if dest.exists():
        if dest.stat().st_size != source.stat().st_size:
            raise ArchiveImportError(f"{dest} exists with another size")

        return "existing"

    dest.parent.mkdir(parents=True, exist_ok=True)
    if mode == "move":
        shutil.move(source, dest, copy_function=shutil.copyfile)
        way = "move"
    elif mode == "copy":
        _copy(source, dest)
        way = "copy"
    else:
        try:
            os.link(source, dest)
            way = "hardlink"
        except OSError as err:
            if err.errno != errno.EXDEV or mode != "auto":
                raise ArchiveImportError(
                    f"can't hardlink {source}: {err.strerror}"
                ) from err

            # another filesystem, a hardlink can't reach
            _copy(source, dest)
            way = "copy"

    if EnvironmentSettings.HOST_UID and EnvironmentSettings.HOST_GID:
        os.chown(
            dest, EnvironmentSettings.HOST_UID, EnvironmentSettings.HOST_GID
        )

    return way


def _copy(source: Path, dest: Path) -> None:
    """copy to a hidden name, then rename: dest is never half written"""
    partial = dest.with_name(f".{dest.name}.part")
    shutil.copyfile(source, partial)
    partial.rename(dest)


def probe(path: Path) -> tuple[int, list[dict]]:
    """
    duration in seconds and the streams of a media file, with ffprobe;
    unlike the downloader's MediaStreamExtractor this doesn't assume that
    a video stream without a bit rate is cover art, which Matroska and
    WebM streams usually are (they carry the rate on the container)
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ArchiveImportError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    container = data.get("format") or {}
    streams = []
    for stream in data.get("streams") or []:
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue

        kind = stream.get("codec_type")
        item = {
            "bitrate": int(stream.get("bit_rate") or 0),
            "codec": stream.get("codec_name", "undefined"),
            "index": stream["index"],
            "type": kind,
        }
        if kind == "video":
            item["height"] = stream.get("height") or 0
            item["width"] = stream.get("width") or 0
        elif kind != "audio":
            continue

        streams.append(item)

    try:
        duration = int(float(container.get("duration")))
    except (TypeError, ValueError):
        duration = 0

    return duration, streams


def grab_frame(media: Path, duration: int, out: Path) -> bool:
    """one frame at 10% of the video as a jpg, False if ffmpeg failed"""
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        str(max(1, duration // 10) if duration > 2 else 0),
        "-i",
        str(media),
        "-frames:v",
        "1",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, check=False)
    return result.returncode == 0 and out.is_file()


class ArchiveImporter:
    """index the media files of a folder as videos of one archived channel"""

    def __init__(
        self,
        root: str | Path,
        channel_name: str | None = None,
        channel_id: str | None = None,
        mode: str = "auto",
        dry_run: bool = False,
        task=None,
    ):
        self.root = Path(root)
        self.channel_name = channel_name
        self.channel_id = channel_id
        self.mode = mode
        self.dry_run = dry_run
        self.task = task
        self.report = ImportReport()

    def run(self) -> ImportReport:
        """import every new media file of the folder"""
        if self.mode not in MODES:
            raise ArchiveImportError(f"mode must be one of {MODES}")
        if not self.root.is_dir():
            raise ArchiveImportError(f"{self.root} is not a folder")

        channel = self._channel()
        self.report.channel_id = channel.channel_id
        files = find_media(self.root)
        seen: set[str] = set()
        for idx, media in enumerate(files, start=1):
            if self.task and self.task.is_stopped():
                self.report.stopped = True
                break

            if self.task:
                self.task.send_progress(
                    [f"Import {media.name}, {idx}/{len(files)}"],
                    progress=idx / len(files),
                )

            self._import_one(channel, media, seen)

        if not self.dry_run and channel.source == Source.ARCHIVE:
            self._channel_art(channel)

        return self.report

    def _channel(self) -> Channel:
        """the channel to import into, made if it is new (not dry run)"""
        if self.channel_id:
            channel = Channel.objects.filter(
                channel_id=self.channel_id
            ).first()
            if not channel:
                raise ArchiveImportError(
                    f"channel {self.channel_id} is not indexed"
                )

            return channel

        nfo = read_nfo(self.root / "tvshow.nfo")
        name = self.channel_name or nfo.get("title") or self.root.name
        channel_id = archive_channel_id(name)
        channel = Channel.objects.filter(channel_id=channel_id).first()
        if channel:
            return channel

        channel = Channel(
            channel_id=channel_id,
            source=Source.ARCHIVE,
            name=name,
            description=nfo.get("description") or None,
            tags=nfo.get("tags") or [],
            tabs=[VideoTypeEnum.VIDEOS.value],
            # nothing subscribes it, so ask for its own show
            emby_show=EmbyShow.ALWAYS,
        )
        if not self.dry_run:
            channel.save()

        return channel

    def _import_one(self, channel: Channel, media: Path, seen: set) -> None:
        """index one file, any problem is recorded and the run goes on"""
        video_id = archive_video_id(channel.channel_id, media.name)
        if video_id in seen:
            self.report.failed.append((media.name, "same name as another"))
            return

        seen.add(video_id)
        if Video.objects.filter(youtube_id=video_id).exists():
            self.report.existing.append(media.name)
            return

        try:
            fields = self._probe(media)
            if not self.dry_run:
                self._store(channel, media, video_id, fields)
        except (ArchiveImportError, OSError, ValueError) as err:
            self.report.failed.append((media.name, str(err)))
            return

        self.report.imported.append(media.name)

    def _probe(self, media: Path) -> dict:
        """what the file and its sidecars say about the video"""
        duration, streams = probe(media)
        if not any(s["type"] == "video" for s in streams):
            raise ArchiveImportError("no video stream")

        side = read_sidecar(media)
        mtime = datetime.fromtimestamp(media.stat().st_mtime, tz=timezone.utc)
        first = next(s for s in streams if s["type"] == "video")
        return {
            "title": side["title"] or media.stem,
            "description": side["description"] or None,
            "tags": side["tags"],
            "published": side["published"] or mtime,
            "duration": duration,
            "streams": streams,
            "media_size": media.stat().st_size,
            "width": first.get("width"),
            "height": first.get("height"),
        }

    def _store(
        self, channel: Channel, media: Path, video_id: str, fields: dict
    ) -> None:
        """place the file, make the thumbnail, add the video"""
        extension = media.suffix.lower()
        media_url = f"{channel.channel_id}/{video_id}{extension}"
        dest = Path(EnvironmentSettings.MEDIA_DIR) / media_url
        place_file(media, dest, self.mode)
        self._thumbnail(media, dest, video_id, fields["duration"])
        with transaction.atomic():
            Video.objects.create(
                youtube_id=video_id,
                channel=channel,
                source=Source.ARCHIVE,
                vid_type=VideoTypeEnum.VIDEOS.value,
                date_downloaded=datetime.now(timezone.utc),
                media_url=media_url,
                vid_thumb_url="",
                **fields,
            )

    @staticmethod
    def _thumbnail(media: Path, dest: Path, video_id: str, duration: int):
        """
        the sidecar image, else a frame of the video, else TA's default;
        fixed to 16:9 and put in the cache like any video thumbnail
        """
        manager = ThumbManager(video_id)
        sidecar = find_thumbnail(media)
        if sidecar:
            manager.fallback = str(sidecar)
            manager.download_video_thumb("")
            return

        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.jpg"
            if grab_frame(dest, duration, frame):
                manager.fallback = str(frame)

            manager.download_video_thumb("")

    def _channel_art(self, channel: Channel) -> None:
        """
        poster, banner and fanart from the folder (poster.jpg, banner.jpg,
        fanart.jpg and the like), else TA's default images; never replaces
        art that is already there
        """
        manager = ThumbManager(channel.channel_id)
        os.makedirs(manager.CHANNEL_DIR, exist_ok=True)
        for kind, item_type, names in CHANNEL_ART:
            path = os.path.join(
                manager.CHANNEL_DIR, f"{channel.channel_id}_{kind}.jpg"
            )
            if os.path.exists(path):
                continue

            found = [
                self.root / f"{name}{ext}"
                for name in names
                for ext in IMAGE_EXTENSIONS
                if (self.root / f"{name}{ext}").is_file()
            ]
            manager.item_type = item_type
            manager.fallback = str(found[0]) if found else False
            try:
                image: Image.Image = manager.download_raw("")
                image.convert("RGB").save(path)
            except OSError as err:
                self.report.failed.append((f"channel {kind}", str(err)))

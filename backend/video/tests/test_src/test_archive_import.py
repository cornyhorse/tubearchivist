"""tests for the archive import: ffprobe and ffmpeg mocked, files real"""

import errno
import json
import os
import subprocess

import pytest
from channel.models import Channel, EmbyShow, Source
from common.src.env_settings import EnvironmentSettings
from download.src.thumbnails import ThumbManagerBase
from PIL import Image
from video.models import Video
from video.src import archive_import as module
from video.src.archive import archive_channel_id, archive_video_id
from video.src.archive_import import (
    ArchiveImporter,
    ArchiveImportError,
    place_file,
)

pytestmark = pytest.mark.django_db

STREAMS = [
    {"bitrate": 1, "codec": "h264", "height": 720, "index": 0, "type": "video",
     "width": 1280},  # fmt: skip
    {"bitrate": 1, "codec": "aac", "index": 1, "type": "audio"},
]


def image(path, size=(64, 48), color="red"):
    """a small real image"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture(name="env")
def fixture_env(tmp_path, monkeypatch):
    """media and cache in tmp_path, probing and frame grabs replaced"""
    media = tmp_path / "media"
    cache = tmp_path / "cache"
    media.mkdir()
    cache.mkdir()
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(ThumbManagerBase, "CACHE_DIR", str(cache))
    monkeypatch.setattr(ThumbManagerBase, "VIDEO_DIR", str(cache / "videos"))
    monkeypatch.setattr(
        ThumbManagerBase, "CHANNEL_DIR", str(cache / "channels")
    )
    default = tmp_path / "app"
    for name in (
        "video-thumb",
        "channel-icon",
        "channel-banner",
        "channel-art",
    ):
        image(default / "static" / "img" / f"default-{name}.jpg", color="gray")
    monkeypatch.setattr(EnvironmentSettings, "APP_DIR", str(default))
    state = {"probe": {}, "frames": True}

    def fake_probe(path):
        value = state["probe"].get(path.name, 125)
        if isinstance(value, Exception):
            raise value
        if path.name in state.get("novideo", ()):
            return value, STREAMS[1:]
        return value, STREAMS

    def grab(media, seconds, out):
        state.setdefault("grabs", []).append((media.name, seconds))
        if state["frames"]:
            image(out, size=(160, 90), color="blue")
        return state["frames"]

    monkeypatch.setattr(module, "probe", fake_probe)
    monkeypatch.setattr(module, "grab_frame", grab)
    state["media"], state["cache"], state["tmp"] = media, cache, tmp_path
    return state


@pytest.fixture(name="folder")
def fixture_folder(env):
    """a folder with two videos, one with sidecars"""
    root = env["tmp"] / "NAS" / "Tyler Jenkins Music"
    root.mkdir(parents=True)
    (root / "Song One.mkv").write_bytes(b"one")
    (root / "Song Two.MP4").write_bytes(b"twotwo")
    image(root / "Song One.jpg", size=(320, 240))
    (root / "Song One.nfo").write_text(
        "<episodedetails><title>Song 1 (live)</title><plot>Notes</plot>"
        "<aired>2014-05-06</aired><tag>live</tag></episodedetails>"
    )
    return root


def dest_of(root, video_id, ext):
    """where the file of a video is placed"""
    channel = archive_channel_id(root.name)
    return EnvironmentSettings.MEDIA_DIR + f"/{channel}/{video_id}{ext}"


def test_import_creates_channel_and_videos(env, folder):
    report = ArchiveImporter(folder).run()

    assert report.message() == (
        "arch_tyler-jenkins-music: 2 imported, 0 already indexed, 0 failed"
    )
    channel = Channel.objects.get()
    assert channel.channel_id == "arch_tyler-jenkins-music"
    assert channel.name == "Tyler Jenkins Music"
    assert channel.source == Source.ARCHIVE and not channel.subscribed
    assert channel.emby_show == EmbyShow.ALWAYS
    assert channel.tabs == ["videos"]

    one = Video.objects.get(title="Song 1 (live)")
    assert one.source == Source.ARCHIVE
    assert one.description == "Notes" and one.tags == ["live"]
    assert one.published.date().isoformat() == "2014-05-06"
    assert one.duration == 125 and one.media_size == 3
    assert (one.width, one.height) == (1280, 720)
    assert one.vid_thumb_url == "" and one.streams == STREAMS
    two = Video.objects.get(title="Song Two")
    assert two.media_url.endswith(f"{two.youtube_id}.mp4")
    assert two.published is not None
    assert one.to_doc()["source"] == "archive"
    assert Video.objects.exclude(source=Source.ARCHIVE).count() == 0


def test_files_are_hardlinked_and_sources_untouched(env, folder):
    ArchiveImporter(folder).run()

    video = Video.objects.get(title="Song 1 (live)")
    dest = EnvironmentSettings.MEDIA_DIR + "/" + video.media_url
    assert os.path.samestat(os.stat(dest), os.stat(folder / "Song One.mkv"))
    assert (folder / "Song One.mkv").read_bytes() == b"one"
    assert (folder / "Song One.nfo").exists()


def test_thumbnails_sidecar_then_frame(env, folder):
    ArchiveImporter(folder).run()

    one = Video.objects.get(title="Song 1 (live)")
    two = Video.objects.get(title="Song Two")
    cache = env["cache"] / "videos"
    from_sidecar = Image.open(cache / "a" / f"{one.youtube_id}.jpg")
    assert from_sidecar.getpixel((5, 5)) == pytest.approx((255, 0, 0), abs=8)
    from_frame = Image.open(cache / "a" / f"{two.youtube_id}.jpg")
    assert from_frame.getpixel((5, 5)) == pytest.approx((0, 0, 255), abs=8)
    # 10% of 125 seconds
    assert env["grabs"] == [(f"{two.youtube_id}.mp4", 125)]


def test_thumbnail_default_when_no_frame(env, folder):
    env["frames"] = False

    ArchiveImporter(folder).run()

    two = Video.objects.get(title="Song Two")
    thumb = Image.open(env["cache"] / "videos" / "a" / f"{two.youtube_id}.jpg")
    assert thumb.getpixel((5, 5)) == pytest.approx((128, 128, 128), abs=8)


def test_channel_art_from_folder_else_default(env, folder):
    image(folder / "poster.png", color="green")

    ArchiveImporter(folder).run()

    channels = env["cache"] / "channels"
    poster = Image.open(channels / "arch_tyler-jenkins-music_thumb.jpg")
    assert poster.getpixel((5, 5)) == pytest.approx((0, 128, 0), abs=8)
    banner = Image.open(channels / "arch_tyler-jenkins-music_banner.jpg")
    assert banner.getpixel((5, 5)) == pytest.approx((128, 128, 128), abs=8)
    assert (channels / "arch_tyler-jenkins-music_tvart.jpg").exists()


def test_channel_art_not_replaced(env, folder):
    channels = env["cache"] / "channels"
    channels.mkdir(parents=True)
    (channels / "arch_tyler-jenkins-music_thumb.jpg").write_bytes(b"mine")

    ArchiveImporter(folder).run()

    assert (channels / "arch_tyler-jenkins-music_thumb.jpg").read_bytes() == (
        b"mine"
    )


def test_channel_art_failure_is_reported_and_retried(env, folder, monkeypatch):
    monkeypatch.setattr(EnvironmentSettings, "APP_DIR", "/nonexistent")

    report = ArchiveImporter(folder).run()

    assert len(report.imported) == 2
    assert [name for name, _ in report.failed] == [
        "channel thumb",
        "channel banner",
        "channel tvart",
    ]
    monkeypatch.setattr(
        EnvironmentSettings, "APP_DIR", str(env["tmp"] / "app")
    )

    assert not ArchiveImporter(folder).run().failed
    assert (
        env["cache"] / "channels" / "arch_tyler-jenkins-music_thumb.jpg"
    ).exists()


def test_existing_youtube_channel_keeps_its_art(env, folder):
    Channel.objects.create(channel_id="UCreal", name="Real")

    ArchiveImporter(folder, channel_id="UCreal").run()

    assert not (env["cache"] / "channels").exists()


def test_rerun_adds_only_new_files(env, folder):
    ArchiveImporter(folder).run()
    (folder / "Song Three.webm").write_bytes(b"three")
    Video.objects.filter(title="Song Two").update(title="Renamed by hand")

    report = ArchiveImporter(folder).run()

    assert report.imported == ["Song Three.webm"]
    assert sorted(report.existing) == ["Song One.mkv", "Song Two.MP4"]
    assert Video.objects.count() == 3
    assert Video.objects.filter(title="Renamed by hand").exists()
    assert Channel.objects.count() == 1


def test_nothing_new_changes_nothing(env, folder):
    ArchiveImporter(folder).run()
    ArchiveImporter(folder).run()
    report = ArchiveImporter(folder).run()
    assert not report.imported and len(report.existing) == 2


def test_dry_run_writes_nothing(env, folder):
    report = ArchiveImporter(folder, dry_run=True).run()

    assert len(report.imported) == 2
    assert not Channel.objects.exists() and not Video.objects.exists()
    assert not os.listdir(EnvironmentSettings.MEDIA_DIR)
    assert not (env["cache"] / "videos").exists()


def test_dry_run_with_existing_channel(env, folder):
    ArchiveImporter(folder).run()
    (folder / "Song Three.webm").write_bytes(b"three")

    report = ArchiveImporter(folder, dry_run=True).run()

    assert report.imported == ["Song Three.webm"]
    assert Video.objects.count() == 2


def test_modes(env, folder):
    ArchiveImporter(folder, mode="copy").run()
    video = Video.objects.get(title="Song 1 (live)")
    dest = EnvironmentSettings.MEDIA_DIR + "/" + video.media_url
    assert not os.path.samestat(
        os.stat(dest), os.stat(folder / "Song One.mkv")
    )
    assert open(dest, "rb").read() == b"one"
    assert not [p for p in os.listdir(os.path.dirname(dest)) if "part" in p]

    Video.objects.all().delete()
    Channel.objects.all().delete()
    os.remove(dest)
    ArchiveImporter(folder, mode="hardlink").run()
    assert os.path.samestat(os.stat(dest), os.stat(folder / "Song One.mkv"))


def test_move_empties_the_source(env, folder):
    ArchiveImporter(folder, mode="move").run()

    assert not (folder / "Song One.mkv").exists()
    video = Video.objects.get(title="Song 1 (live)")
    assert (
        open(
            EnvironmentSettings.MEDIA_DIR + "/" + video.media_url, "rb"
        ).read()
        == b"one"
    )
    # the sidecars stay
    assert (folder / "Song One.nfo").exists()


def test_auto_copies_across_filesystems(env, folder, monkeypatch):
    def cross(source, dest):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(module.os, "link", cross)

    report = ArchiveImporter(folder).run()

    assert len(report.imported) == 2
    video = Video.objects.get(title="Song 1 (live)")
    dest = EnvironmentSettings.MEDIA_DIR + "/" + video.media_url
    assert open(dest, "rb").read() == b"one"


@pytest.mark.parametrize(
    "mode, code", [("hardlink", errno.EXDEV), ("auto", errno.EPERM)]
)
def test_hardlink_failure_is_reported(env, folder, monkeypatch, mode, code):
    def refuse(source, dest):
        raise OSError(code, "no")

    monkeypatch.setattr(module.os, "link", refuse)

    report = ArchiveImporter(folder, mode=mode).run()

    assert not report.imported and len(report.failed) == 2
    assert "can't hardlink" in report.failed[0][1]
    assert not Video.objects.exists()


def test_place_file_keeps_an_earlier_attempt(tmp_path):
    source = tmp_path / "in.mkv"
    source.write_bytes(b"abc")
    dest = tmp_path / "out" / "x.mkv"
    assert place_file(source, dest, "copy") == "copy"
    assert place_file(source, dest, "copy") == "existing"
    assert place_file(source, dest, "move") == "existing"
    assert source.exists()

    dest.write_bytes(b"different size")
    with pytest.raises(ArchiveImportError, match="another size"):
        place_file(source, dest, "copy")


def test_place_file_sets_owner(tmp_path, monkeypatch):
    source = tmp_path / "in.mkv"
    source.write_bytes(b"abc")
    owned = []
    monkeypatch.setattr(EnvironmentSettings, "HOST_UID", 1000)
    monkeypatch.setattr(EnvironmentSettings, "HOST_GID", 1001)
    monkeypatch.setattr(module.os, "chown", lambda *a: owned.append(a))

    place_file(source, tmp_path / "x.mkv", "copy")

    assert owned == [(tmp_path / "x.mkv", 1000, 1001)]


def test_leftover_file_without_a_row_is_reused(env, folder):
    channel_id = archive_channel_id(folder.name)
    video_id = archive_video_id(channel_id, "Song One.mkv")
    leftover = env["media"] / channel_id / f"{video_id}.mkv"
    leftover.parent.mkdir()
    leftover.write_bytes(b"one")

    report = ArchiveImporter(folder).run()

    assert not report.failed
    assert Video.objects.filter(youtube_id=video_id).exists()


def test_problem_files_are_reported_and_skipped(env, folder):
    (folder / "broken.mp4").write_bytes(b"x")
    (folder / "audio.mp4").write_bytes(b"x")
    (folder / "weird.mkv").write_bytes(b"x")
    (folder / "sub").mkdir()
    (folder / "sub" / "Song One.mkv").write_bytes(b"dup")
    env["probe"]["broken.mp4"] = ArchiveImportError("ffprobe failed: bad")
    env["probe"]["weird.mkv"] = ValueError("bad duration")
    env["novideo"] = ["audio.mp4"]

    report = ArchiveImporter(folder).run()

    failed = dict(report.failed)
    assert failed["broken.mp4"] == "ffprobe failed: bad"
    assert failed["audio.mp4"] == "no video stream"
    assert failed["weird.mkv"] == "bad duration"
    assert failed["Song One.mkv"] == "same name as another"
    assert len(report.imported) == 2
    assert Video.objects.count() == 2


def test_channel_name_from_nfo_then_folder(env, folder):
    (folder / "tvshow.nfo").write_text(
        "<tvshow><title>Tyler's Music</title><plot>All of it</plot>"
        "<tag>music</tag></tvshow>"
    )

    ArchiveImporter(folder).run()

    channel = Channel.objects.get()
    assert channel.channel_id == "arch_tylers-music"
    assert channel.description == "All of it" and channel.tags == ["music"]


def test_channel_name_option_wins(env, folder):
    ArchiveImporter(folder, channel_name="Key to the Geyt").run()
    assert Channel.objects.get().channel_id == "arch_key-to-the-geyt"


def test_into_an_indexed_channel(env, folder):
    Channel.objects.create(channel_id="UCreal", name="Real", subscribed=True)

    report = ArchiveImporter(folder, channel_id="UCreal").run()

    assert report.channel_id == "UCreal" and len(report.imported) == 2
    video = Video.objects.get(title="Song 1 (live)")
    assert video.channel_id == "UCreal" and video.source == Source.ARCHIVE
    assert video.media_url.startswith("UCreal/")
    assert Channel.objects.get().source == Source.YOUTUBE


def test_unknown_channel_id(env, folder):
    with pytest.raises(ArchiveImportError, match="not indexed"):
        ArchiveImporter(folder, channel_id="UCnone").run()


def test_bad_arguments(env, tmp_path):
    with pytest.raises(ArchiveImportError, match="mode"):
        ArchiveImporter(tmp_path, mode="symlink").run()
    with pytest.raises(ArchiveImportError, match="not a folder"):
        ArchiveImporter(tmp_path / "missing").run()


class FakeTask:
    """job context that is stopped after the first file"""

    def __init__(self):
        self.lines = []

    def is_stopped(self):
        """stop once something was reported"""
        return bool(self.lines)

    def send_progress(self, message_lines, progress=False):
        """record"""
        self.lines.append((message_lines, progress))


def test_stop_request_ends_the_run(env, folder):
    task = FakeTask()

    report = ArchiveImporter(folder, task=task).run()

    assert report.stopped and len(report.imported) == 1
    assert report.message().endswith(", stopped early")
    assert task.lines[0][0] == ["Import Song One.mkv, 1/2"]
    assert task.lines[0][1] == 0.5
    # a channel with some files still gets its art
    assert (env["cache"] / "channels").exists()


def test_grab_frame(tmp_path, monkeypatch):
    out = tmp_path / "f.jpg"
    calls = []

    def run(cmd, capture_output, check):
        calls.append(cmd)
        out.write_bytes(b"jpg")
        return subprocess.CompletedProcess(cmd, calls_rc[0])

    calls_rc = [0]
    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.grab_frame(tmp_path / "v.mkv", 100, out)
    assert calls[0][calls[0].index("-ss") + 1] == "10"
    assert module.grab_frame(tmp_path / "v.mkv", 1, out)
    assert calls[1][calls[1].index("-ss") + 1] == "0"

    calls_rc[0] = 1
    assert not module.grab_frame(tmp_path / "v.mkv", 100, out)


FFPROBE = {
    "format": {"duration": "123.7"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "mpeg4",
         "width": 320, "height": 180},
        {"index": 1, "codec_type": "audio", "codec_name": "mp3",
         "bit_rate": "128000"},
        {"index": 2, "codec_type": "video", "codec_name": "mjpeg",
         "width": 9, "height": 9, "disposition": {"attached_pic": 1}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "ass"},
    ],
}  # fmt: skip


def ffprobe_says(monkeypatch, data, code=0, err=""):
    """replace the ffprobe call"""

    def run(cmd, capture_output, text, check):
        assert cmd[0] == "ffprobe" and not check
        return subprocess.CompletedProcess(cmd, code, json.dumps(data), err)

    monkeypatch.setattr(module.subprocess, "run", run)


def test_probe_matroska_video_without_a_stream_bit_rate(tmp_path, monkeypatch):
    ffprobe_says(monkeypatch, FFPROBE)

    duration, streams = module.probe(tmp_path / "a.mkv")

    assert duration == 123
    assert streams == [
        {"bitrate": 0, "codec": "mpeg4", "index": 0, "type": "video",
         "height": 180, "width": 320},
        {"bitrate": 128000, "codec": "mp3", "index": 1, "type": "audio"},
    ]  # fmt: skip


def test_probe_without_duration(tmp_path, monkeypatch):
    ffprobe_says(monkeypatch, {"format": {"duration": "N/A"}, "streams": []})
    assert module.probe(tmp_path / "a.webm") == (0, [])

    ffprobe_says(monkeypatch, {})
    assert module.probe(tmp_path / "a.webm") == (0, [])


def test_probe_failure(tmp_path, monkeypatch):
    ffprobe_says(monkeypatch, {}, code=1, err="Invalid data\n")
    with pytest.raises(ArchiveImportError, match="ffprobe failed: Invalid"):
        module.probe(tmp_path / "a.mkv")

"""tests for thumbnail download, embedding and clean up"""

import io
import os

import pytest
import requests
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download
from download.src import thumbnails
from download.src.thumbnails import (
    ThumbFilesystem,
    ThumbManager,
    ThumbValidator,
    _progress,
)
from download.tests.helpers import FakeTask
from PIL import Image
from playlist.models import Playlist
from video.models import Video

CHANNEL_ID = channel_doc()["channel_id"]


def jpeg_bytes(size=(64, 36), color="red") -> bytes:
    """encoded jpeg"""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeResponse:
    """minimal requests response"""

    def __init__(self, status_code=200, content=b""):
        self.status_code = status_code
        self.ok = status_code < 400
        self.raw = io.BytesIO(content)


@pytest.fixture(name="http")
def fixture_http(monkeypatch):
    """replace requests.get, responses are popped from http.responses"""

    class Http:
        """recorded requests and queued responses"""

        def __init__(self):
            self.urls = []
            self.responses = []
            self.default = FakeResponse(content=jpeg_bytes())

        def get(self, url, **kwargs):  # pylint: disable=unused-argument
            """fake requests.get"""
            self.urls.append(url)
            response = (
                self.responses.pop(0) if self.responses else self.default
            )
            if isinstance(response, Exception):
                raise response
            return response

    http = Http()
    monkeypatch.setattr(thumbnails.requests, "get", http.get)
    monkeypatch.setattr(thumbnails, "sleep", lambda secs: None)
    return http


def test_download_raw_ok(http):
    """image from the response"""
    img = ThumbManager("abc").download_raw("https://img/ok.jpg")
    assert img.size == (64, 36)
    assert http.urls == ["https://img/ok.jpg"]


def test_download_raw_no_url_uses_default(app_dir):
    """no url, default artwork of the item type"""
    img = ThumbManager("abc", item_type="playlist").download_raw(None)
    assert img.filename == str(
        app_dir / "static/img/default-playlist-thumb.jpg"
    )


def test_download_raw_custom_fallback(tmp_path, http):
    """fallback file passed in is used on 404"""
    fallback = tmp_path / "fallback.jpg"
    Image.new("RGB", (10, 10)).save(fallback)
    http.responses = [FakeResponse(404)]

    img = ThumbManager("abc", fallback=str(fallback)).download_raw("https://x")
    assert img.filename == str(fallback)


def test_download_raw_broken_image(app_dir, http):
    """unreadable image, fallback"""
    http.responses = [FakeResponse(content=b"not an image")]
    img = ThumbManager("abc").download_raw("https://x")
    assert img.filename.endswith("default-video-thumb.jpg")


def test_download_raw_not_an_image_object(app_dir, http, monkeypatch):
    """Image.open returning something else, fallback"""
    real_open = Image.open
    calls = []

    def fake_open(source, *args, **kwargs):
        calls.append(source)
        if len(calls) == 1:
            return object()
        return real_open(source, *args, **kwargs)

    monkeypatch.setattr(thumbnails.Image, "open", fake_open)
    img = ThumbManager("abc").download_raw("https://x")
    assert img.filename.endswith("default-video-thumb.jpg")


def test_download_raw_retries(app_dir, http):
    """server errors and request errors retry three times"""
    http.responses = [
        FakeResponse(500),
        requests.exceptions.ConnectionError("down"),
        FakeResponse(503),
    ]
    img = ThumbManager("abc").download_raw("https://x")
    assert len(http.urls) == 3
    assert img.filename.endswith("default-video-thumb.jpg")


@pytest.mark.parametrize("size", [(64, 36), (64, 64)])
def test_download_video_thumb(cache_dir, http, size):
    """saved as 16:9 jpeg in the folder of the first letter"""
    http.default = FakeResponse(content=jpeg_bytes(size))
    ThumbManager("Abc").download_video_thumb("https://x")

    thumb = cache_dir / "videos" / "a" / "Abc.jpg"
    width, height = Image.open(thumb).size
    assert width == 64
    assert abs(height - 36) <= 1


def test_download_video_thumb_skip_existing(cache_dir, http):
    """existing thumb is not downloaded again"""
    handler = ThumbManager("abc")
    handler.vid_thumb_path(absolute=True, create_folder=True)
    with open(handler.vid_thumb_path(absolute=True), "wb") as f:
        f.write(b"old")

    handler.download_video_thumb("https://x", skip_existing=True)
    assert not http.urls


def test_vid_thumb_path(cache_dir):
    """relative and absolute, creates folder on request"""
    handler = ThumbManager("Xyz")
    assert handler.vid_thumb_path() == "videos/x/Xyz.jpg"
    assert handler.vid_thumb_path(absolute=True, create_folder=True) == str(
        cache_dir / "videos/x/Xyz.jpg"
    )
    assert (cache_dir / "videos" / "x").is_dir()


def test_download_dispatch(cache_dir, http):
    """download and delete by item type"""
    ThumbManager("vid1").download("https://v")
    ThumbManager("UCchan", item_type="channel").download(
        ("https://t", "https://b", "https://tv")
    )
    ThumbManager("PLlist", item_type="playlist").download("https://p")
    ThumbManager("other", item_type="other").download("https://o")

    assert (cache_dir / "videos/v/vid1.jpg").exists()
    channel_files = sorted(os.listdir(cache_dir / "channels"))
    assert channel_files == [
        "UCchan_banner.jpg",
        "UCchan_thumb.jpg",
        "UCchan_tvart.jpg",
    ]
    assert Image.open(cache_dir / "playlists/PLlist.jpg").size == (336, 189)
    assert len(http.urls) == 5

    ThumbManager("vid1").delete()
    ThumbManager("UCchan", item_type="channel").delete()
    ThumbManager("PLlist", item_type="playlist").delete()
    ThumbManager("other", item_type="other").delete()
    assert not os.listdir(cache_dir / "videos/v")
    assert not os.listdir(cache_dir / "channels")
    assert not os.listdir(cache_dir / "playlists")

    # nothing left to delete
    ThumbManager("vid1").delete()
    ThumbManager("UCchan", item_type="channel").delete()
    ThumbManager("PLlist", item_type="playlist").delete()


def test_channel_art_skip_existing(cache_dir, http):
    """all three channel images exist, nothing downloaded"""
    for suffix in ("thumb", "banner", "tvart"):
        (cache_dir / "channels" / f"UCchan_{suffix}.jpg").write_bytes(b"x")

    ThumbManager("UCchan", item_type="channel").download_channel_art(
        ("https://t", "https://b", "https://tv"), skip_existing=True
    )
    assert not http.urls


def test_playlist_thumb_from_cache_and_skip(cache_dir, http):
    """custom playlist thumb is a path in the cache, already 16:9"""
    Image.new("RGB", (160, 90)).save(cache_dir / "videos" / "custom.jpg")
    handler = ThumbManager("PLcustom", item_type="playlist")

    handler.download_playlist_thumb("videos/custom.jpg")
    assert Image.open(cache_dir / "playlists/PLcustom.jpg").size == (336, 189)

    handler.download_playlist_thumb("https://p", skip_existing=True)
    assert not http.urls


class FakeMP4(dict):
    """mutagen MP4 stand in, records saves"""

    instances: list = []

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.saved = False
        FakeMP4.instances.append(self)

    def save(self):
        """record save"""
        self.saved = True


@pytest.fixture(name="fake_mp4")
def fixture_fake_mp4(monkeypatch):
    """replace mutagen MP4"""
    FakeMP4.instances = []
    monkeypatch.setattr(thumbnails, "MP4", FakeMP4)
    return FakeMP4


def test_embed_video_art_missing_file(cache_dir, fake_mp4):
    """no media file, nothing to embed"""
    ThumbManager("abc").embed_video_art(
        {"media_url": "UC/abc.mp4", "channel": {"channel_id": "UC"}}
    )
    assert not fake_mp4.instances


def test_embed_video_art(cache_dir, fake_mp4):
    """thumb, channel art and playlist art that exist are embedded"""
    media = cache_dir.parent / "media"
    (media / "UC").mkdir()
    (media / "UC" / "abc.mp4").write_bytes(b"mp4")
    handler = ThumbManager("abc")
    handler.vid_thumb_path(create_folder=True)
    with open(handler.vid_thumb_path(absolute=True), "wb") as f:
        f.write(b"thumb")
    (cache_dir / "channels" / "UC_banner.jpg").write_bytes(b"banner")
    (cache_dir / "playlists" / "PL1.jpg").write_bytes(b"pl1")

    handler.embed_video_art(
        {
            "media_url": "UC/abc.mp4",
            "channel": {"channel_id": "UC"},
            "playlist": ["PL1", "PL2"],
        }
    )

    video = fake_mp4.instances[0]
    assert video.saved
    assert bytes(video["covr"][0]) == b"thumb"
    assert sorted(video.keys()) == [
        "----:com.tubearchivist:channel_banner",
        "----:com.tubearchivist:playlist_PL1",
        "covr",
    ]


def test_embed_video_art_no_thumb(cache_dir, fake_mp4):
    """no cached thumb and no art, saved without covers"""
    media = cache_dir.parent / "media"
    (media / "UC").mkdir()
    (media / "UC" / "abc.mp4").write_bytes(b"mp4")

    ThumbManager("abc").embed_video_art(
        {"media_url": "UC/abc.mp4", "channel": {"channel_id": "UC"}}
    )
    assert fake_mp4.instances[0].saved
    assert not fake_mp4.instances[0]


def test_progress():
    """every 100 items and at the end"""
    task = FakeTask()
    _progress(None, "Videos", 100, 200)
    for idx in (1, 99, 100, 150, 200):
        _progress(task, "Videos", idx, 200)
    assert task.lines == [
        "Processing Videos 100/200",
        "Processing Videos 200/200",
    ]


@pytest.mark.django_db
def test_validate(cache_dir, http):
    """missing artwork of videos, queue, channels and playlists"""
    Channel.save_doc(channel_doc(channel_id="UCabc"))
    Channel.save_doc(channel_doc(channel_id="UCold", channel_active=False))
    Video.save_doc(video_doc(channel=channel_doc(channel_id="UCabc")))
    Download.upsert_doc(download_doc(youtube_id="dl1"))
    Download.upsert_doc(download_doc(youtube_id="dl2", status="ignore"))
    Playlist.save_doc(playlist_doc("PL1", [], playlist_thumbnail="https://pl"))
    task = FakeTask()

    ThumbValidator(task=task).validate()

    assert (cache_dir / "videos/v/vid00000001.jpg").exists()
    assert (cache_dir / "videos/d/dl1.jpg").exists()
    assert not (cache_dir / "videos/d/dl2.jpg").exists()
    assert sorted(os.listdir(cache_dir / "channels")) == [
        "UCabc_banner.jpg",
        "UCabc_thumb.jpg",
        "UCabc_tvart.jpg",
    ]
    assert (cache_dir / "playlists/PL1.jpg").exists()
    assert task.lines == [
        "Processing Videos 2/2",
        "Processing Channels 1/1",
        "Processing Playlists 1/1",
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("with_task", [True, False])
def test_clean_up(cache_dir, with_task):
    """thumbs without video, queue item, channel or playlist are deleted"""
    channel_id = "UCchannel000000000000001"
    Channel.save_doc(channel_doc(channel_id=channel_id))
    Video.save_doc(video_doc(youtube_id="vKeep"))
    Download.upsert_doc(download_doc(youtube_id="vQueued"))
    Playlist.save_doc(playlist_doc("PLkeep", []))

    videos = cache_dir / "videos"
    (videos / "v").mkdir()
    (videos / "x").mkdir()
    for name in ("vKeep", "vQueued", "vGone"):
        (videos / "v" / f"{name}.jpg").write_bytes(b"x")
    channels = cache_dir / "channels"
    gone_channel = "UCgone000000000000000001"
    for cid in (channel_id, gone_channel):
        (channels / f"{cid}_thumb.jpg").write_bytes(b"x")
    playlists = cache_dir / "playlists"
    for pid in ("PLkeep", "PLgone"):
        (playlists / f"{pid}.jpg").write_bytes(b"x")
    task = FakeTask() if with_task else False

    ThumbValidator(task=task).clean_up()

    assert sorted(os.listdir(videos / "v")) == ["vKeep.jpg", "vQueued.jpg"]
    assert os.listdir(channels) == [f"{channel_id}_thumb.jpg"]
    assert os.listdir(playlists) == ["PLkeep.jpg"]
    if with_task:
        assert len(task.lines) == 3


@pytest.mark.django_db
def test_clean_up_nothing_to_delete(cache_dir):
    """empty cache folders"""
    ThumbValidator().clean_up()
    assert not os.listdir(cache_dir / "videos")


@pytest.mark.django_db
def test_embed_all(cache_dir, fake_mp4):
    """every indexed video, missing media files are skipped"""
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="vid1"))
    Video.save_doc(video_doc(youtube_id="vid2"))
    media = cache_dir.parent / "media" / CHANNEL_ID
    media.mkdir()
    (media / "vid1.mp4").write_bytes(b"mp4")
    task = FakeTask()

    ThumbFilesystem(task=task).embed()

    assert [i.file_path for i in fake_mp4.instances] == [
        str(media / "vid1.mp4")
    ]
    assert task.lines == ["Processing Videos 2/2"]


def test_playlist_thumb_cropped(cache_dir, http):
    """square playlist thumb is cropped to 16:9 and resized"""
    http.default = FakeResponse(content=jpeg_bytes((90, 90)))
    ThumbManager("PLsq", item_type="playlist").download_playlist_thumb(
        "https://p"
    )
    assert Image.open(cache_dir / "playlists/PLsq.jpg").size == (336, 189)

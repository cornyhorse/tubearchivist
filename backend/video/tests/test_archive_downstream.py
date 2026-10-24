"""archived videos through the rest of the app: API, files, thumbnails, Emby"""

import os
import xml.etree.ElementTree as ET

import pytest
from channel.models import Channel, EmbyShow, Source
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc, video_doc
from download.src import thumbnails as thumb_module
from download.src.thumbnails import ThumbValidator
from emby.src.sync import EmbyPaths, sync_all
from rest_framework.test import APIClient
from video.models import Video
from video.src import meta_embed
from video.src.index import YoutubeVideo
from video.src.meta_embed import MetadataEmbed

pytestmark = pytest.mark.django_db

ARCH = "arch_0123456789abcdef"
CHANNEL = "arch_music"


@pytest.fixture(name="client")
def fixture_client(django_user_model, app_config):
    """api client authenticated as admin"""
    del app_config
    user = django_user_model.objects.create_superuser(
        name="admin", password="test-password"
    )
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user=user)
    return client


@pytest.fixture(name="dirs")
def fixture_dirs(tmp_path, monkeypatch):
    """media and cache in tmp_path"""
    media = tmp_path / "media"
    cache = tmp_path / "cache"
    media.mkdir()
    cache.mkdir()
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    return media, cache


@pytest.fixture(name="archived")
def fixture_archived(dirs):
    """archived channel and video, the file hardlinked to an original"""
    media, cache = dirs
    original = media.parent / "original.mkv"
    original.write_bytes(b"the original")
    placed = media / CHANNEL / f"{ARCH}.mkv"
    placed.parent.mkdir()
    os.link(original, placed)
    thumb = cache / "videos" / "a" / f"{ARCH}.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"jpg")
    channel = Channel.objects.create(
        channel_id=CHANNEL,
        name="Music",
        source=Source.ARCHIVE,
        emby_show=EmbyShow.ALWAYS,
    )
    Video.objects.create(
        **{
            **Video.fields_from_doc(
                video_doc(
                    youtube_id=ARCH, title="A Song", published=1700000000
                )
            ),
            "channel": channel,
            "source": Source.ARCHIVE,
            "media_url": f"{CHANNEL}/{ARCH}.mkv",
            "vid_thumb_url": "",
        }
    )
    return original, placed


def test_source_in_the_documents(archived):
    del archived

    assert Video.load_doc(ARCH)["source"] == "archive"
    assert Channel.load_doc(CHANNEL)["source"] == "archive"
    # youtube documents keep the legacy shape
    Channel.save_doc(channel_doc())
    assert "source" not in Channel.load_doc(channel_doc()["channel_id"])
    # and a document with a source round trips
    Video.save_doc(Video.load_doc(ARCH))
    assert Video.objects.get(pk=ARCH).source == Source.ARCHIVE
    Channel.save_doc(Channel.load_doc(CHANNEL))
    assert Channel.objects.get(pk=CHANNEL).source == Source.ARCHIVE


def test_video_api_shows_source(client, archived):
    del archived

    detail = client.get(f"/api/video/{ARCH}/").json()
    assert detail["source"] == "archive"
    assert detail["channel"]["source"] == "archive"
    assert detail["media_url"].endswith(f"{CHANNEL}/{ARCH}.mkv")
    listing = client.get("/api/video/").json()["data"]
    assert [v["youtube_id"] for v in listing] == [ARCH]
    channel = client.get(f"/api/channel/{CHANNEL}/").json()
    assert channel["source"] == "archive"


def test_search_finds_archived_items(client, archived):
    del archived

    videos = client.get("/api/search/", {"query": "song"}).json()
    channels = client.get("/api/search/", {"query": "music"}).json()

    assert [v["youtube_id"] for v in videos["results"]["video_results"]] == [
        ARCH
    ]
    assert [
        c["channel_id"] for c in channels["results"]["channel_results"]
    ] == [CHANNEL]


def test_delete_removes_our_copy_only(client, archived):
    original, placed = archived

    response = client.delete(f"/api/video/{ARCH}/")

    assert response.status_code == 204
    assert not placed.exists() and not Video.objects.exists()
    assert original.read_bytes() == b"the original"


def test_embed_never_writes_into_archived_files(
    archived, app_config, monkeypatch
):
    del archived
    video = YoutubeVideo(ARCH)
    app_config["downloads"]["add_metadata"] = True
    video.config = app_config

    def never(*args):
        raise AssertionError("wrote into an archived file")

    monkeypatch.setattr(video, "_embed_text_data", never)
    monkeypatch.setattr(video, "_embed_artwork", never)

    video.embed_metadata()


def test_bulk_embed_skips_archived(archived, monkeypatch):
    del archived
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="yt000000001"))
    embedded = []

    class Recorder:
        """record the ids"""

        def __init__(self, youtube_id):
            self.youtube_id = youtube_id

        def embed_metadata(self):
            """record"""
            embedded.append(self.youtube_id)

    monkeypatch.setattr(meta_embed, "YoutubeVideo", Recorder)

    MetadataEmbed().embed()

    assert embedded == ["yt000000001"]


def test_thumbnail_check_does_not_fetch_for_archived(
    archived, dirs, monkeypatch
):
    del archived, dirs
    requested = []

    class Recorder:
        """record what the validator asks to download"""

        def __init__(self, item_id, *args, **kwargs):
            requested.append(item_id)

        def download_video_thumb(self, url, skip_existing=False):
            """no-op"""

        def download_channel_art(self, urls, skip_existing=False):
            """no-op"""

    monkeypatch.setattr(thumb_module, "ThumbManager", Recorder)
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="yt000000001"))

    ThumbValidator().validate()

    assert sorted(requested) == [channel_doc()["channel_id"], "yt000000001"]


def test_thumbnail_cleanup_handles_archive_ids(archived, dirs):
    del archived
    _, cache = dirs
    channels = cache / "channels"
    channels.mkdir()
    for name in ("arch_music_thumb.jpg", "arch_gone_thumb.jpg"):
        (channels / name).write_bytes(b"jpg")
    (cache / "playlists").mkdir()

    ThumbValidator()._clean_up_channels()  # pylint: disable=protected-access

    assert os.listdir(channels) == ["arch_music_thumb.jpg"]
    assert (cache / "videos" / "a" / f"{ARCH}.jpg").exists()
    ThumbValidator()._clean_up_vids()  # pylint: disable=protected-access
    assert (cache / "videos" / "a" / f"{ARCH}.jpg").exists()


def test_emby_writes_archived_channel(archived, dirs, tmp_path):
    del archived
    media, cache = dirs
    paths = EmbyPaths(media, cache, tmp_path / "media" / ".emby")

    sync_all(paths)

    root = paths.output_root / "channels" / f"Music [{CHANNEL}]"
    show = ET.parse(root / "tvshow.nfo").getroot()
    assert show.find("uniqueid").attrib["type"] == "archive"
    episode = next(root.rglob("*.nfo"))
    if episode.name == "tvshow.nfo":
        episode = next(p for p in root.rglob("*.nfo") if p != episode)
    nfo = ET.parse(episode).getroot()
    assert nfo.findtext("title") == "A Song"
    assert nfo.find("uniqueid").attrib["type"] == "archive"
    assert nfo.findtext("season") == "2023"
    files = sorted(p.suffix for p in root.rglob("*") if p.is_file())
    assert ".mkv" in files and files.count(".nfo") == 2
    assert any(p.name.endswith("-thumb.jpg") for p in root.rglob("*"))
    # the player and Emby read the placed copy, linked into the Emby tree
    linked = next(root.rglob("*.mkv"))
    assert os.path.samestat(
        os.stat(linked), os.stat(media / CHANNEL / f"{ARCH}.mkv")
    )

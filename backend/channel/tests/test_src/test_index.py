"""tests for channel indexing, yt-dlp and artwork faked"""

import pytest
from channel.models import Channel
from channel.src.index import ChannelDelete, YoutubeChannel, channel_overwrites
from common.tests.docs import channel_doc, playlist_doc, video_doc
from playlist.models import Playlist, PlaylistEntry
from video.models import Video

pytestmark = pytest.mark.usefixtures("app_config")

CH = "UCchannel000000000000001"
CHANNEL_URL = f"https://www.youtube.com/channel/{CH}"
PLAYLISTS_URL = f"{CHANNEL_URL}/playlists?view=1&sort=dd&shelf_id=0"

THUMBNAILS = [
    {"url": "https://yt3.example/banner", "width": 2560, "height": 424},
    {"url": "https://yt3.example/tvart", "width": 1280, "height": 720},
    {"url": "https://yt3.example/thumb", "width": 900, "height": 900},
    {"url": "https://yt3.example/uncropped", "id": "banner_uncropped"},
    {"url": "https://yt3.example/avatar_uncropped", "id": "avatar"},
]


def channel_meta(**overrides) -> dict:
    """yt-dlp response for a channel"""
    meta = {
        "id": CH,
        "uploader": "Test Channel",
        "description": "about the channel",
        "channel_follower_count": 1200,
        "tags": ["trains"],
        "thumbnails": THUMBNAILS,
    }
    meta.update(overrides)
    return meta


def set_tabs(yt, videos=True, streams=False, shorts=False):
    """tab lookup responses, None is a failed extraction"""
    yt.responses[f"{CHANNEL_URL}/videos"] = (
        {"entries": [{"id": "vid00000001"}]} if videos else None
    )
    yt.responses[f"{CHANNEL_URL}/streams"] = (
        {"entries": [{"id": "live0000001"}]} if streams else None
    )
    yt.responses[f"{CHANNEL_URL}/shorts"] = (
        {"entries": [{"id": "shrt0000001"}]} if shorts else {"entries": []}
    )


def test_build_json_from_db_skips_youtube(yt):
    Channel.save_doc(channel_doc())
    channel = YoutubeChannel(CH)
    channel.build_json()

    assert channel.json_data == channel_doc()
    assert not yt.calls


def test_build_json_from_youtube_uploads(yt, thumbs):
    yt.responses[CHANNEL_URL] = channel_meta()
    set_tabs(yt, videos=True, streams=True)

    channel = YoutubeChannel(CH)
    channel.build_json(upload=True)

    saved = Channel.objects.get()
    assert saved.name == "Test Channel"
    assert saved.subs == 1200
    assert saved.tabs == ["videos", "streams"]
    assert saved.thumb_url == "https://yt3.example/thumb"
    assert saved.tvart_url == "https://yt3.example/uncropped"
    assert saved.banner_url == "https://yt3.example/banner"
    assert thumbs.calls == [
        (
            CH,
            "channel",
            (
                "https://yt3.example/thumb",
                "https://yt3.example/banner",
                "https://yt3.example/uncropped",
            ),
        )
    ]
    tab_obs = [obs for url, obs in yt.calls if url.endswith("/videos")]
    assert tab_obs[0]["playlist_items"] == ":1:1"


def test_build_json_without_upload_keeps_db_empty(yt):
    yt.responses[CHANNEL_URL] = channel_meta(
        uploader=None,
        description="",
        channel_follower_count=None,
        thumbnails=[
            {"url": "https://yt3.example/16x9", "width": 1600, "height": 900}
        ],
    )
    set_tabs(yt, videos=False)

    channel = YoutubeChannel(CH)
    channel.build_json()

    assert not Channel.objects.exists()
    data = channel.json_data
    assert data["channel_name"] == CH
    assert data["channel_description"] is None
    assert data["channel_subs"] == 0
    assert data["channel_tabs"] == []
    assert data["channel_tvart_url"] == "https://yt3.example/16x9"
    assert "channel_thumb_url" not in data
    assert "channel_banner_url" not in data


def test_art_extraction_without_sizes(yt):
    yt.responses[CHANNEL_URL] = channel_meta(
        thumbnails=[{"url": "https://yt3.example/nosize"}]
    )
    set_tabs(yt)

    channel = YoutubeChannel(CH)
    channel.build_json()

    for key in (
        "channel_thumb_url",
        "channel_tvart_url",
        "channel_banner_url",
    ):
        assert key not in channel.json_data


def test_tv_art_skips_wide_and_square(yt):
    yt.responses[CHANNEL_URL] = channel_meta(
        thumbnails=[
            {"url": "https://yt3.example/wide", "width": 2000, "height": 500},
            {"url": "https://yt3.example/square", "width": 10, "height": 10},
        ]
    )
    set_tabs(yt)

    channel = YoutubeChannel(CH)
    channel.build_json()

    assert "channel_tvart_url" not in channel.json_data
    assert (
        channel.json_data["channel_thumb_url"] == "https://yt3.example/square"
    )


def test_build_json_falls_back_to_video_meta(yt, thumbs):
    yt.responses[CHANNEL_URL] = None

    channel = YoutubeChannel(CH)
    channel.build_json(upload=True, fallback={"uploader": "From Video"})

    saved = Channel.objects.get()
    assert saved.name == "From Video"
    assert saved.active is False
    assert saved.subs == 0
    assert not thumbs.calls


def test_build_json_without_meta_raises(yt):
    yt.responses[CHANNEL_URL] = None

    with pytest.raises(ValueError, match="Failed to get metadata"):
        YoutubeChannel(CH).build_json()


def test_change_subscribe_loads_from_db():
    Channel.save_doc(channel_doc(channel_subscribed=False))

    data = YoutubeChannel(CH).change_subscribe(new_subscribe_state=True)

    assert data["channel_subscribed"] is True
    assert Channel.objects.get().subscribed is True


def test_change_subscribe_uses_loaded_json():
    channel = YoutubeChannel(CH)
    channel.json_data = channel_doc(channel_subscribed=True)

    channel.change_subscribe(new_subscribe_state=False)

    assert Channel.objects.get().subscribed is False


def test_delete_channel_not_indexed():
    with pytest.raises(FileNotFoundError):
        YoutubeChannel(CH).delete_channel()


def test_delete_channel_removes_media_playlists_videos(dirs):
    Channel.save_doc(channel_doc())
    Channel.save_doc(channel_doc(channel_id="UCother00000000000000001"))
    Video.save_doc(video_doc())
    other = channel_doc(channel_id="UCother00000000000000001")
    Video.save_doc(video_doc(channel=other, youtube_id="vidother001"))
    Playlist.save_doc(playlist_doc("PLown", ["vid00000001"]))
    Playlist.save_doc(
        playlist_doc(
            "PLforeign",
            ["vidother001"],
            playlist_channel_id="UCother00000000000000001",
        )
    )
    folder = dirs["media"] / CH
    folder.mkdir()
    (folder / "vid00000001.mp4").write_bytes(b"x")

    YoutubeChannel(CH).delete_channel()

    assert not folder.exists()
    assert list(Channel.objects.values_list("pk", flat=True)) == [
        "UCother00000000000000001"
    ]
    assert list(Video.objects.values_list("pk", flat=True)) == ["vidother001"]
    assert list(Playlist.objects.values_list("pk", flat=True)) == ["PLforeign"]
    assert PlaylistEntry.objects.count() == 1


def test_channel_delete_without_media_folder():
    Channel.save_doc(channel_doc())

    ChannelDelete(json_data=channel_doc()).delete()

    assert not Channel.objects.exists()


def test_get_all_playlists_none_found(yt):
    yt.responses[PLAYLISTS_URL] = None
    channel = YoutubeChannel(CH)

    channel.get_all_playlists()

    assert channel.all_playlists == []
    url, obs = yt.calls[0]
    assert obs == {"skip_download": True, "extract_flat": True}


def test_index_channel_playlists_none_found(yt, task):
    Channel.save_doc(channel_doc())
    yt.responses[PLAYLISTS_URL] = {"entries": []}

    YoutubeChannel(CH, task=task).index_channel_playlists()

    assert task.messages == [(["Test Channel: Looking for Playlists"], None)]
    assert not Playlist.objects.exists()


def test_index_channel_playlists(yt, task, thumbs):
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc())
    yt.responses[PLAYLISTS_URL] = {
        "entries": [
            {"id": "PLgood", "title": "good"},
            {"id": "PLbroken", "title": "broken"},
        ]
    }
    yt.responses["https://www.youtube.com/playlist?list=PLgood"] = {
        "title": "Good Playlist",
        "channel": "Test Channel",
        "channel_id": CH,
        "thumbnails": [{"url": "https://i.ytimg.com/pl.jpg"}],
        "entries": [{"id": "vid00000001", "title": "first"}],
    }
    yt.responses["https://www.youtube.com/playlist?list=PLbroken"] = {
        "title": "Broken",
        "channel": None,
        "channel_id": None,
        "thumbnails": [],
        "entries": [],
    }

    YoutubeChannel(CH, task=task).index_channel_playlists()

    assert list(Playlist.objects.values_list("pk", flat=True)) == ["PLgood"]
    assert thumbs.calls == [
        ("PLgood", "playlist", "https://i.ytimg.com/pl.jpg")
    ]
    progress = [p for _, p in task.messages]
    assert progress == [None, 0.5, 1.0, None]
    assert task.messages[-1][0] == [
        f"{CH}: skip failed playlist import",
        "Failed to extract Channel ID for Playlist",
    ]


def test_index_channel_playlists_without_task(yt):
    Channel.save_doc(channel_doc())
    yt.responses[PLAYLISTS_URL] = {"entries": [{"id": "PLx", "title": "x"}]}
    yt.responses["https://www.youtube.com/playlist?list=PLx"] = {
        "title": "x",
        "channel": None,
        "channel_id": None,
        "thumbnails": [],
        "entries": [],
    }

    YoutubeChannel(CH).index_channel_playlists()

    assert not Playlist.objects.exists()


def test_get_channel_videos():
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="vid00000002", vid_type="shorts"))
    Video.save_doc(video_doc())

    assert YoutubeChannel(CH).get_channel_videos() == [
        {"youtube_id": "vid00000001", "vid_type": "videos"},
        {"youtube_id": "vid00000002", "vid_type": "shorts"},
    ]


def test_get_and_set_overwrites():
    channel = YoutubeChannel(CH)
    channel.json_data = channel_doc()
    assert channel.get_overwrites() == {}

    channel.set_overwrites({"autodelete_days": 3, "download_format": None})
    assert channel.get_overwrites() == {
        "autodelete_days": 3,
        "download_format": None,
    }

    channel.set_overwrites({"autodelete_days": None})
    assert channel.get_overwrites() == {"download_format": None}

    with pytest.raises(ValueError, match="invalid overwrite key: bogus"):
        channel.set_overwrites({"bogus": 1})


def test_channel_overwrites_saves():
    Channel.save_doc(channel_doc())

    data = channel_overwrites(CH, {"index_playlists": True})

    assert data["channel_overwrites"] == {"index_playlists": True}
    assert Channel.objects.get().overwrites == {"index_playlists": True}

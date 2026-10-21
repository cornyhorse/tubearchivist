"""tests for playlist indexing and custom playlists, yt-dlp faked"""

import pytest
from appsettings.src.config import AppConfig
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc, video_doc
from playlist.models import Playlist, PlaylistEntry
from playlist.src.index import YoutubePlaylist
from video.models import Video

pytestmark = pytest.mark.usefixtures("app_config")

CH = "UCchannel000000000000001"
OTHER = "UCother00000000000000001"
PL = "PLtest"
PL_URL = f"https://www.youtube.com/playlist?list={PL}"
CUSTOM = "TA_playlist_custom"


def playlist_meta(**overrides) -> dict:
    """yt-dlp response for a playlist"""
    meta = {
        "id": PL,
        "title": "My Playlist",
        "channel": "Test Channel",
        "channel_id": CH,
        "description": "the description",
        "thumbnails": [
            {"url": "https://i.ytimg.com/small.jpg"},
            {"url": "https://i.ytimg.com/big.jpg"},
        ],
        "entries": [
            {"id": "vid00000001", "title": "one", "channel": "Test Channel"},
            {"id": "vid00000002", "title": "two"},
        ],
    }
    meta.update(overrides)
    return meta


def add_videos(*youtube_ids, watched=(), channel=None):
    """index channel and videos"""
    Channel.save_doc(channel or channel_doc())
    for youtube_id in youtube_ids:
        Video.save_doc(
            video_doc(
                channel=channel,
                youtube_id=youtube_id,
                title=f"title {youtube_id}",
                player={"duration": 10, "watched": youtube_id in watched},
            )
        )


def entry_ids(playlist_id) -> list[str]:
    """stored entry order"""
    return list(
        PlaylistEntry.objects.filter(playlist_id=playlist_id)
        .order_by("idx")
        .values_list("youtube_id", flat=True)
    )


def loaded(playlist_id) -> YoutubePlaylist:
    """handler with json_data from the database"""
    playlist = YoutubePlaylist(playlist_id)
    playlist.get_from_db()
    return playlist


def test_build_json_new_playlist(yt):
    add_videos("vid00000001")
    yt.responses[PL_URL] = playlist_meta()

    playlist = YoutubePlaylist(PL)
    playlist.build_json()

    data = playlist.json_data
    assert data["playlist_name"] == "My Playlist"
    assert data["playlist_thumbnail"] == "https://i.ytimg.com/big.jpg"
    assert data["playlist_description"] == "the description"
    assert data["playlist_subscribed"] is False
    assert data["playlist_sort_order"] == "top"
    assert data["playlist_entries"] == [
        {
            "youtube_id": "vid00000001",
            "title": "one",
            "uploader": "Test Channel",
            "idx": 0,
            "downloaded": True,
        },
        {
            "youtube_id": "vid00000002",
            "title": "two",
            "uploader": None,
            "idx": 1,
            "downloaded": False,
        },
    ]
    assert yt.calls == [
        (
            PL_URL,
            {
                "extract_flat": True,
                "allow_playlist_files": True,
                "playlist_items": "::1",
            },
        )
    ]


def test_build_json_existing_no_scrape(yt):
    Playlist.save_doc(playlist_doc(PL, ["vid00000001"]))

    playlist = YoutubePlaylist(PL)
    playlist.build_json()

    assert playlist.json_data["playlist_name"] == f"Playlist {PL}"
    assert not yt.calls


def test_build_json_rescrape_keeps_user_settings(yt):
    add_videos()
    Playlist.save_doc(
        playlist_doc(
            PL,
            [],
            playlist_subscribed=True,
            playlist_sort_order="bottom",
        )
    )
    yt.responses[PL_URL] = playlist_meta(thumbnails=[], description=None)

    playlist = YoutubePlaylist(PL)
    playlist.build_json(scrape=True)

    data = playlist.json_data
    assert data["playlist_subscribed"] is True
    assert data["playlist_sort_order"] == "bottom"
    assert data["playlist_thumbnail"] is False
    assert "playlist_description" not in data
    assert yt.calls[0][1]["playlist_items"] == "::-1"


def test_build_json_youtube_failure(yt):
    yt.responses[PL_URL] = None

    playlist = YoutubePlaylist(PL)
    playlist.build_json()

    assert playlist.json_data is False
    assert playlist.update_playlist() is False


def test_process_meta_requires_channel_id():
    playlist = YoutubePlaylist(PL)
    playlist.youtube_meta = playlist_meta(channel_id=None)

    with pytest.raises(ValueError, match="Channel ID"):
        playlist.process_youtube_meta()


def test_get_playlist_videos():
    add_videos("vid00000002", "vid00000001", "vid00000009")
    Playlist.save_doc(
        playlist_doc(PL, ["vid00000002", "notindexed1", "vid00000001"])
    )

    assert YoutubePlaylist(PL).get_playlist_videos() == [
        {"youtube_id": "vid00000001"},
        {"youtube_id": "vid00000002"},
    ]


def test_change_subscribe():
    Playlist.save_doc(playlist_doc(PL, []))

    data = YoutubePlaylist(PL).change_subscribe(new_subscribe_state=True)

    assert data["playlist_subscribed"] is True
    assert Playlist.objects.get().subscribed is True
    assert Playlist.objects.get().auto_download is False


def test_change_subscribe_auto_download_default():
    """the default applies to a new subscription only"""
    AppConfig().update_config(
        {"subscriptions": {"auto_download_default": True}}
    )
    Playlist.save_doc(playlist_doc(PL, []))

    YoutubePlaylist(PL).change_subscribe(new_subscribe_state=True)
    assert Playlist.objects.get().auto_download is True

    Playlist.objects.update(auto_download=False)
    YoutubePlaylist(PL).change_subscribe(new_subscribe_state=True)
    assert Playlist.objects.get().auto_download is False


def test_match_local():
    add_videos("vid00000001")
    Playlist.save_doc(playlist_doc(PL, ["vid00000001", "notindexed1"]))

    playlist = loaded(PL)
    playlist.match_local()

    entries = playlist.json_data["playlist_entries"]
    assert entries[0]["title"] == "title vid00000001"
    assert entries[0]["downloaded"] is True
    assert entries[1]["title"] == "entry 1"
    stored = PlaylistEntry.objects.get(youtube_id="vid00000001")
    assert stored.title == "title vid00000001"


def test_update_playlist_skip_on_empty(yt, thumbs):
    add_videos()
    yt.responses[PL_URL] = playlist_meta()

    result = YoutubePlaylist(PL).update_playlist(skip_on_empty=True)

    assert result is True
    assert not Playlist.objects.exists()
    assert not thumbs.calls


def test_update_playlist_saves(yt, thumbs):
    add_videos("vid00000002")
    yt.responses[PL_URL] = playlist_meta()

    assert YoutubePlaylist(PL).update_playlist(skip_on_empty=True) is True

    assert entry_ids(PL) == ["vid00000001", "vid00000002"]
    assert thumbs.calls == [(PL, "playlist", "https://i.ytimg.com/big.jpg")]


def test_update_playlist_without_skip(yt):
    add_videos()
    yt.responses[PL_URL] = playlist_meta()

    assert YoutubePlaylist(PL).update_playlist() is True
    assert Playlist.objects.get().name == "My Playlist"


def test_change_sort_order():
    Playlist.save_doc(playlist_doc(PL, []))

    data = YoutubePlaylist(PL).change_sort_order("bottom")

    assert data["playlist_sort_order"] == "bottom"
    assert Playlist.objects.get().sort_order == "bottom"


@pytest.fixture(name="nav_playlist")
def fixture_nav_playlist():
    """three downloaded entries and one missing"""
    add_videos("vid00000001", "vid00000002", "vid00000003")
    Playlist.save_doc(
        playlist_doc(
            PL,
            ["vid00000001", "notindexed1", "vid00000002", "vid00000003"],
        )
    )
    return loaded(PL)


def test_build_nav_middle(nav_playlist, dirs):
    nav_playlist.build_nav("vid00000002")

    nav = nav_playlist.nav
    cache = dirs["cache"]
    assert nav["playlist_meta"] == {
        "current_idx": 2,
        "playlist_id": PL,
        "playlist_name": f"Playlist {PL}",
        "playlist_channel": "Test Channel",
    }
    assert nav["playlist_previous"]["youtube_id"] == "vid00000001"
    assert nav["playlist_previous"]["vid_thumb"] == (
        f"{cache}/videos/v/vid00000001.jpg"
    )
    assert nav["playlist_next"]["youtube_id"] == "vid00000003"
    assert nav["playlist_next"]["vid_thumb"] == (
        f"{cache}/videos/v/vid00000003.jpg"
    )


def test_build_nav_first_and_last(nav_playlist):
    nav_playlist.build_nav("vid00000001")
    assert nav_playlist.nav["playlist_previous"] is None
    assert nav_playlist.nav["playlist_next"]["youtube_id"] == "vid00000002"

    nav_playlist.build_nav("vid00000003")
    assert nav_playlist.nav["playlist_previous"]["youtube_id"] == (
        "vid00000002"
    )
    assert nav_playlist.nav["playlist_next"] is None


def test_build_nav_not_downloaded(nav_playlist):
    nav_playlist.build_nav("notindexed1")

    assert nav_playlist.nav is False


def test_build_nav_single_video():
    add_videos("vid00000001")
    Playlist.save_doc(playlist_doc(PL, ["vid00000001"]))
    playlist = loaded(PL)

    playlist.build_nav("vid00000001")

    assert playlist.nav is False


def test_is_custom_playlist():
    Playlist.save_doc(playlist_doc(PL, []))
    Playlist.save_doc(playlist_doc(CUSTOM, [], playlist_type="custom"))

    assert YoutubePlaylist(PL).is_custom_playlist() is False
    assert loaded(CUSTOM).is_custom_playlist() is True


def test_delete_videos_metadata_all():
    add_videos("vid00000001")
    Playlist.save_doc(playlist_doc(CUSTOM, ["vid00000001", "notindexed1"]))

    YoutubePlaylist(CUSTOM).delete_videos_metadata()

    assert entry_ids(CUSTOM) == []
    assert Playlist.objects.get().thumbnail == ""


def test_delete_videos_metadata_by_channel():
    other = channel_doc(channel_id=OTHER)
    add_videos("vid00000001", "vid00000002")
    add_videos("vidother001", channel=other)
    ids = ["vid00000001", "vidother001", "notindexed1", "vid00000002"]
    Playlist.save_doc(playlist_doc(CUSTOM, ids))

    YoutubePlaylist(CUSTOM).delete_videos_metadata(channel_id=CH)

    assert entry_ids(CUSTOM) == ["vidother001", "notindexed1"]
    playlist = Playlist.objects.get()
    assert playlist.thumbnail == "videos/v/vidother001.jpg"
    assert list(playlist.entries.values_list("idx", flat=True)) == [0, 1]


def test_delete_videos_playlist(dirs):
    add_videos("vid00000001", "vid00000002")
    Playlist.save_doc(
        playlist_doc(
            CUSTOM,
            ["vid00000001", "notindexed1"],
            playlist_type="custom",
        )
    )
    Playlist.save_doc(playlist_doc(PL, ["vid00000001", "vid00000002"]))
    media = dirs["media"] / CH
    media.mkdir()
    (media / "vid00000001.mp4").write_bytes(b"x")

    YoutubePlaylist(CUSTOM).delete_videos_playlist()

    assert not (media / "vid00000001.mp4").exists()
    assert list(Video.objects.values_list("pk", flat=True)) == ["vid00000002"]
    assert list(Playlist.objects.values_list("pk", flat=True)) == [PL]
    # regular playlists keep their entries, downloaded is derived
    assert entry_ids(PL) == ["vid00000001", "vid00000002"]


def test_delete_videos_playlist_not_indexed():
    YoutubePlaylist(PL).delete_videos_playlist()

    assert not Playlist.objects.exists()


def test_create_custom(thumbs):
    playlist = YoutubePlaylist(CUSTOM)

    assert playlist.create("Favorites") is True

    stored = Playlist.objects.get()
    assert stored.name == "Favorites"
    assert stored.playlist_type == "custom"
    assert stored.channel_id == ""
    assert stored.description is None
    assert thumbs.calls == [(CUSTOM, "playlist", False)]


def test_add_video_to_playlist(thumbs):
    add_videos("vid00000001", "vid00000002")
    YoutubePlaylist(CUSTOM).create("Favorites")

    playlist = YoutubePlaylist(CUSTOM)
    assert playlist.add_video_to_playlist("vid00000002") is True
    assert playlist.add_video_to_playlist("vid00000001") is True
    assert playlist.add_video_to_playlist("vid00000002") is True

    assert entry_ids(CUSTOM) == ["vid00000002", "vid00000001"]
    stored = Playlist.objects.get()
    assert stored.thumbnail == "videos/v/vid00000002.jpg"
    assert stored.to_doc()["playlist_entries"][0] == {
        "youtube_id": "vid00000002",
        "idx": 0,
        "title": "title vid00000002",
        "uploader": "Test Channel",
        "downloaded": True,
    }
    assert thumbs.calls[-1] == (
        CUSTOM,
        "playlist",
        "videos/v/vid00000002.jpg",
    )


def test_add_video_not_indexed():
    YoutubePlaylist(CUSTOM).create("Favorites")

    with pytest.raises(TypeError):
        YoutubePlaylist(CUSTOM).add_video_to_playlist("notindexed1")


def test_del_video_then_add_keeps_idx_unique():
    add_videos("vid00000001", "vid00000002", "vid00000003")
    Playlist.save_doc(
        playlist_doc(
            CUSTOM,
            ["vid00000001", "vid00000002"],
            playlist_type="custom",
        )
    )
    playlist = loaded(CUSTOM)
    playlist.del_video("vid00000001")
    playlist.save_to_db()

    YoutubePlaylist(CUSTOM).add_video_to_playlist("vid00000003")

    assert entry_ids(CUSTOM) == ["vid00000002", "vid00000003"]
    assert playlist.json_data["playlist_entries"][0]["idx"] == 0


IDS = ["vid0000000a", "vid0000000b", "vid0000000c", "vid0000000d"]


@pytest.mark.parametrize(
    "video_id, action, hide_watched, expected",
    [
        ("vid0000000c", "up", False, "acbd"),
        ("vid0000000d", "up", True, "abdc"),
        ("vid0000000c", "up", True, "cabd"),
        ("vid0000000a", "down", False, "bacd"),
        ("vid0000000a", "down", True, "bcad"),
        ("vid0000000d", "down", True, "abcd"),
        ("vid0000000c", "top", False, "cabd"),
        ("vid0000000a", "bottom", False, "bcda"),
        ("vid0000000b", "remove", False, "acd"),
    ],
)
def test_move_video(video_id, action, hide_watched, expected):
    add_videos(*IDS, watched=["vid0000000b"])
    Playlist.save_doc(playlist_doc(CUSTOM, IDS, playlist_type="custom"))

    playlist = YoutubePlaylist(CUSTOM)
    result = playlist.move_video(video_id, action, hide_watched=hide_watched)

    assert result is True
    assert "".join(i[-1] for i in entry_ids(CUSTOM)) == expected
    stored = Playlist.objects.get()
    assert list(stored.entries.values_list("idx", flat=True)) == list(
        range(len(expected))
    )


def test_move_video_not_in_playlist():
    add_videos(*IDS)
    Playlist.save_doc(playlist_doc(CUSTOM, IDS, playlist_type="custom"))

    result = YoutubePlaylist(CUSTOM).move_video("vid0000000x", "remove")

    assert result is False
    assert entry_ids(CUSTOM) == IDS


def test_move_last_video_out_clears_thumbnail():
    add_videos("vid0000000a")
    Playlist.save_doc(
        playlist_doc(CUSTOM, ["vid0000000a"], playlist_type="custom")
    )
    playlist = YoutubePlaylist(CUSTOM)

    playlist.move_video("vid0000000a", "remove")

    assert playlist.json_data["playlist_thumbnail"] is False
    assert Playlist.objects.get().thumbnail == ""


def test_playlist_entries_contains():
    playlist = YoutubePlaylist(CUSTOM)
    playlist.json_data = playlist_doc(CUSTOM, ["vid0000000a"])

    assert playlist.playlist_entries_contains("vid0000000a") is True
    assert playlist.playlist_entries_contains("vid0000000b") is False
    assert playlist.get_video_index("vid0000000b") == -1

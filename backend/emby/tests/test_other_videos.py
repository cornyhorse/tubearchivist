"""layout 3: shows are channels, the rest is in "Other videos" """

import xml.etree.ElementTree as ET

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from django.core.management import call_command
from django.core.management.base import CommandError
from emby.models import EpisodeNumber
from emby.src.sync import (
    LAYOUT_KEY,
    OTHER_ID,
    EmbyLayoutOutdated,
    has_show,
    sync_all,
    sync_channel,
    sync_other,
    video_tags,
)
from emby.tests.conftest import OTHER_CHANNEL, add_other_video
from playlist.models import Playlist, PlaylistEntry
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"
OTHER_CHANNEL_ID = OTHER_CHANNEL["channel_id"]
Y2023 = 1700000000  # 2023-11-14
Y2024 = 1705000000  # 2024-01-11


def shows(root):
    """folder names of the shows"""
    base = root / "channels"
    return sorted(p.name for p in base.iterdir()) if base.exists() else []


def episodes(root):
    """{youtube_id: parsed episodedetails} of the Other videos show"""
    found = {}
    for path in root.rglob("*.nfo"):
        nfo = ET.parse(path).getroot()
        if nfo.tag == "episodedetails" and "Other videos" in str(path):
            found[nfo.findtext("uniqueid")] = nfo
    return found


@pytest.mark.parametrize(
    "emby_show, subscribed, expected",
    [
        ("auto", True, True),
        ("auto", False, False),
        ("always", False, True),
        ("always", True, True),
        ("never", True, False),
        ("never", False, False),
    ],
)
def test_has_show(emby_show, subscribed, expected):
    channel = Channel(emby_show=emby_show, subscribed=subscribed)
    assert has_show(channel) is expected


def test_playlists_are_no_shows(library):
    sync_all(library)

    assert shows(library.output_root) == [f"Test Channel [{CHANNEL_ID}]"]
    assert not (library.output_root / "playlists").exists()


def test_unfollowed_channels_share_one_show(library):
    add_other_video(library, "o1", Y2023)
    add_other_video(library, "o2", Y2024)
    other = OTHER_CHANNEL | {"channel_id": "UCsecond00000000000002"}
    other["channel_name"] = "Second Channel"
    add_other_video(library, "o3", Y2023 + 86400, channel=other)

    report = sync_all(library)

    assert shows(library.output_root) == [
        "Other videos [other]",
        f"Test Channel [{CHANNEL_ID}]",
    ]
    assert not report.errors
    found = episodes(library.output_root)
    assert sorted(found) == ["o1", "o2", "o3"]
    assert {k: v.findtext("season") for k, v in found.items()} == {
        "o1": "2023",
        "o2": "2024",
        "o3": "2023",
    }
    assert found["o1"].findtext("studio") == "Unfollowed Channel"
    assert found["o3"].findtext("studio") == "Second Channel"
    assert found["o3"].findtext("plot").startswith("From Second Channel.")
    # the show has no tag, so tag restricted users never see it
    nfo = next(library.output_root.glob("channels/Other*/tvshow.nfo"))
    root = ET.parse(nfo).getroot()
    assert root.findtext("title") == "Other videos"
    assert root.find("tag") is None
    assert root.findtext("premiered") == "2023-11-14"
    stored = EpisodeNumber.objects.filter(series_id=OTHER_ID)
    assert {(n.youtube_id, n.season) for n in stored} == {
        ("o1", 2023),
        ("o2", 2024),
        ("o3", 2023),
    }
    assert {n.series_name for n in stored} == {"Other videos"}


def test_other_numbers_never_change(library):
    add_other_video(library, "o1", Y2024)
    sync_all(library)
    first = dict(
        EpisodeNumber.objects.filter(series_id=OTHER_ID).values_list(
            "youtube_id", "episode"
        )
    )
    # an older video found later is appended, not inserted before
    add_other_video(library, "o0", Y2024 - 86400)

    report = sync_all(library)

    now = dict(
        EpisodeNumber.objects.filter(series_id=OTHER_ID).values_list(
            "youtube_id", "episode"
        )
    )
    assert now["o1"] == first["o1"] and now["o0"] == first["o1"] + 1
    assert not report.renamed


def test_other_episode_tags(library):
    add_other_video(library, "o1", Y2023)
    add_other_video(library, "o2", Y2023 + 86400)
    add_other_video(library, "o3", Y2023 + 2 * 86400)
    Playlist.objects.update(emby_tags=["David"])
    for n, youtube_id in enumerate(["o1", "o2"]):
        PlaylistEntry.objects.create(
            playlist_id="PL1", idx=10 + n, youtube_id=youtube_id
        )
    other = Playlist.objects.create(playlist_id="PL2", name="Second")
    PlaylistEntry.objects.create(playlist=other, idx=0, youtube_id="o2")
    Playlist.objects.filter(pk="PL2").update(emby_tags=["Kids", "David"])
    Channel.objects.filter(pk=OTHER_CHANNEL_ID).update(emby_tags=["Base"])

    sync_all(library)

    found = episodes(library.output_root)
    tags = {k: [t.text for t in v.findall("tag")] for k, v in found.items()}
    assert tags == {
        "o1": ["Base", "David"],
        "o2": ["Base", "David", "Kids"],
        "o3": ["Base"],
    }
    assert video_tags(list(Video.objects.filter(pk="v2"))) == {}


def test_unsubscribing_moves_the_show_into_other(library):
    sync_all(library)
    Channel.objects.update(subscribed=False)

    report = sync_all(library)

    assert shows(library.output_root) == ["Other videos [other]"]
    assert sorted(episodes(library.output_root)) == ["v1", "v2", "v3"]
    assert any(p.endswith("/") for p in report.deleted)
    assert report.created

    Channel.objects.update(emby_show="always")
    sync_all(library)

    assert shows(library.output_root) == [f"Test Channel [{CHANNEL_ID}]"]
    # the channel's own numbers were kept for the return
    assert EpisodeNumber.objects.filter(series_id=CHANNEL_ID).count() == 3


def test_no_other_videos_no_show(library):
    sync_all(library)
    assert "Other videos [other]" not in shows(library.output_root)
    assert not sync_other(library).errors


def test_sync_channel_without_show_is_refused(library):
    Channel.objects.update(emby_show="never")
    with pytest.raises(ValueError, match="no show of its own"):
        sync_channel(CHANNEL_ID, library)


def test_old_layout_blocks_other_sync(library):
    sync_all(library)
    ConfigDocument.objects.filter(key=LAYOUT_KEY).update(value={"version": 2})
    with pytest.raises(EmbyLayoutOutdated, match="ta_emby_migrate"):
        sync_other(library)


def test_command_other_and_channel_errors(library, settings_env, capsys):
    # pylint: disable=unused-argument
    add_other_video(library, "o1", Y2023)

    call_command("ta_emby_sync", str(library.output_root), "--other")

    assert shows(library.output_root) == ["Other videos [other]"]
    Channel.objects.filter(pk=CHANNEL_ID).update(emby_show="never")
    with pytest.raises(CommandError, match="no show of its own"):
        call_command(
            "ta_emby_sync", str(library.output_root), "--channel", CHANNEL_ID
        )


def channel_episode_tags(root):
    return {
        n.findtext("uniqueid"): [t.text for t in n.findall("tag")]
        for n in (
            ET.parse(p).getroot() for p in root.glob("channels/Test*/**/*.nfo")
        )
        if n.tag == "episodedetails"
    }


def test_playlist_tags_reach_episodes_of_an_untagged_channel(library):
    """whatever a tagged playlist holds is approved for the tag"""
    Playlist.objects.update(emby_tags=["David"])  # PL1 holds v3 and v1

    sync_all(library)

    assert channel_episode_tags(library.output_root) == {
        "v1": ["David"],
        "v2": [],
        "v3": ["David"],
    }
    series = next(library.output_root.glob("channels/Test*/tvshow.nfo"))
    assert ET.parse(series).getroot().find("tag") is None

    # taking the tag back rewrites those NFOs
    Playlist.objects.update(emby_tags=[])
    report = sync_all(library)
    assert channel_episode_tags(library.output_root) == {
        "v1": [],
        "v2": [],
        "v3": [],
    }
    assert len([p for p in report.updated if p.endswith(".nfo")]) == 2


def test_series_tag_is_not_repeated_on_episodes(library):
    Channel.objects.update(emby_tags=["David"])
    Playlist.objects.update(emby_tags=["david", "Kids"])

    sync_all(library)

    assert channel_episode_tags(library.output_root) == {
        "v1": ["Kids"],
        "v2": [],
        "v3": ["Kids"],
    }

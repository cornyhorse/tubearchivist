"""the whole v0.5.0 stack together: tags, shows, UTC dates and playlists"""

import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from appsettings.models import ConfigDocument
from common.tests.docs import channel_doc
from django.core.management import call_command
from emby.models import EmbyPlaylistLink, EpisodeNumber
from emby.src.playlist_sync import media_paths, sync_playlists
from emby.src.sync import LAYOUT_KEY, LAYOUT_VERSION, layout_version
from emby.src.writer import Manifest
from emby.tests.conftest import add_other_video, add_video
from emby.tests.test_playlist_sync import SERVER_ROOT, FakeEmby
from mediaserver.src import settings
from playlist.models import Playlist, PlaylistEntry

pytestmark = pytest.mark.django_db

CHANNEL_ID = channel_doc()["channel_id"]
# 2023-11-14 03:00 UTC: still the 13th in New York
EARLY = int(datetime(2023, 11, 14, 3, 0, tzinfo=timezone.utc).timestamp())
NOON = EARLY + 9 * 3600


def nfos(root):
    return {
        n.findtext("uniqueid"): n
        for n in (ET.parse(p).getroot() for p in root.rglob("*.nfo"))
        if n.tag == "episodedetails"
    }


def test_layout_2_database_to_the_finished_stack(
    library, settings_env, monkeypatch
):
    # pylint: disable=unused-argument
    monkeypatch.setattr(settings_env, "TZ", "America/New_York")
    monkeypatch.setenv(settings.KEY_ENV, "k")
    settings.update(
        {
            "type": "emby",
            "url": "http://e:8096",
            "path_to": SERVER_ROOT,
            "playlists_enabled": True,
            "playlist_default_users": ["Matt"],
        }
    )
    add_video(library, "early", EARLY)
    add_other_video(library, "o1", NOON)  # a channel with no show
    # layout 2: numbered by New York dates, one playlist show worth of tags
    for youtube_id, episode in (("v1", 111400), ("early", 111300)):
        EpisodeNumber.objects.create(
            series_kind="channel",
            series_id=CHANNEL_ID,
            scheme="date",
            youtube_id=youtube_id,
            season=2023,
            episode=episode,
            name=youtube_id,
            series_name="Test Channel",
        )
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    for idx, youtube_id in enumerate(["v1", "o1", "early"]):
        PlaylistEntry.objects.create(
            playlist=playlist, idx=idx, youtube_id=youtube_id
        )
    Playlist.objects.update(emby_tags=["David"])
    cmd = "config.management.commands.ta_emby_migrate_layout."
    monkeypatch.setattr(
        cmd + "before_state", lambda client, tags, files: {"David": (set(), 0)}
    )
    monkeypatch.setattr(cmd + "watch_history_at_risk", lambda m: ({}, None))
    out = io.StringIO()

    call_command("ta_emby_migrate_layout", stdout=out)

    assert layout_version() == LAYOUT_VERSION == 4
    lines = out.getvalue().splitlines()
    assert "UTC dates: 1 episodes renumbered" in lines[0]
    assert any("visible to David" in line for line in lines)
    found = nfos(library.output_root)
    assert found["early"].findtext("aired") == "2023-11-14"
    assert found["early"].findtext("episode") == "111401"  # v1 holds 111400
    # the catch-all show and its tag from the playlist
    assert [t.text for t in found["o1"].findall("tag")] == ["David"]
    shows = sorted(
        p.name for p in (library.output_root / "channels").iterdir()
    )
    assert shows == [
        "Other videos [other]",
        f"Test Channel [{CHANNEL_ID}]",
    ]
    # the untagged channel show: its episodes carry the playlist's tag
    assert [t.text for t in found["v1"].findall("tag")] == ["David"]
    assert found["v2"].find("tag") is None
    assert not (library.output_root / "playlists").exists()

    # the playlist sync, with an Emby that scanned everything
    emby = FakeEmby(library)
    emby.known = None
    emby.users_list = [
        {"id": "u1", "name": "Matt", "admin": True},
        {"id": "u2", "name": "David", "admin": False},
    ]
    media = media_paths(Manifest(library.output_root, dry_run=True))
    result = sync_playlists(emby, media)

    assert not result.errors and not result.skipped
    assert len(result.created) == 2
    name = Playlist.objects.get().name
    assert emby.items_of(name, "u2") == ["e-v1", "e-o1", "e-early"]
    assert EmbyPlaylistLink.objects.count() == 2

    # everything is settled: nothing to change on a second pass
    again = io.StringIO()
    call_command("ta_emby_sync", str(library.output_root), stdout=again)
    assert "created 0, updated 0, renamed 0, deleted 0" in again.getvalue()
    assert sync_playlists(emby, media).unchanged == 2

"""the Emby playlist sync against an in memory Emby"""

import io

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from django.core.management import call_command
from django.core.management.base import CommandError
from emby.models import EmbyPlaylistLink
from emby.src import playlist_sync
from emby.src.playlist_sync import (
    PlaylistSyncResult,
    media_paths,
    owner_names,
    record_run,
    sync_playlists,
    sync_state,
)
from emby.src.sync import sync_all
from emby.src.writer import Manifest
from mediaserver.src import settings
from mediaserver.src.client import MediaServerError, MediaServerNotFound
from playlist.models import Playlist, PlaylistEntry

pytestmark = pytest.mark.django_db

SERVER_ROOT = "/mnt/emby"
USERS = [
    {"id": "u1", "name": "Matt", "admin": True},
    {"id": "u2", "name": "David", "admin": False},
]


class FakeEmby:
    """the playlist calls of MediaServerClient, in memory"""

    def __init__(self, library):
        self.library = library
        self.playlists = {}
        self.calls = []
        self.fail = None
        self.users_list = USERS
        self.next_id = 100
        self.known = set()

    def users(self):
        return self.users_list

    def episode_ids(self):
        manifest = Manifest(self.library.output_root, dry_run=True)
        return {
            f"{SERVER_ROOT}/{relpath}": f"e-{entry['owner'].split('/')[-1]}"
            for relpath, entry in manifest.files.items()
            if entry["role"] == "media"
            if self.known is None
            or entry["owner"].split("/")[-1] in self.known
        }

    def _maybe_fail(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise MediaServerError(f"{name} failed")

    def playlist_items(self, playlist_id, user_id):
        self._maybe_fail("items")
        if playlist_id not in self.playlists:
            raise MediaServerNotFound("gone")
        return list(self.playlists[playlist_id]["entries"])

    def create_playlist(self, name, user_id, item_ids):
        self._maybe_fail("create")
        self.next_id += 1
        pid = f"p{self.next_id}"
        self.playlists[pid] = {"name": name, "owner": user_id, "entries": []}
        self.add_to_playlist(pid, user_id, item_ids)
        return pid

    def add_to_playlist(self, playlist_id, user_id, item_ids):
        self._maybe_fail("add")
        entries = self.playlists[playlist_id]["entries"]
        for item in item_ids:
            self.next_id += 1
            entries.append((f"x{self.next_id}", item))

    def remove_from_playlist(self, playlist_id, entry_ids):
        self._maybe_fail("remove")
        entries = self.playlists[playlist_id]["entries"]
        entries[:] = [e for e in entries if e[0] not in entry_ids]

    def delete_item(self, item_id):
        self._maybe_fail("delete")
        if item_id not in self.playlists:
            raise MediaServerNotFound("gone")
        del self.playlists[item_id]

    def items_of(self, name, owner):
        for playlist in self.playlists.values():
            if playlist["name"] == name and playlist["owner"] == owner:
                return [item for _, item in playlist["entries"]]
        return None


@pytest.fixture(name="emby")
def fixture_emby(library, monkeypatch, tmp_path):
    """the library synced to a tree, an Emby that knows its episodes"""
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    settings.update(
        {
            "type": "emby",
            "url": "http://e:8096",
            "api_key": "k",
            "path_to": SERVER_ROOT,
            "playlists_enabled": True,
            "playlist_default_users": ["Matt"],
        }
    )
    sync_all(library)
    fake = FakeEmby(library)
    fake.known = None
    fake.paths = media_paths(Manifest(library.output_root, dry_run=True))
    return fake


def run(fake, dry_run=False):
    return sync_playlists(fake, fake.paths, dry_run)


def test_media_paths(emby):
    assert set(emby.paths) == {"v1", "v2", "v3"}
    assert all(p.endswith(".mp4") for p in emby.paths.values())


def test_owner_names():
    playlist = Playlist(emby_tags=["david", "Kids"])
    assert owner_names(playlist, ["Matt", "David"]) == [
        "Matt",
        "David",
        "Kids",
    ]


def test_create_per_owner_in_playlist_order(emby):
    Playlist.objects.update(emby_tags=["david"])

    result = run(emby)

    assert result.summary()["created"] == 2
    assert not result.errors and not result.warnings
    name = Playlist.objects.get().name
    # PL1 is v3, (not downloaded), v1
    assert emby.items_of(name, "u1") == ["e-v3", "e-v1"]
    assert emby.items_of(name, "u2") == ["e-v3", "e-v1"]
    assert EmbyPlaylistLink.objects.count() == 2


def test_unchanged_run_costs_reads_only(emby):
    run(emby)
    emby.calls.clear()

    result = run(emby)

    assert result.unchanged == 1 and not result.created
    assert set(emby.calls) == {"items"}


def test_dry_run_changes_nothing(emby):
    result = run(emby, dry_run=True)

    assert len(result.created) == 1
    assert not emby.playlists and not EmbyPlaylistLink.objects.exists()
    run(emby)
    PlaylistEntry.objects.create(playlist_id="PL1", idx=9, youtube_id="v2")
    result = run(emby, dry_run=True)
    assert len(result.changed) == 1
    assert [i for _, i in next(iter(emby.playlists.values()))["entries"]] == [
        "e-v3",
        "e-v1",
    ]


def test_appends_new_and_removes_gone(emby):
    run(emby)
    pid = next(iter(emby.playlists))
    PlaylistEntry.objects.create(playlist_id="PL1", idx=9, youtube_id="v2")

    result = run(emby)

    assert len(result.changed) == 1
    assert [i for _, i in emby.playlists[pid]["entries"]] == [
        "e-v3",
        "e-v1",
        "e-v2",
    ]
    PlaylistEntry.objects.filter(youtube_id="v3").delete()
    run(emby)
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v1", "e-v2"]


def test_reorder_rebuilds(emby):
    run(emby)
    pid = next(iter(emby.playlists))
    PlaylistEntry.objects.filter(youtube_id="v1").update(idx=-1)

    run(emby)

    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v1", "e-v3"]


def test_duplicates_and_foreign_items_are_dropped(emby):
    run(emby)
    pid = next(iter(emby.playlists))
    emby.playlists[pid]["entries"] += [("d1", "e-v3"), ("d2", "e-other")]

    run(emby)

    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v3", "e-v1"]


def test_episodes_emby_has_not_scanned_yet(emby):
    emby.known = {"v1"}

    result = run(emby)

    assert result.missing == 1
    assert emby.items_of(Playlist.objects.get().name, "u1") == ["e-v1"]
    emby.known = None
    run(emby)
    assert emby.items_of(Playlist.objects.get().name, "u1") == ["e-v3", "e-v1"]


def test_playlist_without_episodes_is_not_created(emby):
    emby.known = set()

    result = run(emby)

    assert not result.created and not emby.playlists
    assert result.missing == 2


def test_rename_recreates(emby):
    run(emby)
    old = next(iter(emby.playlists))
    Playlist.objects.update(name="New name")

    run(emby)

    assert old not in emby.playlists
    assert emby.items_of("New name", "u1") == ["e-v3", "e-v1"]
    assert EmbyPlaylistLink.objects.get().name == "New name"


def test_switched_off_and_removed_playlists_are_deleted(emby):
    run(emby)
    hand_made = {"name": "mine", "owner": "u1", "entries": []}
    emby.playlists["hand"] = hand_made

    Playlist.objects.update(emby_playlist=False)
    result = run(emby)

    assert len(result.deleted) == 1
    assert list(emby.playlists) == ["hand"]  # never touches others
    assert not EmbyPlaylistLink.objects.exists()


def test_playlist_deleted_in_emby_is_recreated(emby):
    run(emby)
    emby.playlists.clear()

    result = run(emby)

    assert len(result.created) == 1
    assert len(emby.playlists) == 1


def test_stale_link_whose_playlist_is_gone(emby):
    run(emby)
    emby.playlists.clear()
    Playlist.objects.update(emby_playlist=False)

    assert len(run(emby).deleted) == 1
    assert not EmbyPlaylistLink.objects.exists()


def test_unknown_default_user_warns_unknown_tag_does_not(emby):
    settings.update({"playlist_default_users": ["Matt", "Nobody"]})
    Playlist.objects.update(emby_tags=["Kids"])

    result = run(emby)

    assert result.warnings == ["Emby has no user Nobody"]
    assert result.summary()["created"] == 1


def test_errors_are_collected_and_the_rest_continues(emby):
    other = Playlist.objects.create(playlist_id="PL2", name="Other")
    PlaylistEntry.objects.create(playlist=other, idx=0, youtube_id="v2")
    emby.fail = "create"

    result = run(emby)

    assert len(result.errors) == 2 and not emby.playlists
    emby.fail = None
    emby.calls.clear()
    run(emby)
    EmbyPlaylistLink.objects.create(
        playlist_id="gone", user_id="u1", emby_id="zz", name="old"
    )
    emby.fail = "delete"
    result = run(emby)
    assert result.errors and "old" in result.errors[0]


def test_reader_is_an_admin_else_the_owner(emby):
    emby.users_list = [{"id": "u2", "name": "David", "admin": False}]
    settings.update({"playlist_default_users": ["David"]})
    seen = []
    real = emby.playlist_items
    emby.playlist_items = lambda pid, user: seen.append(user) or real(
        pid, user
    )

    run(emby)
    run(emby)

    assert seen == ["u2"]
    assert playlist_sync._reader(USERS, "u2") == "u1"


def test_state_records_runs():
    assert sync_state() == {}
    record_run(error="down")
    assert sync_state()["last_error"] == "down"
    assert "last_success" not in sync_state()

    record_run(result=PlaylistSyncResult())
    state = sync_state()
    assert state["last_error"] is None and state["last_success"]
    assert state["last_summary"]["created"] == 0

    failed = PlaylistSyncResult(errors=["a", "b"])
    record_run(result=failed)
    assert sync_state()["last_error"] == "a; b"
    assert Channel.objects.count() == 0


def test_settings_fields():
    public = settings.update(
        {"playlists_enabled": True, "playlist_default_users": ["Matt"]}
    )
    assert public["playlists_enabled"] is True
    assert public["playlist_default_users"] == ["Matt"]


# the command


def run_command(*args):
    out = io.StringIO()
    call_command("ta_emby_playlists", *args, stdout=out)
    return out.getvalue()


@pytest.fixture(name="command")
def fixture_command(emby, library, monkeypatch):
    """the command with the in memory Emby and the scratch tree"""
    target = "config.management.commands.ta_emby_playlists."
    monkeypatch.setattr(target + "paths_from_env", lambda: library)
    monkeypatch.setattr(target + "MediaServerClient", lambda conn: emby)
    return emby


def test_command(command):
    preview = run_command("--dry-run")
    assert "[dry-run] created Playlist PL1 for Matt" in preview
    assert not command.playlists

    output = run_command()
    assert "created Playlist PL1 for Matt" in output
    assert sync_state()["last_success"]
    assert "'unchanged': 1" in run_command()


def test_command_errors(command, monkeypatch):
    command.fail = "create"
    with pytest.raises(CommandError, match="1 playlists failed"):
        run_command()

    def down(*_):
        raise MediaServerError("down")

    monkeypatch.setattr(
        "config.management.commands.ta_emby_playlists.sync_playlists", down
    )
    with pytest.raises(CommandError, match="down"):
        run_command()

    settings.update({"type": "jellyfin"})
    with pytest.raises(CommandError, match="Emby server"):
        run_command()
    settings.update({"type": "emby"})
    monkeypatch.setattr(
        "config.management.commands.ta_emby_playlists.paths_from_env",
        lambda: None,
    )
    with pytest.raises(CommandError, match="TA_EMBY_ROOT"):
        run_command()


def test_command_prints_warnings_and_dry_run_deletes(command):
    run_command()
    settings.update({"playlist_default_users": ["Matt", "Nobody"]})
    Playlist.objects.update(emby_playlist=False)

    output = run_command("--dry-run")

    assert "[dry-run] deleted Playlist PL1" in output
    assert command.playlists  # a dry run deletes nothing
    assert str(EmbyPlaylistLink.objects.get()).startswith("PL1 for u1")


def test_command_warning_line(command):
    settings.update({"playlist_default_users": ["Matt", "Nobody"]})
    assert "warning: Emby has no user Nobody" in run_command("--dry-run")

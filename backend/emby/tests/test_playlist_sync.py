"""the Emby playlist sync against an in memory Emby"""

import io

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from django.core.management import call_command
from django.core.management.base import CommandError
from emby.models import EmbyPlaylistLink
from emby.src import playlist_sync
from emby.src.health import check_emby_health
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
from jobs.src.locks import LockBusy, playlist_lock
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
        self.other_items = {}

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
            if item in [i for _, i in entries]:
                continue  # Emby does not add an item twice
            self.next_id += 1
            entries.append((f"x{self.next_id}", item))

    def owned_item(self, user_id, item_id):
        self._maybe_fail("owned")
        if item_id in self.other_items:
            return self.other_items[item_id]
        playlist = self.playlists.get(item_id)
        if playlist and playlist["owner"] == user_id:
            return {"type": "Playlist", "name": playlist["name"]}
        return None

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
    # a read of the playlist and the check that it is still ours
    assert set(emby.calls) == {"items", "owned"}


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
    emby.known = {"v1", "v2"}

    result = run(emby)

    assert result.missing == 1
    assert emby.items_of(Playlist.objects.get().name, "u1") == ["e-v1"]
    emby.known = None
    run(emby)
    assert emby.items_of(Playlist.objects.get().name, "u1") == ["e-v3", "e-v1"]


def test_playlist_without_episodes_is_not_created(emby):
    PlaylistEntry.objects.filter(youtube_id="v3").delete()  # only v1 left
    emby.known = {"v2", "v3"}

    result = run(emby)

    assert not result.created and not emby.playlists
    assert result.missing == 1


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

    result = run(emby)

    # gone in Emby already: the link is dropped, nothing to delete
    assert "nothing deleted" in result.warnings[0]
    assert not result.deleted and not EmbyPlaylistLink.objects.exists()


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
    emby.playlists["zz"] = {"name": "old", "owner": "u1", "entries": []}
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


# review round 1 (findings 3, 5, 6, 8, 13)


def test_a_reused_emby_id_is_never_touched(emby):
    """an Emby database that was rebuilt hands the id to something else"""
    run(emby)
    link = EmbyPlaylistLink.objects.get()
    emby.playlists.clear()
    emby.other_items[link.emby_id] = {"type": "Episode", "name": "media.mkv"}

    result = run(emby)

    assert "is not the playlist TA made" in result.warnings[0]
    assert "delete" not in emby.calls
    assert len(result.created) == 1  # a new playlist, a new link
    assert EmbyPlaylistLink.objects.get().emby_id != link.emby_id


def test_a_playlist_of_another_user_or_name_is_not_ours(emby):
    run(emby)
    link = EmbyPlaylistLink.objects.get()
    # same id, but renamed by hand in Emby
    emby.playlists[link.emby_id]["name"] = "My own list"

    result = run(emby)

    assert "is not the playlist TA made" in result.warnings[0]
    assert emby.playlists[link.emby_id]["name"] == "My own list"
    assert len(emby.playlists) == 2  # the hand made one stays


def test_stale_link_that_is_not_ours_is_dropped_not_deleted(emby):
    run(emby)
    link = EmbyPlaylistLink.objects.get()
    emby.playlists[link.emby_id]["name"] = "renamed by hand"
    Playlist.objects.update(emby_playlist=False)

    dry = run(emby, dry_run=True)
    assert dry.warnings and EmbyPlaylistLink.objects.exists()
    result = run(emby)

    assert "nothing deleted" in result.warnings[0]
    assert not result.deleted and not EmbyPlaylistLink.objects.exists()
    assert emby.playlists[link.emby_id]["name"] == "renamed by hand"


def test_a_half_scanned_emby_does_not_empty_playlists(emby, monkeypatch):
    monkeypatch.setattr(playlist_sync, "MIN_LARGE_REMOVAL", 1)
    run(emby)
    pid = next(iter(emby.playlists))
    emby.known = {"v2", "v3"}  # enough files known, but v1 not yet

    # one of two entries going is not more than half
    result = run(emby)
    assert len(result.changed) == 1
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v3"]

    emby.known = {"v1", "v2"}  # now v3 is the one Emby lost
    result = run(emby)
    # its only entry would go: held back, the playlist keeps what it had
    assert "held back" in result.warnings[0]
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v3"]


def test_large_removal_can_be_allowed(emby, monkeypatch):
    monkeypatch.setattr(playlist_sync, "MIN_LARGE_REMOVAL", 1)
    run(emby)
    pid = next(iter(emby.playlists))
    # the TA playlist now holds only v2: both entries of the Emby copy go
    PlaylistEntry.objects.filter(playlist_id="PL1").delete()
    PlaylistEntry.objects.create(playlist_id="PL1", idx=0, youtube_id="v2")

    held = run(emby)
    assert "held back" in held.warnings[0]
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v3", "e-v1"]

    allowed = sync_playlists(emby, emby.paths, allow_large_removal=True)
    assert len(allowed.changed) == 1
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v2"]


def test_emby_that_knows_few_files_holds_back_the_run(emby):
    emby.known = {"v1"}  # 1 of 3 files

    result = run(emby)

    assert result.skipped.startswith("Emby knows 1 of 3 files")
    assert not emby.playlists and not result.created
    assert result.summary()["skipped"] == result.skipped


def test_duplicate_entries_do_not_rebuild_every_run(emby):
    PlaylistEntry.objects.create(playlist_id="PL1", idx=9, youtube_id="v3")
    run(emby)
    emby.calls.clear()

    result = run(emby)

    assert result.unchanged == 1 and not result.changed
    assert set(emby.calls) <= {"items", "owned"}
    pid = next(iter(emby.playlists))
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v3", "e-v1"]


def test_dry_run_shows_a_rename(emby):
    run(emby)
    Playlist.objects.update(name="New name")

    result = run(emby, dry_run=True)

    assert result.changed == ["recreate Playlist PL1 -> New name for Matt"]
    assert emby.items_of("Playlist PL1", "u1") is not None  # nothing done
    real = run(emby)
    assert real.changed == result.changed
    assert emby.items_of("New name", "u1") is not None


def test_a_second_sync_cannot_start_while_one_runs(emby):
    with playlist_lock():
        with pytest.raises(LockBusy):
            with playlist_lock():
                pass  # pragma: no cover - the inner lock must refuse


def test_command_refuses_while_a_sync_runs(command, monkeypatch):
    monkeypatch.setattr(
        "config.management.commands.ta_emby_playlists.COMMAND_WAIT", 0.1
    )
    with playlist_lock():
        with pytest.raises(CommandError, match="another Emby playlist sync"):
            run_command()


def test_command_flags(command):
    assert "held back" not in run_command("--dry-run")
    command.known = {"v1"}
    output = run_command()
    assert "held back: Emby knows 1 of 3 files" in output
    assert "last_success" not in sync_state()
    command.known = None
    run_command("--allow-large-removal")
    assert sync_state()["last_success"]


def test_reused_id_in_a_dry_run_changes_nothing(emby):
    run(emby)
    link = EmbyPlaylistLink.objects.get()
    emby.playlists.clear()
    emby.other_items[link.emby_id] = {"type": "Episode", "name": "media"}

    result = run(emby, dry_run=True)

    assert result.warnings and len(result.created) == 1
    assert EmbyPlaylistLink.objects.get().emby_id == link.emby_id


def test_playlist_vanishing_between_check_and_read(emby, monkeypatch):
    run(emby)

    def gone(*_):
        raise MediaServerNotFound("gone")

    monkeypatch.setattr(emby, "playlist_items", gone)
    result = run(emby)

    assert len(result.created) == 1  # recreated


def test_delete_of_a_playlist_that_just_vanished(emby, monkeypatch):
    run(emby)

    def gone(_):
        raise MediaServerNotFound("gone")

    monkeypatch.setattr(emby, "delete_item", gone)
    Playlist.objects.update(emby_playlist=False)

    result = run(emby)

    assert len(result.deleted) == 1 and not result.errors
    assert not EmbyPlaylistLink.objects.exists()


def test_ordinary_edits_are_not_held_back(emby):
    """replacing the only video of a short playlist is not a large removal"""
    run(emby)
    pid = next(iter(emby.playlists))
    PlaylistEntry.objects.filter(playlist_id="PL1").delete()
    PlaylistEntry.objects.create(playlist_id="PL1", idx=0, youtube_id="v2")

    result = run(emby)

    assert len(result.changed) == 1 and not result.held
    assert [i for _, i in emby.playlists[pid]["entries"]] == ["e-v2"]


def test_held_back_playlists_are_recorded_and_shown_by_health(
    emby, monkeypatch
):
    monkeypatch.setattr(playlist_sync, "MIN_LARGE_REMOVAL", 1)
    run(emby)
    PlaylistEntry.objects.filter(playlist_id="PL1").delete()
    PlaylistEntry.objects.create(playlist_id="PL1", idx=0, youtube_id="v2")

    result = run(emby)
    record_run(result=result)
    first = sync_state()["held"]
    record_run(result=result)

    assert list(first) == ["Playlist PL1 for Matt"]
    assert sync_state()["held"] == first  # the time it was first held
    assert result.summary()["held_back"] == 1

    # fresh: not yet a problem; held since long ago: a problem
    assert not [
        p for p in check_emby_health(emby.library).problems if "held back" in p
    ]
    state = sync_state()
    state["held"] = {"Playlist PL1 for Matt": "2020-01-01T00:00:00+00:00"}
    ConfigDocument.objects.update_or_create(
        key=playlist_sync.STATE_KEY, defaults={"value": state}
    )
    problems = check_emby_health(emby.library).problems
    assert any("held back since 2020" in p for p in problems)

    # once the change goes through, it is no longer held
    allowed = sync_playlists(emby, emby.paths, allow_large_removal=True)
    record_run(result=allowed)
    assert sync_state()["held"] == {}


def test_a_deleted_emby_user_does_not_fail_every_run(emby, monkeypatch):
    run(emby)
    real = emby.owned_item

    def user_gone(user_id, item_id):
        raise MediaServerNotFound("no such user")

    monkeypatch.setattr(emby, "owned_item", user_gone)
    Playlist.objects.update(emby_playlist=False)

    result = run(emby)

    assert not result.errors and not result.deleted
    assert "not the playlist TA made" in result.warnings[0]
    assert not EmbyPlaylistLink.objects.exists()
    monkeypatch.setattr(emby, "owned_item", real)

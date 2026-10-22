"""
Functionality:
- keep Emby playlists in step with the TA playlists (Emby only)
- an Emby playlist belongs to one user and can't be shared, so every
  playlist is created once per owner: the users in the media server
  setting playlist_default_users plus every Emby user whose name is one of
  the playlist's emby_tags
- the items are the episodes of the playlist's downloaded videos, found in
  Emby by the path of the file the Emby writer wrote (its manifest), in
  playlist order; videos Emby has not scanned yet are left out and join on
  the next run
- only playlists TA created are ever changed or deleted (EmbyPlaylistLink)
- idempotent: an unchanged playlist costs one read
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from appsettings.models import ConfigDocument
from emby.models import EmbyPlaylistLink
from emby.src.writer import Manifest
from mediaserver.src import settings
from mediaserver.src.client import (
    MediaServerClient,
    MediaServerError,
    MediaServerNotFound,
)
from playlist.models import Playlist

STATE_KEY = "emby_playlist_state"


@dataclass
class Wanted:
    """a playlist copy: what Emby should hold for one owner"""

    playlist_id: str
    name: str
    owner: str  # Emby user id
    owner_name: str
    item_ids: list[str]
    missing: int  # videos not (yet) known to Emby


class LargeRemoval(Exception):
    """a run would take most of a playlist away"""


@dataclass
class PlaylistSyncResult:
    """what a run did"""

    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    missing: int = 0
    # videos of the Emby tree, and how many of them Emby has scanned
    known: int = 0
    resolved: int = 0
    # set when the whole run was held back, and why
    skipped: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """counts, for the job result"""
        return {
            "created": len(self.created),
            "changed": len(self.changed),
            "deleted": len(self.deleted),
            "unchanged": self.unchanged,
            "not_in_emby_yet": self.missing,
            "errors": len(self.errors),
            **({"skipped": self.skipped} if self.skipped else {}),
        }


def media_paths(manifest: Manifest) -> dict[str, str]:
    """{youtube_id: relpath of the video file in the Emby tree}"""
    return {
        entry["owner"].rsplit("/", 1)[-1]: relpath
        for relpath, entry in manifest.files.items()
        if entry["role"] == "media"
    }


def owner_names(playlist: Playlist, defaults: list[str]) -> list[str]:
    """names of the users who get a playlist, case insensitive, unique"""
    seen: dict[str, str] = {}
    for name in list(defaults) + list(playlist.emby_tags):
        seen.setdefault(name.casefold(), name)
    return list(seen.values())


def plan(
    client: MediaServerClient,
    paths: dict[str, str],
    result: PlaylistSyncResult,
) -> list[Wanted]:
    """every playlist copy Emby should hold"""
    stored = settings.load()
    users = {u["name"].casefold(): u for u in client.users()}
    episodes = client.episode_ids()
    result.known = len(paths)
    result.resolved = sum(
        1 for rel in paths.values() if settings.to_server_path(rel) in episodes
    )
    wanted = []
    for playlist in Playlist.objects.filter(emby_playlist=True).order_by("pk"):
        videos = list(
            playlist.entries.order_by("idx").values_list(
                "youtube_id", flat=True
            )
        )
        known = [v for v in videos if v in paths]
        # an item can be in a playlist once, whatever the TA playlist says
        ids = list(
            dict.fromkeys(
                episodes[server]
                for v in known
                if (server := settings.to_server_path(paths[v])) in episodes
            )
        )
        # a playlist entry that was never downloaded is not "missing"
        missing = len(known) - len(ids)
        for name in owner_names(playlist, stored["playlist_default_users"]):
            user = users.get(name.casefold())
            if user is None:
                if name.casefold() not in {
                    t.casefold() for t in playlist.emby_tags
                }:
                    result.warnings.append(f"Emby has no user {name}")
                continue
            wanted.append(
                Wanted(
                    playlist.pk,
                    playlist.name,
                    user["id"],
                    user["name"],
                    ids,
                    missing,
                )
            )
    return wanted


def _make_current(
    client: MediaServerClient,
    link: EmbyPlaylistLink,
    want: Wanted,
    reader: str,
    dry_run: bool,
    allow_large: bool = False,
) -> str:
    """
    bring the playlist behind link to want; created/changed/unchanged;
    LargeRemoval if that would take more than half of it away (Emby
    still scanning, a wrong path) unless allow_large
    """
    current = client.playlist_items(link.emby_id, reader)
    items = [item for _, item in current]
    if items == want.item_ids:
        return "unchanged"
    wanted = set(want.item_ids)
    going = sum(1 for _, item in current if item not in wanted)
    if going * 2 > len(current) and not allow_large:
        raise LargeRemoval(f"{going} of its {len(current)} entries")
    if dry_run:
        return "changed"
    kept_items: list[str] = []
    drop = []
    for entry, item in current:
        if item in wanted and item not in kept_items:
            kept_items.append(item)
        else:
            drop.append(entry)
    missing = [i for i in want.item_ids if i not in kept_items]
    if kept_items + missing == want.item_ids:
        # the order is right once the extras are gone and the rest appended
        if drop:
            client.remove_from_playlist(link.emby_id, drop)
        if missing:
            client.add_to_playlist(link.emby_id, want.owner, missing)
    else:
        client.remove_from_playlist(link.emby_id, [e for e, _ in current])
        client.add_to_playlist(link.emby_id, want.owner, want.item_ids)
    return "changed"


def _reader(users: list[dict], owner: str) -> str:
    """who reads a playlist: an administrator sees every item, the owner
    only the ones their access rules allow"""
    admin = next((u["id"] for u in users if u["admin"]), None)
    return admin or owner


def _ours(client: MediaServerClient, link: EmbyPlaylistLink) -> bool:
    """
    the Emby item behind a link is still the playlist TA made: the owner
    sees it (only the owner does), it is a playlist, and it has the name
    TA last gave it. An Emby database that was rebuilt or restored can
    hand the id to anything else.
    """
    info = client.owned_item(link.user_id, link.emby_id)
    return (
        bool(info)
        and info["type"] == "Playlist"
        and (info["name"] == link.name)
    )


def _vetted_link(
    client: MediaServerClient,
    want: Wanted,
    result: PlaylistSyncResult,
    dry_run: bool,
) -> EmbyPlaylistLink | None:
    """the link of a playlist copy, None if there is none or it isn't ours"""
    link = EmbyPlaylistLink.objects.filter(
        playlist_id=want.playlist_id, user_id=want.owner
    ).first()
    if link and not _ours(client, link):
        result.warnings.append(
            f"{want.name} for {want.owner_name}: Emby item {link.emby_id} "
            "is not the playlist TA made, link dropped, nothing touched"
        )
        if not dry_run:
            link.delete()
        return None
    return link


def _update(
    client: MediaServerClient,
    link: EmbyPlaylistLink,
    want: Wanted,
    reader: str,
    result: PlaylistSyncResult,
    flags: tuple[bool, bool],
) -> bool:
    """bring an existing copy up to date; False if it is gone"""
    dry_run, allow_large = flags
    label = f"{want.name} for {want.owner_name}"
    try:
        outcome = _make_current(
            client, link, want, reader, dry_run, allow_large
        )
    except MediaServerNotFound:
        link.delete()
        return False
    except LargeRemoval as err:
        result.warnings.append(
            f"{label}: held back, {err}; "
            "ta_emby_playlists --allow-large-removal if that is right"
        )
        return True
    if outcome == "changed":
        result.changed.append(label)
    else:
        result.unchanged += 1
    return True


def _sync_one(
    client: MediaServerClient,
    want: Wanted,
    reader: str,
    result: PlaylistSyncResult,
    dry_run: bool,
    allow_large: bool = False,
) -> None:
    label = f"{want.name} for {want.owner_name}"
    link = _vetted_link(client, want, result, dry_run)
    if link and link.name != want.name:
        # no rename call: recreate under the new name
        result.changed.append(f"recreate {link.name} -> {label}")
        if dry_run:
            return
        _delete(client, link)
        link = None
    if link and _update(
        client, link, want, reader, result, (dry_run, allow_large)
    ):
        return
    if not want.item_ids:
        return
    result.created.append(label)
    if dry_run:
        return
    emby_id = client.create_playlist(want.name, want.owner, want.item_ids)
    EmbyPlaylistLink.objects.update_or_create(
        playlist_id=want.playlist_id,
        user_id=want.owner,
        defaults={"emby_id": emby_id, "name": want.name},
    )


def _delete(client: MediaServerClient, link: EmbyPlaylistLink) -> None:
    try:
        client.delete_item(link.emby_id)
    except MediaServerNotFound:
        pass
    link.delete()


def sync_playlists(
    client: MediaServerClient,
    paths: dict[str, str],
    dry_run: bool = False,
    allow_large_removal: bool = False,
) -> PlaylistSyncResult:
    """
    one run over every playlist; a failing playlist doesn't stop the rest.
    Held back as a whole while Emby knows well under half of the files of
    the tree (it is still scanning, or path_to is wrong), because every
    playlist would shrink.
    """
    result = PlaylistSyncResult()
    wanted = plan(client, paths, result)
    if result.known and result.resolved * 2 < result.known:
        result.skipped = (
            f"Emby knows {result.resolved} of {result.known} files of the "
            "tree, waiting for its scan"
        )
        return result
    users = client.users()
    result.missing = sum(w.missing for w in wanted)
    keep = set()
    for want in wanted:
        # a playlist with no episode in Emby is not created; one that
        # lost them all is emptied rather than deleted
        keep.add((want.playlist_id, want.owner))
        try:
            _sync_one(
                client,
                want,
                _reader(users, want.owner),
                result,
                dry_run,
                allow_large_removal,
            )
        except MediaServerError as err:
            result.errors.append(f"{want.name} for {want.owner_name}: {err}")
    _remove_stale(client, result, keep, dry_run)
    return result


def _remove_stale(
    client: MediaServerClient,
    result: PlaylistSyncResult,
    keep: set[tuple[str, str]],
    dry_run: bool,
) -> None:
    """delete the copies TA made of playlists that no longer want one"""
    for link in EmbyPlaylistLink.objects.order_by("pk"):
        if (link.playlist_id, link.user_id) in keep:
            continue
        try:
            if not _ours(client, link):
                result.warnings.append(
                    f"{link.name}: Emby item {link.emby_id} is not the "
                    "playlist TA made, link dropped, nothing deleted"
                )
                if not dry_run:
                    link.delete()
                continue
            result.deleted.append(f"{link.name} ({link.user_id})")
            if not dry_run:
                _delete(client, link)
        except MediaServerError as err:
            result.errors.append(f"{link.name}: {err}")


def sync_state() -> dict:
    """outcome of the last runs, read by the health check"""
    document = ConfigDocument.objects.filter(key=STATE_KEY).first()
    return dict(document.value) if document else {}


def record_run(
    result: PlaylistSyncResult | None = None, error: str | None = None
) -> None:
    """remember when the sync last ran and succeeded"""
    now = datetime.now(timezone.utc).isoformat()
    state = sync_state()
    state["last_run"] = now
    state["last_error"] = error
    if result is not None:
        state["last_summary"] = result.summary()
        if not result.errors:
            state["last_success"] = now
        else:
            state["last_error"] = "; ".join(result.errors[:3])
    ConfigDocument.objects.update_or_create(
        key=STATE_KEY, defaults={"value": state}
    )

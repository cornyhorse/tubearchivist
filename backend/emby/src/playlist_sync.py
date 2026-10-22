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


@dataclass
class PlaylistSyncResult:
    """what a run did"""

    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    missing: int = 0
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
    wanted = []
    for playlist in Playlist.objects.filter(emby_playlist=True).order_by("pk"):
        videos = list(
            playlist.entries.order_by("idx").values_list(
                "youtube_id", flat=True
            )
        )
        known = [v for v in videos if v in paths]
        ids = [
            episodes[server]
            for v in known
            if (server := settings.to_server_path(paths[v])) in episodes
        ]
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
) -> str:
    """bring the playlist behind link to want; created/changed/unchanged"""
    current = client.playlist_items(link.emby_id, reader)
    items = [item for _, item in current]
    if items == want.item_ids:
        return "unchanged"
    if dry_run:
        return "changed"
    wanted = set(want.item_ids)
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


def _sync_one(
    client: MediaServerClient,
    want: Wanted,
    reader: str,
    result: PlaylistSyncResult,
    dry_run: bool,
) -> None:
    label = f"{want.name} for {want.owner_name}"
    link = EmbyPlaylistLink.objects.filter(
        playlist_id=want.playlist_id, user_id=want.owner
    ).first()
    if link and link.name != want.name and not dry_run:
        # no rename call: recreate under the new name
        _delete(client, link)
        link = None
    if link:
        try:
            outcome = _make_current(client, link, want, reader, dry_run)
        except MediaServerNotFound:
            link.delete()
            link = None
        else:
            if outcome == "changed":
                result.changed.append(label)
            else:
                result.unchanged += 1
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
    client: MediaServerClient, paths: dict[str, str], dry_run: bool = False
) -> PlaylistSyncResult:
    """one run over every playlist; a failing playlist doesn't stop the rest"""
    result = PlaylistSyncResult()
    wanted = plan(client, paths, result)
    users = client.users()
    result.missing = sum(w.missing for w in wanted)
    keep = set()
    for want in wanted:
        # a playlist with no episode in Emby is not created; one that
        # lost them all is emptied rather than deleted
        keep.add((want.playlist_id, want.owner))
        try:
            _sync_one(
                client, want, _reader(users, want.owner), result, dry_run
            )
        except MediaServerError as err:
            result.errors.append(f"{want.name} for {want.owner_name}: {err}")
    for link in EmbyPlaylistLink.objects.order_by("pk"):
        if (link.playlist_id, link.user_id) in keep:
            continue
        result.deleted.append(f"{link.name} ({link.user_id})")
        if dry_run:
            continue
        try:
            _delete(client, link)
        except MediaServerError as err:
            result.errors.append(f"{link.name}: {err}")
    return result


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

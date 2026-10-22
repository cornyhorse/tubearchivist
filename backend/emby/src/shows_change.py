"""
Functionality:
- describe what the move to layout 3 does to the Emby library, for the
  dry run of ta_emby_migrate_layout: shows that disappear, episodes that
  appear in "Other videos"
- list the items a person has watched (or started) that would be removed
  from Emby: a removed or moved file is a new item there, and the watched
  state of the old one is lost. A read only query of the media server.
"""

from dataclasses import dataclass, field

from emby.src.writer import SyncReport
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError

SIDECARS = (".nfo", ".jpg")
OTHER_PREFIX = "channels/Other videos [other]/"


@dataclass
class ShowsChange:
    """the shows the sync removes and the episodes it creates"""

    removed_shows: dict[str, int] = field(default_factory=dict)
    removed_media: list[str] = field(default_factory=list)
    # media files that move (their old paths): Emby normally keeps the
    # watch state of a renamed file, which is not certain
    renamed_media: list[str] = field(default_factory=list)
    other_created: int = 0

    @property
    def removed_playlist_shows(self) -> int:
        """playlist shows that go"""
        return sum(1 for s in self.removed_shows if s.startswith("playlists/"))

    @property
    def removed_channel_shows(self) -> int:
        """channel shows that go (their episodes are in Other videos)"""
        return len(self.removed_shows) - self.removed_playlist_shows


def _is_media(path: str) -> bool:
    return not path.endswith("/") and not path.endswith(SIDECARS)


def describe(report: SyncReport) -> ShowsChange:
    """what a sync report does to the shows"""
    change = ShowsChange()
    removed = [p.rstrip("/") for p in report.deleted if p.endswith("/")]
    show_dirs = {p for p in removed if p.count("/") == 1}
    for folder in sorted(show_dirs):
        change.removed_shows[folder] = 0
    for path in report.deleted:
        if not _is_media(path):
            continue
        folder = "/".join(path.split("/")[:2])
        if folder in change.removed_shows:
            change.removed_shows[folder] += 1
            change.removed_media.append(path)
    change.renamed_media = [old for old, _ in report.renamed if _is_media(old)]
    change.other_created = sum(
        1
        for p in report.created
        if p.startswith(OTHER_PREFIX) and _is_media(p)
    )
    return change


def watch_history_at_risk(
    relpaths: list[str],
) -> tuple[dict[str, list[str]], str | None]:
    """
    {user name: relpaths of removed media they played or started}, and an
    error text if the media server could not be read
    """
    if not relpaths:
        return {}, None
    conn = settings.connection()
    if not conn:
        return {}, "no media server configured"
    server_paths = {settings.to_server_path(p): p for p in relpaths}
    client = MediaServerClient(conn)
    at_risk: dict[str, list[str]] = {}
    try:
        for user in client.users():
            hits = sorted(
                server_paths[p]
                for p in client.watched_paths(user["id"])
                if p in server_paths
            )
            if hits:
                at_risk[user["name"]] = hits
    except MediaServerError as err:
        return {}, str(err)
    return at_risk, None

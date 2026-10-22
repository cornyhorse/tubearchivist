"""
Functionality:
- who can see what, before and after a change of tags or layout: the
  videos a tag opens in Emby now (the shows carrying it, a read only
  query) against the videos TA will tag for it (the channel's tags and
  those of the playlists the video is in, see emby/src/sync.py extra_tags)
- printed by the dry runs of ta_emby_import_tags and
  ta_emby_migrate_layout, so a video someone loses is seen before it
  happens
- Emby items outside the TA tree (the hand made YT-Approved folder) can't
  be matched to a video; they are counted, not compared
"""

from dataclasses import dataclass, field

from channel.models import Channel
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient
from playlist.models import PlaylistEntry
from video.models import Video


@dataclass
class Visibility:
    """what one tag opens, before and after"""

    tag: str
    before: int = 0
    after: int = 0
    lost: list[str] = field(default_factory=list)
    gained: int = 0
    outside: int = 0

    def line(self) -> str:
        """one summary line"""
        return (
            f"visible to {self.tag}: {self.before} videos now, "
            f"{self.after} after, lost {len(self.lost)}, gained "
            f"{self.gained}, {self.outside} Emby items outside the TA "
            "tree not compared"
        )


def media_ids(files: dict[str, dict]) -> dict[str, str]:
    """{relpath: youtube_id} of the media files in a manifest"""
    return {
        relpath: entry["owner"].rsplit("/", 1)[-1]
        for relpath, entry in files.items()
        if entry["role"] == "media"
    }


def emby_visible(
    client: MediaServerClient, tag: str, by_relpath: dict[str, str]
) -> tuple[set[str], int]:
    """(youtube ids of the episodes of the shows with the tag, others)"""
    ids: set[str] = set()
    outside = 0
    for show in client.series_with_tag(tag):
        for path in client.series_episode_paths(show["id"]):
            relpath = settings.from_server_path(path)
            if relpath in by_relpath:
                ids.add(by_relpath[relpath])
            else:
                outside += 1
    return ids, outside


def ta_visible(tag: str) -> set[str]:
    """youtube ids TA tags for a tag: its channels' and its playlists'"""
    folded = tag.casefold()
    channels = [
        c.pk
        for c in Channel.objects.exclude(emby_tags=[])
        if any(t.casefold() == folded for t in c.emby_tags)
    ]
    ids = set(
        Video.objects.filter(active=True, channel_id__in=channels).values_list(
            "youtube_id", flat=True
        )
    )
    entries = PlaylistEntry.objects.exclude(playlist__emby_tags=[])
    wanted = [
        e.youtube_id
        for e in entries.select_related("playlist")
        if any(t.casefold() == folded for t in e.playlist.emby_tags)
    ]
    ids |= set(
        Video.objects.filter(active=True, youtube_id__in=wanted).values_list(
            "youtube_id", flat=True
        )
    )
    return ids


def before_state(
    client: MediaServerClient, tags: list[str], files: dict[str, dict]
) -> dict[str, tuple[set[str], int]]:
    """what Emby shows for each tag right now"""
    by_relpath = media_ids(files)
    return {tag: emby_visible(client, tag, by_relpath) for tag in tags}


def compare(before: dict[str, tuple[set[str], int]]) -> list[Visibility]:
    """the before state against what TA tags now (call after the change)"""
    results = []
    for tag, (now, outside) in before.items():
        after = ta_visible(tag)
        results.append(
            Visibility(
                tag=tag,
                before=len(now),
                after=len(after),
                lost=sorted(now - after),
                gained=len(after - now),
                outside=outside,
            )
        )
    return results


def titles(youtube_ids: list[str]) -> dict[str, str]:
    """{youtube_id: title} for listing lost videos"""
    return dict(
        Video.objects.filter(youtube_id__in=youtube_ids).values_list(
            "youtube_id", "title"
        )
    )


def write_report(
    results: list[Visibility], write, prefix: str = "", limit: int = 30
) -> None:
    """lines for the dry runs; write is stdout.write"""
    for result in results:
        write(f"{prefix}{result.line()}")
        names = titles(result.lost[:limit])
        for youtube_id in result.lost[:limit]:
            write(f"{prefix}  LOST {youtube_id} {names.get(youtube_id, '')}")
        if len(result.lost) > limit:
            write(f"{prefix}  ... and {len(result.lost) - limit} more")

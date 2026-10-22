"""
Functionality:
- one-time import of the Emby tags that were set by hand: read the shows of
  an Emby tag (read only) and map each onto a TA channel or playlist
- a show maps by the YouTube id in its provider ids, else by the [id] in its
  folder name, else by an exact name; anything else is left out and listed,
  and --map NAME=ID decides it by hand
- applying adds the tags to emby_tags (never removes), the next Emby write
  puts them in the tvshow.nfo files
"""

import re
from dataclasses import dataclass

from channel.models import Channel
from emby.src.tags import clean_tags, set_tags
from mediaserver.src import pending, settings
from mediaserver.src.client import MediaServerClient
from playlist.models import Playlist

_FOLDER_ID = re.compile(r"\[([^\]/]+)\]/?$")


@dataclass(frozen=True)
class TagMapping:
    """one Emby show and where its tags go"""

    emby_name: str
    emby_path: str
    tags: tuple[str, ...]
    kind: str | None  # "channel", "playlist" or None: not mapped
    target_id: str | None
    target_name: str | None
    how: str  # provider id, folder id, name, manual or why not mapped


def _lookup(target_id: str) -> tuple[str, str, str] | None:
    """(kind, id, name) of the channel or playlist with this id"""
    channel = Channel.objects.filter(pk=target_id).first()
    if channel:
        return "channel", channel.pk, channel.name
    playlist = Playlist.objects.filter(pk=target_id).first()
    if playlist:
        return "playlist", playlist.pk, playlist.name
    return None


def _by_name(name: str) -> tuple[str, str, str] | str:
    """the one channel or playlist called name, else why not"""
    folded = name.casefold()
    found = [
        ("channel", c.pk, c.name)
        for c in Channel.objects.all()
        if c.name.casefold() == folded
    ] + [
        ("playlist", p.pk, p.name)
        for p in Playlist.objects.all()
        if p.name.casefold() == folded
    ]
    if len(found) == 1:
        return found[0]
    return "no match by name" if not found else "several matches by name"


def _resolve(
    show: dict, manual: dict[str, str]
) -> tuple[tuple[str, str, str] | None, str]:
    """(target, how) for an Emby show"""
    if show["name"] in manual:
        target = _lookup(manual[show["name"]])
        return (target, "manual") if target else (None, "manual id unknown")
    provider_id = next(
        (
            value
            for key, value in show["provider_ids"].items()
            if key.casefold() == "youtube"
        ),
        None,
    )
    if provider_id and (target := _lookup(provider_id)):
        return target, "provider id"
    folder = _FOLDER_ID.search(show["path"])
    if folder and (target := _lookup(folder[1])):
        return target, "folder id"
    named = _by_name(show["name"])
    if isinstance(named, str):
        return None, named
    return named, "name"


def plan_import(
    client: MediaServerClient,
    tags: list[str],
    manual: dict[str, str] | None = None,
) -> list[TagMapping]:
    """map the shows of each tag; a show with several wanted tags once"""
    shows: dict[str, dict] = {}
    for tag in tags:
        for show in client.series_with_tag(tag):
            entry = shows.setdefault(show["path"], {**show, "tags": set()})
            entry["tags"].add(tag)
    plan = []
    for show in sorted(shows.values(), key=lambda s: s["name"].casefold()):
        target, how = _resolve(show, manual or {})
        plan.append(
            TagMapping(
                emby_name=show["name"],
                emby_path=show["path"],
                tags=tuple(clean_tags(list(show["tags"]))),
                kind=target[0] if target else None,
                target_id=target[1] if target else None,
                target_name=target[2] if target else None,
                how=how,
            )
        )
    return plan


def apply_import(plan: list[TagMapping]) -> dict[str, list[str]]:
    """
    add the tags to their channels and playlists; returns the changed ones
    as "<kind>/<id>": tags after the import. Several shows on one target
    (a channel and its YT-Approved folder) merge.
    """
    wanted: dict[tuple[str, str], set[str]] = {}
    for item in plan:
        if item.kind and item.target_id:
            wanted.setdefault((item.kind, item.target_id), set()).update(
                item.tags
            )
    changed = {}
    for (kind, target_id), tags in wanted.items():
        model = Channel if kind == "channel" else Playlist
        current = list(model.objects.get(pk=target_id).emby_tags)
        merged = clean_tags(current + sorted(tags))
        if merged != current:
            set_tags(kind, [target_id], merged)
            changed[f"{kind}/{target_id}"] = merged
    return changed


def tag_drift(client: MediaServerClient, tags: list[str]) -> list[str]:
    """
    problems for the Emby shows of TA's tree that carry a tag set by hand
    which TA does not write: the next refresh of the show sets its tags to
    exactly what TA writes and takes the tag away. A show whose refresh is
    already waiting is the normal moment after a change in TA, not drift.
    """
    waiting = set(pending.entries())
    problems = []
    for item in plan_import(client, tags):
        relpath = settings.from_server_path(item.emby_path)
        if relpath is None or f"series:{relpath}" in waiting:
            continue
        if not item.kind:
            problems.append(
                f"Emby show {item.emby_name} is tagged {', '.join(item.tags)}"
                " by hand and matches no channel or playlist of TA"
            )
            continue
        have = {t.casefold() for t in _tags_of(item.kind, item.target_id)}
        missing = [t for t in item.tags if t.casefold() not in have]
        if missing:
            problems.append(
                f"Emby show {item.emby_name} is tagged {', '.join(missing)} "
                f"by hand but TA does not tag {item.kind} {item.target_id}: "
                "the tag is removed at the show's next refresh; tag it in "
                "TA (ta_emby_import_tags)"
            )
    return problems


def _tags_of(kind: str, target_id: str) -> list[str]:
    model = Channel if kind == "channel" else Playlist
    return list(model.objects.get(pk=target_id).emby_tags)

"""
Functionality:
- Emby access tags of channels and playlists: written by the Emby writer as
  <tag> in the tvshow.nfo, so access survives library rebuilds and metadata
  refreshes
- no tags means hidden from tag-restricted Emby users (allow list)
- the tag names offered in the UI: the media server setting known_tags plus
  every tag in use
"""

from channel.models import Channel
from mediaserver.src import settings as media_settings
from playlist.models import Playlist

MODELS = {"channel": Channel, "playlist": Playlist}
MAX_TAGS = 20
MAX_TAG_LENGTH = 64


def clean_tags(tags: list[str]) -> list[str]:
    """
    tags without surrounding space, empty ones and duplicates (case
    insensitive, first spelling wins), sorted
    """
    seen: dict[str, str] = {}
    for tag in tags:
        tag = " ".join(str(tag).split())[:MAX_TAG_LENGTH]
        if tag:
            seen.setdefault(tag.casefold(), tag)
    return sorted(seen.values(), key=str.casefold)[:MAX_TAGS]


def set_tags(kind: str, ids: list[str], tags: list[str]) -> list[str]:
    """set the tags of channels or playlists, the ids changed"""
    model = MODELS[kind]
    value = clean_tags(tags)
    found = list(model.objects.filter(pk__in=ids).values_list("pk", flat=True))
    model.objects.filter(pk__in=found).update(emby_tags=value)
    return found


def known_tags() -> list[str]:
    """tag names for the UI: the configured ones and those in use"""
    used = []
    for model in MODELS.values():
        for tags in model.objects.exclude(emby_tags=[]).values_list(
            "emby_tags", flat=True
        ):
            used.extend(tags)
    return clean_tags(media_settings.load()["known_tags"] + used)

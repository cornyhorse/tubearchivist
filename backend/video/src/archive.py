"""
Functionality:
- ids of archived videos and channels: files on disk with no YouTube
  metadata (docs/fork/archive-import.md)
- read the optional sidecars next to a media file: Emby/Kodi .nfo,
  yt-dlp .info.json, and a thumbnail image
- pure helpers: no database and no network
"""

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

from django.utils.text import slugify

# a YouTube video id is 11 characters and a channel id starts with UC, so
# neither can ever start with this
ARCHIVE_PREFIX = "arch_"

MEDIA_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mkv",
        ".webm",
        ".mov",
        ".avi",
        ".flv",
        ".wmv",
        ".mpg",
        ".mpeg",
        ".ts",
        ".m2ts",
        ".3gp",
    }
)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_DIGITS = re.compile(r"\d{4}-\d{2}-\d{2}|\d{8}")


def is_archive_id(item_id: str | None) -> bool:
    """an id made by the archive import, never a YouTube id"""
    return bool(item_id) and item_id.startswith(ARCHIVE_PREFIX)


def _normalize(value: str) -> str:
    """unicode composed form: macOS and Linux disagree on file names"""
    return unicodedata.normalize("NFC", value)


def channel_slug(name: str) -> str:
    """folder and id safe form of a channel name"""
    slug = slugify(_normalize(name))[:40].strip("-_")
    if slug:
        return slug
    return hashlib.sha1(_normalize(name).encode()).hexdigest()[:12]


def archive_channel_id(name: str) -> str:
    """stable id of an archived channel, from its name"""
    return f"{ARCHIVE_PREFIX}{channel_slug(name)}"


def archive_video_id(channel_id: str, file_name: str) -> str:
    """
    stable id of an archived video: the channel and the file name decide it,
    not where the file is or how it was placed, so a re-run finds the same
    ids; 16 hex digits make a collision in one channel practically
    impossible
    """
    key = f"{channel_id}\0{_normalize(file_name)}".encode()
    return f"{ARCHIVE_PREFIX}{hashlib.sha256(key).hexdigest()[:16]}"


def find_media(root: Path) -> list[Path]:
    """media files under root, sorted; hidden and @eaDir folders skipped"""
    found = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(p.startswith(".") or p == "@eaDir" for p in parts):
            continue
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            found.append(path)

    return found


def find_thumbnail(media: Path) -> Path | None:
    """<name>.jpg, <name>-thumb.jpg or <name>.thumb.jpg next to the media"""
    for stem in (media.stem, f"{media.stem}-thumb", f"{media.stem}.thumb"):
        for ext in IMAGE_EXTENSIONS:
            candidate = media.with_name(stem + ext)
            if candidate.is_file():
                return candidate

    return None


def _parse_date(value) -> datetime | None:
    """YYYY-MM-DD or YYYYMMDD (a longer date time keeps its first ten)"""
    match = _DIGITS.match(str(value or "").strip())
    if not match:
        return None

    text = match.group(0).replace("-", "")
    try:
        parsed = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None

    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def _text(root: ET.Element, *tags: str) -> str:
    """first non empty text of the tags, in order"""
    for tag in tags:
        element = root.find(tag)
        if element is not None and (element.text or "").strip():
            return element.text.strip()

    return ""


def read_nfo(path: Path) -> dict:
    """title, description, published, tags of an Emby/Kodi .nfo, or {}"""
    try:
        root = ET.parse(path).getroot()  # nosec B314 - local, own files
    except (ET.ParseError, OSError):
        return {}

    tags = [
        (i.text or "").strip()
        for i in [*root.findall("genre"), *root.findall("tag")]
        if (i.text or "").strip()
    ]
    published = None
    for tag in ("aired", "premiered", "releasedate", "dateadded"):
        published = _parse_date(_text(root, tag))
        if published:
            break

    if not published and _text(root, "year").isdigit():
        published = _parse_date(f"{_text(root, 'year')}0101")

    return {
        "title": _text(root, "title", "originaltitle"),
        "description": _text(root, "plot", "outline"),
        "published": published,
        "tags": list(dict.fromkeys(tags)),
    }


def read_info_json(path: Path) -> dict:
    """the same fields of a yt-dlp .info.json, or {}"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    published = None
    timestamp = data.get("timestamp")
    if isinstance(timestamp, (int, float)):
        published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if not published:
        published = _parse_date(data.get("upload_date"))

    tags = data.get("tags")
    return {
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "published": published,
        "tags": [str(i) for i in tags] if isinstance(tags, list) else [],
    }


def read_sidecar(media: Path) -> dict:
    """
    metadata next to a media file; the .nfo wins over the .info.json,
    field by field. Returns title, description, published and tags, with
    empty values when no sidecar has them.
    """
    sources = [
        read_nfo(media.with_suffix(".nfo")),
        read_info_json(media.with_suffix(".info.json")),
    ]
    merged: dict = {
        "title": "",
        "description": "",
        "published": None,
        "tags": [],
    }
    for source in sources:
        for key, value in source.items():
            if value and not merged[key]:
                merged[key] = value

    return merged

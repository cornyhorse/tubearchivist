"""
Loose collection of helper functions
- don't import AppConfig class here to avoid circular imports
"""

import os
import random
import string
import subprocess
from datetime import datetime, timezone
from time import sleep
from typing import Any
from urllib.parse import urlparse

import requests


def ignore_filelist(filelist: list[str]) -> list[str]:
    """ignore temp files for os.listdir sanitizer"""
    to_ignore = [
        "@eaDir",
        "Icon\r\r",
        "Network Trash Folder",
        "Temporary Items",
    ]
    cleaned: list[str] = []
    for file_name in filelist:
        if file_name.startswith(".") or file_name in to_ignore:
            continue

        cleaned.append(file_name)

    return cleaned


def randomizor(length: int) -> str:
    """generate random alpha numeric string"""
    pool: str = string.digits + string.ascii_letters
    return "".join(random.choice(pool) for i in range(length))


def rand_sleep(config) -> None:
    """randomized sleep based on config"""
    sleep_config = config["downloads"].get("sleep_interval")
    if not sleep_config:
        return

    secs = random.randrange(int(sleep_config * 0.5), int(sleep_config * 1.5))
    sleep(secs)


def requests_headers() -> dict[str, str]:
    """build header with random user agent for requests outside of yt-dlp"""

    chrome_versions = (
        "90.0.4430.212",
        "90.0.4430.24",
        "90.0.4430.70",
        "90.0.4430.72",
        "90.0.4430.85",
        "90.0.4430.93",
        "91.0.4472.101",
        "91.0.4472.106",
        "91.0.4472.114",
        "91.0.4472.124",
        "91.0.4472.164",
        "91.0.4472.19",
        "91.0.4472.77",
        "92.0.4515.107",
        "92.0.4515.115",
        "92.0.4515.131",
        "92.0.4515.159",
        "92.0.4515.43",
        "93.0.4556.0",
        "93.0.4577.15",
        "93.0.4577.63",
        "93.0.4577.82",
        "94.0.4606.41",
        "94.0.4606.54",
        "94.0.4606.61",
        "94.0.4606.71",
        "94.0.4606.81",
        "94.0.4606.85",
        "95.0.4638.17",
        "95.0.4638.50",
        "95.0.4638.54",
        "95.0.4638.69",
        "95.0.4638.74",
        "96.0.4664.18",
        "96.0.4664.45",
        "96.0.4664.55",
        "96.0.4664.93",
        "97.0.4692.20",
    )
    template = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        + "AppleWebKit/537.36 (KHTML, like Gecko) "
        + f"Chrome/{random.choice(chrome_versions)} Safari/537.36"
    )

    return {"User-Agent": template}


def date_parser(timestamp: int | float | str | None) -> str | None:
    """return formatted date string"""
    if timestamp is None:
        return None

    if isinstance(timestamp, int):
        date_obj = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    elif isinstance(timestamp, float):
        date_obj = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    elif isinstance(timestamp, str) and timestamp.isdigit():
        date_obj = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    elif isinstance(timestamp, str):
        date_obj = datetime.strptime(timestamp, "%Y-%m-%d")
        date_obj = date_obj.replace(tzinfo=timezone.utc)
    else:
        raise TypeError(f"invalid timestamp: {timestamp}")

    return date_obj.isoformat()


def time_parser(timestamp: str) -> float:
    """return seconds from timestamp, false on empty"""
    if not timestamp:
        return False

    if timestamp.isnumeric():
        return int(timestamp)

    hours, minutes, seconds = timestamp.split(":", maxsplit=3)
    return int(hours) * 60 * 60 + int(minutes) * 60 + float(seconds)


def deep_merge(target: dict, source: dict) -> None:
    """inplace nested dict merge, recursive"""
    for key, value in source.items():
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            deep_merge(target[key], value)
        else:
            target[key] = value


def clear_dl_cache(cache_dir: str) -> int:
    """clear leftover files from dl cache"""
    print("clear download cache")
    download_cache_dir = os.path.join(cache_dir, "download")
    leftover_files = ignore_filelist(os.listdir(download_cache_dir))
    for cached in leftover_files:
        to_delete = os.path.join(download_cache_dir, cached)
        os.remove(to_delete)

    return len(leftover_files)


def is_shorts(youtube_id: str) -> bool:
    """check if youtube_id is a shorts video, bot not it it's not a shorts"""
    shorts_url = f"https://www.youtube.com/shorts/{youtube_id}"
    cookies = {"SOCS": "CAI"}
    try:
        response = requests.head(
            shorts_url, cookies=cookies, headers=requests_headers(), timeout=10
        )
    except requests.exceptions.RequestException:
        # assume video on error
        return False

    return response.status_code == 200


def get_duration_sec(file_path: str) -> int:
    """get duration of media file from file path"""

    duration = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        capture_output=True,
        check=True,
    )
    duration_raw = duration.stdout.decode().strip()
    if duration_raw == "N/A":
        return 0

    duration_sec = int(float(duration_raw))
    return duration_sec


def get_duration_str(seconds: int | float) -> str:
    """Return a human-readable duration string from seconds."""
    if not seconds:
        return "NA"

    seconds = int(seconds)
    units = [("y", 31536000), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    duration_parts = []

    for unit_label, unit_seconds in units:
        if seconds >= unit_seconds:
            unit_count, seconds = divmod(seconds, unit_seconds)
            duration_parts.append(f"{unit_count:02}{unit_label}")

    duration_parts[0] = duration_parts[0].lstrip("0")

    return " ".join(duration_parts)


def ta_host_parser(ta_host: str) -> tuple[list[str], list[str]]:
    """parse ta_host env var for ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS"""
    allowed_hosts: list[str] = [
        "localhost",
        "tubearchivist",
    ]
    csrf_trusted_origins: list[str] = [
        "http://localhost",
        "http://tubearchivist",
    ]
    for host in ta_host.split():
        host_clean = host.strip()
        if not host_clean.startswith("http"):
            host_clean = f"http://{host_clean}"

        parsed = urlparse(host_clean)
        allowed_hosts.append(f"{parsed.hostname}")
        cors_url = f"{parsed.scheme}://{parsed.hostname}"

        if parsed.port:
            cors_url = f"{cors_url}:{parsed.port}"

        csrf_trusted_origins.append(cors_url)

    return allowed_hosts, csrf_trusted_origins


def get_stylesheets() -> list:
    """Get all valid stylesheets from /static/css"""

    stylesheets = [
        "dark.css",
        "light.css",
        "matrix.css",
        "midnight.css",
        "custom.css",
    ]
    return stylesheets


def check_stylesheet(stylesheet: str):
    """Check if a stylesheet exists. Return dark.css as a fallback"""
    if stylesheet in get_stylesheets():
        return stylesheet

    return "dark.css"


def _project(doc: dict, source: list[str] | None) -> dict:
    """keep only source keys, like ES _source filtering"""
    if not source:
        return doc
    return {key: doc[key] for key in source if key in doc}


def is_missing(
    to_check: str | list[str],
    index_name: str = "ta_video,ta_download",
    on_key: str = "youtube_id",
) -> list[str]:
    """id or list of ids that are missing from index_name"""
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel
    from download.models import Download
    from playlist.models import Playlist
    from video.models import Video

    if isinstance(to_check, str):
        to_check = [to_check]

    models = {
        "ta_video": Video,
        "ta_download": Download,
        "ta_channel": Channel,
        "ta_playlist": Playlist,
    }
    existing: set[str] = set()
    for index in index_name.split(","):
        model = models[index]
        existing.update(
            model.objects.filter(pk__in=to_check).values_list("pk", flat=True)
        )
    del on_key  # always the primary key of index_name

    return [i for i in to_check if i not in existing]


def get_channel_overwrites() -> dict[str, dict[str, Any]]:
    """get overwrites indexed my channel_id"""
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel

    overwrites = {}
    for channel in Channel.objects.all():
        if channel_overwrites := channel.overwrites:
            overwrites[channel.channel_id] = channel_overwrites

    return overwrites


def get_channels(
    subscribed_only: bool, source: list[str] | None = None
) -> list[dict]:
    """get a list of all channels as documents, sorted by name"""
    # pylint: disable=import-outside-toplevel
    from channel.models import Channel

    channels = Channel.objects.order_by("name", "channel_id")
    if subscribed_only:
        channels = channels.filter(subscribed=True)

    return [_project(channel.to_doc(), source) for channel in channels]


def get_playlists(
    subscribed_only: bool, source: list[str] | None = None
) -> list[dict]:
    """get list of active playlists as documents"""
    # pylint: disable=import-outside-toplevel
    from playlist.models import Playlist

    playlists = Playlist.objects.filter(active=True).order_by(
        "-channel_name", "playlist_id"
    )
    if subscribed_only:
        playlists = playlists.filter(subscribed=True)

    return [_project(playlist.to_doc(), source) for playlist in playlists]


def calc_is_watched(duration: float, position: float) -> bool:
    """considered watched based on duration position"""

    if not duration or duration <= 0:
        return False

    if duration < 60:
        threshold = 0.5
    elif duration > 900:
        threshold = 1 - (180 / duration)
    else:
        threshold = 0.9

    return position >= duration * threshold

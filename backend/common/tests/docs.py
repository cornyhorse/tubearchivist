"""builders for legacy ES documents, shaped like real TA data"""

import copy

BASE_CHANNEL = {
    "channel_id": "UCchannel000000000000001",
    "channel_name": "Test Channel",
    "channel_description": "about the channel",
    "channel_active": True,
    "channel_subscribed": True,
    "channel_subs": 1200,
    "channel_last_refresh": 1785417878,
    "channel_thumb_url": "https://yt3.example/thumb",
    "channel_banner_url": "https://yt3.example/banner",
    "channel_tvart_url": "https://yt3.example/tvart",
    "channel_tags": ["trains", "coasters"],
    "channel_tabs": ["videos", "shorts"],
    "emby_numbering": "date",
}

BASE_VIDEO = {
    "youtube_id": "vid00000001",
    "title": "Riding the Big Coaster",
    "description": "a ride on the coaster",
    "category": ["Travel & Events"],
    "tags": ["roller coaster", "pov"],
    "vid_type": "videos",
    "active": True,
    "published": 1700000000,
    "date_downloaded": 1785420349,
    "vid_last_refresh": 1785420349,
    "media_url": "UCchannel000000000000001/vid00000001.mp4",
    "media_size": 1000,
    "vid_thumb_url": "https://i.ytimg.com/vi/vid00000001/maxresdefault.jpg",
    "player": {"duration": 458, "duration_str": "7m 38s", "watched": False},
    "stats": {
        "view_count": 10,
        "like_count": 2,
        "dislike_count": 0,
        "average_rating": 4.5,
    },
    "streams": [
        {
            "bitrate": 2845275,
            "codec": "av1",
            "height": 1080,
            "index": 0,
            "type": "video",
            "width": 1920,
        },
        {"bitrate": 127999, "codec": "aac", "index": 1, "type": "audio"},
    ],
}

BASE_DOWNLOAD = {
    "youtube_id": "dl000000001",
    "title": "Queued video",
    "channel_id": "UCchannel000000000000001",
    "channel_name": "Test Channel",
    "channel_indexed": True,
    "vid_type": "videos",
    "duration": "3m 2s",
    "published": 1700000000,
    "timestamp": 1785000000,
    "status": "pending",
    "auto_start": False,
    "message": None,
    "vid_thumb_url": "https://i.ytimg.com/vi/dl000000001/maxresdefault.jpg",
}


def channel_doc(**overrides) -> dict:
    """ta_channel _source"""
    doc = copy.deepcopy(BASE_CHANNEL)
    doc.update(overrides)
    return doc


def video_doc(channel: dict | None = None, **overrides) -> dict:
    """ta_video _source with embedded channel"""
    doc = copy.deepcopy(BASE_VIDEO)
    doc["channel"] = copy.deepcopy(channel or BASE_CHANNEL)
    youtube_id = overrides.get("youtube_id")
    if youtube_id:
        doc["media_url"] = f"{doc['channel']['channel_id']}/{youtube_id}.mp4"
    doc.update(overrides)
    return doc


def download_doc(**overrides) -> dict:
    """ta_download _source"""
    doc = copy.deepcopy(BASE_DOWNLOAD)
    doc.update(overrides)
    return doc


def playlist_doc(playlist_id: str, entries: list[str], **overrides) -> dict:
    """ta_playlist _source; entries are youtube ids in order"""
    doc = {
        "playlist_id": playlist_id,
        "playlist_name": f"Playlist {playlist_id}",
        "playlist_description": "a playlist",
        "playlist_channel": "Test Channel",
        "playlist_channel_id": BASE_CHANNEL["channel_id"],
        "playlist_active": True,
        "playlist_subscribed": False,
        "playlist_last_refresh": 1785000000,
        "playlist_sort_order": "top",
        "playlist_thumbnail": "https://i.ytimg.com/pl.jpg",
        "playlist_type": "regular",
        "emby_numbering": "playlist_position",
        "playlist_entries": [
            {
                "youtube_id": youtube_id,
                "idx": idx,
                "title": f"entry {idx}",
                "uploader": "Test Channel",
                "downloaded": False,
            }
            for idx, youtube_id in enumerate(entries)
        ],
    }
    doc.update(overrides)
    return doc


def subtitle_doc(youtube_id: str, index: int, line: str) -> dict:
    """ta_subtitle _source"""
    return {
        "subtitle_fragment_id": f"{youtube_id}-en-{index}",
        "youtube_id": youtube_id,
        "title": "Riding the Big Coaster",
        "subtitle_channel": "Test Channel",
        "subtitle_channel_id": BASE_CHANNEL["channel_id"],
        "subtitle_lang": "en",
        "subtitle_source": "auto",
        "subtitle_index": index,
        "subtitle_start": "00:00:01.000",
        "subtitle_end": "00:00:02.000",
        "subtitle_line": line,
        "subtitle_last_refresh": 1785000000,
    }

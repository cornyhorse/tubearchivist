"""stats test fixtures"""

from datetime import datetime, timedelta, timezone

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download
from playlist.models import Playlist
from video.models import Video

CHANNEL_A = "UCchannel00000000000000aa"
CHANNEL_B = "UCchannel00000000000000bb"
CHANNEL_C = "UCchannel00000000000000cc"
OLD = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

# youtube_id, channel, vid_type, active, duration, media_size, watched
VIDEOS = [
    ("vid00000001", CHANNEL_A, "videos", True, 100, 1000, False),
    ("vid00000002", CHANNEL_A, "shorts", True, 50, 500, True),
    ("vid00000003", CHANNEL_B, "videos", False, 200, 3000, False),
    ("vid00000004", CHANNEL_B, "streams", True, 3700, 10000, True),
    ("vid00000005", CHANNEL_C, "videos", True, 10, 20000, False),
]


@pytest.fixture(name="library")
def fixture_library(db):  # pylint: disable=unused-argument
    """three channels, five videos, two playlists, a download queue"""
    channels = {
        CHANNEL_A: channel_doc(
            channel_id=CHANNEL_A, channel_name="coaster fans"
        ),
        CHANNEL_B: channel_doc(
            channel_id=CHANNEL_B,
            channel_name="rail adventures",
            channel_active=False,
            channel_subscribed=False,
        ),
        CHANNEL_C: channel_doc(
            channel_id=CHANNEL_C,
            channel_name="big files",
            channel_subscribed=False,
        ),
    }
    for doc in channels.values():
        Channel.save_doc(doc)

    for video_id, channel, vid_type, active, duration, size, watched in VIDEOS:
        doc = video_doc(
            channel=channels[channel],
            youtube_id=video_id,
            vid_type=vid_type,
            active=active,
            media_size=size,
            date_downloaded=OLD,
        )
        doc["player"].update({"duration": duration, "watched": watched})
        Video.save_doc(doc)

    Playlist.save_doc(playlist_doc("PL1", [], playlist_subscribed=True))
    Playlist.save_doc(playlist_doc("PL2", []))

    for idx, (status, vid_type) in enumerate(
        [
            ("pending", "videos"),
            ("pending", "videos"),
            ("pending", "shorts"),
            ("ignore", "videos"),
        ]
    ):
        Download.upsert_doc(
            download_doc(
                youtube_id=f"dl{idx:09d}", status=status, vid_type=vid_type
            )
        )


@pytest.fixture(name="client")
def fixture_client(db):  # pylint: disable=unused-argument
    """API client logged in as a regular user"""
    # pylint: disable=import-outside-toplevel
    from common.src.helper import randomizor
    from rest_framework.test import APIClient
    from user.models import Account

    client = APIClient()
    client.force_authenticate(
        user=Account.objects.create_user("viewer", randomizor(16))
    )
    return client

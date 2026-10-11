"""tests for PendingList: extracting urls into the download queue"""

import copy

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, download_doc, video_doc
from download.models import Download, DownloadRule
from download.src import queue
from download.src.queue import PendingList
from download.tests.helpers import FakeTask
from video.models import Video
from video.src.constants import VideoTypeEnum

CHANNEL_ID = "UCchannel000000000000001"


def yt_meta(youtube_id, **overrides) -> dict:
    """video metadata as yt-dlp returns it"""
    meta = {
        "id": youtube_id,
        "title": f"Video {youtube_id}",
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "duration": 600,
        "timestamp": 1700000000,
        "thumbnail": f"https://i.ytimg.com/vi/{youtube_id}/max.jpg",
        "width": 1920,
        "height": 1080,
    }
    meta.update(overrides)
    return meta


class FakeVideo:
    """YoutubeVideo stand in, metadata from FakeVideo.metas"""

    metas: dict = {}

    def __init__(self, youtube_id):
        self.youtube_id = youtube_id
        self.youtube_meta = None
        self.error = None

    def get_from_youtube(self):
        """copy of the registered metadata"""
        meta = self.metas.get(self.youtube_id)
        self.youtube_meta = copy.deepcopy(meta)
        if not meta:
            self.error = "Video unavailable"


class FakeChannel:
    """YoutubeChannel stand in"""

    json_data: dict | None = None

    def __init__(self, channel_id):
        self.channel_id = channel_id

    def build_json(self, upload=False):  # pylint: disable=unused-argument
        """nothing to build"""


class FakePlaylist:
    """YoutubePlaylist stand in"""

    youtube_meta: dict | None = None

    def __init__(self, playlist_id):
        self.playlist_id = playlist_id
        self.json_data = {"playlist_name": f"Playlist {playlist_id}"}

    def update_playlist(self):
        """nothing to update"""


class FakeThumbs:
    """ThumbManager stand in, records downloads"""

    downloaded: list = []

    def __init__(self, item_id):
        self.item_id = item_id

    def download_video_thumb(self, url):
        """record"""
        self.downloaded.append((self.item_id, url))


@pytest.fixture(name="yt")
def fixture_yt(monkeypatch, app_config):  # pylint: disable=unused-argument
    """fake every remote call PendingList makes"""

    class Remote:
        """registry of remote data"""

        channel_videos: list = []
        channel_calls: list = []
        shorts: set = set()

    remote = Remote()
    FakeVideo.metas = {}
    FakeChannel.json_data = {
        "channel_id": CHANNEL_ID,
        "channel_name": "Test Channel",
    }
    FakePlaylist.youtube_meta = None
    FakeThumbs.downloaded = []

    def last_videos(channel_id, config, limit, query_filter):
        remote.channel_calls.append((channel_id, limit, query_filter))
        return copy.deepcopy(remote.channel_videos)

    monkeypatch.setattr(queue, "YoutubeVideo", FakeVideo)
    monkeypatch.setattr(queue, "YoutubeChannel", FakeChannel)
    monkeypatch.setattr(queue, "YoutubePlaylist", FakePlaylist)
    monkeypatch.setattr(queue, "ThumbManager", FakeThumbs)
    monkeypatch.setattr(queue, "get_last_channel_videos", last_videos)
    monkeypatch.setattr(queue, "is_shorts", lambda i: i in remote.shorts)
    remote.videos = FakeVideo.metas
    return remote


def video_url(youtube_id, vid_type=VideoTypeEnum.VIDEOS) -> dict:
    """parsed video url"""
    return {"type": "video", "url": youtube_id, "vid_type": vid_type}


def queued(youtube_id) -> Download | None:
    """queue item or None"""
    return Download.objects.filter(pk=youtube_id).first()


def test_add_video(yt):
    """full extract of a single video into the queue"""
    yt.videos["vid1"] = yt_meta("vid1")
    task = FakeTask()

    added = PendingList([video_url("vid1")], task=task).parse_url_list()

    assert added == 1
    item = queued("vid1")
    assert item.title == "Video vid1"
    assert item.status == "pending"
    assert item.duration_seconds == 600
    assert item.duration == "10m"
    assert item.vid_type == "videos"
    assert not item.auto_start
    assert FakeThumbs.downloaded == [
        ("vid1", "https://i.ytimg.com/vi/vid1/max.jpg")
    ]
    assert "Full extracting Video: 'Video vid1'" in task.lines
    assert "Added 1 videos." in task.lines


def test_add_video_as_ignored_auto_start(yt):
    """status and auto start are applied to the added items"""
    yt.videos["vid1"] = yt_meta("vid1")

    PendingList([video_url("vid1")], auto_start=True).parse_url_list(
        status="ignore"
    )
    item = queued("vid1")
    assert (item.status, item.auto_start) == ("ignore", True)


def test_auto_start_existing_pending_gets_priority(yt):
    """already queued video is moved to the front"""
    Download.upsert_doc(download_doc(youtube_id="vid1", message="err"))

    added = PendingList([video_url("vid1")], auto_start=True).parse_url_list()

    assert added == 0
    item = queued("vid1")
    assert (item.auto_start, item.message) == (True, None)


def test_skip_indexed_and_queued(yt):
    """indexed videos and queue items are not extracted again"""
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="indexed"))
    Download.upsert_doc(download_doc(youtube_id="ignored", status="ignore"))
    task = FakeTask()

    added = PendingList(
        [video_url("indexed"), video_url("ignored")], task=task
    ).parse_url_list()

    assert added == 0
    assert queued("indexed") is None
    assert queued("ignored").status == "ignore"
    assert not task.lines[-1].startswith("Added")


def test_force_readds_indexed_but_not_queued(yt):
    """force redownloads indexed videos, queue items stay as they are"""
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="indexed"))
    Download.upsert_doc(
        download_doc(youtube_id="pending", title="old", message="failed")
    )
    Download.upsert_doc(download_doc(youtube_id="ignored", status="ignore"))
    for youtube_id in ("indexed", "pending", "ignored"):
        yt.videos[youtube_id] = yt_meta(youtube_id)

    added = PendingList(
        [video_url("indexed"), video_url("pending"), video_url("ignored")],
        force=True,
    ).parse_url_list()

    assert added == 1
    assert queued("indexed").status == "pending"
    assert (queued("pending").title, queued("pending").message) == (
        "old",
        "failed",
    )
    assert queued("ignored").status == "ignore"


def test_invalid_url_type(yt):
    """unknown type raises"""
    with pytest.raises(ValueError):
        PendingList([{"type": "user", "url": "x"}]).parse_url_list()


def test_stops_after_entry(yt):
    """stopped task ends the url loop"""
    yt.videos["vid1"] = yt_meta("vid1")
    yt.videos["vid2"] = yt_meta("vid2")
    task = FakeTask(stop_after=0)

    added = PendingList(
        [video_url("vid1"), video_url("vid2")], task=task
    ).parse_url_list()

    assert added == 1
    assert queued("vid2") is None


@pytest.mark.parametrize("with_task", [True, False])
@pytest.mark.parametrize(
    "meta, expected_line",
    [
        (None, "Video unavailable"),
        (
            {"id": "vid1", "title": "no channel"},
            "Metadata extraction incomplete.",
        ),
    ],
)
def test_parse_video_extraction_failed(yt, with_task, meta, expected_line):
    """failed and incomplete metadata are reported and skipped"""
    if meta:
        yt.videos["vid1"] = meta
    task = FakeTask() if with_task else None

    added = PendingList([video_url("vid1")], task=task).parse_url_list()

    assert added == 0
    if with_task:
        assert expected_line in task.lines
        assert task.messages[1]["level"] == "error"


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "redirected"},
        {"live_status": "is_upcoming"},
        {"live_status": "is_live"},
    ],
)
def test_parse_entry_skips(yt, overrides):
    """redirects, upcoming and live videos are not added"""
    yt.videos["vid1"] = yt_meta("vid1", **overrides)

    assert PendingList([video_url("vid1")]).parse_url_list() == 0
    assert not FakeThumbs.downloaded


@pytest.mark.parametrize("with_task", [True, False])
def test_parse_entry_invalid_metadata(yt, with_task):
    """serializer rejects the entry"""
    yt.videos["vid1"] = yt_meta("vid1", title="")
    task = FakeTask() if with_task else None

    assert PendingList([video_url("vid1")], task=task).parse_url_list() == 0
    if with_task:
        assert "Error: invalid metadata" in task.lines
        assert "Failed Videos: vid1" in task.lines


@pytest.mark.parametrize("with_task", [True, False])
def test_add_to_pending_db_error(yt, with_task):
    """database errors are reported, nothing added"""
    yt.videos["vid1"] = yt_meta("vid1", channel_id="UC" + "x" * 100)
    task = FakeTask() if with_task else None

    assert PendingList([video_url("vid1")], task=task).parse_url_list() == 0
    assert queued("vid1") is None
    if with_task:
        assert "Adding extracted videos failed." in task.lines
        assert "Failed Videos: vid1" in task.lines


def test_add_to_pending_nothing_missing(yt):
    """empty notification, with and without task"""
    task = FakeTask()
    assert PendingList([], task=task).add_to_pending() == 0
    assert task.lines == [
        "Extracting videos completed.",
        "No new videos found to add.",
    ]
    assert PendingList([]).add_to_pending() == 0


def test_get_channels_maps_overwrites(yt):
    """pending videos of channels with overwrites are mapped"""
    Channel.save_doc(
        channel_doc(channel_overwrites={"download_format": "worst"})
    )
    Channel.save_doc(channel_doc(channel_id="UCplain"))
    Download.upsert_doc(download_doc(youtube_id="a"))
    Download.upsert_doc(download_doc(youtube_id="b", channel_id="UCplain"))
    Download.upsert_doc(download_doc(youtube_id="c", status="ignore"))

    pending = PendingList([])
    pending.get_download()
    pending.get_channels()

    assert sorted(pending.all_channels) == sorted([CHANNEL_ID, "UCplain"])
    assert pending.video_overwrites == {"a": {"download_format": "worst"}}
    assert [i["youtube_id"] for i in pending.all_ignored] == ["c"]


def channel_url(vid_type="videos", limit=None) -> dict:
    """parsed channel url"""
    return {
        "type": "channel",
        "url": CHANNEL_ID,
        "vid_type": vid_type,
        "limit": limit,
    }


def flat_entry(youtube_id, **overrides) -> dict:
    """flat channel or playlist entry from yt-dlp"""
    entry = {
        "id": youtube_id,
        "title": f"Video {youtube_id}",
        "duration": 30,
        "thumbnails": [{"url": "small"}, {"url": f"https://big/{youtube_id}"}],
    }
    entry.update(overrides)
    return entry


def test_channel_flat(yt):
    """flat channel entries get channel from metadata, notify bulk"""
    yt.channel_videos = [
        flat_entry("vid1"),
        flat_entry("vid2", channel="Other Name", channel_id="UCx"),
    ]
    yt.shorts = {"vid1"}
    task = FakeTask()

    added = PendingList(
        [channel_url("shorts", limit=5)], task=task, flat=True
    ).parse_url_list()

    assert added == 2
    assert yt.channel_calls == [(CHANNEL_ID, 5, VideoTypeEnum.SHORTS)]
    vid1, vid2 = queued("vid1"), queued("vid2")
    assert (vid1.channel_name, vid1.channel_id) == (
        "Test Channel",
        CHANNEL_ID,
    )
    assert (vid2.channel_name, vid2.channel_id) == ("Other Name", "UCx")
    assert vid1.vid_thumb_url == "https://big/vid1"
    assert vid1.vid_type == "shorts"
    assert vid2.vid_type == "videos"
    assert "Bulk extracting Channel: 'Test Channel'." in task.lines
    assert not FakeThumbs.downloaded


def test_channel_full_extract(yt):
    """not flat: every video is extracted, enum vid_type passed on"""
    yt.channel_videos = [flat_entry("vid1"), flat_entry("indexed")]
    yt.videos["vid1"] = yt_meta("vid1", width=None, height=None)
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="indexed"))
    task = FakeTask()

    added = PendingList(
        [channel_url(VideoTypeEnum.STREAMS)], task=task
    ).parse_url_list()

    assert added == 1
    assert queued("vid1").vid_type == "streams"
    assert queued("vid1").channel_indexed
    assert "Full extracting Channel: 'Test Channel'" in task.lines


def test_channel_nothing_to_add(yt):
    """no videos or no channel metadata"""
    assert PendingList([channel_url()]).parse_url_list() == 0

    yt.channel_videos = [flat_entry("vid1")]
    FakeChannel.json_data = None
    assert PendingList([channel_url()], flat=True).parse_url_list() == 0
    assert queued("vid1") is None


def test_channel_stopped(yt):
    """stop during channel videos"""
    yt.channel_videos = [flat_entry("vid1"), flat_entry("vid2")]
    task = FakeTask(stop_after=0)

    PendingList([channel_url()], task=task, flat=True).parse_url_list()
    assert not Download.objects.exists()


def test_channel_rules(yt):
    """rule skips or adds as ignored with the reason"""
    Channel.save_doc(channel_doc())
    DownloadRule.objects.create(
        channel_id=CHANNEL_ID, min_duration=60, action="skip"
    )
    DownloadRule.objects.create(channel=None, title_exclude="trailer")
    yt.channel_videos = [
        flat_entry("short", duration=10),
        flat_entry("long", duration=600),
        flat_entry("other", channel_id="UCother", title="A trailer"),
    ]

    pending = PendingList([channel_url()], flat=True)
    added = pending.parse_url_list()

    assert added == 2
    assert queued("short") is None
    assert "short" in pending.to_skip
    assert queued("long").status == "pending"
    other = queued("other")
    assert other.status == "ignore"
    assert other.ignore_reason == "title matches excluded /trailer/"


def playlist_url(limit=None) -> dict:
    """parsed playlist url"""
    return {
        "type": "playlist",
        "url": "PL1",
        "vid_type": VideoTypeEnum.UNKNOWN,
        "limit": limit,
    }


def test_playlist_flat(yt):
    """flat entries, limit, skip indexed, channel from playlist"""
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="indexed"))
    FakePlaylist.youtube_meta = {
        "channel": "Playlist Owner",
        "channel_id": "UCowner",
        "entries": [
            flat_entry("indexed"),
            flat_entry("vid1", duration=200),
            flat_entry("vid2", channel="Uploader", channel_id="UCup"),
            flat_entry("vid3"),
        ],
    }
    task = FakeTask()

    added = PendingList(
        [playlist_url(limit=3)], task=task, flat=True
    ).parse_url_list()

    assert added == 2
    assert queued("vid1").channel_id == "UCowner"
    assert queued("vid1").vid_type == "videos"
    assert queued("vid2").channel_id == "UCup"
    assert queued("vid3") is None
    assert "Bulk extracting Playlist: 'Playlist PL1'." in task.lines


def test_playlist_full_extract(yt):
    """not flat: entries extracted, failures skipped"""
    FakePlaylist.youtube_meta = {
        "channel": "Owner",
        "channel_id": "UCowner",
        "entries": [flat_entry("vid1"), flat_entry("broken")],
    }
    yt.videos["vid1"] = yt_meta("vid1", vid_type="unknown")

    assert PendingList([playlist_url()]).parse_url_list() == 1
    assert queued("vid1").vid_type == "videos"


def test_playlist_failed_or_stopped(yt):
    """no metadata, or stopped during entries"""
    assert PendingList([playlist_url()]).parse_url_list() == 0

    FakePlaylist.youtube_meta = {
        "channel": "Owner",
        "channel_id": "UCowner",
        "entries": [flat_entry("vid1")],
    }
    task = FakeTask(stop_after=0)
    assert (
        PendingList([playlist_url()], task=task, flat=True).parse_url_list()
        == 0
    )


@pytest.mark.parametrize(
    "video_data, expected",
    [
        ({"duration": 61.5}, 61),
        ({"duration": 10}, 10),
        ({"duration": "10"}, None),
        ({}, None),
    ],
)
def test_extract_seconds(video_data, expected):
    """numbers only"""
    assert PendingList._extract_seconds(video_data) == expected


def test_extract_thumb(app_config):
    """thumbnail, last of thumbnails, or None"""
    pending = PendingList([])
    assert pending._extract_thumb({"thumbnail": "a"}) == "a"
    assert pending._extract_thumb({"thumbnails": [{"url": "x"}]}) == "x"
    assert pending._extract_thumb({"thumbnails": []}) is None


def test_extract_published_fallbacks():
    """bad timestamp string falls back, bad upload date is None"""
    assert (
        PendingList._extract_published(
            {"timestamp": "soon", "upload_date": "20200101"}
        )
        == 1577836800
    )
    assert (
        PendingList._extract_published(
            {"id": "vid1", "upload_date": "2020-01-01"}
        )
        is None
    )


@pytest.mark.parametrize(
    "video_data, shorts, expected",
    [
        ({"vid_type": "shorts"}, False, "shorts"),
        ({"vid_type": VideoTypeEnum.STREAMS}, False, "streams"),
        ({"live_status": "was_live"}, False, "streams"),
        ({"width": 1920, "height": 1080}, True, "videos"),
        ({"duration": 600}, True, "videos"),
        ({"duration": 30}, True, "shorts"),
        ({"duration": 30.0}, True, "shorts"),
        ({"duration": 30}, False, "videos"),
    ],
)
def test_extract_vid_type(
    app_config, monkeypatch, video_data, shorts, expected
):
    """explicit type, live, landscape, long, shorts check"""
    monkeypatch.setattr(queue, "is_shorts", lambda youtube_id: shorts)
    video_data["id"] = "vid1"
    assert PendingList([])._extract_vid_type(video_data) == expected


def test_get_download_unknown_status(app_config):
    """item with an unknown status is skipped but not listed"""
    Download.upsert_doc(download_doc(youtube_id="odd", status="archived"))

    pending = PendingList([])
    pending.get_download()

    assert pending.to_skip == ["odd"]
    assert not pending.all_pending
    assert not pending.all_ignored


def test_notify_fail_without_ids(app_config):
    """no failed videos line"""
    task = FakeTask()
    PendingList([], task=task)._notify_fail("boom")
    assert task.lines == ["Adding extracted videos failed.", "Error: boom"]


def test_streams_each_video_with_found_by(yt):
    """with on_added, each video is stored and reported as it is found"""
    Channel.save_doc(channel_doc())
    DownloadRule.objects.create(channel_id=CHANNEL_ID, max_duration=100)
    yt.channel_videos = [
        flat_entry("vid1"),
        flat_entry("long", duration=600),
        flat_entry("vid2"),
    ]
    reported = []

    def on_added(items):
        stored = list(Download.objects.values_list("youtube_id", flat=True))
        reported.append(([i["youtube_id"] for i in items], sorted(stored)))

    added = PendingList(
        [channel_url()],
        flat=True,
        on_added=on_added,
        found_by={"found_by_kind": "channel_check", "found_by_job": 7},
    ).parse_url_list()

    assert added == 3
    # stored one at a time, before the next video is looked at
    assert reported == [
        (["vid1"], ["vid1"]),
        (["long"], ["long", "vid1"]),
        (["vid2"], ["long", "vid1", "vid2"]),
    ]
    long = queued("long")
    assert (long.status, long.ignore_reason_key) == ("ignore", "too_long")
    assert (long.found_by_kind, long.found_by_job) == ("channel_check", 7)


@pytest.mark.parametrize(
    "rule, expected",
    [
        (None, "pending"),
        (DownloadRule(min_duration=1000), "ignore"),
        (DownloadRule(min_duration=1000, enabled=False), "pending"),
    ],
)
def test_rule_override(yt, rule, expected):
    """a one-off rule replaces the stored ones, None means no rule"""
    DownloadRule.objects.create(channel=None, max_duration=1)
    yt.channel_videos = [flat_entry("vid1", duration=600)]

    PendingList([channel_url()], flat=True, rule=rule).parse_url_list()

    assert queued("vid1").status == expected


def test_playlist_streams(yt):
    """playlist videos stream too"""
    FakePlaylist.youtube_meta = {
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "entries": [flat_entry("p1"), flat_entry("p2")],
    }
    reported = []

    PendingList(
        [playlist_url()],
        flat=True,
        on_added=lambda items: reported.append(len(items)),
    ).parse_url_list()

    assert reported == [1, 1]


def test_playlist_rules(yt):
    """stored rules apply to playlist videos, a one-off rule replaces them"""
    DownloadRule.objects.create(channel=None, max_duration=1)
    FakePlaylist.youtube_meta = {
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "entries": [flat_entry("p1", duration=600)],
    }

    PendingList([playlist_url()], flat=True).parse_url_list()
    assert queued("p1").status == "ignore"

    Download.objects.all().delete()
    rule = DownloadRule(min_duration=1000, action="skip")
    PendingList([playlist_url()], flat=True, rule=rule).parse_url_list()
    assert queued("p1") is None


def test_duplicate_in_batch_keeps_the_batch(yt):
    """review P3: a video twice in one playlist doesn't drop the rest"""
    FakePlaylist.youtube_meta = {
        "channel": "Test Channel",
        "channel_id": CHANNEL_ID,
        "entries": [flat_entry("p1"), flat_entry("p2"), flat_entry("p1")],
    }

    added = PendingList([playlist_url()], flat=True).parse_url_list()

    assert added == 2
    assert queued("p1") and queued("p2")


def test_rules_checked_on_flat_data_first(yt, monkeypatch):
    """review T3: a video the rules turn away is not fully extracted"""
    Channel.save_doc(channel_doc())
    DownloadRule.objects.create(
        channel_id=CHANNEL_ID, max_duration=100, action="skip"
    )
    yt.channel_videos = [
        flat_entry("long", duration=5000),
        flat_entry("short", duration=50),
    ]
    yt.videos["short"] = yt_meta("short", duration=50)
    extracted = []
    real = PendingList._parse_video

    def parse_video(self, url, vid_type):
        extracted.append(url)
        return real(self, url, vid_type)

    monkeypatch.setattr(PendingList, "_parse_video", parse_video)

    PendingList([channel_url()]).parse_url_list()

    assert extracted == ["short"]
    assert queued("long") is None
    assert queued("short").status == "pending"

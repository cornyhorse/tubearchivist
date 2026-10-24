"""archived videos are left alone by the filesystem scanner and reindex"""

from datetime import datetime, timedelta, timezone

import pytest
from appsettings.src.filesystem import Scanner
from appsettings.src.reindex import Reindex, ReindexManual, ReindexPopulate
from channel.models import Channel, Source
from common.src.ta_redis import RedisQueue
from common.tests.docs import channel_doc, video_doc
from video.models import Video

pytestmark = pytest.mark.django_db

ARCH = "arch_0123456789abcdef"
VIDEO_Q = "reindex:ta_video"
CHANNEL_Q = "reindex:ta_channel"


def long_ago():
    """a date well past every refresh interval"""
    return datetime.now(timezone.utc) - timedelta(days=400)


@pytest.fixture(name="archive")
def fixture_archive():
    """an archived channel with one archived video, due for a refresh"""
    channel = Channel.objects.create(
        channel_id="arch_music",
        name="Music",
        source=Source.ARCHIVE,
        last_refresh=long_ago(),
    )
    Video.objects.create(
        **{
            **Video.fields_from_doc(
                video_doc(
                    youtube_id=ARCH,
                    published=int(datetime.now().timestamp()),
                )
            ),
            "channel": channel,
            "source": Source.ARCHIVE,
            "media_url": f"arch_music/{ARCH}.mkv",
            "last_refresh": long_ago(),
        }
    )
    return channel


def test_scanner_neither_indexes_nor_removes(tmp_path, monkeypatch, archive):
    del archive
    root = tmp_path / "media"
    (root / "arch_music").mkdir(parents=True)
    (root / "arch_music" / f"{ARCH}.mkv").write_bytes(b"x")
    # an archive id that is not indexed is not a YouTube id to look up
    (root / "arch_music" / "arch_ffffffffffffffff.mp4").write_bytes(b"x")
    monkeypatch.setattr(Scanner, "VIDEOS", str(root))
    scanner = Scanner()

    scanner.scan()

    assert not scanner.to_index and not scanner.to_delete


def test_scanner_still_removes_youtube_videos(tmp_path, monkeypatch, archive):
    del archive
    channel = channel_doc(channel_id="UCa")
    Channel.save_doc(channel)
    Video.save_doc(video_doc(channel=channel, youtube_id="gone0000001"))
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(Scanner, "VIDEOS", str(root))
    scanner = Scanner()

    scanner.scan()

    assert {i for i, _ in scanner.to_delete} == {"gone0000001"}


def test_populate_leaves_archived_items_out(app_config, redis_clean, archive):
    del app_config, redis_clean
    Channel.save_doc(channel_doc())
    Channel.objects.update(last_refresh=long_ago())
    Video.save_doc(
        video_doc(
            youtube_id="yt000000001",
            published=int(datetime.now().timestamp()),
        )
    )
    Video.objects.filter(pk="yt000000001").update(last_refresh=long_ago())
    populate = ReindexPopulate()

    populate.add_recent()
    populate.add_outdated()

    assert RedisQueue(VIDEO_Q).get_all() == ["yt000000001"]
    assert RedisQueue(CHANNEL_Q).get_all() == [channel_doc()["channel_id"]]
    assert (
        populate._get_total_hits(  # pylint: disable=protected-access
            populate.REINDEX_CONFIG["video"]
        )
        == 1
    )


def test_reindex_skips_queued_archive_ids(
    app_config, redis_clean, archive, monkeypatch
):
    del app_config, redis_clean, archive
    ReindexManual().extract_data({"video": [ARCH], "channel": ["arch_music"]})

    def never(*args, **kwargs):
        raise AssertionError("asked YouTube about an archived item")

    monkeypatch.setattr(Reindex, "reindex_single_video", never)
    monkeypatch.setattr(Reindex, "_reindex_single_channel", never)

    Reindex().reindex_all()

    assert not RedisQueue(VIDEO_Q).get_all()
    assert not RedisQueue(CHANNEL_Q).get_all()


def test_reindex_single_video_does_not_deactivate(app_config, archive):
    del app_config, archive

    assert Reindex().reindex_single_video(ARCH) is None
    assert Reindex().reindex_single_video(ARCH, from_download=True) is None

    assert Video.objects.get(pk=ARCH).active is True

"""
acceptance: CoasterFan2105 (59 videos) must become 59 distinct Emby episodes
today Emby shows 42 entries, because TA's <movie> NFOs carry no numbers and
Emby folds numbers parsed from titles into "versions"

the fixture keeps the 59 real upload times; ids and titles are synthetic.
set TA_ACCEPTANCE_EXPORT=<es_export dir> to also run against the real export
"""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, video_doc
from emby.src.sync import EmbyPaths, sync_all, sync_channel
from emby.src.writer import MANIFEST_NAME
from video.models import Video

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).parent / "fixtures" / "coasterfan2105.json"
CHANNEL_ID = "UCXOPfBqPphU15a6mpW7hwVw"


def make_paths(tmp_path) -> EmbyPaths:
    """scratch media, cache and output roots"""
    paths = EmbyPaths(
        media_root=tmp_path / "media",
        cache_root=tmp_path / "cache",
        output_root=tmp_path / "emby",
        timezone="America/New_York",
    )
    for path in (paths.media_root, paths.cache_root, paths.output_root):
        path.mkdir()
    return paths


def add_media(paths: EmbyPaths, video: Video) -> None:
    """placeholder media file and cached thumb for a video"""
    media = paths.media_root / video.media_url
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(video.youtube_id.encode())
    thumb = paths.video_thumb(video.youtube_id)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"jpg")


def load_fixture(paths: EmbyPaths, scheme: str) -> list[Video]:
    """channel plus 59 videos with real upload times"""
    channel = channel_doc(
        channel_id=CHANNEL_ID,
        channel_name="CoasterFan2105",
        emby_numbering=scheme,
    )
    Channel.save_doc(channel)
    videos = []
    for item in json.loads(FIXTURE.read_text())["videos"]:
        doc = video_doc(
            channel=channel,
            youtube_id=item["youtube_id"],
            title=item["title"],
            published=item["published"],
        )
        doc["player"]["duration"] = item["duration"]
        video = Video.from_doc(doc)
        video.save()
        add_media(paths, video)
        videos.append(video)
    return videos


def episodes_on_disk(root: Path) -> list[ET.Element]:
    """parsed episode NFOs under root"""
    return [
        ET.parse(p).getroot()
        for p in sorted(root.rglob("*.nfo"))
        if p.name != "tvshow.nfo"
    ]


def assert_59_distinct(paths: EmbyPaths, videos: list[Video]):
    """the acceptance criteria"""
    root = paths.output_root
    nfos = episodes_on_disk(root)
    numbers = {(n.findtext("season"), n.findtext("episode")) for n in nfos}
    ids = {n.findtext("uniqueid") for n in nfos}
    links = [p for p in root.rglob("*.mp4")]

    assert len(nfos) == 59
    assert all(n.tag == "episodedetails" for n in nfos)
    assert len(numbers) == 59, "two videos share a season/episode"
    assert ids == {v.youtube_id for v in videos}
    assert len(links) == 59
    assert len({p.stat().st_ino for p in links}) == 59
    for nfo in nfos:
        assert nfo.findtext("uniqueid") not in nfo.findtext("title")
    # every filename states its own SxxEyy, so filename parsing can't fold
    stems = {p.stem.split(" - ")[1] for p in links}
    assert len(stems) == 59


@pytest.mark.parametrize("scheme", ["date", "year_frozen", "absolute_frozen"])
def test_coasterfan_59_episodes(tmp_path, scheme):
    paths = make_paths(tmp_path)
    videos = load_fixture(paths, scheme)

    report = sync_channel(CHANNEL_ID, paths)

    assert not report.conflicts
    assert_59_distinct(paths, videos)


def test_coasterfan_date_numbers(tmp_path):
    paths = make_paths(tmp_path)
    load_fixture(paths, "date")
    sync_channel(CHANNEL_ID, paths)

    first = json.loads(FIXTURE.read_text())["videos"][0]
    nfo = next(
        n
        for n in episodes_on_disk(paths.output_root)
        if n.findtext("uniqueid") == first["youtube_id"]
    )
    # 2025-06-20 16:01 UTC = 12:01 in New York
    assert (nfo.findtext("season"), nfo.findtext("episode")) == (
        "2025",
        "6200",
    )
    assert nfo.findtext("aired") == "2025-06-20"


def test_coasterfan_rerun_and_backfill(tmp_path):
    paths = make_paths(tmp_path)
    videos = load_fixture(paths, "year_frozen")
    sync_channel(CHANNEL_ID, paths)
    root = paths.output_root
    before = {
        p: (p.stat().st_ino, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }

    assert sync_channel(CHANNEL_ID, paths).changed == 0

    # an older video shows up later: it gets a new number, nothing else moves
    old = Video.from_doc(
        video_doc(
            channel=videos[0].channel.to_doc(),
            youtube_id="cfbackfill1",
            title="Backfilled 2025 ride",
            published=int(
                datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp()
            ),
        )
    )
    old.save()
    add_media(paths, old)
    report = sync_channel(CHANNEL_ID, paths)

    assert len(report.created) == 3
    assert not report.updated and not report.renamed and not report.deleted
    after = {
        p: (p.stat().st_ino, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }
    assert {p: after[p] for p in before} == before


@pytest.mark.skipif(
    not os.environ.get("TA_ACCEPTANCE_EXPORT"),
    reason="set TA_ACCEPTANCE_EXPORT to a storage.es_export dir",
)
def test_real_export_coasterfan(tmp_path):
    # pylint: disable=import-outside-toplevel
    from storage.importer import EsImporter

    report = EsImporter(Path(os.environ["TA_ACCEPTANCE_EXPORT"])).run()
    assert report.ok, report.lines()
    paths = make_paths(tmp_path)
    videos = list(Video.objects.filter(channel_id=CHANNEL_ID))
    for video in videos:
        add_media(paths, video)

    sync_report = sync_all(paths)

    assert not sync_report.conflicts
    channel_root = next(
        (paths.output_root / "channels").glob(f"*[[]{CHANNEL_ID}[]]")
    )
    assert len(videos) == 59
    assert_59_distinct(
        EmbyPaths(paths.media_root, paths.cache_root, channel_root), videos
    )

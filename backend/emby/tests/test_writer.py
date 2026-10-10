"""tests for the Emby tree writer"""

import os
from datetime import date
from pathlib import Path

import pytest
from emby.src.layout import EpisodeSource, SeriesSource, plan_series, series_dir
from emby.src.nfo import EpisodeInfo, SeriesInfo
from emby.src.writer import MANIFEST_NAME, EmbyWriter


@pytest.fixture(name="media")
def fixture_media(tmp_path):
    """fake archive with three small media files and thumbs"""
    root = tmp_path / "media"
    cache = tmp_path / "cache"
    (root / "UC1").mkdir(parents=True)
    (cache / "videos").mkdir(parents=True)
    for youtube_id in ("aaa", "bbb", "ccc"):
        (root / "UC1" / f"{youtube_id}.mp4").write_bytes(b"video " + youtube_id.encode())
        thumb_dir = cache / "videos" / youtube_id[0]
        thumb_dir.mkdir(exist_ok=True)
        (thumb_dir / f"{youtube_id}.jpg").write_bytes(b"jpg " + youtube_id.encode())
    return root, cache


def series_files(media, titles: dict[str, str], episodes=None):
    """desired files of channel UC1 with the given titles"""
    root, cache = media
    series = SeriesSource("channels", SeriesInfo("UC1", "My Channel"))
    numbers = episodes or {yid: (2025, n) for n, yid in enumerate(titles, 1)}
    sources = [
        EpisodeSource(
            EpisodeInfo(
                youtube_id=yid,
                title=title,
                show_title="",
                season=numbers[yid][0],
                episode=numbers[yid][1],
                aired=date(2025, 1, numbers[yid][1]),
            ),
            media=root / "UC1" / f"{yid}.mp4",
            thumb=cache / "videos" / yid[0] / f"{yid}.jpg",
        )
        for yid, title in titles.items()
    ]
    desired, _ = plan_series(series, sources)
    return desired, series_dir(series)


def snapshot(root: Path) -> dict:
    """path -> (inode, mtime_ns) of every file under root"""
    return {
        str(p.relative_to(root)): (p.stat().st_ino, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    }


TITLES = {"aaa": "First", "bbb": "Second", "ccc": "Third"}


def test_first_run_creates_tree(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)

    report = EmbyWriter(out).sync(desired, scope)

    assert len(report.created) == 1 + 3 * 3
    season = out / scope / "Season 2025"
    link = season / "My Channel - S2025E01 - First.mp4"
    assert link.stat().st_ino == (media[0] / "UC1" / "aaa.mp4").stat().st_ino
    assert (season / "My Channel - S2025E01 - First.nfo").exists()
    assert (season / "My Channel - S2025E01 - First-thumb.jpg").read_bytes() == b"jpg aaa"


def test_second_run_changes_nothing(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    before = snapshot(out)

    report = EmbyWriter(out).sync(desired, scope)

    assert report.changed == 0
    assert report.unchanged == 10
    assert snapshot(out) == before


def test_title_change_renames_and_keeps_inodes(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    season = out / scope / "Season 2025"
    old_inode = (season / "My Channel - S2025E02 - Second.mp4").stat().st_ino

    desired, _ = series_files(media, {**TITLES, "bbb": "Second, renamed"})
    report = EmbyWriter(out).sync(desired, scope)

    new_media = season / "My Channel - S2025E02 - Second, renamed.mp4"
    assert new_media.stat().st_ino == old_inode
    assert len(report.renamed) == 3
    assert report.updated == [
        f"{scope}/Season 2025/My Channel - S2025E02 - Second, renamed.nfo"
    ]
    assert not (season / "My Channel - S2025E02 - Second.mp4").exists()


def test_removed_video_deletes_only_its_files(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    unrelated = out / scope / "Season 2025" / "notes.txt"
    unrelated.write_text("mine")

    desired, _ = series_files(media, {"aaa": "First", "bbb": "Second"})
    report = EmbyWriter(out).sync(desired, scope)

    assert len(report.deleted) == 3
    assert unrelated.read_text() == "mine"
    assert (media[0] / "UC1" / "ccc.mp4").exists()


def test_empty_series_prunes_only_created_dirs(tmp_path, media):
    out = tmp_path / "out"
    (out / "channels").mkdir(parents=True)
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)

    EmbyWriter(out).sync([], scope)

    assert not (out / scope).exists()
    assert (out / "channels").is_dir()


def test_foreign_file_is_never_overwritten(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    nfo = out / scope / "Season 2025" / "My Channel - S2025E01 - First.nfo"
    nfo.parent.mkdir(parents=True)
    nfo.write_text("hand written")

    report = EmbyWriter(out).sync(desired, scope)

    assert nfo.read_text() == "hand written"
    assert report.conflicts == [f"{scope}/Season 2025/My Channel - S2025E01 - First.nfo: not owned, kept"]


def test_lost_manifest_rebuilds_without_writes(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    before = snapshot(out)
    (out / MANIFEST_NAME).unlink()

    report = EmbyWriter(out).sync(desired, scope)

    assert report.changed == 0
    assert snapshot(out) == before


def test_dry_run_writes_nothing(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)

    report = EmbyWriter(out, dry_run=True).sync(desired, scope)

    assert len(report.created) == 10
    assert not out.exists()


def test_redownloaded_media_is_relinked_atomically(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    source = media[0] / "UC1" / "aaa.mp4"
    source.unlink()
    source.write_bytes(b"new download")

    report = EmbyWriter(out).sync(desired, scope)

    link = out / scope / "Season 2025" / "My Channel - S2025E01 - First.mp4"
    assert link.read_bytes() == b"new download"
    assert report.updated == [str(link.relative_to(out))]
    assert not [p for p in link.parent.iterdir() if ".tmp-" in p.name]


def test_paths_cannot_escape_root(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, {"aaa": "../../../etc"})

    EmbyWriter(out).sync(desired, scope)

    assert all(os.path.commonpath([out, p]) == str(out) for p in out.rglob("*"))

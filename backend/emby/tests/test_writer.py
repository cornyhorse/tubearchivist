"""tests for the Emby tree writer"""

import errno
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from emby.src import writer as writer_module
from emby.src.layout import EpisodeSource, SeriesSource, plan_series, series_dir
from emby.src.nfo import EpisodeInfo, SeriesInfo
from emby.src.writer import MANIFEST_NAME, DesiredFile, EmbyWriter


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


def data_file(relpath, data=b"x", owner="o", role="r"):
    """a desired data file"""
    return DesiredFile(relpath, "data", owner, role, data=data)


def test_sync_rejects_bad_input(tmp_path):
    writer = EmbyWriter(tmp_path / "out")

    with pytest.raises(ValueError, match="duplicate"):
        writer.sync([data_file("a/x"), data_file("a/x")], "a")
    with pytest.raises(ValueError, match="outside scope"):
        writer.sync([data_file("b/x")], "a")
    with pytest.raises(ValueError, match="escapes root"):
        writer.sync([data_file("a/../../x")], None)


def test_unsupported_manifest_version(tmp_path):
    (tmp_path / MANIFEST_NAME).write_text('{"version": 99}')

    with pytest.raises(ValueError, match="unsupported manifest"):
        EmbyWriter(tmp_path)


def test_missing_source_is_skipped(tmp_path):
    item = DesiredFile("a/x.mp4", "link", "o", "media", source=tmp_path / "nope")

    report = EmbyWriter(tmp_path / "out").sync([item], "a")

    assert report.warnings == ["a/x.mp4: missing source"]
    assert not report.created


def test_rename_skipped_when_old_file_changed(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data_file("a/old", b"v1")], "a")
    (out / "a" / "old").write_bytes(b"edited by hand")

    report = EmbyWriter(out).sync([data_file("a/new", b"v1")], "a")

    assert report.warnings == ["a/old: changed outside writer"]
    assert report.created == ["a/new"]
    assert (out / "a" / "old").read_bytes() == b"edited by hand"


def test_changed_file_is_not_deleted(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data_file("a/x"), data_file("a/y", owner="p")], "a")
    (out / "a" / "x").write_bytes(b"edited by hand")
    (out / "a" / "y").unlink()

    report = EmbyWriter(out).sync([], "a")

    assert report.warnings == ["a/x: changed outside writer"]
    assert (out / "a" / "x").exists()
    assert not EmbyWriter(out).manifest.files


def test_changed_link_is_not_deleted(tmp_path):
    source = tmp_path / "media.mp4"
    source.write_bytes(b"v")
    out = tmp_path / "out"
    link = DesiredFile("a/x.mp4", "link", "o", "media", source=source)
    EmbyWriter(out).sync([link], "a")
    (out / "a" / "x.mp4").unlink()
    (out / "a" / "x.mp4").write_bytes(b"v")

    report = EmbyWriter(out).sync([], "a")

    assert report.warnings == ["a/x.mp4: changed outside writer"]


def test_dry_run_on_existing_tree_changes_nothing(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)
    before = snapshot(out)
    manifest = (out / MANIFEST_NAME).read_bytes()

    desired, _ = series_files(media, {"aaa": "First, renamed"})
    report = EmbyWriter(out, dry_run=True).sync(desired, scope)

    assert report.renamed and report.deleted
    assert snapshot(out) == before
    assert (out / MANIFEST_NAME).read_bytes() == manifest


def test_dry_run_reports_dir_prune(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    EmbyWriter(out).sync(desired, scope)

    report = EmbyWriter(out, dry_run=True).sync([], scope)

    assert f"{scope}/Season 2025/" in report.deleted
    assert f"{scope}/" in report.deleted
    assert (out / scope / "Season 2025").is_dir()


def test_vanished_dir_is_forgotten(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data_file("a/b/x")], "a")
    (out / "a" / "b" / "x").unlink()
    (out / "a" / "b").rmdir()

    EmbyWriter(out).sync([], "a")

    assert EmbyWriter(out).manifest.dirs == set()


def test_nonempty_dir_is_kept(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data_file("a/x")], "a")
    (out / "a" / "mine.txt").write_text("keep")

    EmbyWriter(out).sync([], "a")

    assert (out / "a" / "mine.txt").exists()
    assert EmbyWriter(out).manifest.dirs == {"a"}


def test_atomic_write_cleans_up_on_failure(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(writer_module.os, "replace", fail)

    with pytest.raises(OSError, match="disk full"):
        writer_module.atomic_write(tmp_path / "x", b"data", 0o644)
    assert not list(tmp_path.iterdir())


def test_atomic_link_cleans_up_on_failure(tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.write_bytes(b"v")

    def fail(*_):
        raise OSError("busy")

    monkeypatch.setattr(writer_module.os, "replace", fail)

    with pytest.raises(OSError, match="busy"):
        writer_module.atomic_link(source, tmp_path / "x")
    assert [p.name for p in tmp_path.iterdir()] == ["src"]


@pytest.mark.parametrize(
    "error, message",
    [(errno.EXDEV, "across filesystems"), (errno.EPERM, "denied")],
)
def test_atomic_link_errors(tmp_path, monkeypatch, error, message):
    def fail(*_):
        raise OSError(error, "denied")

    monkeypatch.setattr(writer_module.os, "link", fail)

    with pytest.raises(OSError, match=message):
        writer_module.atomic_link(tmp_path / "src", tmp_path / "x")


def test_plan_series_warnings(tmp_path, media):
    root, cache = media
    channels = cache / "channels"
    channels.mkdir()
    (channels / "UC1_thumb.jpg").write_bytes(b"poster")
    series = SeriesSource(
        "channels",
        SeriesInfo("UC1", "My Channel"),
        poster=channels / "UC1_thumb.jpg",
        fanart=channels / "UC1_tvart.jpg",
    )
    info = EpisodeInfo("aaa", "First", "", 2025, 1, date(2025, 1, 1))
    episodes = [
        EpisodeSource(info, root / "UC1" / "aaa.mp4", thumb=None),
        EpisodeSource(replace(info, youtube_id="zzz"), root / "UC1" / "zzz.mp4"),
    ]

    desired, warnings = plan_series(series, episodes)

    assert {d.role for d in desired} == {"tvshow", "poster", "media", "nfo"}
    assert warnings == [
        f"zzz: missing media {root / 'UC1' / 'zzz.mp4'}",
        f"channels/UC1: no cached fanart at {channels / 'UC1_tvart.jpg'}",
        "aaa: no cached thumbnail",
    ]
    assert plan_series(series, []) == ([], [])
    assert plan_series(series, episodes[1:]) == (
        [],
        [f"zzz: missing media {root / 'UC1' / 'zzz.mp4'}"],
    )


def swap_files(media, out, dry_run=False):
    """sync, then swap the episode numbers of aaa and bbb"""
    desired, scope = series_files(media, {"aaa": "Same", "bbb": "Same"})
    EmbyWriter(out).sync(desired, scope)
    swapped, _ = series_files(
        media,
        {"aaa": "Same", "bbb": "Same"},
        episodes={"aaa": (2025, 2), "bbb": (2025, 1)},
    )
    return EmbyWriter(out, dry_run=dry_run).sync(swapped, scope), scope


def test_swapped_numbers_keep_inodes(tmp_path, media):
    out = tmp_path / "out"
    source_a = (media[0] / "UC1" / "aaa.mp4").stat().st_ino

    report, scope = swap_files(media, out)

    season = out / scope / "Season 2025"
    assert (season / "My Channel - S2025E02 - Same.mp4").stat().st_ino == source_a
    assert len(report.renamed) == 6
    assert not report.created and not report.deleted
    assert not [p for p in season.iterdir() if p.name.startswith(".ta-move")]
    nfo = (season / "My Channel - S2025E02 - Same.nfo").read_text()
    assert "<uniqueid" in nfo and ">aaa<" in nfo
    assert EmbyWriter(out).sync(
        series_files(
            media,
            {"aaa": "Same", "bbb": "Same"},
            episodes={"aaa": (2025, 2), "bbb": (2025, 1)},
        )[0],
        scope,
    ).changed == 0


def test_swap_dry_run_matches_real_run(tmp_path, media):
    dry, _ = swap_files(media, tmp_path / "dry", dry_run=True)
    real, _ = swap_files(media, tmp_path / "real")

    assert sorted(dry.renamed) == sorted(real.renamed)
    assert (dry.created, dry.updated, dry.deleted) == (
        real.created,
        real.updated,
        real.deleted,
    )


def test_symlink_at_target_is_a_conflict(tmp_path, media):
    out = tmp_path / "out"
    desired, scope = series_files(media, TITLES)
    nfo = out / scope / "Season 2025" / "My Channel - S2025E01 - First.nfo"
    nfo.parent.mkdir(parents=True)
    nfo.symlink_to(tmp_path)

    report = EmbyWriter(out).sync(desired, scope)

    assert nfo.is_symlink()
    assert len(report.conflicts) == 1


def test_symlinked_dir_escaping_root_is_refused(tmp_path, media):
    out = tmp_path / "out"
    (out / "channels").mkdir(parents=True)
    (out / "channels" / "My Channel [UC1]").symlink_to(tmp_path)
    desired, scope = series_files(media, TITLES)

    with pytest.raises(ValueError, match="escapes root"):
        EmbyWriter(out).sync(desired, scope)

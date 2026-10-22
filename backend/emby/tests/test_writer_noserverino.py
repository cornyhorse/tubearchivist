"""
the 2026-10-21 production failure: the CIFS client autodisabled server
inode numbers, so a video and its .emby hardlink reported different
st_ino through the mount and the inode-based preflight refused to sync;
links are now recognised by size, mtime and source, and the preflight
tests hardlinks by behaviour
"""

import errno
import json
import os
from pathlib import Path

import pytest

from emby.src import writer as writer_module
from emby.src.writer import (
    MANIFEST_NAME,
    EmbyRootError,
    EmbyWriter,
    check_hardlinks,
)
from emby.tests.test_writer_hardening import (
    FakeStat,
    data,
    link,
    media_file,
    read_manifest,
    write_manifest,
)


def noserverino(monkeypatch, root: Path, remount: bool = False):
    """
    from now on files under root report made-up inode numbers, as a CIFS
    mount does after "Autodisabling the use of server inode numbers";
    remount also changes st_dev of every file
    """
    real_stat = Path.stat
    root = str(root)

    def stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        fields = {}
        if str(self).startswith(root):
            fields["st_ino"] = result.st_ino + 10**9
        if remount:
            fields["st_dev"] = result.st_dev + 1000
        return FakeStat(result, **fields)

    monkeypatch.setattr(Path, "stat", stat)


def real_inode(path: Path) -> int:
    """inode as the filesystem has it, bypassing the patched Path.stat"""
    return os.stat(path).st_ino


def legacy_manifest(out: Path, version: int = 2) -> None:
    """rewrite the manifest as version 2 recorded links: st_ino and size"""
    manifest = read_manifest(out)
    manifest["version"] = version
    for relpath, entry in manifest["files"].items():
        if entry["kind"] == "link":
            entry["ino"] = real_inode(out / relpath)
            del entry["mtime_ns"], entry["source"]
    write_manifest(out, manifest)


@pytest.fixture(name="two")
def fixture_two(tmp_path):
    """two videos linked into out, as a first sync left them"""
    first = media_file(tmp_path, "a.mp4", b"first video")
    second = media_file(tmp_path, "b.mp4", b"second")
    out = tmp_path / "out"
    EmbyWriter(out).sync(
        [
            link("s/a.mp4", first, "a"),
            link("s/b.mp4", second, "b"),
            data("s/a.nfo", b"nfo", "a"),
        ],
        "s",
    )
    return first, second, out


@pytest.mark.parametrize("remount", [False, True])
def test_mismatched_inodes_rename_and_delete(two, monkeypatch, remount):
    first, _, out = two
    noserverino(monkeypatch, out, remount)
    assert (out / "s" / "a.mp4").stat().st_ino != first.stat().st_ino

    report = EmbyWriter(out).sync(
        [link("s/c.mp4", first, "a"), data("s/c.nfo", b"nfo", "a")], "s"
    )

    assert sorted(report.renamed) == [
        ("s/a.mp4", "s/c.mp4"),
        ("s/a.nfo", "s/c.nfo"),
    ]
    assert report.deleted == ["s/b.mp4"]
    assert not report.created and not report.updated and not report.warnings
    assert real_inode(out / "s" / "c.mp4") == real_inode(first)


def test_mismatched_inodes_relink_nothing(two, monkeypatch):
    first, second, out = two
    noserverino(monkeypatch, out, remount=True)
    desired = [link("s/a.mp4", first, "a"), link("s/b.mp4", second, "b")]

    report = EmbyWriter(out).sync(desired, "s")

    assert report.unchanged == 2
    assert report.changed == 1  # the nfo is no longer wanted
    assert report.deleted == ["s/a.nfo"]


def test_new_links_on_a_noserverino_mount(tmp_path, monkeypatch):
    source = media_file(tmp_path)
    out = tmp_path / "out"
    noserverino(monkeypatch, out)

    first = EmbyWriter(out).sync([link("s/v.mp4", source)], "s")
    second = EmbyWriter(out).sync([link("s/v.mp4", source)], "s")

    assert first.created == ["s/v.mp4"]
    assert second.unchanged == 1 and second.changed == 0


@pytest.mark.parametrize("version", [1, 2])
def test_old_manifest_is_upgraded_without_relinking(two, monkeypatch, version):
    first, second, out = two
    legacy_manifest(out)
    if version == 1:
        manifest = read_manifest(out)
        manifest["version"] = 1
        for entry in manifest["files"].values():
            if entry["kind"] == "link":
                entry["inode"] = [7, entry.pop("ino")]
                del entry["size"]
        write_manifest(out, manifest)
    noserverino(monkeypatch, out, remount=True)

    report = EmbyWriter(out).sync(
        [link("s/c.mp4", first, "a"), link("s/b.mp4", second, "b")], "s"
    )

    assert report.renamed == [("s/a.mp4", "s/c.mp4")]
    assert report.unchanged == 2  # moved, then found current
    assert not report.created and not report.updated and not report.warnings
    files = read_manifest(out)["files"]
    assert read_manifest(out)["version"] == 3
    assert files["s/c.mp4"] == {
        "kind": "link",
        "owner": "a",
        "role": "media",
        "size": 11,
        "mtime_ns": os.stat(first).st_mtime_ns,
        "source": str(first),
    }
    assert "ino" not in files["s/b.mp4"]


def test_old_entry_with_its_inode_is_upgraded(two):
    first, second, out = two
    legacy_manifest(out)

    EmbyWriter(out).sync(
        [link("s/a.mp4", first, "a"), link("s/b.mp4", second, "b")], "s"
    )

    assert all(
        "mtime_ns" in e
        for e in read_manifest(out)["files"].values()
        if e["kind"] == "link"
    )


def test_dry_run_upgrades_in_memory_only(two, monkeypatch):
    first, second, out = two
    legacy_manifest(out)
    before = (out / MANIFEST_NAME).read_bytes()
    noserverino(monkeypatch, out)

    report = EmbyWriter(out, dry_run=True).sync(
        [link("s/c.mp4", first, "a"), link("s/b.mp4", second, "b")], "s"
    )

    assert report.renamed == [("s/a.mp4", "s/c.mp4")]
    assert (out / MANIFEST_NAME).read_bytes() == before
    assert (out / "s" / "a.mp4").exists()


def test_old_entry_that_cant_be_upgraded_is_kept(two, monkeypatch):
    first, _, out = two
    legacy_manifest(out)
    noserverino(monkeypatch, out)

    # b is no longer wanted: no source to compare it with, and its
    # inode looks wrong, so it can't be shown to be ours
    report = EmbyWriter(out).sync([link("s/a.mp4", first, "a")], "s")

    assert report.warnings == ["s/b.mp4: changed outside writer"]
    assert (out / "s" / "b.mp4").exists()
    assert report.unchanged == 1


def test_old_entry_of_another_file_is_not_upgraded(two, monkeypatch):
    first, _, out = two
    legacy_manifest(out)
    target = out / "s" / "a.mp4"
    target.unlink()
    target.write_bytes(b"first video")  # same size, another file
    os.utime(target, ns=(1, 1))
    noserverino(monkeypatch, out)

    report = EmbyWriter(out).sync([link("s/c.mp4", first, "a")], "s")

    assert report.warnings == [
        "s/a.mp4: changed outside writer",
        "s/b.mp4: changed outside writer",
    ]
    assert target.read_bytes() == b"first video"
    assert report.created == ["s/c.mp4"]


def test_upgrade_skips_what_it_cant_compare(two, monkeypatch):
    first, second, out = two
    legacy_manifest(out)
    (out / "s" / "b.mp4").unlink()
    gone = first.with_name("gone.mp4")

    report = EmbyWriter(out).sync(
        [
            link("s/a.mp4", gone, "a"),
            link("s/b.mp4", second, "b"),
            data("s/a.nfo", b"nfo", "a"),
            link("s/n.mp4", first, "new"),
        ],
        "s",
    )

    assert report.created == ["s/b.mp4", "s/n.mp4"]
    assert report.warnings == ["s/a.mp4: missing source"]
    files = read_manifest(out)["files"]
    assert "ino" in files["s/a.mp4"]


def test_symlink_in_place_of_an_old_link_is_not_upgraded(two):
    first, _, out = two
    legacy_manifest(out)
    target = out / "s" / "a.mp4"
    target.unlink()
    target.symlink_to(first)

    report = EmbyWriter(out).sync([link("s/c.mp4", first, "a")], "s")

    assert "s/a.mp4: changed outside writer" in report.warnings
    assert target.is_symlink()


def test_source_changed_in_place_is_still_ours(two, monkeypatch):
    first, _, out = two
    noserverino(monkeypatch, out, remount=True)
    with open(first, "ab") as f:  # the link shares the change
        f.write(b" with more")

    report = EmbyWriter(out).sync([link("s/c.mp4", first, "a")], "s")

    assert ("s/a.mp4", "s/c.mp4") in report.renamed
    assert report.deleted == ["s/a.nfo", "s/b.mp4"]
    assert not report.warnings


def test_changed_link_without_usable_source_is_not_ours(two):
    first, _, out = two
    manifest = read_manifest(out)
    manifest["files"]["s/a.mp4"]["mtime_ns"] = 1
    manifest["files"]["s/b.mp4"]["mtime_ns"] = 1
    manifest["files"]["s/b.mp4"]["source"] = 5
    write_manifest(out, manifest)
    first.unlink()

    report = EmbyWriter(out).sync([], "s")

    assert report.warnings == [
        "s/a.mp4: changed outside writer",
        "s/b.mp4: changed outside writer",
    ]
    assert (out / "s" / "a.mp4").exists() and (out / "s" / "b.mp4").exists()


def test_a_link_recorded_as_its_own_source_is_never_removed(two):
    _, _, out = two
    target = out / "s" / "a.mp4"
    manifest = read_manifest(out)
    manifest["files"]["s/a.mp4"]["source"] = str(target)
    write_manifest(out, manifest)

    report = EmbyWriter(out).sync([], "s")

    assert "s/a.mp4: is its own source, kept" in report.warnings
    assert target.exists()


def test_own_source_doesnt_vouch_for_a_changed_link(two):
    first, _, out = two
    target = out / "s" / "a.mp4"
    manifest = read_manifest(out)
    manifest["files"]["s/a.mp4"]["mtime_ns"] = 1
    manifest["files"]["s/a.mp4"]["source"] = str(target)
    write_manifest(out, manifest)

    report = EmbyWriter(out).sync([link("s/c.mp4", first, "a")], "s")

    assert "s/a.mp4: changed outside writer" in report.warnings
    assert target.exists()


def test_bool_is_not_an_identity(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    entry = {
        "kind": "link",
        "owner": "o",
        "role": "r",
        "size": True,
        "mtime_ns": 1,
    }
    write_manifest(out, {"version": 3, "files": {"s/x": entry}, "dirs": []})

    assert EmbyWriter(out).manifest.files == {}


# the preflight


def test_preflight_passes_on_a_noserverino_mount(tmp_path, monkeypatch):
    media, out = tmp_path / "media", tmp_path / "out"
    media.mkdir()
    out.mkdir()
    noserverino(monkeypatch, tmp_path, remount=True)

    check_hardlinks(media, out)

    assert not list(media.iterdir()) and not list(out.iterdir())


def test_preflight_probes_the_closest_existing_dir(tmp_path):
    media = tmp_path / "media"
    media.mkdir()

    check_hardlinks(media, tmp_path / "not" / "yet")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["media"]


def test_preflight_cleans_up_after_a_failure(tmp_path, monkeypatch):
    media, out = tmp_path / "media", tmp_path / "out"
    media.mkdir()
    out.mkdir()

    def broken_read(self):
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(Path, "read_bytes", broken_read)

    with pytest.raises(OSError):
        check_hardlinks(media, out)
    monkeypatch.undo()
    assert not list(media.iterdir()) and not list(out.iterdir())


def test_preflight_needs_a_writable_media_dir(tmp_path):
    with pytest.raises(EmbyRootError, match="can't create a hardlink probe"):
        check_hardlinks(tmp_path / "missing", tmp_path)


def test_writer_probes_in_its_media_root(tmp_path, monkeypatch):
    source = media_file(tmp_path)
    seen = []
    monkeypatch.setattr(
        writer_module, "check_hardlinks", lambda m, r: seen.append((m, r))
    )

    EmbyWriter(tmp_path / "out", media_root=tmp_path / "lib").sync(
        [link("s/v.mp4", source)], "s"
    )
    EmbyWriter(tmp_path / "out").sync([link("s/v.mp4", source)], "s")

    assert seen == [
        (tmp_path / "lib", tmp_path / "out"),
        (source.parent, tmp_path / "out"),
    ]


def test_manifest_from_a_newer_writer_is_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / MANIFEST_NAME).write_text(
        json.dumps({"version": 4, "files": {}, "dirs": []})
    )

    with pytest.raises(writer_module.ManifestTooNew):
        EmbyWriter(out)

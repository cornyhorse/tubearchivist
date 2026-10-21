"""
regression tests for the 2026-10-11 review of the Emby writer:
remounts (E2), tampered manifests (E3), crashes (E4), cross-filesystem
roots (E5), corrupt manifests (E10), temp files (E12) and per-series
failures (E1)
"""

import errno
import json
import os
import shutil
from pathlib import Path

import pytest
from emby.src import writer as writer_module
from emby.src.writer import (
    MANIFEST_NAME,
    DesiredFile,
    EmbyRootError,
    EmbyWriter,
    safe_relpath,
)


def media_file(tmp_path, name="v.mp4", data=b"video"):
    """a file in a fake archive next to the output root"""
    path = tmp_path / "media" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def link(relpath, source, owner="o"):
    """a desired media hardlink"""
    return DesiredFile(relpath, "link", owner, "media", source=source)


def data(relpath, content=b"x", owner="o", role="nfo"):
    """a desired sidecar"""
    return DesiredFile(relpath, "data", owner, role, data=content)


def read_manifest(out: Path) -> dict:
    """the manifest as written"""
    return json.loads((out / MANIFEST_NAME).read_text())


def write_manifest(out: Path, manifest: dict) -> None:
    """replace the manifest, as a tampering hand or old writer would"""
    (out / MANIFEST_NAME).write_text(json.dumps(manifest))


# E2: CIFS st_dev changes on every remount


class FakeStat:
    """a stat result with some fields replaced, the rest passed through"""

    def __init__(self, real, **fields):
        self._real = real
        self._fields = fields

    def __getattr__(self, name):
        if name in self._fields:
            return self._fields[name]
        return getattr(self._real, name)


def remount(monkeypatch):
    """every later stat reports another st_dev, as after a CIFS remount"""
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        return FakeStat(result, st_dev=result.st_dev + 1000)

    monkeypatch.setattr(Path, "stat", stat)


def test_links_are_recognised_after_remount(tmp_path, monkeypatch):
    first, second = media_file(tmp_path, "a.mp4"), media_file(tmp_path)
    out = tmp_path / "out"
    EmbyWriter(out).sync(
        [link("s/a.mp4", first, "a"), link("s/b.mp4", second, "b")], "s"
    )
    remount(monkeypatch)

    report = EmbyWriter(out).sync([link("s/c.mp4", first, "a")], "s")

    assert report.renamed == [("s/a.mp4", "s/c.mp4")]
    assert report.deleted == ["s/b.mp4"]
    assert not report.warnings
    assert sorted(p.name for p in (out / "s").iterdir()) == ["c.mp4"]


def test_manifest_records_size_mtime_and_source(tmp_path):
    source = media_file(tmp_path)
    out = tmp_path / "out"

    EmbyWriter(out).sync([link("s/v.mp4", source)], "s")

    manifest = read_manifest(out)
    assert manifest["version"] == 3
    assert manifest["files"]["s/v.mp4"] == {
        "kind": "link",
        "owner": "o",
        "role": "media",
        "size": 5,
        "mtime_ns": source.stat().st_mtime_ns,
        "source": str(source),
    }


def test_version_1_manifest_is_upgraded(tmp_path):
    source = media_file(tmp_path)
    out = tmp_path / "out"
    EmbyWriter(out).sync([link("s/v.mp4", source), data("s/v.nfo")], "s")
    manifest = read_manifest(out)
    manifest["version"] = 1
    manifest["files"]["s/v.mp4"] = {
        "kind": "link",
        "owner": "o",
        "role": "media",
        "inode": [12345, source.stat().st_ino],
    }
    write_manifest(out, manifest)

    report = EmbyWriter(out).sync([], "s")

    assert sorted(report.deleted) == ["s/", "s/v.mp4", "s/v.nfo"]
    assert read_manifest(out) == {"version": 3, "files": {}, "dirs": []}


def test_replaced_link_is_not_ours(tmp_path):
    source = media_file(tmp_path)
    out = tmp_path / "out"
    EmbyWriter(out).sync([link("s/v.mp4", source)], "s")
    (out / "s" / "v.mp4").unlink()
    (out / "s" / "v.mp4").write_bytes(b"someone else's file")

    report = EmbyWriter(out).sync([], "s")

    assert report.warnings == ["s/v.mp4: changed outside writer"]
    assert (out / "s" / "v.mp4").exists()


# E3: manifest entries are not trusted


@pytest.mark.parametrize(
    "relpath",
    [
        "../victim.mp4",
        "s/../../victim.mp4",
        "/abs/victim.mp4",
        "s//v.mp4",
        "./s/v.mp4",
        "s\\v.mp4",
        "s/v\x00.mp4",
        "",
        MANIFEST_NAME,
    ],
)
def test_unsafe_relpaths(relpath):
    assert not safe_relpath(relpath)


def test_safe_relpaths():
    assert safe_relpath("channels/Name [UC1]/Season 2025/a..b.mp4")
    assert not safe_relpath(None)


def test_tampered_manifest_cannot_reach_outside_root(tmp_path):
    victim = media_file(tmp_path, "victim.mp4")
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/v.nfo")], "s")
    (out / "evil").symlink_to(victim.parent)
    manifest = read_manifest(out)
    entry = {
        "kind": "link",
        "owner": "x",
        "role": "media",
        "ino": victim.stat().st_ino,
        "size": victim.stat().st_size,
    }
    for relpath in ("../media/victim.mp4", str(victim), "evil/victim.mp4"):
        manifest["files"][relpath] = entry
    manifest["dirs"] += ["../media", "evil"]
    write_manifest(out, manifest)

    report = EmbyWriter(out).sync([], None)

    assert victim.read_bytes() == b"video"
    assert victim.parent.is_dir()
    assert any("dropped '../media/victim.mp4'" in w for w in report.warnings)
    assert any("dropped dir '../media'" in w for w in report.warnings)
    assert len(report.errors) == 1
    assert report.errors[0].startswith("evil")
    assert "escapes root" in report.errors[0]


@pytest.mark.parametrize(
    "entry, problem",
    [
        ("text", "not an object"),
        ({"kind": "data", "role": "r", "sha256": "0"}, "no owner or role"),
        ({"kind": "link", "owner": "o", "role": "r"}, "link without identity"),
        ({"kind": "data", "owner": "o", "role": "r"}, "unknown kind"),
        ({"kind": "dir", "owner": "o", "role": "r"}, "unknown kind"),
        (
            {"kind": "link", "owner": "o", "role": "r", "inode": [1, 2, 3]},
            "link without identity",
        ),
    ],
)
def test_malformed_entries_are_dropped(tmp_path, entry, problem):
    out = tmp_path / "out"
    out.mkdir()
    write_manifest(out, {"version": 1, "files": {"s/x": entry}, "dirs": ["s"]})

    writer = EmbyWriter(out)

    assert writer.manifest.files == {}
    assert writer.manifest.warnings == [f"manifest: dropped 's/x': {problem}"]


# E4: a crash mid-run


def test_crash_after_vacate_resumes_the_moves(tmp_path, monkeypatch):
    first, second = media_file(tmp_path, "a.mp4"), media_file(tmp_path)
    out = tmp_path / "out"
    EmbyWriter(out).sync(
        [link("s/1.mp4", first, "a"), link("s/2.mp4", second, "b")], "s"
    )
    swapped = [link("s/2.mp4", first, "a"), link("s/1.mp4", second, "b")]

    def crash(*_):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(EmbyWriter, "_move_into_place", crash)
        with pytest.raises(KeyboardInterrupt):
            EmbyWriter(out).sync(swapped, "s")

    on_disk = sorted(read_manifest(out)["files"])
    assert all(p.startswith("s/.ta-move-") for p in on_disk)

    report = EmbyWriter(out).sync(swapped, "s")

    assert len(report.renamed) == 2 and not report.created
    assert (out / "s" / "2.mp4").stat().st_ino == first.stat().st_ino
    assert sorted(p.name for p in (out / "s").iterdir()) == ["1.mp4", "2.mp4"]


def test_move_temp_names_are_not_reused(tmp_path):
    names = {
        writer_module._temp_name(tmp_path / "x.mp4", ".ta-move-")
        for _ in range(20)
    }
    assert len(names) == 20
    assert all(n.suffix == ".mp4" for n in names)


def test_leftover_temp_files_are_swept(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/v.nfo")], "s")
    leftovers = [
        ".ta-tmp-abc",
        ".ta-move-123-4.mp4",
        ".v.nfo.tmp-77",
        ".ta-preflight-1.mp4",
    ]
    for name in leftovers:
        (out / "s" / name).write_bytes(b"left")
    (out / "s" / "keep.txt").write_bytes(b"foreign")
    (out / "foreign").mkdir()
    (out / "foreign" / ".ta-tmp-x").write_bytes(b"not our dir")

    dry = EmbyWriter(out, dry_run=True).sync([data("s/v.nfo")], "s")
    assert len(dry.warnings) == 4
    assert (out / "s" / ".ta-tmp-abc").exists()

    report = EmbyWriter(out).sync([data("s/v.nfo")], "s")

    assert sorted(report.warnings) == sorted(
        f"s/{name}: removed leftover" for name in leftovers
    )
    assert sorted(p.name for p in (out / "s").iterdir()) == [
        "keep.txt",
        "v.nfo",
    ]
    assert (out / "foreign" / ".ta-tmp-x").exists()


def test_sweep_skips_symlinked_and_escaping_dirs(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/t/v.nfo")], "s")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".ta-tmp-x").write_bytes(b"not ours")
    shutil.rmtree(out / "s" / "t")
    (out / "s" / "t").symlink_to(outside)
    manifest = read_manifest(out)
    manifest["dirs"].append("s/t/deeper")
    write_manifest(out, manifest)

    EmbyWriter(out, dry_run=True).sync([], "x")

    assert (outside / ".ta-tmp-x").exists()


# E5: different filesystem or fake inodes


def test_preflight_refuses_other_filesystem(tmp_path, monkeypatch):
    source = media_file(tmp_path)

    def exdev(*_):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(writer_module.os, "link", exdev)

    with pytest.raises(EmbyRootError, match="another filesystem"):
        EmbyWriter(tmp_path / "out").sync([link("s/v.mp4", source)], "s")
    assert list((tmp_path / "out").iterdir()) == []


def test_preflight_refuses_a_link_that_is_not_one_file(tmp_path, monkeypatch):
    source = media_file(tmp_path)
    monkeypatch.setattr(writer_module.os, "link", shutil.copyfile)

    with pytest.raises(EmbyRootError, match="not the same file"):
        EmbyWriter(tmp_path / "out").sync([link("s/v.mp4", source)], "s")
    assert list((tmp_path / "out").iterdir()) == []
    assert sorted(p.name for p in (tmp_path / "media").iterdir()) == ["v.mp4"]


def test_preflight_refuses_other_link_errors(tmp_path, monkeypatch):
    source = media_file(tmp_path)

    def denied(*_):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(writer_module.os, "link", denied)

    with pytest.raises(EmbyRootError, match="can't hardlink from"):
        EmbyWriter(tmp_path / "out").sync([link("s/v.mp4", source)], "s")


def test_dry_run_runs_the_preflight_too(tmp_path, monkeypatch):
    source = media_file(tmp_path)

    def refuse(*_):
        raise EmbyRootError("no")

    monkeypatch.setattr(writer_module, "check_hardlinks", refuse)

    with pytest.raises(EmbyRootError):
        EmbyWriter(tmp_path / "out", dry_run=True).sync(
            [link("s/v.mp4", source)], "s"
        )


def test_missing_sources_skip_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(writer_module, "check_hardlinks", None)

    gone = tmp_path / "gone.mp4"
    report = EmbyWriter(tmp_path / "out").sync([link("s/v.mp4", gone)], "s")
    assert report.warnings == ["s/v.mp4: missing source"]


# E10: an unreadable manifest


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"version": 2, "files": [], "dirs": []}',
        '{"version": 0, "files": {}, "dirs": []}',
        '{"version": "2", "files": {}, "dirs": []}',
        '{"version": 2, "dirs": []}',
        b"\xff\xfe",
    ],
)
def test_corrupt_manifest_is_set_aside_and_rebuilt(tmp_path, content):
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/v.nfo")], "s")
    inode = (out / "s" / "v.nfo").stat().st_ino
    if isinstance(content, bytes):
        (out / MANIFEST_NAME).write_bytes(content)
    else:
        (out / MANIFEST_NAME).write_text(content)

    dry = EmbyWriter(out, dry_run=True).sync([data("s/v.nfo")], "s")
    assert not list(out.glob(f"{MANIFEST_NAME}.corrupt-*"))
    assert "unreadable" in dry.warnings[0]

    report = EmbyWriter(out).sync([data("s/v.nfo")], "s")

    assert "unreadable" in report.warnings[0]
    assert report.unchanged == 1 and not report.changed
    assert (out / "s" / "v.nfo").stat().st_ino == inode
    assert len(list(out.glob(f"{MANIFEST_NAME}.corrupt-*"))) == 1
    assert "s/v.nfo" in read_manifest(out)["files"]


# E12: temp files


def test_planted_symlink_at_old_temp_name_is_not_followed(tmp_path):
    victim = media_file(tmp_path, "victim.txt", b"keep me")
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/v.nfo", b"one")], "s")
    planted = out / "s" / f".v.nfo.tmp-{os.getpid()}"
    planted.symlink_to(victim)

    EmbyWriter(out).sync([data("s/v.nfo", b"two")], "s")

    assert victim.read_bytes() == b"keep me"
    assert (out / "s" / "v.nfo").read_bytes() == b"two"
    assert not planted.is_symlink()


def test_atomic_write_never_reuses_a_name(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def replace(src, dst):
        seen.append(Path(src).name)
        real_replace(src, dst)

    monkeypatch.setattr(writer_module.os, "replace", replace)
    for n in range(3):
        writer_module.atomic_write(tmp_path / "x", str(n).encode(), 0o644)

    assert len(set(seen)) == 3
    assert all(name.startswith(".ta-tmp-") for name in seen)
    assert (tmp_path / "x").read_bytes() == b"2"


# E1: one failing series doesn't stop the others


def test_failure_in_one_series_leaves_it_alone(tmp_path, monkeypatch):
    out = tmp_path / "out"
    EmbyWriter(out).sync(
        [
            data("c/A/a.nfo", owner="a"),
            data("c/A/old.nfo", owner="old"),
            data("c/B/b.nfo", owner="b"),
        ],
        None,
    )
    real_write = writer_module.atomic_write

    def fail_in_a(path, *args, **kwargs):
        if "/c/A/" in str(path):
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        real_write(path, *args, **kwargs)

    monkeypatch.setattr(writer_module, "atomic_write", fail_in_a)

    report = EmbyWriter(out).sync(
        [data("c/A/a.nfo", b"new", "a"), data("c/B/b.nfo", b"new", "b")],
        None,
    )

    assert len(report.errors) == 1
    assert report.errors[0].startswith("c/A/a.nfo: ")
    assert "File name too long" in report.errors[0]
    assert (out / "c" / "A" / "old.nfo").exists()
    assert (out / "c" / "A" / "a.nfo").read_bytes() == b"x"
    assert (out / "c" / "B" / "b.nfo").read_bytes() == b"new"
    assert "c/A/old.nfo" in read_manifest(out)["files"]


def test_kept_series_is_not_cleaned_up(tmp_path):
    out = tmp_path / "out"
    EmbyWriter(out).sync(
        [data("c/A/a.nfo"), data("c/B/b.nfo", owner="b")], None
    )

    report = EmbyWriter(out).sync([], None, keep={"c/A"})

    assert report.deleted == ["c/B/b.nfo", "c/B/"]
    assert (out / "c" / "A" / "a.nfo").exists()


def test_abs_refuses_unsafe_paths(tmp_path):
    with pytest.raises(ValueError, match="unsafe path"):
        EmbyWriter(tmp_path)._abs("../x")


def test_owned_file_replaced_by_symlink_is_left(tmp_path):
    victim = media_file(tmp_path, "victim.txt", b"keep me")
    out = tmp_path / "out"
    EmbyWriter(out).sync([data("s/v.nfo")], "s")
    (out / "s" / "v.nfo").unlink()
    (out / "s" / "v.nfo").symlink_to(victim)

    report = EmbyWriter(out).sync([], "s")

    assert report.warnings == ["s/v.nfo: changed outside writer"]
    assert victim.read_bytes() == b"keep me"

"""
Functionality:
- reconcile a desired Emby tree against disk, touching only what changed
- media are hardlinks to the archive, sidecars are written atomically
  (temp file + rename) and only when their bytes differ
- every file written is recorded in a manifest; only manifest files are
  ever renamed or deleted, nothing is wiped, directories are removed only
  when empty and created by the writer
- links are recognised by size, mtime and their recorded source, never by
  inode number or device: a CIFS mount changes st_dev on every remount and
  stops reporting the server's inode numbers when it autodisables
  serverino, while a hardlink still shares size and mtime with its source
- a failure in one series is reported and leaves that series as it was,
  the other series still sync
"""

import errno
import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MANIFEST_NAME = ".ta-emby-manifest.json"
# 1: links recorded as [st_dev, st_ino]; 2: st_ino and size, because the
# st_dev of a CIFS mount is anonymous and changes on every remount;
# 3: size, mtime_ns and source path, because a CIFS mount that autodisabled
# server inode numbers (noserverino) reports made-up st_ino values.
# Older link entries keep their st_ino until a sync upgrades them
MANIFEST_VERSION = 3
TEMP_PREFIX = ".ta-tmp-"
MOVE_PREFIX = ".ta-move-"
PREFLIGHT_PREFIX = ".ta-preflight-"
# temp names written before TEMP_PREFIX existed
_LEGACY_TEMP = re.compile(r"\..+\.tmp-\d+")


class EmbyRootError(RuntimeError):
    """the Emby root can't hold hardlinks to the media"""


class ManifestTooNew(RuntimeError):
    """manifest written by a newer writer, refuse to guess"""


@dataclass(frozen=True)
class DesiredFile:
    """
    one file the tree should contain
    kind "link": hardlink to source; kind "data": exactly these bytes
    owner/role identify the file across renames, e.g. (youtube_id, "nfo")
    """

    relpath: str
    kind: str
    owner: str
    role: str
    source: Path | None = None
    data: bytes | None = None


@dataclass
class SyncReport:
    """what a sync did"""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # failures; the series they belong to was left as it was
    errors: list[str] = field(default_factory=list)
    # series folder (or <kind>/<id> if it couldn't be planned): first error
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> int:
        """number of filesystem changes made"""
        return (
            len(self.created)
            + len(self.updated)
            + len(self.renamed)
            + len(self.deleted)
        )


def sha256(data: bytes) -> str:
    """hex digest"""
    return hashlib.sha256(data).hexdigest()


def safe_relpath(relpath) -> bool:
    """a normalised relative path that can't leave the root"""
    if not isinstance(relpath, str) or not relpath:
        return False
    if "\\" in relpath or "\x00" in relpath or relpath == MANIFEST_NAME:
        return False
    path = PurePosixPath(relpath)
    return (
        not path.is_absolute()
        and str(path) == relpath
        and ".." not in path.parts
    )


def series_scope(relpath: str) -> str:
    """the series folder a relpath belongs to: <kind>/<folder>"""
    return "/".join(PurePosixPath(relpath).parts[:2])


def _entry_problem(entry) -> str | None:
    """why a manifest entry can't be used, None if it can"""
    if not isinstance(entry, dict):
        return "not an object"
    if not all(isinstance(entry.get(k), str) for k in ("owner", "role")):
        return "no owner or role"
    if entry.get("kind") == "link":
        if _has_identity(entry) or isinstance(entry.get("ino"), int):
            return None
        return "link without identity"
    if entry.get("kind") == "data" and isinstance(entry.get("sha256"), str):
        return None
    return "unknown kind"


class Manifest:
    """record of files and directories the writer owns under root"""

    def __init__(self, root: Path, dry_run: bool = False):
        self.path = root / MANIFEST_NAME
        self.dry_run = dry_run
        self.files: dict[str, dict] = {}
        self.dirs: set[str] = set()
        self.warnings: list[str] = []
        if self.path.exists() or self.path.is_symlink():
            self._load()
        self._by_owner = {
            (e["owner"], e["role"]): relpath
            for relpath, e in self.files.items()
        }

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            version = raw["version"]
            if isinstance(version, int) and version > MANIFEST_VERSION:
                raise ManifestTooNew(
                    f"{self.path} is version {version}, this writer "
                    f"knows {MANIFEST_VERSION}; refusing to touch it"
                )
            if version not in (1, 2, MANIFEST_VERSION):
                raise ValueError(f"unknown version {version!r}")
            files, dirs = raw["files"], raw["dirs"]
            if not isinstance(files, dict) or not isinstance(dirs, list):
                raise ValueError("files or dirs of the wrong type")
            self._take(files, dirs, version)
        except (ValueError, KeyError, TypeError) as err:
            self._quarantine(err)

    def _take(self, files: dict, dirs: list, version: int) -> None:
        """keep the entries that are safe to act on"""
        for relpath, entry in files.items():
            if version == 1:
                entry = _v1_entry(entry)
            problem = _entry_problem(entry)
            if not safe_relpath(relpath):
                problem = "unsafe path"
            if problem:
                self.warnings.append(
                    f"manifest: dropped {relpath!r}: {problem}"
                )
                continue
            self.files[relpath] = entry
        for relpath in dirs:
            if safe_relpath(relpath):
                self.dirs.add(relpath)
            else:
                self.warnings.append(f"manifest: dropped dir {relpath!r}")

    def _quarantine(self, err: Exception) -> None:
        """
        an unreadable manifest is set aside and rebuilt: identical files
        are adopted again, so a rebuild writes nothing
        """
        self.files, self.dirs = {}, set()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{MANIFEST_NAME}.corrupt-{stamp}")
        if not self.dry_run:
            os.rename(self.path, target)
        self.warnings.append(
            f"manifest unreadable ({err}), moved to {target.name}, rebuilding"
        )

    def owned_by(self, owner: str, role: str) -> str | None:
        """relpath currently recorded for owner and role"""
        relpath = self._by_owner.get((owner, role))
        entry = self.files.get(relpath)
        if entry and (entry["owner"], entry["role"]) == (owner, role):
            return relpath
        return None

    def set(self, relpath: str, entry: dict) -> None:
        """record a file"""
        self.files[relpath] = entry
        self._by_owner[(entry["owner"], entry["role"])] = relpath

    def forget(self, relpath: str) -> dict:
        """stop recording a file"""
        return self.files.pop(relpath)

    def save(self) -> None:
        """write atomically, sorted for stable diffs"""
        payload = {
            "version": MANIFEST_VERSION,
            "files": dict(sorted(self.files.items())),
            "dirs": sorted(self.dirs),
        }
        data = json.dumps(payload, indent=1, ensure_ascii=False).encode()
        atomic_write(self.path, data, mode=0o644)


def _v1_entry(entry):
    """version 1 recorded links as [st_dev, st_ino]; keep only st_ino"""
    if not isinstance(entry, dict) or "inode" not in entry:
        return entry
    inode = entry["inode"]
    upgraded = {k: v for k, v in entry.items() if k != "inode"}
    if isinstance(inode, list) and len(inode) == 2:
        upgraded["ino"] = inode[1]
    return upgraded


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """
    write to a new unpredictable temp file in the same dir (O_EXCL, so an
    existing name or symlink is never followed), fsync, rename over path
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=TEMP_PREFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _temp_name(path: Path, prefix: str) -> Path:
    return path.with_name(f"{prefix}{secrets.token_hex(8)}{path.suffix}")


def atomic_link(source: Path, path: Path) -> None:
    """hardlink source to a new temp name, rename over path"""
    tmp = _temp_name(path, TEMP_PREFIX)
    try:
        os.link(source, tmp)
    except OSError as err:
        if err.errno == errno.EXDEV:
            raise OSError(
                err.errno,
                f"can't hardlink across filesystems: {source} -> {path}",
            ) from err
        raise
    try:
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _has_identity(entry: dict) -> bool:
    """a version 3 link entry: size and mtime_ns recorded"""
    return all(
        isinstance(entry.get(key), int) and not isinstance(entry[key], bool)
        for key in ("size", "mtime_ns")
    )


def link_identity(stat: os.stat_result) -> dict:
    """
    what a hardlink shares with its source whatever the mount reports for
    st_ino and st_dev: one inode on the server, so one size and one mtime
    """
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _same_content(a: os.stat_result, b: os.stat_result) -> bool:
    return os.path.samestat(a, b) or link_identity(a) == link_identity(b)


def _write_through(path: Path, data: bytes) -> None:
    with open(path, "r+b") as f:
        f.write(data)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())


def nearest_dir(path: Path) -> Path:
    """path, or its closest existing parent"""
    return next(p for p in (path, *path.parents) if p.is_dir())


def check_hardlinks(media_dir: Path, root: Path) -> None:
    """
    preflight, by behaviour rather than inode numbers (a CIFS mount that
    autodisabled serverino reports different st_ino for two names of one
    file): create a probe in a scratch dir under media_dir, hardlink it
    into root, write through one name and read through the other, both
    ways; fails if the link can't be made or the names aren't one file
    """
    root = nearest_dir(root)
    try:
        scratch = Path(
            tempfile.mkdtemp(prefix=PREFLIGHT_PREFIX, dir=media_dir)
        )
    except OSError as err:
        raise EmbyRootError(
            f"can't create a hardlink probe in {media_dir}: {err}"
        ) from err
    source = scratch / "probe"
    probe = _temp_name(root / "probe", PREFLIGHT_PREFIX)
    try:
        source.write_bytes(b"ta-emby-preflight")
        try:
            os.link(source, probe)
        except OSError as err:
            if err.errno == errno.EXDEV:
                raise EmbyRootError(
                    f"{root} is on another filesystem than the media "
                    f"({media_dir}); hardlinks need the same mount"
                ) from err
            raise EmbyRootError(
                f"can't hardlink from {media_dir} into {root}: {err}"
            ) from err
        for written, read in ((probe, source), (source, probe)):
            token = secrets.token_hex(16).encode()
            _write_through(written, token)
            if read.read_bytes() != token:
                raise EmbyRootError(
                    f"a hardlink in {root} is not the same file as its "
                    f"source in {media_dir}: a write through one name "
                    "didn't show through the other"
                )
    finally:
        for path in (probe, source):
            if os.path.lexists(path):
                path.unlink()
        scratch.rmdir()


class EmbyWriter:
    """sync desired files into root"""

    def __init__(
        self,
        root: Path,
        dry_run: bool = False,
        file_mode: int = 0o644,
        dir_mode: int = 0o755,
        media_root: Path | None = None,
    ):
        self.root = Path(root)
        # where the preflight puts its probe; defaults to a source's dir
        self.media_root = Path(media_root) if media_root else None
        self.dry_run = dry_run
        self.file_mode = file_mode
        self.dir_mode = dir_mode
        if not dry_run:
            self.root.mkdir(parents=True, exist_ok=True)
        self.manifest = Manifest(self.root, dry_run=dry_run)
        self.report = SyncReport()
        # dry run bookkeeping: nothing moves on disk, so remember where a
        # file really is (_virtual) and which paths a move vacated
        self._virtual: dict[str, str] = {}
        self._vacated: set[str] = set()
        self._moved_from: dict[str, str] = {}
        # series folders to leave alone: failed now or failed to plan
        self._keep: set[str] = set()

    def sync(
        self,
        desired: list[DesiredFile],
        scope: str | None,
        keep: set[str] | None = None,
    ) -> SyncReport:
        """
        make root match desired within scope (a relpath prefix such as
        "channels/Name [id]"), None for the whole root; series folders in
        keep are not cleaned up (their desired files couldn't be planned)
        """
        self.report = SyncReport(warnings=list(self.manifest.warnings))
        self.manifest.warnings.clear()
        self._keep = set(keep or ())
        self._check_desired(desired, scope)
        self._preflight(desired)
        self._sweep_temp()
        self._upgrade_links(desired)

        self._vacate_moves(desired)
        if self._moved_from and not self.dry_run:
            # a crash from here on finds the vacated files in the manifest
            self.manifest.save()
        for item in desired:
            self._guarded(item.relpath, self._sync_file, item)

        wanted = {d.relpath for d in desired}
        for relpath in sorted(self.manifest.files):
            if relpath not in wanted and self._may_clean(relpath, scope):
                self._guarded(relpath, self._remove, relpath)

        self._prune_dirs(scope, wanted)
        if not self.dry_run:
            self.manifest.save()

        return self.report

    @staticmethod
    def _check_desired(desired: list[DesiredFile], scope: str | None):
        paths = [d.relpath for d in desired]
        if len(set(paths)) != len(paths):
            raise ValueError("desired files contain duplicate paths")
        for item in desired:
            if not safe_relpath(item.relpath):
                raise ValueError(f"unsafe path: {item.relpath!r}")
            if scope and not _within(item.relpath, scope):
                raise ValueError(f"{item.relpath} outside scope {scope}")

    def _preflight(self, desired: list[DesiredFile]) -> None:
        """
        the same check in a dry run, so a dry run can't pass where the
        real run then fails; it only creates and removes a probe
        """
        media_dir = self.media_root or next(
            (
                d.source.parent
                for d in desired
                if d.kind == "link" and d.source.is_file()
            ),
            None,
        )
        if media_dir:
            check_hardlinks(media_dir, self.root)

    def _upgrade_links(self, desired: list[DesiredFile]) -> None:
        """
        give link entries from manifest version 1 and 2 (st_ino) the
        version 3 identity, without relinking: the recorded file still
        has the inode we recorded, or it matches the source it should be
        a link of in size and mtime
        """
        for item in desired:
            if item.kind != "link":
                continue
            relpath = self.manifest.owned_by(item.owner, item.role)
            entry = self.manifest.files.get(relpath) if relpath else None
            if not entry or _has_identity(entry) or not self._exists(relpath):
                continue
            path = self._real(relpath)
            if path.is_symlink() or not item.source.is_file():
                continue
            have, want = path.stat(), item.source.stat()
            same_size = have.st_size == entry.get("size", have.st_size)
            if same_size and (
                have.st_ino == entry["ino"] or _same_content(have, want)
            ):
                upgraded = {k: v for k, v in entry.items() if k != "ino"}
                upgraded.update(link_identity(have), source=str(item.source))
                self.manifest.set(relpath, upgraded)

    def _guarded(self, relpath: str, operation, *args) -> None:
        """run one file operation, a failure only stops its series"""
        if series_scope(relpath) in self._keep:
            return
        try:
            operation(*args)
        except (OSError, ValueError) as err:
            self.report.errors.append(f"{relpath}: {err}")
            self.report.failed.setdefault(series_scope(relpath), str(err))
            self._keep.add(series_scope(relpath))

    def _may_clean(self, relpath: str, scope: str | None) -> bool:
        return _within(relpath, scope) and not any(
            _within(relpath, kept) for kept in self._keep
        )

    def leftover_temp_files(self) -> list[str]:
        """temp files a crashed run left in directories we own"""
        found = []
        for rel in sorted({"."} | self.manifest.dirs):
            try:
                path = self.root if rel == "." else self._abs(rel)
            except ValueError:
                continue
            if path.is_symlink() or not path.is_dir():
                continue
            for child in sorted(path.iterdir()):
                relpath = str(child.relative_to(self.root))
                if relpath not in self.manifest.files and _is_temp(child.name):
                    found.append(relpath)
        return found

    def _sweep_temp(self) -> None:
        for relpath in self.leftover_temp_files():
            if not self.dry_run:
                (self.root / relpath).unlink()
            self.report.warnings.append(f"{relpath}: removed leftover")

    def _abs(self, relpath: str) -> Path:
        # resolve the parent only: a symlinked dir escaping root is an
        # error, a symlink in place of a file is handled as a conflict
        if not safe_relpath(relpath):
            raise ValueError(f"unsafe path: {relpath!r}")
        path = (self.root / relpath).parent.resolve()
        if not _within(str(path), str(self.root.resolve())):
            raise ValueError(f"path escapes root: {relpath}")
        return self.root / relpath

    def _ensure_dir(self, relpath: str) -> None:
        """create parents of relpath, recording the ones we create"""
        parts = Path(relpath).parent.parts
        for depth in range(1, len(parts) + 1):
            rel = str(Path(*parts[:depth]))
            path = self.root / rel
            if path.is_dir():
                continue
            if not self.dry_run:
                path.mkdir(mode=self.dir_mode)
                os.chmod(path, self.dir_mode)
            self.manifest.dirs.add(rel)

    def _real(self, relpath: str) -> Path:
        """where the file recorded at relpath is on disk"""
        return self._abs(self._virtual.get(relpath, relpath))

    def _exists(self, relpath: str) -> bool:
        if relpath in self._vacated:
            return False
        path = self._real(relpath)
        return path.exists() or path.is_symlink()

    def _vacate_moves(self, desired: list[DesiredFile]) -> None:
        """
        first rename phase: move every file whose name changes to a hidden
        temp name next to it, so swaps and cycles can't overwrite each other
        """
        for item in desired:
            old = self.manifest.owned_by(item.owner, item.role)
            if old and old != item.relpath:
                self._guarded(old, self._vacate, old)

    def _vacate(self, old: str) -> None:
        if not self._is_ours(old):
            return
        tmp = str(_temp_name(Path(old), MOVE_PREFIX))
        if self.dry_run:
            self._virtual[tmp] = old
            self._vacated.add(old)
        else:
            os.rename(self._abs(old), self._abs(tmp))
        self.manifest.set(tmp, self.manifest.forget(old))
        self._moved_from[tmp] = self._moved_from.pop(old, old)

    def _is_current(self, item: DesiredFile, relpath: str) -> bool:
        if not self._exists(relpath):
            return False
        path = self._real(relpath)
        if path.is_symlink():
            return False
        if item.kind == "link":
            return _same_content(path.stat(), item.source.stat())
        return path.read_bytes() == item.data

    def _is_ours(self, relpath: str) -> bool:
        """file at relpath is still the one we recorded"""
        entry = self.manifest.files.get(relpath)
        if not entry or not self._exists(relpath):
            return False
        path = self._real(relpath)
        if path.is_symlink():
            return False
        if entry["kind"] == "link":
            return self._is_our_link(entry, path)
        return sha256(path.read_bytes()) == entry["sha256"]

    @staticmethod
    def _is_our_link(entry: dict, path: Path) -> bool:
        """
        the file still looks like the link we made: as recorded, or (the
        source changed in place, and the link with it) like the recorded
        source, which must be another file than this one
        """
        stat = path.stat()
        if not _has_identity(entry):
            # version 1/2 entry no sync could upgrade
            size = entry.get("size", stat.st_size)
            return (stat.st_ino, stat.st_size) == (entry["ino"], size)
        if link_identity(stat) == {
            "size": entry["size"],
            "mtime_ns": entry["mtime_ns"],
        }:
            return True
        source = entry.get("source")
        if not isinstance(source, str) or not os.path.isfile(source):
            return False
        if os.path.realpath(source) == os.path.realpath(path):
            return False
        return link_identity(stat) == link_identity(os.stat(source))

    def _record(self, item: DesiredFile) -> None:
        entry = {"kind": item.kind, "owner": item.owner, "role": item.role}
        if item.kind == "link":
            entry.update(link_identity(item.source.stat()))
            entry["source"] = str(item.source)
        else:
            entry["sha256"] = sha256(item.data)
        self.manifest.set(item.relpath, entry)

    def _sync_file(self, item: DesiredFile) -> None:
        path = self._abs(item.relpath)
        if item.kind == "link" and not item.source.exists():
            self.report.warnings.append(f"{item.relpath}: missing source")
            return

        self._move_into_place(item, path)

        # an identical file is adopted, so a lost manifest rebuilds without
        # rewriting anything
        if self._is_current(item, item.relpath):
            self._record(item)
            self.report.unchanged += 1
            return

        exists = self._exists(item.relpath)
        if exists and item.relpath not in self.manifest.files:
            self.report.conflicts.append(f"{item.relpath}: not owned, kept")
            return

        self._ensure_dir(item.relpath)
        if not self.dry_run:
            if item.kind == "link":
                atomic_link(item.source, path)
            else:
                atomic_write(path, item.data, self.file_mode)

        self._record(item)
        if exists:
            self.report.updated.append(item.relpath)
        else:
            self.report.created.append(item.relpath)

    def _move_into_place(self, item: DesiredFile, path: Path) -> None:
        """
        same owner and role recorded under another name: move it, keeping
        the inode, so Emby sees a rename instead of delete plus create
        (second phase, after _vacate_moves)
        """
        old = self.manifest.owned_by(item.owner, item.role)
        if not old or old == item.relpath or self._exists(item.relpath):
            return
        if not self._is_ours(old):
            self.report.warnings.append(f"{old}: changed outside writer")
            self.manifest.forget(old)
            return
        self._ensure_dir(item.relpath)
        if self.dry_run:
            self._virtual[item.relpath] = self._virtual.pop(old, old)
            self._vacated.discard(item.relpath)
        else:
            os.rename(self._abs(old), path)
        self.manifest.set(item.relpath, self.manifest.forget(old))
        origin = self._moved_from.pop(old, old)
        self.report.renamed.append((origin, item.relpath))

    def _remove(self, relpath: str) -> None:
        origin = self._moved_from.pop(relpath, relpath)
        if self._is_source(relpath):
            # never unlink the archive's own name of a video
            self.report.warnings.append(f"{origin}: is its own source, kept")
        elif self._is_ours(relpath):
            if not self.dry_run:
                self._abs(relpath).unlink()
            self.report.deleted.append(origin)
        elif self._exists(relpath):
            self.report.warnings.append(f"{origin}: changed outside writer")
        self.manifest.forget(relpath)

    def _is_source(self, relpath: str) -> bool:
        entry = self.manifest.files[relpath]
        source = entry.get("source")
        return isinstance(source, str) and os.path.realpath(
            source
        ) == os.path.realpath(self._real(relpath))

    def _prune_dirs(self, scope: str | None, wanted: set[str]) -> None:
        """remove empty directories we created that nothing needs"""
        needed = set()
        for relpath in wanted | set(self.manifest.files):
            parent = Path(relpath).parent
            while str(parent) != ".":
                needed.add(str(parent))
                parent = parent.parent

        candidates = [
            d
            for d in self.manifest.dirs
            if self._may_clean(d, scope) and d not in needed
        ]
        # in a dry run nothing is removed, so count what would be gone
        gone = {p.rstrip("/") for p in self.report.deleted}
        for rel in sorted(candidates, key=lambda d: -len(Path(d).parts)):
            self._guarded(rel, self._prune_dir, rel, gone)

    def _prune_dir(self, rel: str, gone: set[str]) -> None:
        path = self._abs(rel)
        if path.is_symlink() or not path.is_dir():
            self.manifest.dirs.discard(rel)
            return
        remaining = [
            child
            for child in path.iterdir()
            if str(child.relative_to(self.root)) not in gone
        ]
        if not remaining:
            if not self.dry_run:
                path.rmdir()
            self.manifest.dirs.discard(rel)
            self.report.deleted.append(rel + "/")
            gone.add(rel)


def _is_temp(name: str) -> bool:
    return name.startswith(
        (TEMP_PREFIX, MOVE_PREFIX, PREFLIGHT_PREFIX)
    ) or bool(_LEGACY_TEMP.fullmatch(name))


def _within(relpath: str, scope: str | None) -> bool:
    if scope is None:
        return True
    return relpath == scope or relpath.startswith(scope.rstrip("/") + "/")

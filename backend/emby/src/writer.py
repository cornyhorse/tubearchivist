"""
Functionality:
- reconcile a desired Emby tree against disk, touching only what changed
- media are hardlinks to the archive, sidecars are written atomically
  (temp file + rename) and only when their bytes differ
- every file written is recorded in a manifest; only manifest files are
  ever renamed or deleted, nothing is wiped, directories are removed only
  when empty and created by the writer
"""

import errno
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = ".ta-emby-manifest.json"
MANIFEST_VERSION = 1


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


class Manifest:
    """record of files and directories the writer owns under root"""

    def __init__(self, root: Path):
        self.path = root / MANIFEST_NAME
        self.files: dict[str, dict] = {}
        self.dirs: set[str] = set()
        self._by_owner: dict[tuple[str, str], str] | None = None
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") != MANIFEST_VERSION:
                raise ValueError(f"unsupported manifest: {self.path}")
            self.files = raw["files"]
            self.dirs = set(raw["dirs"])

    def owned_by(self, owner: str, role: str) -> str | None:
        """relpath currently recorded for owner and role"""
        if self._by_owner is None:
            self._by_owner = {
                (e["owner"], e["role"]): relpath
                for relpath, e in self.files.items()
            }
        relpath = self._by_owner.get((owner, role))
        if relpath in self.files:
            return relpath
        return None

    def set(self, relpath: str, entry: dict) -> None:
        """record a file"""
        self.files[relpath] = entry
        if self._by_owner is not None:
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


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """write to a temp file in the same dir, fsync, rename over path"""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_link(source: Path, path: Path) -> None:
    """hardlink source to a temp name, rename over path"""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
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
        if tmp.exists():
            tmp.unlink()


class EmbyWriter:
    """sync desired files into root"""

    def __init__(
        self,
        root: Path,
        dry_run: bool = False,
        file_mode: int = 0o644,
        dir_mode: int = 0o755,
    ):
        self.root = Path(root)
        self.dry_run = dry_run
        self.file_mode = file_mode
        self.dir_mode = dir_mode
        if not dry_run:
            self.root.mkdir(parents=True, exist_ok=True)
        self.manifest = Manifest(self.root)
        self.report = SyncReport()

    def sync(self, desired: list[DesiredFile], scope: str | None) -> SyncReport:
        """
        make root match desired within scope (a relpath prefix such as
        "channels/Name [id]"), None for the whole root
        """
        self.report = SyncReport()
        paths = [d.relpath for d in desired]
        if len(set(paths)) != len(paths):
            raise ValueError("desired files contain duplicate paths")
        for item in desired:
            if scope and not _within(item.relpath, scope):
                raise ValueError(f"{item.relpath} outside scope {scope}")

        for item in desired:
            self._sync_file(item)

        wanted = set(paths)
        for relpath in sorted(self.manifest.files):
            if relpath not in wanted and _within(relpath, scope):
                self._remove(relpath)

        self._prune_dirs(scope, wanted)
        if not self.dry_run:
            self.manifest.save()

        return self.report

    def _abs(self, relpath: str) -> Path:
        path = (self.root / relpath).resolve()
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

    def _is_current(self, item: DesiredFile, path: Path) -> bool:
        if not path.exists() or path.is_symlink():
            return False
        if item.kind == "link":
            have, want = path.stat(), item.source.stat()
            return (have.st_dev, have.st_ino) == (want.st_dev, want.st_ino)
        return path.read_bytes() == item.data

    def _is_ours(self, relpath: str) -> bool:
        """file at relpath is still the one we recorded"""
        entry = self.manifest.files.get(relpath)
        path = self.root / relpath
        if not entry or not path.exists() or path.is_symlink():
            return False
        if entry["kind"] == "link":
            stat = path.stat()
            return [stat.st_dev, stat.st_ino] == entry["inode"]
        return sha256(path.read_bytes()) == entry["sha256"]

    def _record(self, item: DesiredFile) -> None:
        entry = {"kind": item.kind, "owner": item.owner, "role": item.role}
        if item.kind == "link":
            stat = item.source.stat()
            entry["inode"] = [stat.st_dev, stat.st_ino]
        else:
            entry["sha256"] = sha256(item.data)
        self.manifest.set(item.relpath, entry)

    def _sync_file(self, item: DesiredFile) -> None:
        path = self._abs(item.relpath)
        if item.kind == "link" and not item.source.exists():
            self.report.warnings.append(f"{item.relpath}: missing source")
            return

        # same owner and role recorded under another name: move it, keeping
        # the inode, so Emby sees a rename instead of delete plus create
        old = self.manifest.owned_by(item.owner, item.role)
        if old and old != item.relpath and not path.exists():
            if self._is_ours(old):
                self._ensure_dir(item.relpath)
                if not self.dry_run:
                    os.rename(self.root / old, path)
                self.manifest.set(item.relpath, self.manifest.forget(old))
                self.report.renamed.append((old, item.relpath))
            else:
                self.report.warnings.append(f"{old}: changed outside writer")
                self.manifest.forget(old)

        # an identical file is adopted, so a lost manifest rebuilds without
        # rewriting anything
        if self._is_current(item, path):
            self._record(item)
            self.report.unchanged += 1
            return

        exists = path.exists() or path.is_symlink()
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

    def _remove(self, relpath: str) -> None:
        if self._is_ours(relpath):
            if not self.dry_run:
                (self.root / relpath).unlink()
            self.report.deleted.append(relpath)
        elif (self.root / relpath).exists():
            self.report.warnings.append(f"{relpath}: changed outside writer")
        self.manifest.forget(relpath)

    def _prune_dirs(self, scope: str | None, wanted: set[str]) -> None:
        """remove empty directories we created that nothing needs"""
        needed = set()
        for relpath in wanted | set(self.manifest.files):
            parent = Path(relpath).parent
            while str(parent) != ".":
                needed.add(str(parent))
                parent = parent.parent

        candidates = [
            d for d in self.manifest.dirs if _within(d, scope) and d not in needed
        ]
        for rel in sorted(candidates, key=lambda d: -len(Path(d).parts)):
            path = self.root / rel
            if path.is_dir() and not any(path.iterdir()):
                if not self.dry_run:
                    path.rmdir()
                self.manifest.dirs.discard(rel)
                self.report.deleted.append(rel + "/")
            elif not path.exists():
                self.manifest.dirs.discard(rel)


def _within(relpath: str, scope: str | None) -> bool:
    if scope is None:
        return True
    return relpath == scope or relpath.startswith(scope.rstrip("/") + "/")

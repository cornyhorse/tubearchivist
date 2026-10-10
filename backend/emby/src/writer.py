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
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") != MANIFEST_VERSION:
                raise ValueError(f"unsupported manifest: {self.path}")
            self.files = raw["files"]
            self.dirs = set(raw["dirs"])
        self._by_owner = {
            (e["owner"], e["role"]): relpath
            for relpath, e in self.files.items()
        }

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
        # dry run bookkeeping: nothing moves on disk, so remember where a
        # file really is (_virtual) and which paths a move vacated
        self._virtual: dict[str, str] = {}
        self._vacated: set[str] = set()
        self._moved_from: dict[str, str] = {}

    def sync(
        self, desired: list[DesiredFile], scope: str | None
    ) -> SyncReport:
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

        self._vacate_moves(desired)
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
        # resolve the parent only: a symlinked dir escaping root is an
        # error, a symlink in place of a file is handled as a conflict
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
        return self.root / self._virtual.get(relpath, relpath)

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
        for n, item in enumerate(desired):
            old = self.manifest.owned_by(item.owner, item.role)
            if not old or old == item.relpath or not self._is_ours(old):
                continue
            parent = Path(old).parent
            tmp = str(parent / f".ta-move-{os.getpid()}-{n}{Path(old).suffix}")
            if self.dry_run:
                self._virtual[tmp] = old
                self._vacated.add(old)
            else:
                os.rename(self.root / old, self.root / tmp)
            self.manifest.set(tmp, self.manifest.forget(old))
            self._moved_from[tmp] = old

    def _is_current(self, item: DesiredFile, relpath: str) -> bool:
        if not self._exists(relpath):
            return False
        path = self._real(relpath)
        if path.is_symlink():
            return False
        if item.kind == "link":
            have, want = path.stat(), item.source.stat()
            return (have.st_dev, have.st_ino) == (want.st_dev, want.st_ino)
        return path.read_bytes() == item.data

    def _is_ours(self, relpath: str) -> bool:
        """file at relpath is still the one we recorded"""
        entry = self.manifest.files.get(relpath)
        path = self._real(relpath)
        if not entry or not self._exists(relpath) or path.is_symlink():
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
        # (second phase, after _vacate_moves)
        old = self.manifest.owned_by(item.owner, item.role)
        if old and old != item.relpath and not self._exists(item.relpath):
            if self._is_ours(old):
                self._ensure_dir(item.relpath)
                if self.dry_run:
                    self._virtual[item.relpath] = self._virtual.pop(old, old)
                    self._vacated.discard(item.relpath)
                else:
                    os.rename(self.root / old, path)
                self.manifest.set(item.relpath, self.manifest.forget(old))
                origin = self._moved_from.pop(old, old)
                self.report.renamed.append((origin, item.relpath))
            else:
                self.report.warnings.append(f"{old}: changed outside writer")
                self.manifest.forget(old)

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

    def _remove(self, relpath: str) -> None:
        origin = self._moved_from.pop(relpath, relpath)
        if self._is_ours(relpath):
            if not self.dry_run:
                (self.root / relpath).unlink()
            self.report.deleted.append(origin)
        elif self._exists(relpath):
            self.report.warnings.append(f"{origin}: changed outside writer")
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
            d
            for d in self.manifest.dirs
            if _within(d, scope) and d not in needed
        ]
        # in a dry run nothing is removed, so count what would be gone
        gone = {p.rstrip("/") for p in self.report.deleted}
        for rel in sorted(candidates, key=lambda d: -len(Path(d).parts)):
            path = self.root / rel
            if not path.is_dir():
                self.manifest.dirs.discard(rel)
                continue
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


def _within(relpath: str, scope: str | None) -> bool:
    if scope is None:
        return True
    return relpath == scope or relpath.startswith(scope.rstrip("/") + "/")

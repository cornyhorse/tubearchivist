"""
Functionality:
- periodic health check of the Emby sync (review E1, Matt 2026-10-11):
  detect a sync that is stuck or failing, repair what is safe to repair,
  and report the rest so it can be notified
- detects: no successful sync_all for max_age_hours, the last run's
  error, a series failing on failing_runs runs in a row, a corrupt or
  too-new manifest, temp files older than stale_minutes, the old layout
- repairs: names too long for the filesystem are frozen shorter, stale
  temp files are removed, a corrupt manifest is set aside so the next
  sync rebuilds it, an interrupted rename is resumed by the next sync
- the caller must not run it while a sync runs (same lock as emby_sync),
  and should queue an emby_sync when HealthReport.rerun_sync is set
"""

import errno
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from emby.models import EpisodeNumber, SeriesKind
from emby.src.layout import safe_name
from emby.src.sync import (
    EmbyLayoutOutdated,
    EmbyPaths,
    check_layout,
    sync_state,
)
from emby.src.writer import (
    MOVE_PREFIX,
    EmbyWriter,
    Manifest,
    ManifestTooNew,
)

# a name repaired after ENAMETOOLONG: well under any filesystem limit
REPAIRED_NAME_BYTES = 60
_TOO_LONG = (
    os.strerror(errno.ENAMETOOLONG),
    "File name too long",
    f"[Errno {errno.ENAMETOOLONG}]",
)
_KINDS = {"channels": SeriesKind.CHANNEL, "playlists": SeriesKind.PLAYLIST}


@dataclass
class HealthReport:
    """what the check found and did"""

    problems: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    # repairs that take effect on the next sync
    rerun_sync: bool = False

    @property
    def healthy(self) -> bool:
        """nothing left that needs a person"""
        return not self.problems

    def message(self) -> str:
        """notification text"""
        lines = [f"problem: {p}" for p in self.problems]
        lines += [f"repaired: {r}" for r in self.repaired]
        return "\n".join(lines) or "Emby sync healthy"


def check_emby_health(
    paths: EmbyPaths,
    max_age_hours: float = 6,
    failing_runs: int = 3,
    stale_minutes: float = 60,
    repair: bool = True,
) -> HealthReport:
    """detect problems of the Emby sync, repair the safe ones"""
    report = HealthReport()
    try:
        check_layout(dry_run=True)
    except EmbyLayoutOutdated as err:
        report.problems.append(str(err))
    _check_runs(report, max_age_hours, failing_runs, repair)
    _check_manifest(report, paths, stale_minutes, repair)
    return report


def _check_runs(
    report: HealthReport, max_age_hours: float, failing: int, repair: bool
) -> None:
    state = sync_state()
    last = state.get("last_success")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    if not last or datetime.fromisoformat(last) < cutoff:
        report.problems.append(
            f"no successful Emby sync since {last or 'ever'}"
        )
    if error := state.get("last_error"):
        report.problems.append(f"last Emby sync failed: {error}")

    for key, entry in sorted(state.get("failing", {}).items()):
        if entry["runs"] < failing:
            continue
        if _name_too_long(entry["error"]) and repair:
            renamed = shorten_names(key)
            report.repaired.append(
                f"{key}: name too long, {renamed} names shortened to "
                f"{REPAIRED_NAME_BYTES} bytes"
            )
            report.rerun_sync = True
            continue
        report.problems.append(
            f"{key} failed {entry['runs']} syncs in a row since "
            f"{entry['since']}: {entry['error']}"
        )


def _name_too_long(error: str) -> bool:
    return any(marker in error for marker in _TOO_LONG)


def shorten_names(key: str) -> int:
    """
    freeze the names of a series (<kind>/<series_id>) shorter, so its
    files fit the filesystem; returns the number of rows changed
    """
    kind, series_id = key.split("/", 1)
    rows = list(
        EpisodeNumber.objects.filter(
            series_kind=_KINDS[kind], series_id=series_id
        )
    )
    for row in rows:
        row.name = safe_name(row.name, row.youtube_id, REPAIRED_NAME_BYTES)
        row.series_name = safe_name(
            row.series_name, series_id, REPAIRED_NAME_BYTES
        )
    EpisodeNumber.objects.bulk_update(rows, ["name", "series_name"])
    return len(rows)


def _check_manifest(
    report: HealthReport, paths: EmbyPaths, stale_minutes: float, repair
) -> None:
    root = paths.output_root
    if not root.is_dir():
        return
    try:
        manifest = Manifest(root, dry_run=not repair)
    except ManifestTooNew as err:
        report.problems.append(str(err))
        return
    for warning in manifest.warnings:
        if repair:
            report.repaired.append(warning)
            report.rerun_sync = True
        else:
            report.problems.append(warning)

    writer = EmbyWriter(root, dry_run=True)
    cutoff = time.time() - stale_minutes * 60
    for relpath in writer.leftover_temp_files():
        path = root / relpath
        if path.lstat().st_mtime > cutoff:
            continue
        if repair:
            path.unlink()
            report.repaired.append(f"{relpath}: removed stale temp file")
        else:
            report.problems.append(f"{relpath}: stale temp file")

    moving = [p for p in writer.manifest.files if _is_move(p)]
    if moving:
        report.repaired.append(
            f"{len(moving)} files left mid-rename, the next sync "
            "finishes the rename"
        )
        report.rerun_sync = True


def _is_move(relpath: str) -> bool:
    return relpath.rsplit("/", 1)[-1].startswith(MOVE_PREFIX)

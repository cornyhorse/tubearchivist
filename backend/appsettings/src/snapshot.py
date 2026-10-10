"""
functionality:
- daily database snapshots with pg_dump, replacing ES snapshots
- same interface the settings API used for ES: setup, list, take,
  restore, delete
- a snapshot covers TA's data tables, not users or sessions, like the ES
  snapshot covered the ta_* indices
"""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from common.src.env_settings import EnvironmentSettings
from django.conf import settings
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

# tables owned by TA's data apps, dumped and restored together
DATA_APPS = ["channel", "video", "playlist", "download", "appsettings", "emby"]
EXTRA_TABLES = ["user_userconfig"]
NAME_RE = re.compile(r"^ta_daily_(\d{8}T\d{6})$")
RETENTION_DAYS = 30
MIN_COUNT = 5
MAX_COUNT = 50
SCHEDULE_HOUR = 12
TASK_NAME = "db_snapshot"


class SnapshotError(RuntimeError):
    """pg_dump or pg_restore failed, or the snapshot doesn't fit"""


class DbSnapshot:
    """pg_dump snapshots of the TA data tables"""

    def __init__(self, snapshot_dir: Path | None = None):
        self.snapshot_dir = Path(
            snapshot_dir
            or Path(EnvironmentSettings.CACHE_DIR) / "backup" / "snapshots"
        )
        self.tz = ZoneInfo(EnvironmentSettings.TZ)

    # connection

    @staticmethod
    def _pg_env() -> dict:
        db = settings.DATABASES["default"]
        env = os.environ.copy()
        env.update(
            {
                "PGHOST": str(db["HOST"]),
                "PGPORT": str(db["PORT"]),
                "PGDATABASE": str(db["NAME"]),
                "PGUSER": str(db["USER"]),
                "PGPASSWORD": str(db["PASSWORD"]),
            }
        )
        return env

    @staticmethod
    def _run(args: list[str]) -> None:
        result = subprocess.run(
            args,
            env=DbSnapshot._pg_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SnapshotError(f"{args[0]} failed: {result.stderr.strip()}")

    @staticmethod
    def data_tables() -> list[str]:
        """TA data tables that exist in the database"""
        tables = connection.introspection.table_names()
        return sorted(
            t
            for t in tables
            if t.split("_")[0] in DATA_APPS or t in EXTRA_TABLES
        )

    @staticmethod
    def migration_state() -> dict[str, str]:
        """latest applied migration per data app"""
        state: dict[str, str] = {}
        for app, name in MigrationRecorder(connection).applied_migrations():
            if app in DATA_APPS + ["user", "common"]:
                state[app] = max(state.get(app, ""), name)
        return state

    # schedule

    def setup(self) -> None:
        """make sure the daily schedule exists and take a snapshot if due"""
        print("snapshot: run setup")
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schedule()
        if self._needs_startup_snapshot():
            self.take_snapshot_now()

    @staticmethod
    def _ensure_schedule() -> None:
        # pylint: disable=import-outside-toplevel
        from django_celery_beat.models import CrontabSchedule, PeriodicTask

        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute="0",
            hour=str(SCHEDULE_HOUR),
            day_of_week="*",
            day_of_month="*",
            month_of_year="*",
            timezone=EnvironmentSettings.TZ,
        )
        PeriodicTask.objects.update_or_create(
            name=TASK_NAME,
            defaults={"task": TASK_NAME, "crontab": schedule, "enabled": True},
        )

    @staticmethod
    def remove_schedule() -> None:
        """disable daily snapshots"""
        # pylint: disable=import-outside-toplevel
        from django_celery_beat.models import PeriodicTask

        PeriodicTask.objects.filter(name=TASK_NAME).delete()

    def _needs_startup_snapshot(self) -> bool:
        snapshots = self._get_all_snapshots()
        if not snapshots:
            print("snapshot: create initial snapshot")
            return True

        age = datetime.now(self.tz).timestamp() - snapshots[0]["end_stamp"]
        if age > 24 * 60 * 60:
            print("snapshot: is outdated, create new now")
            return True

        print("snapshot: last snapshot is up-to-date")
        return False

    # take, list, restore, delete

    def take_snapshot_now(self, wait: bool = True) -> dict:
        """dump the data tables now, then apply retention"""
        del wait  # pg_dump is synchronous
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        start = datetime.now(self.tz)
        name = f"ta_daily_{start.strftime('%Y%m%dT%H%M%S')}"
        dump = self.snapshot_dir / f"{name}.dump"
        tmp = dump.with_suffix(".dump.tmp")
        tables = self.data_tables()
        args = ["pg_dump", "--format=custom", f"--file={tmp}"]
        args += [f"--table={table}" for table in tables]
        try:
            self._run(args)
            tmp.replace(dump)
        finally:
            tmp.unlink(missing_ok=True)

        end = datetime.now(self.tz)
        meta = {
            "id": name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "tables": tables,
            "migrations": self.migration_state(),
        }
        (self.snapshot_dir / f"{name}.json").write_text(json.dumps(meta))
        print(f"snapshot: created {name}")
        self._apply_retention()
        return {"snapshot_name": name}

    def _apply_retention(self) -> None:
        snapshots = self._get_all_snapshots() or []
        cutoff = (datetime.now(self.tz) - timedelta(days=RETENTION_DAYS)).timestamp()
        for idx, snapshot in enumerate(snapshots):
            expired = snapshot["end_stamp"] < cutoff and idx >= MIN_COUNT
            if expired or idx >= MAX_COUNT:
                self.delete_single_snapshot(snapshot["id"])

    def _load(self, snapshot_id: str) -> dict | None:
        if not NAME_RE.match(snapshot_id):
            return None
        meta = self.snapshot_dir / f"{snapshot_id}.json"
        dump = self.snapshot_dir / f"{snapshot_id}.dump"
        if not meta.is_file() or not dump.is_file():
            return None
        return json.loads(meta.read_text())

    def _parse(self, meta: dict) -> dict:
        start = datetime.fromisoformat(meta["start"]).astimezone(self.tz)
        end = datetime.fromisoformat(meta["end"]).astimezone(self.tz)
        return {
            "id": meta["id"],
            "state": "SUCCESS",
            "es_version": f"postgres {connection.pg_version // 10000}",
            "start_date": start.strftime("%Y-%m-%d %H:%M"),
            "end_date": end.strftime("%Y-%m-%d %H:%M"),
            "end_stamp": int(end.timestamp()),
            "duration_s": int((end - start).total_seconds()),
        }

    def _get_all_snapshots(self) -> list[dict] | bool:
        """all snapshots, newest first"""
        if not self.snapshot_dir.is_dir():
            print("snapshots: not configured")
            return False

        found = []
        for meta_file in self.snapshot_dir.glob("ta_daily_*.json"):
            meta = self._load(meta_file.stem)
            if meta:
                found.append(self._parse(meta))

        if not found:
            print("snapshots: no snapshots found")
            return False

        return sorted(found, key=lambda s: s["end_stamp"], reverse=True)

    def get_snapshot_stats(self) -> dict:
        """schedule details plus snapshots, for the frontend"""
        # pylint: disable=import-outside-toplevel
        from django_celery_beat.models import PeriodicTask

        stats = {
            "next_exec": None,
            "next_exec_str": None,
            "expire_after": f"{RETENTION_DAYS}d",
            "snapshots": self._get_all_snapshots() or [],
        }
        if PeriodicTask.objects.filter(name=TASK_NAME, enabled=True).exists():
            now = datetime.now(self.tz)
            next_exec = now.replace(
                hour=SCHEDULE_HOUR, minute=0, second=0, microsecond=0
            )
            if next_exec <= now:
                next_exec += timedelta(days=1)
            stats["next_exec"] = int(next_exec.timestamp())
            stats["next_exec_str"] = next_exec.strftime("%Y-%m-%d %H:%M")

        return stats

    def get_single_snapshot(self, snapshot_id: str) -> dict | bool:
        """metadata of one snapshot"""
        meta = self._load(snapshot_id)
        if not meta:
            print(f"snapshots: not found: {snapshot_id}")
            return False
        return self._parse(meta)

    def restore_all(self, snapshot_name: str) -> dict | bool:
        """replace the data tables with the snapshot, in one transaction"""
        meta = self._load(snapshot_name)
        if not meta:
            print(f"snapshot: not found: {snapshot_name}")
            return False

        if meta["migrations"] != self.migration_state():
            print(f"snapshot: {snapshot_name} is from another schema version")
            return False

        dump = self.snapshot_dir / f"{snapshot_name}.dump"
        try:
            self._run(
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--single-transaction",
                    "--no-owner",
                    f"--dbname={settings.DATABASES['default']['NAME']}",
                    str(dump),
                ]
            )
        except SnapshotError as err:
            print(f"snapshot: failed to restore, {err}")
            return False

        print(f"snapshot: restored {snapshot_name}")
        return {"accepted": True}

    def delete_single_snapshot(self, snapshot_id: str) -> dict | bool:
        """delete one snapshot"""
        if not self._load(snapshot_id):
            print(f"snapshot: failed to delete, not found: {snapshot_id}")
            return False

        for suffix in (".dump", ".json"):
            (self.snapshot_dir / f"{snapshot_id}{suffix}").unlink(missing_ok=True)
        print(f"snapshot: deleted {snapshot_id}")
        return {"acknowledged": True}

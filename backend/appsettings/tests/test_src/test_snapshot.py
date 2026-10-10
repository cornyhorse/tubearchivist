"""tests for pg_dump snapshots, subprocess mocked"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from appsettings.src import snapshot as snapshot_module
from appsettings.src.snapshot import (
    MAX_COUNT,
    MIN_COUNT,
    TASK_NAME,
    DbSnapshot,
    SnapshotError,
)
from common.src.env_settings import EnvironmentSettings
from django_celery_beat.models import CrontabSchedule, PeriodicTask

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc)


class FakeCompleted:
    """subprocess.CompletedProcess stand in"""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


class Runs(list):
    """recorded subprocess.run calls plus the return code to fake"""

    def __init__(self):
        super().__init__()
        self.state = {"returncode": 0}


@pytest.fixture(name="runs")
def fixture_runs(monkeypatch):
    """record subprocess.run calls; pg_dump writes its --file"""
    calls = Runs()
    state = calls.state

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if state["returncode"]:
            return FakeCompleted(state["returncode"], " boom \n")
        for arg in args:
            if arg.startswith("--file="):
                Path(arg.removeprefix("--file=")).write_bytes(b"PGDMP")
        return FakeCompleted()

    monkeypatch.setattr(snapshot_module.subprocess, "run", fake_run)
    return calls


@pytest.fixture(name="clock")
def fixture_clock(monkeypatch):
    """freeze datetime.now in the snapshot module, settable"""
    state = {"now": NOW}

    class FrozenDatetime(datetime):
        """datetime with a settable now"""

        @classmethod
        def now(cls, tz=None):
            return state["now"].astimezone(tz)

    monkeypatch.setattr(snapshot_module, "datetime", FrozenDatetime)
    return state


@pytest.fixture(name="snap")
def fixture_snap(tmp_path):
    """snapshot handler on a scratch dir"""
    return DbSnapshot(tmp_path / "snapshots")


def write_snapshot(snap, end: datetime, migrations=None):
    """fake a snapshot pair on disk, return its id"""
    snap.snapshot_dir.mkdir(parents=True, exist_ok=True)
    name = f"ta_daily_{end.strftime('%Y%m%dT%H%M%S')}"
    (snap.snapshot_dir / f"{name}.dump").write_bytes(b"PGDMP")
    meta = {
        "id": name,
        "start": (end - timedelta(seconds=7)).isoformat(),
        "end": end.isoformat(),
        "tables": [],
        "migrations": migrations or DbSnapshot.migration_state(),
    }
    (snap.snapshot_dir / f"{name}.json").write_text(json.dumps(meta))
    return name


def test_default_dir_under_cache():
    expected = Path(EnvironmentSettings.CACHE_DIR) / "backup" / "snapshots"
    assert DbSnapshot().snapshot_dir == expected


def test_pg_env_has_connection(settings):
    db = settings.DATABASES["default"]

    env = DbSnapshot._pg_env()  # pylint: disable=protected-access

    assert env["PGHOST"] == str(db["HOST"])
    assert env["PGPORT"] == str(db["PORT"])
    assert env["PGDATABASE"] == db["NAME"]
    assert env["PGUSER"] == db["USER"]
    assert env["PGPASSWORD"] == db["PASSWORD"]
    assert "PATH" in env


def test_run_raises_with_stderr(runs):
    runs.state["returncode"] = 1

    with pytest.raises(SnapshotError, match="pg_dump failed: boom"):
        DbSnapshot._run(["pg_dump"])  # pylint: disable=protected-access

    _, kwargs = runs[0]
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["check"] is False


def test_data_tables():
    tables = DbSnapshot.data_tables()

    assert "channel_channel" in tables
    assert "video_video" in tables
    assert "appsettings_configdocument" in tables
    assert "user_userconfig" in tables
    assert "user_account" not in tables
    assert not [t for t in tables if t.startswith(("django_", "auth_"))]
    assert tables == sorted(tables)


def test_migration_state():
    state = DbSnapshot.migration_state()

    assert state["video"].startswith("00")
    assert "user" in state
    assert "admin" not in state and "auth" not in state


def test_take_snapshot_now(snap, runs, clock, settings):
    # pylint: disable=unused-argument
    result = snap.take_snapshot_now()

    name = "ta_daily_20261010T093000"
    assert result == {"snapshot_name": name}
    args, kwargs = runs[0]
    assert args[:2] == ["pg_dump", "--format=custom"]
    assert args[2] == f"--file={snap.snapshot_dir / name}.dump.tmp"
    assert "--table=video_video" in args
    assert "--table=user_userconfig" in args
    password = settings.DATABASES["default"]["PASSWORD"]
    assert kwargs["env"]["PGPASSWORD"] == password
    assert (snap.snapshot_dir / f"{name}.dump").read_bytes() == b"PGDMP"
    assert not (snap.snapshot_dir / f"{name}.dump.tmp").exists()
    meta = json.loads((snap.snapshot_dir / f"{name}.json").read_text())
    assert meta["migrations"] == DbSnapshot.migration_state()
    assert snap.get_single_snapshot(name)["duration_s"] == 0


def test_take_snapshot_failure_leaves_nothing(snap):
    def failing_run(args):
        Path(args[2].removeprefix("--file=")).write_bytes(b"partial")
        raise SnapshotError("pg_dump failed")

    snap._run = failing_run  # pylint: disable=protected-access

    with pytest.raises(SnapshotError):
        snap.take_snapshot_now()

    assert not list(snap.snapshot_dir.iterdir())


def test_setup_creates_schedule_and_first_snapshot(snap, runs, clock):
    # pylint: disable=unused-argument
    snap.setup()

    task = PeriodicTask.objects.get(name=TASK_NAME)
    assert task.task == TASK_NAME and task.enabled
    assert task.crontab.hour == "12" and task.crontab.minute == "0"
    assert str(task.crontab.timezone) == EnvironmentSettings.TZ
    assert len(runs) == 1

    snap.setup()

    assert PeriodicTask.objects.filter(name=TASK_NAME).count() == 1
    assert CrontabSchedule.objects.filter(hour="12").count() == 1
    assert len(runs) == 1, "fresh snapshot exists, no new one"


def test_setup_replaces_outdated(snap, runs, clock):
    # pylint: disable=unused-argument
    write_snapshot(snap, NOW - timedelta(days=2))

    snap.setup()

    assert len(runs) == 1


def test_remove_schedule(snap, runs, clock):
    # pylint: disable=unused-argument
    snap.setup()

    DbSnapshot.remove_schedule()

    assert not PeriodicTask.objects.filter(name=TASK_NAME).exists()


def test_retention(snap, runs, clock):
    # pylint: disable=unused-argument
    old = [
        write_snapshot(snap, NOW - timedelta(days=40 + i)) for i in range(7)
    ]
    recent = [
        write_snapshot(snap, NOW - timedelta(hours=i + 1)) for i in range(3)
    ]

    snap.take_snapshot_now()

    kept = {s["id"] for s in snap.get_snapshot_stats()["snapshots"]}
    assert set(recent) <= kept
    assert len(kept) == MIN_COUNT
    assert set(old[: MIN_COUNT - 4]) <= kept
    assert not set(old[MIN_COUNT - 4 :]) & kept


def test_retention_max_count(snap, runs, clock):
    # pylint: disable=unused-argument
    for i in range(MAX_COUNT + 2):
        write_snapshot(snap, NOW - timedelta(minutes=10 * (i + 1)))

    snap.take_snapshot_now()

    assert len(snap.get_snapshot_stats()["snapshots"]) == MAX_COUNT


def test_get_all_snapshots_without_dir(snap):
    # pylint: disable=protected-access
    assert snap._get_all_snapshots() is False
    assert snap.get_snapshot_stats()["snapshots"] == []


def test_get_all_snapshots_skips_incomplete(snap, clock):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW)
    (snap.snapshot_dir / f"{name}.dump").unlink()
    (snap.snapshot_dir / "ta_daily_bogus.json").write_text("{}")

    assert snap._get_all_snapshots() is False  # pylint: disable=W0212


def test_stats_without_schedule(snap, clock):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW - timedelta(hours=1))

    stats = snap.get_snapshot_stats()

    assert stats["next_exec"] is None and stats["next_exec_str"] is None
    assert stats["expire_after"] == "30d"
    item = stats["snapshots"][0]
    assert item["id"] == name and item["state"] == "SUCCESS"
    assert item["es_version"].startswith("postgres ")
    assert item["end_date"] == "2026-10-10 08:30"
    assert item["duration_s"] == 7


@pytest.mark.parametrize(
    "now, expected",
    [
        (NOW, "2026-10-10 12:00"),
        (NOW.replace(hour=12), "2026-10-11 12:00"),
        (NOW.replace(hour=15), "2026-10-11 12:00"),
    ],
)
def test_stats_next_exec(snap, runs, clock, now, expected):
    # pylint: disable=unused-argument
    DbSnapshot._ensure_schedule()  # pylint: disable=protected-access
    clock["now"] = now

    stats = snap.get_snapshot_stats()

    assert stats["next_exec_str"] == expected
    assert stats["next_exec"] > now.timestamp()


def test_get_single_snapshot_not_found(snap):
    assert snap.get_single_snapshot("ta_daily_20260101T000000") is False
    assert snap.get_single_snapshot("../../etc/passwd") is False


def test_restore(snap, runs, clock, settings):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW)

    assert snap.restore_all(name) == {"accepted": True}

    args, kwargs = runs[0]
    db_name = settings.DATABASES["default"]["NAME"]
    assert args == [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--no-owner",
        f"--dbname={db_name}",
        str(snap.snapshot_dir / f"{name}.dump"),
    ]
    assert kwargs["env"]["PGDATABASE"] == db_name
    assert (
        kwargs["env"]["PGPASSWORD"]
        == settings.DATABASES["default"]["PASSWORD"]
    )


def test_restore_not_found(snap, runs):
    assert snap.restore_all("ta_daily_20260101T000000") is False
    assert not runs


def test_restore_refuses_other_schema(snap, runs, clock):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW, migrations={"video": "0001_initial"})

    assert snap.restore_all(name) is False
    assert not runs


def test_restore_failure(snap, runs, clock):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW)
    runs.state["returncode"] = 1

    assert snap.restore_all(name) is False


def test_delete(snap, clock):
    # pylint: disable=unused-argument
    name = write_snapshot(snap, NOW)

    assert snap.delete_single_snapshot(name) == {"acknowledged": True}
    assert not list(snap.snapshot_dir.iterdir())
    assert snap.delete_single_snapshot(name) is False

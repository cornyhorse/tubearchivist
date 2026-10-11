"""tests for the Emby sync health check (review E1)"""

import errno
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from appsettings.models import ConfigDocument
from common.tests.docs import channel_doc
from django.core.management import CommandError, call_command
from emby.models import EpisodeNumber
from emby.src import sync as sync_module
from emby.src import writer as writer_module
from emby.src.health import (
    REPAIRED_NAME_BYTES,
    HealthReport,
    check_emby_health,
)
from emby.src.sync import (
    LAYOUT_KEY,
    STATE_KEY,
    EmbyLayoutOutdated,
    series_key,
    sync_all,
    sync_state,
)
from emby.src.writer import MANIFEST_NAME, EmbyRootError

pytestmark = pytest.mark.django_db
CHANNEL_ID = channel_doc()["channel_id"]


def set_state(**state):
    """pretend sync_all recorded this"""
    ConfigDocument.objects.update_or_create(
        key=STATE_KEY, defaults={"value": state}
    )


def ago(**delta) -> str:
    """an ISO timestamp in the past"""
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def test_healthy_after_a_sync(library):
    sync_all(library)

    report = check_emby_health(library)

    assert report.healthy and not report.repaired
    assert report.message() == "Emby sync healthy"


def test_sync_all_records_runs_and_failing_series(library, monkeypatch):
    sync_all(library)
    first = sync_state()
    assert first["last_error"] is None and first["failing"] == {}

    def broken(*_):
        raise ValueError("bad playlist")

    monkeypatch.setattr(sync_module, "playlist_series", broken)
    sync_all(library)
    sync_all(library)

    state = sync_state()
    assert state["failing"]["playlists/PL1"]["runs"] == 2
    assert state["failing"]["playlists/PL1"]["error"] == "bad playlist"
    assert state["failing"]["playlists/PL1"]["since"] > first["last_run"]


def test_writer_failures_are_keyed_by_series(library, monkeypatch):
    real_write = writer_module.atomic_write

    def too_long(path, *args, **kwargs):
        if path.name == MANIFEST_NAME:
            return real_write(path, *args, **kwargs)
        raise OSError(errno.ENAMETOOLONG, os.strerror(errno.ENAMETOOLONG))

    monkeypatch.setattr(writer_module, "atomic_write", too_long)

    report = sync_all(library)

    assert set(report.failed) == {
        f"channels/{CHANNEL_ID}",
        "playlists/PL1",
    }
    assert sync_state()["failing"]["playlists/PL1"]["runs"] == 1


def test_series_key():
    assert series_key("channels/A [b] c [UC1]") == "channels/UC1"
    assert series_key("other/x") == "other/x"


def test_exceptions_are_recorded(library, monkeypatch):
    def refuse(*_):
        raise EmbyRootError("noserverino")

    monkeypatch.setattr("emby.src.writer.check_hardlinks", refuse)

    with pytest.raises(EmbyRootError):
        sync_all(library)

    state = sync_state()
    assert state["last_error"] == "EmbyRootError: noserverino"
    assert "last_success" not in state
    report = check_emby_health(library)
    assert report.problems == [
        "no successful Emby sync since ever",
        "last Emby sync failed: EmbyRootError: noserverino",
    ]


def test_dry_runs_are_not_recorded(library):
    sync_all(library, dry_run=True)
    sync_all(library)
    before = sync_state()
    ConfigDocument.objects.filter(key=LAYOUT_KEY).delete()

    with pytest.raises(EmbyLayoutOutdated):
        sync_all(library, dry_run=True)

    assert sync_state() == before


def test_stale_success_and_failing_series(library):
    sync_all(library)
    set_state(
        last_success=ago(hours=7),
        last_error=None,
        failing={
            "playlists/PL1": {"runs": 3, "error": "boom", "since": "t0"},
            "channels/UC9": {"runs": 2, "error": "boom", "since": "t1"},
        },
    )

    report = check_emby_health(library)

    assert report.problems[0].startswith("no successful Emby sync since ")
    assert report.problems[1:] == [
        "playlists/PL1 failed 3 syncs in a row since t0: boom"
    ]
    assert "problem: playlists/PL1" in report.message()
    assert check_emby_health(library, max_age_hours=8).problems == [
        "playlists/PL1 failed 3 syncs in a row since t0: boom"
    ]


def test_too_long_names_are_shortened(library):
    sync_all(library)
    EpisodeNumber.objects.filter(series_kind="channel").update(
        name="x" * 120, series_name="Long channel " * 9
    )
    error = f"[Errno {errno.ENAMETOOLONG}] {os.strerror(errno.ENAMETOOLONG)}"
    set_state(
        last_success=ago(minutes=5),
        failing={
            f"channels/{CHANNEL_ID}": {"runs": 3, "error": error, "since": "t"}
        },
    )

    dry = check_emby_health(library, repair=False)
    assert len(dry.problems) == 1

    report = check_emby_health(library)

    assert report.healthy and report.rerun_sync
    assert "3 names shortened" in report.repaired[0]
    for row in EpisodeNumber.objects.filter(series_kind="channel"):
        assert len(row.name.encode()) <= REPAIRED_NAME_BYTES
        assert len(row.series_name.encode()) <= REPAIRED_NAME_BYTES
    assert EpisodeNumber.objects.filter(series_kind="playlist")[0].name == (
        "Riding the Big Coaster"
    )


def test_old_layout_is_a_problem(library):
    sync_all(library)
    ConfigDocument.objects.filter(key=LAYOUT_KEY).delete()

    report = check_emby_health(library)

    assert "ta_emby_migrate_layout" in report.problems[0]


def test_corrupt_manifest_is_set_aside(library):
    sync_all(library)
    (library.output_root / MANIFEST_NAME).write_text("{broken")

    dry = check_emby_health(library, repair=False)
    assert "unreadable" in dry.problems[0]
    assert (library.output_root / MANIFEST_NAME).exists()

    report = check_emby_health(library)

    assert report.healthy and report.rerun_sync
    assert "unreadable" in report.repaired[0]
    assert not (library.output_root / MANIFEST_NAME).exists()
    rebuilt = sync_all(library)
    assert not rebuilt.changed and rebuilt.unchanged


def test_newer_manifest_is_a_problem(library):
    sync_all(library)
    (library.output_root / MANIFEST_NAME).write_text('{"version": 9}')

    report = check_emby_health(library)

    assert "refusing" in report.problems[0]


def test_stale_temp_files(library):
    sync_all(library)
    series = next((library.output_root / "channels").iterdir())
    old, fresh = series / ".ta-tmp-old", series / ".ta-tmp-new"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    hour_ago = time.time() - 3700
    os.utime(old, (hour_ago, hour_ago))

    dry = check_emby_health(library, repair=False)
    assert dry.problems == [
        f"{old.relative_to(library.output_root)}: stale temp file"
    ]

    report = check_emby_health(library)

    assert report.healthy
    assert not old.exists() and fresh.exists()


def test_interrupted_rename_reruns_the_sync(library):
    sync_all(library)
    manifest_path = library.output_root / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    relpath = next(p for p in manifest["files"] if p.endswith(".nfo"))
    moved = relpath.rsplit("/", 1)[0] + "/.ta-move-abc.nfo"
    (library.output_root / relpath).rename(library.output_root / moved)
    manifest["files"][moved] = manifest["files"].pop(relpath)
    manifest_path.write_text(json.dumps(manifest))

    report = check_emby_health(library)

    assert report.rerun_sync
    assert "mid-rename" in report.repaired[0]
    assert len(sync_all(library).renamed) == 1


def test_missing_root_is_only_a_state_check(library):
    report = check_emby_health(library)

    assert report.problems == ["no successful Emby sync since ever"]


def test_health_command(library, settings_env):
    with pytest.raises(CommandError, match="1 problems"):
        call_command("ta_emby_health", "--no-repair", stdout=io.StringIO())

    sync_all(library)
    (library.output_root / MANIFEST_NAME).write_text("{broken")
    out = io.StringIO()
    call_command("ta_emby_health", stdout=out)
    assert "repaired: manifest unreadable" in out.getvalue()
    assert "run ta_emby_sync" in out.getvalue()

    out = io.StringIO()
    sync_all(library)
    call_command("ta_emby_health", stdout=out)
    assert out.getvalue().strip() == "Emby sync healthy"


def test_health_command_needs_emby_root(settings_env, monkeypatch):
    monkeypatch.setattr(settings_env, "EMBY_ROOT", "")

    with pytest.raises(CommandError, match="TA_EMBY_ROOT"):
        call_command("ta_emby_health")


def test_report_defaults():
    assert HealthReport().healthy

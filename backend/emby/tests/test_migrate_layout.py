"""
tests for the one-time Emby layout migration (review E6, E7, E8): the
old tree is renamed in exactly one pass and nothing moves afterwards
"""

import io
import os
import shutil
from datetime import date
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command

from appsettings.models import ConfigDocument
from channel.models import Channel
from common.tests.docs import channel_doc
from emby.models import EpisodeNumber
from emby.src import numbering
from emby.src import sync as sync_module
from emby.src.migrate_layout import (
    AlreadyMigrated,
    MigrationSummary,
    convert_date_number,
    migrate_layout,
    place_spilled,
)
from emby.src.numbering import NumberingError
from emby.src.sync import (
    LAYOUT_KEY,
    layout_version,
    playlist_numbers,
    sync_all,
)
from emby.tests.conftest import add_video
from emby.tests.test_sync import numbers
from emby.tests.test_writer_noserverino import noserverino
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db
CHANNEL_ID = channel_doc()["channel_id"]
DAY = 1700000000  # 2023-11-14 22:13 UTC


def old_layout(paths, monkeypatch):
    """
    the tree and database as the first layout left them: MMDD*10+n,
    live names, playlists on playlist_position, no layout document
    """
    Playlist.objects.update(emby_numbering="playlist_position")
    with monkeypatch.context() as patch:
        patch.setattr(numbering, "DATE_SLOTS_PER_DAY", 10)
        sync_all(paths)
        # the playlist was a show of its own, numbered by position
        playlist_numbers(Playlist.objects.get(), ZoneInfo("UTC"))
    EpisodeNumber.objects.update(name="", series_name="")
    ConfigDocument.objects.filter(key=LAYOUT_KEY).delete()


def run(*args) -> str:
    """call a command, return its stdout"""
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def media_inodes(root):
    """inodes of every linked media file"""
    return sorted(p.stat().st_ino for p in root.rglob("*.mp4"))


def test_convert_date_number():
    assert convert_date_number(31400) == 314000
    assert convert_date_number(3149) == 31409
    assert convert_date_number(12310) == 123100


def test_migration_renames_once(library, monkeypatch):
    old_layout(library, monkeypatch)
    root = library.output_root
    assert numbers(root / "channels")["v1"] == (2023, 11140)
    inodes = media_inodes(root)

    summary = migrate_layout("UTC")
    report = sync_all(library)

    assert summary.date_numbers == 3
    assert summary.playlists_frozen == ["PL1"]
    assert summary.names_frozen == 7
    assert numbers(root / "channels")["v1"] == (2023, 111400)
    assert not (root / "playlists").exists()
    # channel: 3 episodes x (media, nfo, thumb)
    assert len(report.renamed) == 9
    assert not report.created and not report.errors
    assert media_inodes(root) == inodes
    assert layout_version() == 4

    assert sync_all(library).changed == 0
    Video.objects.update(title="Renamed later")
    Channel.objects.update(name="Renamed later")
    assert not sync_all(library).renamed


def test_spilled_numbers_return_to_their_day(library, monkeypatch):
    for n in range(10):
        add_video(library, f"same{n:02d}", DAY + 100 + n)
    old_layout(library, monkeypatch)
    old = dict(
        EpisodeNumber.objects.filter(series_kind="channel").values_list(
            "youtube_id", "episode"
        )
    )
    # eleven uploads on 11-14 (ties sort by id): v1 spilled into the
    # 11-15 block, so v2 (uploaded 11-15) got the second slot there
    assert old["same09"] == 11149
    assert (old["v1"], old["v2"]) == (11150, 11151)

    summary = migrate_layout("UTC")
    report = sync_all(library)

    new = dict(
        EpisodeNumber.objects.filter(series_kind="channel").values_list(
            "youtube_id", "episode"
        )
    )
    assert new["same09"] == 111409
    assert new["v1"] == 111410
    assert new["v2"] == 111501
    assert summary.moved_to_own_day == [f"{CHANNEL_ID}/v1"]
    assert numbers(library.output_root / "channels")["v1"] == (2023, 111410)
    assert not report.errors


def test_spilled_number_stays_when_its_day_is_full():
    """more than 100 uploads on one day: nowhere to move back to"""
    rows = [
        EpisodeNumber(
            series_id="UC1", youtube_id=f"v{n}", season=2025, episode=e
        )
        for n, e in enumerate([*range(31400, 31500), 31500])
    ]
    days = {f"v{n}": date(2025, 3, 14) for n in range(101)}
    summary = MigrationSummary()

    placed = place_spilled(rows, days, summary)

    assert placed[-1].episode == 31500
    assert not summary.moved_to_own_day


def test_undated_and_other_season_rows_keep_converted_numbers(library):
    EpisodeNumber.objects.bulk_create(
        [
            EpisodeNumber(
                series_kind="channel",
                series_id=CHANNEL_ID,
                scheme="date",
                youtube_id="gone",
                season=2020,
                episode=1011,
                series_name="Already frozen",
            ),
            EpisodeNumber(
                series_kind="channel",
                series_id=CHANNEL_ID,
                scheme="date",
                youtube_id="v1",
                season=2019,
                episode=1012,
            ),
        ]
    )

    summary = migrate_layout("UTC")

    assert sorted(EpisodeNumber.objects.values_list("episode", flat=True)) == [
        10101,
        10102,
    ]
    assert not summary.moved_to_own_day
    gone = EpisodeNumber.objects.get(youtube_id="gone")
    assert (gone.name, gone.series_name) == ("", "Already frozen")


def test_kept_playlists_and_empty_database(library, monkeypatch):
    old_layout(library, monkeypatch)

    summary = migrate_layout("UTC", keep_position={"PL1"})

    assert summary.playlists_kept == ["PL1"]
    assert Playlist.objects.get().emby_numbering == "playlist_position"
    with pytest.raises(AlreadyMigrated):
        migrate_layout("UTC")


def test_unsynced_playlist_switches_without_numbers(library):
    Playlist.objects.update(emby_numbering="playlist_position")

    summary = migrate_layout("UTC")

    assert summary.playlists_frozen == ["PL1"]
    assert summary.date_numbers == 0
    assert not EpisodeNumber.objects.exists()


def test_command_dry_run_changes_nothing(library, monkeypatch, settings_env):
    old_layout(library, monkeypatch)
    rows = list(EpisodeNumber.objects.order_by("pk").values())
    inodes = media_inodes(library.output_root)

    output = run("ta_emby_migrate_layout", "--dry-run", "--verbose-changes")

    assert "[dry-run] renamed 9," in output
    assert "[dry-run] rename channels/" in output
    assert "rolled back" in output
    assert list(EpisodeNumber.objects.order_by("pk").values()) == rows
    assert layout_version() == 1
    assert media_inodes(library.output_root) == inodes


def test_command_migrates_and_syncs(library, monkeypatch, settings_env):
    old_layout(library, monkeypatch)
    Video.objects.filter(pk="v2").delete()
    add_video(library, "fresh", DAY + 5 * 86400)
    (library.cache_root / "channels").rename(library.cache_root / "gone")

    output = run("ta_emby_migrate_layout", "--verbose-changes")

    assert "names frozen 7" in output
    assert "renamed 6," in output and "errors 0" in output
    assert "delete channels/" in output
    assert "create channels/" in output
    assert "warning: " in output
    assert "created 0, updated 0, renamed 0, deleted 0" in run(
        "ta_emby_sync", str(library.output_root)
    )
    with pytest.raises(CommandError, match="already on Emby layout"):
        run("ta_emby_migrate_layout")


def test_command_no_sync(library, monkeypatch, settings_env):
    old_layout(library, monkeypatch)

    output = run("ta_emby_migrate_layout", "--no-sync")

    assert "not synced" in output
    assert layout_version() == 4


def test_command_needs_emby_root(library, monkeypatch, settings_env):
    monkeypatch.setattr(settings_env, "EMBY_ROOT", "")

    with pytest.raises(CommandError, match="TA_EMBY_ROOT"):
        run("ta_emby_migrate_layout")
    output = run("ta_emby_migrate_layout", "--dry-run")
    assert "rolled back" in output and "renamed" not in output


def test_emby_sync_command_refuses_old_layout(library, monkeypatch):
    old_layout(library, monkeypatch)

    with pytest.raises(CommandError, match="ta_emby_migrate_layout"):
        run("ta_emby_sync", str(library.output_root))


def test_commands_report_failed_series(library, monkeypatch, settings_env):
    for n in range(10):
        add_video(library, f"same{n:02d}", DAY + 100 + n)
    old_layout(library, monkeypatch)

    def broken(*_):
        raise NumberingError("duplicate positions")

    monkeypatch.setattr(sync_module, "other_series", broken)

    output = run("ta_emby_migrate_layout")

    assert f"back to its own day: {CHANNEL_ID}/v1" in output
    assert "error: channels/other: duplicate positions" in output
    with pytest.raises(CommandError, match="1 series failed"):
        run("ta_emby_sync", str(library.output_root))


def test_command_checks_hardlinks_before_migrating(
    library, monkeypatch, settings_env
):
    """
    2026-10-21: the database migrated, then the sync's preflight refused
    the root; now the preflight runs first, in a dry run too
    """
    old_layout(library, monkeypatch)
    rows = list(EpisodeNumber.objects.order_by("pk").values())
    monkeypatch.setattr(os, "link", shutil.copyfile)

    for args in ([], ["--dry-run"]):
        with pytest.raises(CommandError, match="not the same file"):
            run("ta_emby_migrate_layout", *args)
        assert layout_version() == 1
        assert list(EpisodeNumber.objects.order_by("pk").values()) == rows

    assert "not synced" in run("ta_emby_migrate_layout", "--no-sync")


def test_command_migrates_on_a_noserverino_mount(
    library, monkeypatch, settings_env
):
    old_layout(library, monkeypatch)
    inodes = media_inodes(library.output_root)
    noserverino(monkeypatch, library.output_root, remount=True)

    dry = run("ta_emby_migrate_layout", "--dry-run")
    output = run("ta_emby_migrate_layout")

    assert "[dry-run] renamed 9, created 0" in dry
    assert "renamed 9, created 0, updated 3" in output
    assert "errors 0" in output
    assert (
        sorted(os.stat(p).st_ino for p in library.output_root.rglob("*.mp4"))
        == inodes
    )

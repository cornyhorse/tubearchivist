"""layout 3: dates are the UTC date of the upload, whatever TZ is"""

import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc
from django.core.management import call_command
from emby.models import EpisodeNumber
from emby.src.migrate_layout import (
    MigrationSummary,
    _free_slot,
    _migrate_utc_dates,
    migrate_layout,
)
from emby.src.sync import (
    LAYOUT_KEY,
    UTC,
    EmbyPaths,
    numbering_date,
    sync_all,
)
from emby.tests.conftest import add_video
from emby.tests.test_sync import numbers
from video.models import Video

pytestmark = pytest.mark.django_db

NY = ZoneInfo("America/New_York")
CHANNEL_ID = "UCchannel000000000000001"
# 2024-01-01 00:30 UTC is 2023-12-31 19:30 in New York
NEW_YEAR = int(datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc).timestamp())
# 2023-11-14 03:00 UTC is 2023-11-13 22:00 in New York
EARLY = int(datetime(2023, 11, 14, 3, 0, tzinfo=timezone.utc).timestamp())
NOON = int(datetime(2023, 11, 14, 12, 0, tzinfo=timezone.utc).timestamp())


@pytest.fixture(name="ny_library")
def fixture_ny_library(tmp_path):
    """a library whose paths say TZ is New York"""
    Channel.objects.create(**Channel.fields_from_doc(channel_doc()))
    paths = EmbyPaths(
        tmp_path / "media",
        tmp_path / "cache",
        tmp_path / "emby",
        timezone="America/New_York",
    )
    return paths


def episode_nfos(root):
    return {
        n.findtext("uniqueid"): n
        for n in (ET.parse(p).getroot() for p in root.rglob("*.nfo"))
        if n.tag == "episodedetails"
    }


def test_numbering_date_is_utc_by_default():
    video = Video(published=datetime.fromtimestamp(EARLY, timezone.utc))
    assert numbering_date(video).isoformat() == "2023-11-14"
    assert numbering_date(video, NY).isoformat() == "2023-11-13"
    assert numbering_date(Video(), UTC) is None


def test_aired_and_season_use_the_utc_date(ny_library):
    add_video(ny_library, "early", EARLY)
    add_video(ny_library, "newyear", NEW_YEAR)

    sync_all(ny_library)

    nfos = episode_nfos(ny_library.output_root)
    assert nfos["early"].findtext("aired") == "2023-11-14"
    assert nfos["early"].findtext("premiered") == "2023-11-14"
    assert nfos["early"].findtext("episode") == "111400"
    assert nfos["newyear"].findtext("aired") == "2024-01-01"
    assert nfos["newyear"].findtext("season") == "2024"
    assert nfos["newyear"].findtext("episode") == "10100"
    assert numbers(ny_library.output_root)["newyear"] == (2024, 10100)


def old_numbers(video_id, season, episode, scheme="date", series=CHANNEL_ID):
    return EpisodeNumber.objects.create(
        series_kind="channel",
        series_id=series,
        scheme=scheme,
        youtube_id=video_id,
        season=season,
        episode=episode,
    )


def test_free_slot():
    taken = {(2023, 111400), (2023, 111401)}
    day = datetime(2023, 11, 14).date()
    assert _free_slot(taken, 2023, day, 0) == 111402
    assert _free_slot(taken, 2023, day, 5) == 111405
    assert _free_slot(taken, 2023, day, -1) == 111402
    full = {(2023, 111400 + n) for n in range(100)}
    assert _free_slot(full, 2023, day, 0) == 111500
    full.add((2023, 111500))
    assert _free_slot(full, 2023, day, 0) == 111501


def test_migration_renumbers_the_early_utc_uploads(ny_library):
    add_video(ny_library, "early", EARLY)
    add_video(ny_library, "newyear", NEW_YEAR)
    add_video(ny_library, "noon", NOON)
    # numbered by New York dates: 11-13 slot 0, 12-31 of 2023, 11-14 slot 0
    old_numbers("early", 2023, 111300)
    old_numbers("newyear", 2023, 123100)
    old_numbers("noon", 2023, 111400)
    # the slot on the new day is taken by noon, so early takes the next
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})

    summary = migrate_layout("America/New_York")

    got = {
        r.youtube_id: (r.season, r.episode)
        for r in EpisodeNumber.objects.all()
    }
    assert got == {
        "early": (2023, 111401),
        "newyear": (2024, 10100),
        "noon": (2023, 111400),
    }
    assert sorted(summary.utc_renumbered) == [
        f"{CHANNEL_ID}/early",
        f"{CHANNEL_ID}/newyear",
    ]
    assert summary.utc_season_changes == 1

    # the sync then moves the files once and settles
    report = sync_all(ny_library)
    assert not report.errors
    assert sync_all(ny_library).changed == 0


def test_year_scheme_only_changes_a_new_season(ny_library):
    add_video(ny_library, "early", EARLY)
    add_video(ny_library, "newyear", NEW_YEAR)
    add_video(ny_library, "y24", NEW_YEAR + 86400)
    Channel.objects.update(emby_numbering="year_frozen")
    old_numbers("early", 2023, 7, "year_frozen")
    old_numbers("newyear", 2023, 8, "year_frozen")
    old_numbers("y24", 2024, 1, "year_frozen")
    summary = MigrationSummary()

    _migrate_utc_dates(NY, summary)

    got = {
        r.youtube_id: (r.season, r.episode)
        for r in EpisodeNumber.objects.all()
    }
    assert got == {
        "early": (2023, 7),
        "newyear": (2024, 2),
        "y24": (2024, 1),
    }
    assert summary.utc_renumbered == [f"{CHANNEL_ID}/newyear"]


def test_migration_ignores_manual_and_other_schemes(ny_library):
    add_video(ny_library, "early", EARLY)
    old_numbers("early", 1, 5, "absolute_frozen")
    old_numbers("early", 2023, 99, "manual")
    summary = MigrationSummary()

    _migrate_utc_dates(NY, summary)

    assert not summary.utc_renumbered
    assert set(EpisodeNumber.objects.values_list("episode", flat=True)) == {
        5,
        99,
    }
    assert numbering_date(Video.objects.get()) is not None


def test_dry_run_prints_the_count_first(ny_library, monkeypatch):
    monkeypatch.setattr(EnvironmentSettings, "TZ", "America/New_York")
    monkeypatch.setattr(EnvironmentSettings, "EMBY_ROOT", "")
    add_video(ny_library, "early", EARLY)
    old_numbers("early", 2023, 111300)
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    out = io.StringIO()

    call_command(
        "ta_emby_migrate_layout",
        "--dry-run",
        "--verbose-changes",
        stdout=out,
    )

    lines = out.getvalue().splitlines()
    assert lines[0] == (
        "[dry-run] UTC dates: 1 episodes renumbered, 0 of them change season"
    )
    assert f"[dry-run]   UTC date: {CHANNEL_ID}/early" in lines
    assert EpisodeNumber.objects.get().episode == 111300  # rolled back

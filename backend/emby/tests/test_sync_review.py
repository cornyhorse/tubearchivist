"""
regression tests for the 2026-10-11 review of the Emby sync: frozen
names (E6), dry runs (E9), media paths (E13), the device check (E5),
long names and one failing series (E1), and the layout gate
"""

import os
from pathlib import Path

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from common.tests.docs import channel_doc
from emby.models import EpisodeNumber
from emby.src import sync as sync_module
from emby.src.layout import MAX_BASENAME_BYTES
from emby.src.numbering import NumberingError
from emby.src.sync import (
    LAYOUT_KEY,
    EmbyLayoutOutdated,
    layout_version,
    sync_all,
    sync_channel,
    sync_playlist,
    validate_output_root,
)
from emby.tests.test_sync import numbers
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db
CHANNEL_ID = channel_doc()["channel_id"]


def files(root: Path) -> dict[str, int]:
    """relpath -> inode of every file under root, manifest excluded"""
    return {
        str(p.relative_to(root)): p.stat().st_ino
        for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".ta-emby")
    }


def test_renamed_titles_never_move_files(library):
    sync_all(library)
    before = files(library.output_root)
    Video.objects.filter(pk="v1").update(title="A brand new title")
    Channel.objects.update(name="Renamed Channel")
    Playlist.objects.update(name="Renamed Playlist")

    report = sync_all(library)

    assert not report.renamed and not report.created and not report.deleted
    after = files(library.output_root)
    assert after.keys() == before.keys()
    media = [p for p in before if p.endswith(".mp4")]
    assert [after[p] for p in media] == [before[p] for p in media]
    nfos = [p.read_text() for p in library.output_root.rglob("*.nfo")]
    assert sum("A brand new title" in n for n in nfos) == 2
    assert sum("<title>Renamed Channel</title>" in n for n in nfos) == 1


def test_names_are_frozen_with_the_numbers(library):
    sync_channel(CHANNEL_ID, library)

    rows = EpisodeNumber.objects.filter(series_id=CHANNEL_ID)
    assert {r.series_name for r in rows} == {"Test Channel"}
    assert all(r.name == "Riding the Big Coaster" for r in rows)


def test_scheme_switch_keeps_frozen_names(library):
    sync_channel(CHANNEL_ID, library)
    Video.objects.update(title="Changed")
    Channel.objects.update(name="Changed", emby_numbering="year_frozen")

    report = sync_channel(CHANNEL_ID, library)

    assert len(report.renamed) == 9
    assert all("Riding the Big Coaster" in new for _, new in report.renamed)
    assert all("/Test Channel [" in new for _, new in report.renamed)


def test_unnamed_rows_are_named_on_the_next_sync(library):
    sync_channel(CHANNEL_ID, library)
    EpisodeNumber.objects.update(name="", series_name="")
    Video.objects.filter(pk="v2").update(title="Second title")

    sync_channel(CHANNEL_ID, library)

    names = dict(
        EpisodeNumber.objects.filter(series_id=CHANNEL_ID).values_list(
            "youtube_id", "name"
        )
    )
    assert names["v2"] == "Second title"
    assert set(
        EpisodeNumber.objects.values_list("series_name", flat=True)
    ) == {"Test Channel"}


def test_playlist_position_keeps_names_across_renumbering(library):
    Playlist.objects.update(emby_numbering="playlist_position")
    sync_playlist("PL1", library)
    Video.objects.update(title="Changed")
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    playlist.entries.create(idx=0, youtube_id="v1")
    playlist.entries.create(idx=1, youtube_id="v3")

    report = sync_playlist("PL1", library)

    assert numbers(library.output_root) == {"v1": (1, 1), "v3": (1, 2)}
    assert all("Changed" not in new for _, new in report.renamed)


def test_playlists_default_to_frozen_order(library):
    field = Playlist._meta.get_field("emby_numbering")
    assert field.default == "playlist_frozen"
    Playlist.objects.update(emby_numbering=field.default)
    sync_playlist("PL1", library)
    assert numbers(library.output_root) == {"v3": (1, 1), "v1": (1, 2)}
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    playlist.entries.create(idx=0, youtube_id="v2")
    playlist.entries.create(idx=1, youtube_id="v1")
    playlist.entries.create(idx=2, youtube_id="v3")

    report = sync_playlist("PL1", library)

    assert numbers(library.output_root) == {
        "v3": (1, 1),
        "v1": (1, 2),
        "v2": (1, 3),
    }
    assert not report.renamed


def test_dry_run_stores_nothing(library):
    """E9"""
    sync_all(library, dry_run=True)
    sync_channel(CHANNEL_ID, library, dry_run=True)
    sync_playlist("PL1", library, dry_run=True)

    assert not EpisodeNumber.objects.exists()
    assert not ConfigDocument.objects.filter(key=LAYOUT_KEY).exists()


def test_dry_run_of_a_scheme_switch_keeps_stored_numbers(library):
    Playlist.objects.update(emby_numbering="playlist_position")
    sync_playlist("PL1", library)
    before = list(EpisodeNumber.objects.values_list("pk", "episode"))
    Playlist.objects.update(emby_numbering="playlist_position")
    playlist = Playlist.objects.get()
    playlist.entries.all().delete()
    playlist.entries.create(idx=5, youtube_id="v1")

    sync_playlist("PL1", library, dry_run=True)

    assert list(EpisodeNumber.objects.values_list("pk", "episode")) == before


def test_media_url_outside_media_dir_is_skipped(library):
    """E13"""
    secret = library.media_root.parent / "secret.mp4"
    secret.write_bytes(b"not archive media")
    Video.objects.filter(pk="v2").update(media_url="../secret.mp4")

    report = sync_channel(CHANNEL_ID, library)

    assert any("outside the media dir" in w for w in report.warnings)
    linked = {p.stat().st_ino for p in library.output_root.rglob("*.mp4")}
    assert secret.stat().st_ino not in linked
    assert len(linked) == 2


def test_long_titles_fit_the_name_limit(library):
    """E1"""
    Channel.objects.update(name="Ch" * 100)
    Video.objects.filter(pk="v1").update(title="é" * 300)

    report = sync_all(library)

    assert not report.errors
    for path in library.output_root.rglob("*"):
        assert len(path.name.encode()) < 255
        if path.suffix == ".mp4":
            assert len(path.stem.encode()) <= MAX_BASENAME_BYTES


def test_failing_series_is_left_and_others_sync(library, monkeypatch):
    """E1: one bad series no longer stops sync_all"""
    sync_all(library)
    playlist_dir = next((library.output_root / "playlists").iterdir())
    before = files(playlist_dir)
    real = sync_module.playlist_series

    def broken(*_):
        raise NumberingError("duplicate playlist positions")

    monkeypatch.setattr(sync_module, "playlist_series", broken)
    Video.objects.filter(pk="v2").update(active=False)

    report = sync_all(library)

    assert report.errors == ["playlists/PL1: duplicate playlist positions"]
    assert files(playlist_dir) == before
    assert len(numbers(library.output_root / "channels")) == 2
    monkeypatch.setattr(sync_module, "playlist_series", real)
    assert not sync_all(library).errors


def test_media_on_another_device_is_refused(tmp_path, monkeypatch):
    """E5"""
    media = tmp_path / "media"
    media.mkdir()
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == media:
            fields = list(result[:10])
            fields[2] += 1
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stat)

    with pytest.raises(ValueError, match="another filesystem"):
        validate_output_root(media, tmp_path / "emby" / "not-yet")


def test_old_layout_numbers_block_sync(library):
    sync_all(library)
    ConfigDocument.objects.filter(key=LAYOUT_KEY).delete()

    for run in (
        lambda: sync_all(library),
        lambda: sync_channel(CHANNEL_ID, library),
        lambda: sync_playlist("PL1", library, dry_run=True),
    ):
        with pytest.raises(EmbyLayoutOutdated, match="ta_emby_migrate"):
            run()


def test_fresh_database_starts_on_the_current_layout(library):
    assert layout_version() == 1

    sync_all(library)

    assert layout_version() == 2
    ConfigDocument.objects.filter(key=LAYOUT_KEY).update(value={})
    assert layout_version() == 1

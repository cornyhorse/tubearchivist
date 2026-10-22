"""a sync that stops halfway must not forget the NFOs it rewrote"""

from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from emby.src import writer as writer_module
from emby.src.health import check_emby_health
from emby.src.sync import sync_all
from emby.src.tag_import import tag_drift
from emby.src.writer import DesiredFile, EmbyWriter
from jobs.models import Job
from mediaserver.src import jobs as ms_jobs
from mediaserver.src import pending, settings
from mediaserver.tests.test_mediaserver import queue
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db


@pytest.fixture(name="server", autouse=True)
def fixture_server(monkeypatch, tmp_path):
    """a media server is set up; nothing waits at the start"""
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    settings.update({"type": "emby", "url": "http://e:8096", "api_key": "k"})
    for entry in pending.entries():
        pending.done(entry)


def test_writer_tells_about_rewritten_data_files_only(tmp_path):
    told = []
    writer = EmbyWriter(tmp_path, on_update=told.append)
    item = DesiredFile("a/x.nfo", "data", "o", "nfo", data=b"1")

    writer.sync([item], scope=None)
    assert told == []  # a new file is not an update

    changed = DesiredFile("a/x.nfo", "data", "o", "nfo", data=b"2")
    EmbyWriter(tmp_path, dry_run=True, on_update=told.append).sync(
        [changed], scope=None
    )
    assert told == []  # nor is a dry run

    EmbyWriter(tmp_path, on_update=told.append).sync([changed], scope=None)
    assert told == [changed]


def test_sync_remembers_tag_changes_for_refresh(library):
    sync_all(library)
    assert pending.count() == 0  # creating files is for the scan

    Channel.objects.update(emby_tags=["David"])
    Playlist.objects.update(emby_tags=["Kids"])
    sync_all(library)

    entries = pending.entries()
    assert [e for e in entries if e.startswith("series:channels/")]
    assert not [e for e in entries if e.startswith("episode:")]

    # a changed episode NFO is remembered with the media file it belongs to
    Video.objects.filter(pk="v1").update(title="A new title")
    sync_all(library)
    episodes = [e for e in pending.entries() if e.startswith("episode:")]
    assert episodes and all(e.endswith(".mp4") for e in episodes)
    assert any(e.startswith("episode:channels/") for e in episodes)


def test_a_sync_that_stops_halfway_keeps_what_it_did(library, monkeypatch):
    sync_all(library)
    Channel.objects.update(emby_tags=["David"], description="changed")
    real = writer_module.atomic_write
    calls = []

    def crash(path, data, mode):
        calls.append(path.name)
        if len(calls) > 1:
            raise RuntimeError("the container was restarted")
        return real(path, data, mode)

    monkeypatch.setattr(writer_module, "atomic_write", crash)
    with pytest.raises(RuntimeError, match="restarted"):
        sync_all(library)
    monkeypatch.setattr(writer_module, "atomic_write", real)

    # the first rewritten NFO was remembered before the crash
    assert len(pending.entries()) == 1
    # the next sync finds that one unchanged, and so cannot queue it again
    sync_all(library)
    assert len(pending.entries()) == 1
    # but the refresh job is queued for what waits, from anywhere
    assert ms_jobs.queue_item_refresh() is not None
    assert Job.objects.filter(type="media_refresh_items").exists()


def test_recorder_ignores_other_files_and_unknown_media(library):
    sync_all(library)
    writer = EmbyWriter(library.output_root)
    from emby.src.sync import _remember_for_refresh  # noqa: PLC0415

    remember = _remember_for_refresh(writer)

    remember(DesiredFile("a/poster.jpg", "data", "o", "poster", data=b"x"))
    remember(DesiredFile("a/e.nfo", "data", "nobody", "nfo", data=b"x"))
    assert pending.count() == 0

    remember(DesiredFile("a/tvshow.nfo", "data", "o", "tvshow", data=b"x"))
    assert pending.entries() == ["series:a"]


def test_no_recording_without_a_media_server(library):
    settings.update({"type": None})
    sync_all(library)
    Channel.objects.update(emby_tags=["David"])

    sync_all(library)

    assert pending.count() == 0


def test_health_reports_refreshes_that_wait_too_long(library):
    sync_all(library)
    assert check_emby_health(library).healthy
    pending.add("series:a")
    assert check_emby_health(library).healthy  # fresh

    old = datetime.now(timezone.utc) - timedelta(hours=7)
    pending._conn().set(  # pylint: disable=protected-access
        pending._key(pending.SINCE_KEY),  # pylint: disable=protected-access
        old.isoformat(),
    )
    report = check_emby_health(library)

    assert "1 Emby item refreshes have waited since" in report.problems[0]
    assert queue(set()) is not None  # and the job can be queued again


class DriftClient:
    """shows tagged by hand in Emby"""

    def __init__(self, shows):
        self.shows = shows

    def series_with_tag(self, tag):
        return [s for s in self.shows if tag in s["tags"]]


def test_tag_drift(library):
    base = "/mnt/emby"
    settings.update({"path_to": base})
    channel = Channel.objects.get()
    folder = f"channels/Test [{channel.pk}]"
    shows = [
        {
            "id": "1",
            "name": "Test",
            "path": f"{base}/{folder}",
            "provider_ids": {},
            "tags": ["David"],
        },
        {
            "id": "2",
            "name": "Handmade",
            "path": "/other/place/Handmade",
            "provider_ids": {},
            "tags": ["David"],
        },
        {
            "id": "3",
            "name": "Orphan",
            "path": f"{base}/channels/Orphan [UCnone]",
            "provider_ids": {},
            "tags": ["David"],
        },
    ]
    client = DriftClient(shows)

    problems = tag_drift(client, ["David"])

    assert len(problems) == 2
    assert "Test is tagged David by hand but TA does not tag channel" in (
        problems[1] if "Test" in problems[1] else problems[0]
    )
    assert any(
        "Orphan is tagged David by hand and matches no" in p for p in problems
    )

    # TA tags it: no drift; a refresh waiting for it: no drift either
    Channel.objects.update(emby_tags=["david"])
    assert len(tag_drift(client, ["David"])) == 1
    Channel.objects.update(emby_tags=[])
    pending.add(f"series:{folder}")
    assert len(tag_drift(client, ["David"])) == 1
    assert PurePosixPath(folder).name.startswith("Test")

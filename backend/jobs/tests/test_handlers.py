"""job handlers: scans queue downloads as they find videos"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from django.utils import timezone
from download.models import Download, DownloadRule
from download.src import queue as queue_mod
from download.tests.test_src.test_queue import (  # noqa: F401
    fixture_yt,
    flat_entry,
)
from jobs.models import Job, JobStatus, Priority
from jobs.src import handlers
from jobs.src.context import JobContext
from jobs.src.engine import enqueue
from jobs.src.registry import Defer, JobError
from playlist.models import Playlist
from video.models import Video

CHANNEL_ID = "UCchannel000000000000001"


def running(job_type, target=None, **kwargs):
    """a claimed job and its context"""
    job, _ = enqueue(job_type, target, dispatch=False, **kwargs)
    job.status = JobStatus.RUNNING
    job.save()
    return job, JobContext(job)


def children(job_type=None):
    """(type, target, parent) of jobs after the first"""
    jobs = Job.objects.exclude(parent__isnull=True).order_by("id")
    if job_type:
        jobs = jobs.filter(type=job_type)
    return [(j.type, j.target_id, j.parent_id) for j in jobs]


class Recorder:
    """fake worker class recording calls"""

    calls: list = []
    returns: dict = {}

    def __init__(self, *args, **kwargs):
        self.calls.append(("init", args, kwargs))

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.returns.get(name)

        return method


@pytest.fixture(name="fake")
def fixture_fake(monkeypatch):
    """replace a name in jobs.src.handlers with a fresh Recorder"""

    def install(name, returns=None):
        fake = type(name, (Recorder,), {"calls": [], "returns": returns or {}})
        monkeypatch.setattr(handlers, name, fake)
        return fake

    return install


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params, expected",
    [({}, False), ({"rule": None}, None)],
)
def test_rule_from_params_simple(params, expected):
    assert handlers.rule_from_params(params) is expected


@pytest.mark.django_db
def test_rule_from_params_draft():
    rule = handlers.rule_from_params(
        {"rule": {"min_duration": 3600, "published_after": "2024-01-02"}}
    )
    assert isinstance(rule, DownloadRule) and rule.pk is None
    assert (rule.min_duration, rule.published_after) == (
        3600,
        date(2024, 1, 2),
    )


@pytest.mark.django_db(transaction=True)
def test_channel_check_downloads_while_scanning(
    yt, no_broker, monkeypatch, app_config
):
    """
    the fix for 'a scan blocks downloads': the first allowed video's
    download job is running before the scan looks at the next video
    """
    # pylint: disable=unused-argument
    DownloadRule.objects.create(channel=None, max_duration=1000)
    yt.channel_videos = [
        flat_entry("vid1", duration=600),
        flat_entry("long", duration=5000),
        flat_entry("vid2", duration=600),
    ]
    monkeypatch.setattr(queue_mod, "rand_sleep", lambda config: None)
    seen_when_parsing = {}
    real_parse = queue_mod.PendingList._parse_entry

    def parse(self, youtube_id, video_data):
        seen_when_parsing[youtube_id] = [
            Job.objects.get(pk=args[0]).target_id
            for name, args, _ in no_broker
            if name == "run_job"
        ]
        return real_parse(self, youtube_id, video_data)

    monkeypatch.setattr(queue_mod.PendingList, "_parse_entry", parse)
    job, ctx = running(
        "check_channel",
        CHANNEL_ID,
        params={"download_now": True, "flat": True},
        created_by="user",
        priority=Priority.HIGH,
    )

    result = handlers.check_channel(ctx)

    assert result == {
        "found": 3,
        "allowed": 2,
        "ignored": 1,
        "queued_downloads": 2,
    }
    # vid1 was already downloading when the scan reached the next videos
    assert seen_when_parsing["vid1"] == []
    assert seen_when_parsing["long"] == ["vid1"]
    downloads = Job.objects.filter(type="download_video").order_by("id")
    assert [(d.target_id, d.priority, d.created_by) for d in downloads] == [
        ("vid1", Priority.HIGH, "user"),
        ("vid2", Priority.HIGH, "user"),
    ]
    assert downloads[0].parent_id == job.pk
    assert Download.objects.get(pk="vid1").found_by_kind == "user"
    assert Download.objects.get(pk="long").status == "ignore"


@pytest.mark.django_db
def test_scan_waits_unless_opted_in(yt, app_config):
    # pylint: disable=unused-argument
    yt.channel_videos = [flat_entry("vid1")]
    _, ctx = running(
        "check_channel",
        CHANNEL_ID,
        params={"flat": True},
        created_by="schedule",
    )

    result = handlers.check_channel(ctx)

    # auto download is off by default: found, not queued
    assert result["queued_downloads"] == 0
    assert Download.objects.get().found_by_kind == "channel_check"


@pytest.mark.django_db
def test_check_channel_entries(fake, app_config):
    # pylint: disable=unused-argument
    calls = []
    Channel.save_doc(channel_doc(channel_tabs=["videos", "shorts"]))

    def fake_scan(ctx, entries, kind, playlist_id=None, source=None):
        calls.append((entries, kind, playlist_id))
        return {}

    handlers_scan = handlers.scan
    try:
        handlers.scan = fake_scan
        subscribed = fake(
            "ChannelSubscription", returns={"channel_urls": ["from-tabs"]}
        )
        _, ctx = running("check_channel", CHANNEL_ID)
        handlers.check_channel(ctx)
        assert subscribed.calls[1][0] == "channel_urls"

        _, ctx = running(
            "check_channel", CHANNEL_ID, params={"entries": ["given"]}
        )
        Job.objects.update(status=JobStatus.DONE)
        handlers.check_channel(ctx)

        Job.objects.update(status=JobStatus.DONE)
        _, ctx = running("check_channel", CHANNEL_ID, params={"newest_n": 3})
        handlers.check_channel(ctx)
    finally:
        handlers.scan = handlers_scan

    assert calls[0] == (["from-tabs"], "channel_check", None)
    assert calls[1] == (["given"], "channel_check", None)
    assert calls[2][0][0]["limit"] == 3


@pytest.mark.django_db
def test_check_subscriptions(app_config):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc(channel_subscribed=True))
    Channel.save_doc(channel_doc(channel_id="UCnot", channel_subscribed=False))
    Playlist.save_doc(playlist_doc("PL1", [], playlist_subscribed=True))
    job, ctx = running(
        "check_subscriptions",
        params={"download_now": True},
        priority=Priority.LOW,
        created_by="schedule",
    )

    result = handlers.check_subscriptions(ctx)

    assert result == {"channels": 1, "playlists": 1}
    assert children() == [
        ("check_channel", CHANNEL_ID, job.pk),
        ("check_playlist", "PL1", job.pk),
    ]
    child = Job.objects.get(type="check_channel")
    assert (child.priority, child.created_by, child.params) == (
        Priority.LOW,
        "schedule",
        {"download_now": True},
    )
    assert child.target_title == "Test Channel"


@pytest.mark.django_db
def test_check_playlist_limits(app_config):
    # pylint: disable=unused-argument
    calls = []
    real = handlers.scan
    handlers.scan = lambda ctx, entries, kind, playlist_id=None, **_: (
        calls.append((entries[0]["limit"], kind, playlist_id))
    )
    try:
        _, ctx = running("check_playlist", "PL1")
        handlers.check_playlist(ctx)
        Job.objects.update(status=JobStatus.DONE)
        Playlist.save_doc(playlist_doc("PL1", [], playlist_subscribed=True))
        _, ctx = running("check_playlist", "PL1")
        handlers.check_playlist(ctx)
        Job.objects.update(status=JobStatus.DONE)
        _, ctx = running("check_playlist", "PL1", params={"newest_n": 2})
        handlers.check_playlist(ctx)
    finally:
        handlers.scan = real

    size = app_config["subscriptions"]["playlist_size"]
    assert calls == [
        (None, "playlist_check", "PL1"),
        (size, "playlist_check", "PL1"),
        (2, "playlist_check", "PL1"),
    ]


@pytest.mark.django_db
def test_fetch_video(yt, app_config):
    # pylint: disable=unused-argument,import-outside-toplevel
    from download.tests.test_src.test_queue import yt_meta

    yt.videos["vid00000001"] = yt_meta("vid00000001")
    _, ctx = running(
        "fetch_video",
        "vid00000001",
        params={"download_now": True},
        created_by="agent",
    )

    assert handlers.fetch_video(ctx)["queued_downloads"] == 1
    assert Download.objects.get().found_by_kind == "agent"


@pytest.mark.django_db
def test_download_video_gone(app_config):
    # pylint: disable=unused-argument
    _, ctx = running("download_video", "nope")
    with pytest.raises(JobError, match="not in found videos"):
        handlers.download_video(ctx)

    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="done"))
    _, ctx = running("download_video", "done")
    assert handlers.download_video(ctx) == {"skipped": "already downloaded"}

    Download.upsert_doc(download_doc(youtube_id="ign", status="ignore"))
    _, ctx = running("download_video", "ign")
    assert handlers.download_video(ctx) == {"skipped": "ignored"}


@pytest.mark.django_db
def test_download_video(app_config, monkeypatch):
    # pylint: disable=unused-argument
    Download.upsert_doc(download_doc(youtube_id="ok", message="old error"))
    seen = []

    class FakeDownloader:
        def __init__(self, task):
            self.task = task

        def download_one(self, video_data):
            seen.append(video_data["message"])
            return True

    monkeypatch.setattr(handlers, "VideoDownloader", FakeDownloader)
    job, ctx = running("download_video", "ok")

    assert handlers.download_video(ctx) == {"downloaded": "ok"}
    assert seen == [None]
    post = Job.objects.get(type="post_download")
    assert (post.parent_id, post.priority) == (job.pk, Priority.LOW)
    assert post.not_before > timezone.now() + timedelta(seconds=30)


@pytest.mark.django_db
def test_download_video_fails(app_config, monkeypatch):
    # pylint: disable=unused-argument
    Download.upsert_doc(download_doc(youtube_id="bad"))
    Download.upsert_doc(download_doc(youtube_id="silent"))

    class FakeDownloader:
        def __init__(self, task):
            pass

        def download_one(self, video_data):
            if video_data["youtube_id"] == "bad":
                Download.objects.filter(pk="bad").update(message="410 Gone")
            return False

    monkeypatch.setattr(handlers, "VideoDownloader", FakeDownloader)
    _, ctx = running("download_video", "bad")
    with pytest.raises(JobError, match="410 Gone"):
        handlers.download_video(ctx)
    _, ctx = running("download_video", "silent")
    with pytest.raises(JobError, match="download failed"):
        handlers.download_video(ctx)


@pytest.mark.django_db
def test_post_download(fake, monkeypatch):
    post = fake("DownloadPostProcess")
    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    enqueue("download_video", "busy", dispatch=False)
    job, ctx = running("post_download")

    with pytest.raises(Defer):
        handlers.post_download(ctx)

    Job.objects.filter(type="download_video").update(status=JobStatus.DONE)
    assert handlers.post_download(ctx) == {"done": True}
    assert post.calls[0][2] == {"task": ctx}
    assert children("emby_write") == [("emby_write", None, job.pk)]


@pytest.mark.django_db
def test_emby_write(monkeypatch, capsys):
    monkeypatch.setattr(handlers, "paths_from_env", lambda: None)
    _, ctx = running("emby_write")
    assert handlers.emby_write(ctx) == {"skipped": "TA_EMBY_ROOT is not set"}

    report = SimpleNamespace(
        created=["c/A/1.nfo", "c/A/2.nfo"],
        updated=["c/A/tvshow.nfo"],
        renamed=[],
        deleted=["c/B/"],
        warnings=["w1"],
        errors=[],
        failed={},
    )
    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    monkeypatch.setattr(handlers, "sync_all", lambda paths: report)
    assert handlers.emby_write(ctx) == {
        "created": 2,
        "updated": 1,
        "renamed": 0,
        "removed": 1,
        "errors": 0,
    }
    assert "[emby] w1" in capsys.readouterr().out

    report.errors = ["channels/X: name too long"]
    report.failed = {"channels/X": "name too long"}
    result = handlers.emby_write(ctx)
    assert (result["errors"], result["failed"]) == (
        1,
        {"channels/X": "name too long"},
    )
    assert "[emby] error: channels/X" in capsys.readouterr().out


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [
        handlers.EmbyLayoutOutdated("run ta_emby_migrate_layout"),
        handlers.EmbyRootError("no hardlinks"),
    ],
)
def test_emby_write_needs_a_person(monkeypatch, error):
    def fail(paths):
        raise error

    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    monkeypatch.setattr(handlers, "sync_all", fail)
    _, ctx = running("emby_write")
    with pytest.raises(JobError, match=str(error)):
        handlers.emby_write(ctx)


@pytest.mark.django_db
def test_emby_jobs_wait_for_the_lock(monkeypatch):
    # pylint: disable=import-outside-toplevel
    from jobs.src.locks import emby_lock

    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    for job_type in ["emby_write", "emby_health"]:
        _, ctx = running(job_type)
        with emby_lock():
            with pytest.raises(Defer):
                getattr(handlers, job_type)(ctx)
        Job.objects.update(status=JobStatus.DONE)


@pytest.mark.django_db
def test_emby_health(monkeypatch):
    monkeypatch.setattr(handlers, "paths_from_env", lambda: None)
    job, ctx = running("emby_health")
    assert handlers.emby_health(ctx) == {"skipped": "TA_EMBY_ROOT is not set"}

    report = SimpleNamespace(
        healthy=True,
        repaired=["channels/X: name too long, 3 names shortened"],
        rerun_sync=True,
        message=lambda: "repaired: channels/X",
    )
    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    monkeypatch.setattr(
        handlers, "check_emby_health", lambda paths, repair: report
    )
    assert handlers.emby_health(ctx) == {
        "healthy": True,
        "repaired": report.repaired,
    }
    assert children("emby_write") == [("emby_write", None, job.pk)]

    report.healthy, report.rerun_sync = False, False
    report.message = lambda: "problem: no successful Emby sync since ever"
    with pytest.raises(JobError, match="no successful Emby sync"):
        handlers.emby_health(ctx)


@pytest.mark.django_db
def test_request_emby_write(monkeypatch):
    monkeypatch.setattr(handlers, "paths_from_env", lambda: None)
    assert handlers.request_emby_write() is None
    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    assert handlers.request_emby_write().type == "emby_write"


@pytest.mark.django_db
def test_reindex(fake, monkeypatch):
    populate = fake("ReindexPopulate")
    reindex = fake("Reindex", returns={"build_message": "3 refreshed"})
    monkeypatch.setattr(handlers, "paths_from_env", lambda: None)

    _, ctx = running("reindex")
    assert handlers.reindex(ctx) == {"message": "3 refreshed"}
    assert [c[0] for c in populate.calls] == [
        "init",
        "get_interval",
        "add_recent",
        "add_outdated",
    ]
    assert ("reindex_all", (), {}) in reindex.calls

    Job.objects.update(status=JobStatus.DONE)
    populate.calls.clear()
    _, ctx = running("reindex", params={"populate": False})
    handlers.reindex(ctx)
    assert not populate.calls


@pytest.mark.django_db
@pytest.mark.parametrize(
    "job_type, target, params, fake_name, expected_calls",
    [
        (
            "backup",
            None,
            {"reason": "manual"},
            "DatabaseBackup",
            ["init", "backup_all_indexes"],
        ),
        (
            "restore",
            "b.zip",
            {"filename": "b.zip"},
            "DatabaseBackup",
            ["init", "restore"],
        ),
        (
            "thumbnail_check",
            None,
            {},
            "ThumbValidator",
            ["init", "validate", "clean_up"],
        ),
        ("resync_metadata", None, {}, "MetadataEmbed", ["init", "embed"]),
        ("manual_import", None, {}, "ImportFolderScanner", ["init", "scan"]),
        (
            "index_playlists",
            "UC1",
            {},
            "YoutubeChannel",
            ["init", "index_channel_playlists"],
        ),
        (
            "subscribe",
            None,
            {"urls": "UC1"},
            "SubscriptionHandler",
            ["init", "subscribe"],
        ),
        ("version_check", None, {}, "ReleaseVersion", ["init", "check"]),
        ("snapshot", None, {}, "DbSnapshot", ["init", "take_snapshot_now"]),
    ],
)
def test_maintenance(
    fake, job_type, target, params, fake_name, expected_calls
):
    # pylint: disable=too-many-arguments
    recorder = fake(fake_name)
    _, ctx = running(job_type, target, params=params)

    result = getattr(handlers, job_type)(ctx)

    assert result
    assert [c[0] for c in recorder.calls] == expected_calls


@pytest.mark.django_db
def test_filesystem_rescan(fake):
    scanner = fake("Scanner")
    job, ctx = running("filesystem_rescan", params={"ignore_error": True})

    handlers.filesystem_rescan(ctx)

    assert scanner.calls[0][2]["ignore_error"] is True
    assert [c[0] for c in scanner.calls] == ["init", "scan", "apply"]
    assert children() == [("thumbnail_check", None, job.pk)]


@pytest.mark.django_db
def test_prepare_channel(fake, app_config):
    # pylint: disable=unused-argument
    channel = fake("YoutubeChannel")
    Channel.save_doc(channel_doc())
    _, ctx = running(
        "check_channel",
        CHANNEL_ID,
        params={
            "subscribe": True,
            "save_rule": {"min_duration": 60},
            "numbering": "absolute_frozen",
        },
    )

    handlers.prepare_channel(ctx)

    assert channel.calls[1] == (
        "change_subscribe",
        (),
        {"new_subscribe_state": True},
    )
    assert DownloadRule.objects.get(channel_id=CHANNEL_ID).min_duration == 60
    assert Channel.objects.get().emby_numbering == "absolute_frozen"

    # without subscribing the channel is indexed first; null rule deletes
    channel.calls.clear()
    ctx.job.params = {"save_rule": None}
    handlers.prepare_channel(ctx)
    assert channel.calls[1] == ("build_json", (), {"upload": True})
    assert not DownloadRule.objects.exists()

    channel.calls.clear()
    ctx.job.params = {}
    handlers.prepare_channel(ctx)
    assert not channel.calls


@pytest.mark.django_db
def test_playlist_subscribe_and_numbering(fake, app_config, monkeypatch):
    # pylint: disable=unused-argument
    playlist = fake("YoutubePlaylist")

    def fake_scan(ctx, entries, kind, playlist_id=None, source=None):
        Playlist.save_doc(playlist_doc("PL1", []))
        return {"found": 0}

    monkeypatch.setattr(handlers, "scan", fake_scan)
    _, ctx = running(
        "check_playlist",
        "PL1",
        params={"subscribe": True, "numbering": "playlist_position"},
    )

    assert handlers.check_playlist(ctx) == {"found": 0}
    assert playlist.calls[1][0] == "change_subscribe"
    assert Playlist.objects.get().emby_numbering == "playlist_position"

    # storing a rule without subscribing indexes the playlist first
    Job.objects.update(status=JobStatus.DONE)
    playlist.calls.clear()
    _, ctx = running(
        "check_playlist", "PL1", params={"save_rule": {"max_duration": 60}}
    )
    handlers.check_playlist(ctx)
    assert playlist.calls[1][0] == "update_playlist"
    assert DownloadRule.objects.get(playlist_id="PL1").max_duration == 60


@pytest.mark.django_db
def test_fetch_video_subscribe(fake, monkeypatch, app_config):
    # pylint: disable=unused-argument
    subscriber = fake("SubscriptionHandler")
    monkeypatch.setattr(handlers, "scan", lambda ctx, entries, kind: {})
    _, ctx = running("fetch_video", "vid00000001", params={"subscribe": True})

    handlers.fetch_video(ctx)

    assert subscriber.calls == [
        ("init", ("vid00000001",), {}),
        ("subscribe", (), {}),
    ]

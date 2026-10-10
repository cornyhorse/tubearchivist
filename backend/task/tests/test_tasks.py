"""celery task bodies and BaseTask callbacks, heavy workers faked"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from common.src.ta_redis import RedisArchivist, TaskRedis
from task import tasks
from task.src.notify import Notifications
from task.src.task_manager import TaskManager

pytestmark = pytest.mark.django_db


class Recorder:
    """fake worker class: records construction and every method call"""

    calls: list = []
    returns: dict = {}
    attrs: dict = {}

    def __init__(self, *args, **kwargs):
        self.calls.append(("init", args, kwargs))
        for key, value in self.attrs.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.returns.get(name)

        return method


@pytest.fixture(name="fake")
def fixture_fake(monkeypatch):
    """replace a class in task.tasks with a fresh Recorder"""

    def install(name, returns=None, attrs=None):
        fake = type(
            name,
            (Recorder,),
            {"calls": [], "returns": returns or {}, "attrs": attrs or {}},
        )
        monkeypatch.setattr(tasks, name, fake)
        return fake

    return install


@pytest.fixture(name="emby_paths")
def fixture_emby_paths(monkeypatch):
    """toggle emby sync on or off"""

    def install(enabled):
        value = SimpleNamespace(root="emby") if enabled else None
        monkeypatch.setattr(tasks, "paths_from_env", lambda: value)

    return install


def run(task, *args, **kwargs):
    """run a bound task body with a request id, as the worker would"""
    task.push_request(id=str(uuid4()))
    try:
        return task.run(*args, **kwargs)
    finally:
        task.pop_request()


def make_pending(task):
    """an earlier run of task is still pending"""
    TaskManager().init(
        SimpleNamespace(name=task.name, request=SimpleNamespace(id="busy"))
    )


def messages(conn):
    """all progress messages written so far"""
    keys = conn.keys(f"{RedisArchivist.NAME_SPACE}message:*")
    return [json.loads(conn.get(key)) for key in keys]


# BaseTask callbacks


def test_callbacks(redis_ns, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(
        Notifications, "send", lambda self, *args: sent.append(args)
    )
    task = tasks.thumbnail_check
    task.push_request(id="abcd-1234")
    try:
        task.before_start("abcd-1234", (), {})
        task.on_success(None, "abcd-1234", (), {})
        TaskRedis().set_key("abcd-1234", {"command": "STOP"})
        task.on_failure(ValueError("bad"), "abcd-1234", (), {}, None)
        task.send_progress(["half"], progress=0.5, title="Thumbs")
        task.after_return("SUCCESS", None, "abcd-1234", (), {}, None)
        stopped = task.is_stopped()
    finally:
        task.pop_request()

    stored = RedisArchivist().get_message_dict(
        "message:setting:thumbnailcheck:abcd"
    )
    assert stored["messages"] == ["half"]
    assert stored["progress"] == 0.5
    assert stored["title"] == "Thumbs"
    assert stored["command"] == "STOP"
    assert stopped is True
    assert sent == [("abcd-1234", "Check your Thumbnails")]
    output = capsys.readouterr().out
    assert "Failed callback" in output and "success callback" in output


# pending guard, shared by most tasks

PENDING_GUARDED = [
    (tasks.update_subscribed, ()),
    (tasks.download_pending, ()),
    (tasks.check_reindex, ()),
    (tasks.manual_import, (False, False)),
    (tasks.run_backup, ()),
    (tasks.run_restore_backup, ("backup.zip",)),
    (tasks.rescan_filesystem, (False, False)),
    (tasks.thumbnail_check, ()),
    (tasks.re_sync_metadata, ()),
]


@pytest.mark.parametrize("task, args", PENDING_GUARDED)
def test_already_running(redis_ns, task, args):
    make_pending(task)

    assert run(task, *args) is None
    assert any(
        "already" in " ".join(i["messages"]) for i in messages(redis_ns)
    )


def test_emby_sync_already_running(redis_ns, emby_paths):
    # pylint: disable=unused-argument
    emby_paths(True)
    make_pending(tasks.emby_sync)

    assert run(tasks.emby_sync) is None


# task bodies


@pytest.mark.parametrize(
    "added, auto_start, expected, queued",
    [
        (3, True, "Found 3 videos to add to the queue.", True),
        (3, False, "Found 3 videos to add to the queue.", False),
        (0, True, None, False),
    ],
)
def test_update_subscribed(
    redis_ns, fake, no_broker, added, auto_start, expected, queued
):
    # pylint: disable=unused-argument,too-many-arguments
    scanner = fake(
        "SubscriptionScanner",
        returns={"scan": added},
        attrs={"auto_start": auto_start},
    )

    assert run(tasks.update_subscribed) == expected
    assert ("scan", (), {}) in scanner.calls
    assert bool(no_broker) is queued
    if queued:
        assert no_broker[0] == ("download_pending", (), {"auto_only": True})


@pytest.mark.parametrize(
    "downloaded, emby, expected, sent",
    [
        (2, True, "downloaded 2 video(s).", ["emby_sync"]),
        (2, False, "downloaded 2 video(s).", []),
        (0, True, None, []),
    ],
)
def test_download_pending(
    redis_ns, fake, emby_paths, no_broker, downloaded, emby, expected, sent
):
    # pylint: disable=unused-argument,too-many-arguments
    downloader = fake(
        "VideoDownloader", returns={"run_queue": (downloaded, 0)}
    )
    emby_paths(emby)

    assert run(tasks.download_pending, auto_only=True) == expected
    assert ("run_queue", (), {"auto_only": True}) in downloader.calls
    assert [i[0] for i in no_broker] == sent


def test_download_pending_retry(redis_ns, fake):
    fake("VideoDownloader", returns={"run_queue": (0, 1)})

    with pytest.raises(Retry):
        run(tasks.download_pending)

    assert {"messages": ["Videos failed, retry."]}.items() <= next(
        i for i in messages(redis_ns) if "retry" in i["messages"][0]
    ).items()


def test_download_pending_failed_but_stopped(redis_ns, fake, monkeypatch):
    # pylint: disable=unused-argument
    fake("VideoDownloader", returns={"run_queue": (1, 1)})
    monkeypatch.setattr(tasks.BaseTask, "is_stopped", lambda self: True)
    monkeypatch.setattr(tasks, "paths_from_env", lambda: None)

    assert run(tasks.download_pending) == "downloaded 1 video(s)."


@pytest.mark.parametrize(
    "youtube_ids, auto_start, added, expected",
    [
        ("abc", True, 2, "added 2 Videos to Queue"),
        ([{"type": "video", "url": "abc"}], False, 0, None),
    ],
)
def test_extract_download(
    redis_ns, fake, no_broker, youtube_ids, auto_start, added, expected
):
    # pylint: disable=unused-argument,too-many-arguments
    parsed = [{"type": "video", "url": "parsed"}]
    parser = fake("Parser", returns={"parse": parsed})
    pending = fake("PendingList", returns={"parse_url_list": added})

    result = run(
        tasks.extrac_dl, youtube_ids, auto_start=auto_start, status="pending"
    )

    assert result == expected
    init = pending.calls[0]
    if isinstance(youtube_ids, str):
        assert parser.calls[0][1] == ("abc",)
        assert init[2]["youtube_ids"] == parsed
    else:
        assert not parser.calls
        assert init[2]["youtube_ids"] == youtube_ids
    assert ("parse_url_list", (), {"status": "pending"}) in pending.calls
    assert bool(no_broker) is auto_start


def test_check_reindex_scheduled(redis_ns, fake, emby_paths, no_broker):
    # pylint: disable=unused-argument
    populate = fake("ReindexPopulate")
    reindex = fake("Reindex", returns={"build_message": "reindexed"})
    emby_paths(True)

    assert run(tasks.check_reindex) == "reindexed"
    assert [i[0] for i in populate.calls] == [
        "init",
        "get_interval",
        "add_recent",
        "add_outdated",
    ]
    assert ("reindex_all", (), {}) in reindex.calls
    assert [i[0] for i in no_broker] == ["emby_sync"]


def test_check_reindex_manual(redis_ns, fake, emby_paths):
    # pylint: disable=unused-argument
    manual = fake("ReindexManual")
    populate = fake("ReindexPopulate")
    fake("Reindex", returns={"build_message": "done"})
    emby_paths(False)
    data = {"video": ["abc"]}

    assert run(tasks.check_reindex, data=data, extract_videos=True) == "done"
    assert manual.calls == [
        ("init", (), {"extract_videos": True}),
        ("extract_data", (data,), {}),
    ]
    assert not populate.calls


def test_check_reindex_manual_while_running(redis_ns, fake):
    manual = fake("ReindexManual")
    reindex = fake("Reindex")
    make_pending(tasks.check_reindex)

    assert run(tasks.check_reindex, data={"video": ["abc"]}) is None
    assert manual.calls[-1][0] == "extract_data"
    assert not reindex.calls
    assert len(messages(redis_ns)) == 1


def test_manual_import(redis_ns, fake):
    # pylint: disable=unused-argument
    scanner = fake("ImportFolderScanner")

    run(tasks.manual_import, True, False)

    assert scanner.calls[0][2]["ignore_error"] is True
    assert scanner.calls[0][2]["prefer_local"] is False
    assert scanner.calls[1][0] == "scan"


def test_run_backup(redis_ns, fake):
    # pylint: disable=unused-argument
    backup = fake("DatabaseBackup")

    run(tasks.run_backup, reason="manual")

    assert backup.calls[0][2]["reason"] == "manual"
    assert backup.calls[1][0] == "backup_all_indexes"


def test_restore_backup(redis_ns, fake):
    # pylint: disable=unused-argument
    backup = fake("DatabaseBackup")

    result = run(tasks.run_restore_backup, "ta_backup.zip")

    assert result == "backup restore completed: ta_backup.zip"
    assert backup.calls[1] == ("restore", ("ta_backup.zip",), {})


def test_rescan_filesystem(redis_ns, fake, no_broker):
    # pylint: disable=unused-argument
    scanner = fake("Scanner")

    run(tasks.rescan_filesystem, False, True)

    assert [i[0] for i in scanner.calls] == ["init", "scan", "apply"]
    assert no_broker[0][0] == "thumbnail_check"


def test_thumbnail_check(redis_ns, fake):
    # pylint: disable=unused-argument
    validator = fake("ThumbValidator")

    run(tasks.thumbnail_check)

    assert [i[0] for i in validator.calls] == ["init", "validate", "clean_up"]


def test_resync_metadata(redis_ns, fake):
    # pylint: disable=unused-argument
    embed = fake("MetadataEmbed")

    run(tasks.re_sync_metadata)

    assert [i[0] for i in embed.calls] == ["init", "embed"]


def test_subscribe_to(redis_ns, fake):
    # pylint: disable=unused-argument
    handler = fake("SubscriptionHandler")

    run(tasks.subscribe_to, "https://youtube.com/@x", expected_type="channel")

    assert handler.calls[0][1] == ("https://youtube.com/@x",)
    assert handler.calls[1] == ("subscribe", ("channel",), {})


def test_index_playlists(redis_ns, fake):
    # pylint: disable=unused-argument
    channel = fake("YoutubeChannel")

    run(tasks.index_channel_playlists, "UC123")

    assert channel.calls[0][1] == ("UC123",)
    assert channel.calls[1][0] == "index_channel_playlists"


def test_emby_sync_disabled(emby_paths):
    emby_paths(False)

    assert run(tasks.emby_sync) == "Emby sync is disabled, set TA_EMBY_ROOT"


def test_emby_sync(redis_ns, emby_paths, monkeypatch, capsys):
    # pylint: disable=unused-argument
    emby_paths(True)
    report = SimpleNamespace(
        created=[1, 2],
        updated=[1],
        renamed=[],
        deleted=[1],
        warnings=[f"warning {i}" for i in range(25)],
    )
    synced = []
    monkeypatch.setattr(
        tasks, "sync_all", lambda paths: synced.append(paths) or report
    )

    result = run(tasks.emby_sync)

    assert result == "Emby: 2 created, 1 updated, 0 renamed, 1 removed"
    assert synced[0].root == "emby"
    output = capsys.readouterr().out
    assert "[emby] warning 19" in output
    assert "[emby] warning 20" not in output


def test_db_snapshot(fake):
    snapshot = fake("DbSnapshot")

    tasks.db_snapshot.run()

    assert snapshot.calls[1][0] == "take_snapshot_now"


def test_version_check(fake):
    release = fake("ReleaseVersion")

    tasks.version_check.run()

    assert release.calls[1][0] == "check"

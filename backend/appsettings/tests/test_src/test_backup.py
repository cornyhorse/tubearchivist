"""tests for zip backups: real round trip through the database"""

import json
import os
import zipfile
from datetime import datetime, timezone

import pytest
from appsettings.models import ConfigDocument
from appsettings.src import backup as backup_module
from appsettings.src.backup import DatabaseBackup, iter_documents, wipe_data
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django_celery_beat.models import CrontabSchedule
from download.models import Download
from playlist.models import Playlist, PlaylistEntry
from storage.importer import ImportReport
from task.models import CustomPeriodicTask
from user.models import Account, UserConfig
from video.models import Comment, CommentRefresh, SubtitleLine, Video

pytestmark = pytest.mark.django_db

MODELS = [
    ConfigDocument,
    UserConfig,
    Channel,
    Video,
    Playlist,
    PlaylistEntry,
    Download,
    SubtitleLine,
    Comment,
    CommentRefresh,
]
NOT_COMPARED = {"id", "updated", "search"}


@pytest.fixture(name="backup_dir")
def fixture_backup_dir(tmp_path, monkeypatch):
    """point DatabaseBackup at a scratch backup dir"""
    backup_dir = tmp_path / "backup"
    monkeypatch.setattr(DatabaseBackup, "BACKUP_DIR", str(backup_dir))
    return backup_dir


@pytest.fixture(name="library")
def fixture_library():
    """one of everything a backup holds"""
    user = Account.objects.create_user("viewer", "pw")
    UserConfig.objects.create(user=user, config={"page_size": 25})
    ConfigDocument.objects.create(key="appsettings", value={"a": {"b": 1}})
    ConfigDocument.objects.create(key="notify", value={"urls": ["x://y"]})
    channel = channel_doc(channel_overwrites={"autodelete_days": 3})
    Channel.save_doc(channel)
    for n in range(3):
        Video.save_doc(
            video_doc(
                channel=channel,
                youtube_id=f"vid0000000{n}",
                published=1700000000 + n,
                subtitles=[{"lang": "en", "source": "auto"}],
            )
        )
    doc = playlist_doc("PL1", ["vid00000001", "notdownload", "vid00000000"])
    Playlist.save_doc(doc)
    Download.objects.create(**Download.fields_from_doc(download_doc()))
    Download.objects.create(
        **Download.fields_from_doc(
            download_doc(youtube_id="dl000000002", status="ignore")
        )
    )
    for idx in range(2):
        SubtitleLine.objects.create(
            **SubtitleLine.fields_from_doc(
                subtitle_doc("vid00000001", idx, f"line {idx}")
            )
        )
    CommentRefresh.objects.create(
        video_id="vid00000002",
        last_refresh=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    for position in range(2):
        Comment.objects.create(
            video_id="vid00000002",
            **Comment.fields_from_doc(
                {
                    "comment_id": f"c{position}",
                    "comment_text": f"nice {position}",
                    "comment_timestamp": 1700000000,
                },
                position,
            ),
        )


def table_state() -> dict[str, list[dict]]:
    """every row of every backed up table, minus surrogate keys"""
    state = {}
    for model in MODELS:
        rows = []
        for row in model.objects.order_by("pk").values():
            rows.append(
                {k: v for k, v in row.items() if k not in NOT_COMPARED}
            )
        state[model.__name__] = sorted(
            rows, key=lambda row: json.dumps(row, sort_keys=True, default=str)
        )
    return state


def test_round_trip(library, backup_dir, task):
    # pylint: disable=unused-argument
    before = table_state()
    handler = DatabaseBackup(reason="manual", task=task)

    handler.backup_all_indexes()

    zips = handler.get_all_backup_files()
    assert [z["reason"] for z in zips] == ["manual"]
    assert sorted(os.listdir(backup_dir)) == [zips[0]["filename"]]
    with zipfile.ZipFile(zips[0]["file_path"]) as zip_f:
        names = sorted(zip_f.namelist())
    assert len(names) == 7 and all(n.startswith("es_") for n in names)
    assert "Scanning your index." in task.lines
    assert "Compress files to zip archive." in task.lines

    wipe_data()
    assert not any(table_state().values())

    report = DatabaseBackup(task=task).restore(zips[0]["filename"])

    assert report.ok, report.lines()
    assert table_state() == before
    assert task.messages[-1] == (["Restore completed."], 1, "info")


def test_round_trip_keeps_fork_fields(backup_dir):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    Channel.objects.update(emby_numbering="absolute_frozen")
    handler = DatabaseBackup(reason="manual")
    handler.backup_all_indexes()
    filename = handler.get_all_backup_files()[0]["filename"]

    handler.restore(filename)

    assert Channel.objects.get().emby_numbering == "absolute_frozen"


def test_round_trip_keeps_rules_and_episode_numbers(backup_dir):
    # pylint: disable=unused-argument,import-outside-toplevel
    from datetime import date

    from download.models import DownloadRule
    from emby.models import EpisodeNumber

    channel = Channel.save_doc(channel_doc())
    DownloadRule.objects.create(
        channel=channel, min_duration=60, published_after=date(2025, 1, 2)
    )
    DownloadRule.objects.create(channel=None, vid_types=["videos"])
    EpisodeNumber.objects.create(
        series_kind="channel",
        series_id=channel.pk,
        scheme="year_frozen",
        youtube_id="v1",
        season=2025,
        episode=7,
    )
    handler = DatabaseBackup(reason="manual")
    handler.backup_all_indexes()
    filename = handler.get_all_backup_files()[0]["filename"]

    handler.restore(filename)

    rule = DownloadRule.objects.get(channel_id=channel.pk)
    assert (rule.min_duration, rule.published_after) == (60, date(2025, 1, 2))
    assert DownloadRule.objects.get(channel=None).vid_types == ["videos"]
    number = EpisodeNumber.objects.get()
    assert (number.season, number.episode) == (2025, 7)


def test_restore_failed_verification_rolls_back(
    library, backup_dir, monkeypatch
):
    # pylint: disable=unused-argument
    before = table_state()
    handler = DatabaseBackup(reason="manual")
    handler.backup_all_indexes()
    filename = handler.get_all_backup_files()[0]["filename"]
    monkeypatch.setattr(
        "storage.importer.EsImporter.run",
        lambda self: ImportReport(mismatches=["ta_video: 1 rows != 3"]),
    )

    with pytest.raises(ValueError, match="ta_video: 1 rows != 3"):
        handler.restore(filename)

    assert table_state() == before


def test_iter_documents_unknown_index():
    with pytest.raises(ValueError, match="unknown index"):
        list(iter_documents("nope"))


def test_iter_documents_config(library):
    # pylint: disable=unused-argument
    docs = dict(iter_documents("config"))

    user_id = Account.objects.get().id
    assert docs["appsettings"] == {"a": {"b": 1}}
    assert docs[f"user_{user_id}"] == {"config": {"page_size": 25}}


def test_iter_documents_comment(library):
    # pylint: disable=unused-argument
    (doc_id, doc), *rest = list(iter_documents("comment"))

    assert not rest
    assert doc_id == "vid00000002"
    assert [c["comment_id"] for c in doc["comment_comments"]] == ["c0", "c1"]


def test_backup_requires_reason(backup_dir):
    with pytest.raises(ValueError, match="missing backup reason"):
        DatabaseBackup().backup_all_indexes()
    assert not backup_dir.exists()


def test_backup_index_chunks(library, backup_dir, monkeypatch, task):
    # pylint: disable=unused-argument
    monkeypatch.setattr(backup_module, "CHUNK_SIZE", 2)
    backup_dir.mkdir()
    handler = DatabaseBackup(reason="manual", task=task)

    handler.backup_index("video")

    files = sorted(os.listdir(backup_dir))
    stamp = handler.timestamp
    assert files == [f"es_video-{stamp}-0.json", f"es_video-{stamp}-1.json"]
    lines = (backup_dir / files[0]).read_text().splitlines()
    assert json.loads(lines[0]) == {
        "index": {"_index": "ta_video", "_id": "vid00000000"}
    }
    assert json.loads(lines[1])["youtube_id"] == "vid00000000"
    assert len(lines) == 4
    assert task.lines == ["Processing Videos", "Processing Videos"]


def test_backup_empty_index_writes_nothing(backup_dir):
    backup_dir.mkdir()

    DatabaseBackup(reason="manual").backup_index("channel")

    assert not os.listdir(backup_dir)


def test_get_all_backup_files_without_dir(backup_dir):
    # pylint: disable=unused-argument
    assert not DatabaseBackup().get_all_backup_files()


def test_get_all_backup_files_filters_and_sorts(backup_dir):
    backup_dir.mkdir()
    for name in [
        "ta_backup-20260101-auto.zip",
        "ta_backup-20260301-manual.zip",
        "ta_backup-20260201.zip",
        ".ta_backup-hidden.zip",
        "other.zip",
        "ta_backup-20260401-auto.json",
    ]:
        (backup_dir / name).write_bytes(b"zip")

    files = DatabaseBackup().get_all_backup_files()

    assert [(f["timestamp"], f["reason"]) for f in files] == [
        ("20260301", "manual"),
        ("20260201", False),
        ("20260101", "auto"),
    ]
    assert files[0]["file_size"] == 3
    assert files[0]["file_path"] == str(
        backup_dir / "ta_backup-20260301-manual.zip"
    )


def test_build_backup_file_data_missing(backup_dir):
    # pylint: disable=unused-argument
    assert DatabaseBackup().build_backup_file_data("ta_x-1-auto.zip") is False


def test_build_backup_file_data_bad_name(backup_dir):
    backup_dir.mkdir()
    (backup_dir / "ta_a-b-c-d.zip").write_bytes(b"")

    with pytest.raises(ValueError):
        DatabaseBackup().build_backup_file_data("ta_a-b-c-d.zip")


def test_zip_to_export(tmp_path):
    zip_path = tmp_path / "backup.zip"
    action = {"index": {"_index": "ta_video_v2", "_id": "v1"}}
    with zipfile.ZipFile(zip_path, "w") as zip_f:
        zip_f.writestr(
            "es_video-1-0.json",
            json.dumps(action) + "\n\n" + json.dumps({"title": "t"}) + "\n",
        )
        zip_f.writestr("readme.txt", "ignored")
        zip_f.writestr("es_notes.md", "ignored")
    export_dir = tmp_path / "export"
    export_dir.mkdir()

    counts = DatabaseBackup.zip_to_export(str(zip_path), export_dir)

    assert counts == {"ta_video": 1}
    assert json.loads((export_dir / "manifest.json").read_text()) == counts
    hit = json.loads((export_dir / "ta_video.ndjson").read_text())
    assert hit == {"_id": "v1", "_source": {"title": "t"}}


@pytest.mark.parametrize(
    "index", ["../../x", "ta_video/../../x", "/tmp/x", "ta_unknown"]
)
def test_zip_to_export_rejects_unknown_index(tmp_path, index):
    zip_path = tmp_path / "backup.zip"
    action = {"index": {"_index": index, "_id": "v1"}}
    with zipfile.ZipFile(zip_path, "w") as zip_f:
        zip_f.writestr(
            "es_video-1-0.json",
            json.dumps(action) + "\n" + json.dumps({"title": "t"}) + "\n",
        )
    export_dir = tmp_path / "a" / "b" / "export"
    export_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="unknown index"):
        DatabaseBackup.zip_to_export(str(zip_path), export_dir)

    assert not list(export_dir.iterdir())
    assert not list(tmp_path.glob("**/x*"))


def test_restore_rejects_path_in_index(library, backup_dir):
    # pylint: disable=unused-argument
    before = table_state()
    backup_dir.mkdir()
    action = {"index": {"_index": "../../x", "_id": "v1"}}
    zip_path = backup_dir / "ta_backup-20260101-manual.zip"
    with zipfile.ZipFile(zip_path, "w") as zip_f:
        zip_f.writestr(
            "es_video-1-0.json",
            json.dumps(action) + "\n" + json.dumps({"title": "t"}) + "\n",
        )

    with pytest.raises(ValueError, match="unknown index"):
        DatabaseBackup().restore("ta_backup-20260101-manual.zip")

    assert table_state() == before


def make_auto_backups(backup_dir, count):
    """fake auto backup zips, oldest first"""
    backup_dir.mkdir(exist_ok=True)
    names = [f"ta_backup-2026010{n}-auto.zip" for n in range(1, count + 1)]
    for name in names:
        (backup_dir / name).write_bytes(b"zip")
    (backup_dir / "ta_backup-20250101-manual.zip").write_bytes(b"zip")
    return names


def rotate_task(rotate):
    """the run_backup periodic task with a rotate config"""
    crontab = CrontabSchedule.objects.create(minute="0", hour="1")
    CustomPeriodicTask.objects.create(
        name="run_backup",
        task="run_backup",
        crontab=crontab,
        task_config={"rotate": rotate} if rotate is not None else {},
    )


def test_rotate_without_task(backup_dir):
    names = make_auto_backups(backup_dir, 3)

    DatabaseBackup(reason="auto").rotate_backup()

    assert set(names) <= set(os.listdir(backup_dir))


@pytest.mark.parametrize("rotate", [None, 0, 5])
def test_rotate_keeps_all(backup_dir, rotate):
    names = make_auto_backups(backup_dir, 3)
    rotate_task(rotate)

    DatabaseBackup(reason="auto").rotate_backup()

    assert set(names) <= set(os.listdir(backup_dir))


def test_auto_backup_rotates(backup_dir, library):
    # pylint: disable=unused-argument
    names = make_auto_backups(backup_dir, 3)
    rotate_task(2)
    handler = DatabaseBackup(reason="auto")
    handler.timestamp = "20260109"

    handler.backup_all_indexes()

    assert sorted(os.listdir(backup_dir)) == [
        "ta_backup-20250101-manual.zip",
        names[-1],
        "ta_backup-20260109-auto.zip",
    ]


def test_delete_file(backup_dir):
    backup_dir.mkdir()
    (backup_dir / "ta_backup-20260101-auto.zip").write_bytes(b"zip")
    handler = DatabaseBackup()

    deleted = handler.delete_file("ta_backup-20260101-auto.zip")

    assert deleted == str(backup_dir / "ta_backup-20260101-auto.zip")
    assert not os.listdir(backup_dir)
    assert handler.delete_file("ta_backup-20260101-auto.zip") is False


def test_video_documents_load_playlists_per_chunk(monkeypatch):
    """one PlaylistEntry query per chunk, not per video"""
    monkeypatch.setattr(backup_module, "CHUNK_SIZE", 2)
    Channel.save_doc(channel_doc())
    for i in range(5):
        Video.save_doc(video_doc(youtube_id=f"vid0000000{i}"))
    Playlist.save_doc(playlist_doc("PL1", ["vid00000003"]))

    with CaptureQueriesContext(connection) as context:
        docs = dict(iter_documents("video"))

    assert sorted(docs) == [f"vid0000000{i}" for i in range(5)]
    assert docs["vid00000003"]["playlist"] == ["PL1"]
    entry_queries = [
        q for q in context.captured_queries if "playlistentry" in q["sql"]
    ]
    assert len(entry_queries) == 3

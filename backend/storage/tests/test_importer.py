"""tests for the ES export -> Postgres importer"""

import json

import pytest
from appsettings.models import ConfigDocument
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    subtitle_doc,
    video_doc,
)
from download.models import Download
from playlist.models import Playlist
from storage.importer import EsImporter, ImportReport
from user.models import Account, UserConfig
from video.models import Comment, CommentRefresh, SubtitleLine, Video

pytestmark = pytest.mark.django_db

ORPHAN_CHANNEL = channel_doc(
    channel_id="UCorphan0000000000000001", channel_name="Orphan"
)


def write_export(path, indexes: dict[str, list[tuple[str, dict]]]):
    """write ndjson export like storage.es_export does"""
    manifest = {}
    for index, docs in indexes.items():
        with open(path / f"{index}.ndjson", "w", encoding="utf-8") as f:
            for _id, source in docs:
                f.write(json.dumps({"_id": _id, "_source": source}) + "\n")
        manifest[index] = len(docs)
    (path / "manifest.json").write_text(json.dumps(manifest))
    return path


@pytest.fixture(name="export_dir")
def fixture_export_dir(tmp_path):
    """small export covering every index and the edge cases"""
    videos = [
        video_doc(youtube_id="vid00000001", playlist=["PL1"]),
        video_doc(youtube_id="vid00000002", published=1700000100),
        video_doc(channel=ORPHAN_CHANNEL, youtube_id="vid00000003"),
    ]
    return write_export(
        tmp_path,
        {
            "ta_config": [
                ("appsettings", {"downloads": {"subtitle": "en"}}),
                ("user_1", {"config": {"page_size": 25}}),
            ],
            "ta_channel": [(channel_doc()["channel_id"], channel_doc())],
            "ta_video": [(v["youtube_id"], v) for v in videos],
            "ta_playlist": [
                ("PL1", playlist_doc("PL1", ["vid00000001", "notdownloaded"]))
            ],
            "ta_download": [
                ("dl1", download_doc(youtube_id="dl1")),
                ("dl2", download_doc(youtube_id="dl2", status="ignore")),
            ],
            "ta_comment": [],
            "ta_subtitle": [
                ("s1", subtitle_doc("vid00000001", 0, "hello coaster")),
                ("s2", subtitle_doc("gone0000000", 0, "orphan line")),
            ],
        },
    )


def test_import_counts_and_verification(export_dir):
    report = EsImporter(export_dir).run()

    assert report.ok, report.lines()
    assert Channel.objects.count() == 2
    assert Video.objects.count() == 3
    assert Playlist.objects.get().entries.count() == 2
    assert Download.objects.filter(status="pending").count() == 1
    assert SubtitleLine.objects.count() == 1
    assert ConfigDocument.objects.get(key="appsettings").value == {
        "downloads": {"subtitle": "en"}
    }


def test_import_reports_skips(export_dir):
    report = EsImporter(export_dir).run()

    assert any("rebuilt from video" in r for r in report.skipped["ta_channel"])
    assert report.skipped["ta_subtitle"] == ["s2: video not indexed"]
    assert report.skipped["ta_config"] == ["user_1: user does not exist yet"]


def test_import_is_idempotent(export_dir):
    EsImporter(export_dir).run()
    Video.objects.filter(pk="vid00000001").update(title="changed locally")

    report = EsImporter(export_dir).run()

    assert report.ok, report.lines()
    assert Video.objects.count() == 3
    assert Video.objects.get(pk="vid00000001").title == "Riding the Big Coaster"


def test_import_round_trips_video_doc(export_dir):
    EsImporter(export_dir).run()
    video = Video.objects.select_related("channel").get(pk="vid00000001")

    doc = video.to_doc()

    assert doc["playlist"] == ["PL1"]
    assert doc["player"]["duration_str"] == "7m 38s"
    assert doc["channel"]["channel_name"] == "Test Channel"
    assert video.height == 1080 and video.width == 1920


def test_manifest_mismatch_fails(export_dir):
    manifest = json.loads((export_dir / "manifest.json").read_text())
    manifest["ta_video"] = 4
    (export_dir / "manifest.json").write_text(json.dumps(manifest))

    report = EsImporter(export_dir).run()

    assert not report.ok
    assert any("ta_video" in m for m in report.mismatches)


COMMENTS_DOC = {
    "youtube_id": "vid00000001",
    "comment_channel_id": "UCchannel000000000000001",
    "comment_last_refresh": 1785000000,
    "comment_comments": [
        {"comment_id": "c1", "comment_parent": "root", "comment_text": "a"},
        {"comment_id": "c2", "comment_parent": "c1", "comment_text": "b"},
    ],
}


@pytest.fixture(name="full_export")
def fixture_full_export(tmp_path):
    """export with comments, a channel without videos, a channelless video"""
    videos = [video_doc(youtube_id="vid00000001", playlist=["PL1"])]
    broken = video_doc(youtube_id="nochannel01")
    broken["channel"] = {}
    return write_export(
        tmp_path,
        {
            "ta_config": [("user_1", {"config": {"page_size": 50}})],
            "ta_channel": [
                (channel_doc()["channel_id"], channel_doc()),
                ("UCempty", channel_doc(channel_id="UCempty")),
            ],
            "ta_video": [(v["youtube_id"], v) for v in videos]
            + [("nochannel01", broken)],
            "ta_playlist": [("PL1", playlist_doc("PL1", ["vid00000001"]))],
            "ta_download": [("dl1", download_doc(youtube_id="dl1"))],
            "ta_comment": [
                ("vid00000001", COMMENTS_DOC),
                ("gone0000001", {**COMMENTS_DOC, "youtube_id": "gone0000001"}),
            ],
        },
    )


def test_import_comments_users_and_missing_files(full_export):
    Account.objects.create_user("admin", "secret-for-tests")
    user_id = Account.objects.get().id
    lines = (full_export / "ta_config.ndjson").read_text()
    (full_export / "ta_config.ndjson").write_text(
        lines.replace("user_1", f"user_{user_id}")
    )

    report = EsImporter(full_export).run()

    assert report.ok, report.lines()
    assert report.counts["comment"] == 2
    assert [c.parent for c in Comment.objects.order_by("position")] == ["root", "c1"]
    assert CommentRefresh.objects.get().video_id == "vid00000001"
    assert report.skipped["ta_video"] == ["nochannel01: no channel"]
    assert report.skipped["ta_comment"] == ["gone0000001: video not indexed"]
    assert UserConfig.objects.get().config == {"page_size": 50}
    assert SubtitleLine.objects.count() == 0  # no ta_subtitle file at all


def test_report_lines(full_export):
    report = EsImporter(full_export).run()

    lines = report.lines()

    assert "video: 1" in lines
    assert "ta_video: skipped 1" in lines
    assert "  - nochannel01: no channel" in lines
    assert lines[-1] == "OK"


def test_verify_reports_every_mismatch(full_export):
    importer = EsImporter(full_export)
    importer.run()
    Video.objects.filter(pk="vid00000001").update(title="edited", media_size=1)
    Download.objects.update(status="ignore")
    Playlist.objects.get().entries.all().delete()
    Channel.objects.filter(pk="UCempty").delete()
    Comment.objects.all().delete()

    importer.report = ImportReport()
    importer.verify()

    text = "\n".join(importer.report.lines())
    assert "ta_channel: 1 rows < 2 docs" in text
    assert "video media_size sum 1 != 1000" in text
    assert "video vid00000001: title 'edited'" in text
    assert "pending downloads 0 != 1" in text
    assert "playlist PL1: entries differ" in text
    assert text.endswith("FAILED")


def test_verify_reports_per_channel_counts(full_export):
    importer = EsImporter(full_export)
    importer.run()
    other = Channel.objects.get(pk="UCempty")
    Video.objects.update(channel=other)

    importer.report = ImportReport()
    importer.verify()

    assert any("videos != 1" in m for m in importer.report.mismatches)


def test_verify_reports_missing_video(full_export):
    importer = EsImporter(full_export)
    importer.run()
    Video.objects.all().delete()

    importer.report = ImportReport()
    importer.verify()

    assert "video vid00000001: missing" in importer.report.mismatches

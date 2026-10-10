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
from storage.importer import EsImporter
from video.models import SubtitleLine, Video

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

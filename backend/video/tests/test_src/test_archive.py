"""tests for archived video ids and sidecar reading, no database"""

import json
from datetime import datetime, timezone

import pytest
from video.src import archive


def test_is_archive_id():
    assert archive.is_archive_id("arch_0123456789abcdef")
    assert not archive.is_archive_id("dQw4w9WgXcQ")
    assert not archive.is_archive_id("")
    assert not archive.is_archive_id(None)


def test_channel_id_is_stable_and_safe():
    assert archive.archive_channel_id("Tyler Jenkins Music") == (
        "arch_tyler-jenkins-music"
    )
    assert archive.archive_channel_id("Tyler  Jenkins Music!") == (
        "arch_tyler-jenkins-music"
    )
    long = archive.archive_channel_id("x" * 200)
    assert len(long) == len("arch_") + 40


def test_channel_id_without_letters_uses_a_hash():
    first = archive.archive_channel_id("日本語")
    assert first == archive.archive_channel_id("日本語")
    assert first != archive.archive_channel_id("中文")
    assert len(first) == len("arch_") + 12


def test_video_id_depends_on_channel_and_name_only():
    one = archive.archive_video_id("arch_a", "clip.mkv")
    assert one == archive.archive_video_id("arch_a", "clip.mkv")
    assert one != archive.archive_video_id("arch_b", "clip.mkv")
    assert one != archive.archive_video_id("arch_a", "clip.mp4")
    assert archive.is_archive_id(one) and len(one) == len("arch_") + 16


def test_video_id_ignores_unicode_form():
    composed = "café.mp4"
    decomposed = "café.mp4"
    assert archive.archive_video_id("arch_a", composed) == (
        archive.archive_video_id("arch_a", decomposed)
    )


def test_find_media(tmp_path):
    for rel in [
        "b.mkv",
        "a.MP4",
        "sub/c.webm",
        "notes.txt",
        "a.jpg",
        ".hidden.mp4",
        ".trash/d.mp4",
        "@eaDir/e.mp4",
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    (tmp_path / "folder.mp4").mkdir()

    found = archive.find_media(tmp_path)

    assert [p.relative_to(tmp_path).as_posix() for p in found] == [
        "a.MP4",
        "b.mkv",
        "sub/c.webm",
    ]


@pytest.mark.parametrize(
    "image", ["clip.jpg", "clip.png", "clip-thumb.jpg", "clip.thumb.webp"]
)
def test_find_thumbnail(tmp_path, image):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    assert archive.find_thumbnail(media) is None
    (tmp_path / image).write_bytes(b"x")
    assert archive.find_thumbnail(media) == tmp_path / image


NFO = """<?xml version="1.0" encoding="utf-8"?>
<episodedetails>
  <title>A Title</title>
  <plot>The story.</plot>
  <aired>2015-03-04</aired>
  <genre>Music</genre>
  <tag>skyrim</tag>
  <tag>Music</tag>
  <tag> </tag>
</episodedetails>"""


def test_read_nfo(tmp_path):
    path = tmp_path / "a.nfo"
    path.write_text(NFO, encoding="utf-8")

    assert archive.read_nfo(path) == {
        "title": "A Title",
        "description": "The story.",
        "published": datetime(2015, 3, 4, tzinfo=timezone.utc),
        "tags": ["Music", "skyrim"],
    }


def test_read_nfo_other_date_fields(tmp_path):
    path = tmp_path / "a.nfo"
    path.write_text(
        "<movie><originaltitle>O</originaltitle><outline>Out</outline>"
        "<premiered>20190101</premiered></movie>"
    )
    found = archive.read_nfo(path)
    assert found["title"] == "O" and found["description"] == "Out"
    assert found["published"] == datetime(2019, 1, 1, tzinfo=timezone.utc)

    path.write_text("<movie><year>2012</year></movie>")
    assert archive.read_nfo(path)["published"] == datetime(
        2012, 1, 1, tzinfo=timezone.utc
    )

    path.write_text("<movie><aired>2012-13-45</aired></movie>")
    assert archive.read_nfo(path)["published"] is None

    path.write_text("<movie><aired>sometime</aired></movie>")
    assert archive.read_nfo(path)["published"] is None


def test_read_nfo_unusable(tmp_path):
    assert archive.read_nfo(tmp_path / "missing.nfo") == {}
    bad = tmp_path / "bad.nfo"
    bad.write_text("<episodedetails><title>x")
    assert archive.read_nfo(bad) == {}


def test_read_info_json(tmp_path):
    path = tmp_path / "a.info.json"
    path.write_text(
        json.dumps(
            {
                "title": "T",
                "description": "D",
                "timestamp": 1700000000,
                "tags": ["a", 2],
            }
        )
    )
    assert archive.read_info_json(path) == {
        "title": "T",
        "description": "D",
        "published": datetime.fromtimestamp(1700000000, tz=timezone.utc),
        "tags": ["a", "2"],
    }

    path.write_text(json.dumps({"upload_date": "20200229", "tags": "x"}))
    found = archive.read_info_json(path)
    assert found["published"] == datetime(2020, 2, 29, tzinfo=timezone.utc)
    assert found["title"] == "" and found["tags"] == []


def test_read_info_json_unusable(tmp_path):
    assert archive.read_info_json(tmp_path / "missing.json") == {}
    path = tmp_path / "a.info.json"
    path.write_text("not json")
    assert archive.read_info_json(path) == {}
    path.write_text("[1, 2]")
    assert archive.read_info_json(path) == {}


def test_read_sidecar_nfo_wins_field_by_field(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    (tmp_path / "a.nfo").write_text("<e><title>From NFO</title></e>")
    (tmp_path / "a.info.json").write_text(
        json.dumps({"title": "From JSON", "description": "json plot"})
    )

    side = archive.read_sidecar(media)

    assert side["title"] == "From NFO"
    assert side["description"] == "json plot"
    assert side["published"] is None and side["tags"] == []


def test_read_sidecar_without_sidecars(tmp_path):
    media = tmp_path / "a.mp4"
    assert archive.read_sidecar(media) == {
        "title": "",
        "description": "",
        "published": None,
        "tags": [],
    }

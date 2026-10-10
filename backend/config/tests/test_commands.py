"""tests for the fork's management commands"""

import io
import json

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from common.tests.docs import channel_doc, playlist_doc, video_doc
from django.core.management import CommandError, call_command
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db
CHANNEL_ID = channel_doc()["channel_id"]


def run(*args) -> str:
    """call a command, return its stdout"""
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def write_export(path, videos):
    """minimal export dir"""
    (path / "ta_video.ndjson").write_text(
        "".join(
            json.dumps({"_id": v["youtube_id"], "_source": v}) + "\n"
            for v in videos
        )
    )
    (path / "ta_channel.ndjson").write_text(
        json.dumps({"_id": CHANNEL_ID, "_source": channel_doc()}) + "\n"
    )
    manifest = {"ta_video": len(videos), "ta_channel": 1}
    (path / "manifest.json").write_text(json.dumps(manifest))


def test_import_es(tmp_path):
    write_export(tmp_path, [video_doc()])

    output = run("ta_import_es", str(tmp_path))

    assert "video: 1" in output and output.strip().endswith("OK")
    assert Video.objects.count() == 1


def test_import_es_needs_manifest(tmp_path):
    with pytest.raises(CommandError, match="no manifest.json"):
        run("ta_import_es", str(tmp_path))


def test_import_es_fails_on_mismatch(tmp_path):
    write_export(tmp_path, [video_doc()])
    (tmp_path / "manifest.json").write_text('{"ta_video": 2, "ta_channel": 1}')

    with pytest.raises(CommandError, match="verification failed"):
        run("ta_import_es", str(tmp_path))


@pytest.fixture(name="library")
def fixture_library(tmp_path, monkeypatch):
    """one channel, one video with media, one playlist; env pointed at tmp"""
    media, cache = tmp_path / "media", tmp_path / "cache"
    monkeypatch.setattr(EnvironmentSettings, "MEDIA_DIR", str(media))
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(EnvironmentSettings, "TZ", "UTC")
    Channel.objects.create(**Channel.fields_from_doc(channel_doc()))
    video = Video.from_doc(video_doc())
    video.save()
    (media / CHANNEL_ID).mkdir(parents=True)
    (media / video.media_url).write_bytes(b"v")
    doc = playlist_doc("PL1", [video.youtube_id])
    Playlist.objects.create(**Playlist.fields_from_doc(doc))
    for entry in Playlist.entries_from_doc(doc):
        entry.save()
    return tmp_path


def test_emby_sync_modes(library):
    out = library / "emby"

    channel = run("ta_emby_sync", str(out), "--channel", CHANNEL_ID)
    playlist = run("ta_emby_sync", str(out), "--playlist", "PL1")
    everything = run("ta_emby_sync", str(out))

    assert "created 3," in channel and "warning:" in channel
    assert "created 3," in playlist
    assert "created 0, updated 0, renamed 0, deleted 0, unchanged 6" in everything


def test_emby_sync_verbose_dry_run(library):
    out = library / "emby"
    run("ta_emby_sync", str(out))
    Video.objects.update(title="Renamed")
    Playlist.objects.all().delete()
    Channel.objects.update(description="changed")

    output = run("ta_emby_sync", str(out), "--dry-run", "--verbose-changes")

    assert "[dry-run] rename " in output
    assert "[dry-run] update " in output
    assert "[dry-run] delete " in output
    assert "[dry-run] created 0" in output

    Video.objects.all().delete()
    Channel.objects.all().delete()
    first = run("ta_emby_sync", str(library / "fresh"))
    assert first.startswith("created 0")


def test_emby_sync_verbose_create(library):
    output = run("ta_emby_sync", str(library / "emby"), "--verbose-changes")

    assert output.count("create ") >= 6


def test_emby_sync_refuses_media_dir(library):
    with pytest.raises(CommandError, match="dot folder"):
        run("ta_emby_sync", str(library / "media" / "emby"))

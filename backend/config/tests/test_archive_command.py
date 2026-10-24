"""tests for ta_import_archive"""

import io

import pytest
from channel.models import Channel
from config.management.commands import ta_import_archive as command
from django.core.management import CommandError, call_command
from jobs.models import Job
from video.models import Video
from video.tests.test_src.test_archive_import import (  # noqa: F401
    fixture_env,
    fixture_folder,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(name="emby")
def fixture_emby(monkeypatch):
    """record requests for an Emby write"""
    calls = []
    monkeypatch.setattr(command, "request_emby_write", lambda: calls.append(1))
    return calls


def run(*args):
    """call the command, return stdout and stderr"""
    out, err = io.StringIO(), io.StringIO()
    call_command("ta_import_archive", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_import(env, folder, emby):
    out, _ = run(str(folder))

    assert "imported Song One.mkv" in out and "imported Song Two.MP4" in out
    assert "2 imported, 0 already indexed, 0 failed" in out
    assert Video.objects.count() == 2
    assert emby == [1]

    out, _ = run(str(folder))
    assert "0 imported, 2 already indexed" in out and emby == [1]


def test_dry_run(env, folder, emby):
    out, _ = run(str(folder), "--dry-run")

    assert "would import Song One.mkv" in out
    assert not Video.objects.exists() and not emby


def test_no_emby(env, folder, emby):
    run(str(folder), "--no-emby", "--channel-name", "Other", "--mode", "copy")

    assert Channel.objects.get().channel_id == "arch_other"
    assert not emby


def test_failures_make_the_command_fail(env, folder, emby):
    (folder / "bad.mkv").write_bytes(b"x")
    env["novideo"] = ["bad.mkv"]

    with pytest.raises(CommandError, match="1 files failed"):
        run(str(folder))

    assert Video.objects.count() == 2 and emby == [1]


def test_bad_folder(env, tmp_path):
    with pytest.raises(CommandError, match="not a folder"):
        run(str(tmp_path / "missing"))


def test_queue(env, folder, monkeypatch):
    queued = []

    def fake_enqueue(job_type, **kwargs):
        queued.append((job_type, kwargs))
        return Job(pk=7), False

    monkeypatch.setattr(command, "enqueue", fake_enqueue)

    out, _ = run(str(folder), "--queue", "--channel-id", "UCx")

    assert out.strip() == "queued job 7"
    job_type, kwargs = queued[0]
    assert job_type == "archive_import"
    assert kwargs["params"]["root"] == str(folder.resolve())
    assert kwargs["params"]["channel_id"] == "UCx"
    assert kwargs["dedupe_key"] == f"archive_import:{folder.resolve()}"
    assert not Video.objects.exists()

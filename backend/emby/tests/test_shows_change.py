"""what the layout 3 migration does to the shows, and who loses watch state"""

from io import StringIO

import pytest
from django.core.management import call_command

from appsettings.models import ConfigDocument
from common.src.env_settings import EnvironmentSettings
from emby.src import shows_change
from emby.src.sync import LAYOUT_KEY, sync_all
from emby.src.writer import SyncReport
from mediaserver.src import client as client_mod
from mediaserver.src import settings
from mediaserver.src.client import MediaServerError
from mediaserver.tests.test_mediaserver import FakeHttp

pytestmark = pytest.mark.django_db

REPORT = SyncReport(
    deleted=[
        "playlists/PL [PL1]/Season 1/e.mp4",
        "playlists/PL [PL1]/Season 1/e.nfo",
        "playlists/PL [PL1]/Season 1/e-thumb.jpg",
        "playlists/PL [PL1]/",
        "channels/Gone [UC1]/Season 2023/a.mkv",
        "channels/Gone [UC1]/Season 2023/",
        "channels/Gone [UC1]/",
        "channels/Kept [UC2]/Season 1/old.mp4",
    ],
    created=[
        "channels/Other videos [other]/tvshow.nfo",
        "channels/Other videos [other]/Season 2023/a.mkv",
        "channels/Other videos [other]/Season 2023/a.nfo",
    ],
)


def test_describe():
    change = shows_change.describe(REPORT)

    assert change.removed_shows == {
        "channels/Gone [UC1]": 1,
        "playlists/PL [PL1]": 1,
    }
    assert change.removed_playlist_shows == 1
    assert change.removed_channel_shows == 1
    assert change.removed_media == [
        "playlists/PL [PL1]/Season 1/e.mp4",
        "channels/Gone [UC1]/Season 2023/a.mkv",
    ]
    assert change.other_created == 1


@pytest.fixture(name="server")
def fixture_server(monkeypatch, tmp_path):
    """a configured Emby server, HTTP faked"""
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(EnvironmentSettings, "EMBY_ROOT", "/youtube/.emby")
    fake = FakeHttp()
    monkeypatch.setattr(client_mod.requests, "request", fake)
    settings.update(
        {
            "type": "emby",
            "url": "http://emby.test:8096",
            "api_key": "k",
            "path_to": "/mnt/.emby",
        }
    )
    return fake


class PagedHttp(FakeHttp):
    """answers /Users/<id>/Items by Filters, two items per page"""

    def __init__(self, items):
        super().__init__()
        self.items = items

    def __call__(self, method, url, headers=None, timeout=None, **kwargs):
        path = url.split("8096", 1)[1]
        params = kwargs.get("params") or {}
        if path == "/Users":
            body = [{"Id": "d1", "Name": "David"}, {"Id": "m1", "Name": "M"}]
        else:
            user = path.split("/")[2]
            wanted = (
                self.items.get((user, params["Filters"]), [])
                if user == "d1"
                else []
            )
            start = params["StartIndex"]
            page = wanted[start : start + 2]
            body = {
                "Items": [{"Path": p} for p in page] + [{"Name": "no path"}],
                "TotalRecordCount": len(wanted),
            }
        self.calls.append((method, url, headers, kwargs))
        return type(
            "R", (), {"status_code": 200, "json": lambda self_: body}
        )()


def test_watch_history_at_risk(server, monkeypatch):
    # pylint: disable=unused-argument
    paged = PagedHttp(
        {
            ("d1", "IsPlayed"): [
                "/mnt/.emby/playlists/PL [PL1]/Season 1/e.mp4",
                "/mnt/.emby/channels/Kept/other.mp4",
                "/mnt/.emby/channels/Gone [UC1]/Season 2023/a.mkv",
            ],
            ("d1", "IsResumable"): [
                "/mnt/.emby/playlists/PL [PL1]/Season 1/e.mp4"
            ],
        }
    )
    monkeypatch.setattr(client_mod.requests, "request", paged)
    monkeypatch.setattr(client_mod, "PAGE", 2)
    media = shows_change.describe(REPORT).removed_media

    at_risk, error = shows_change.watch_history_at_risk(media)

    assert error is None
    assert at_risk == {
        "David": [
            "channels/Gone [UC1]/Season 2023/a.mkv",
            "playlists/PL [PL1]/Season 1/e.mp4",
        ]
    }
    assert shows_change.watch_history_at_risk([]) == ({}, None)


def test_watch_history_without_server(server, monkeypatch):
    # pylint: disable=unused-argument
    server.raise_error = client_mod.requests.ConnectionError("down")
    at_risk, error = shows_change.watch_history_at_risk(["a/b.mp4"])
    assert at_risk == {} and "can't reach" in error

    settings.update({"type": None})
    assert shows_change.watch_history_at_risk(["a/b.mp4"]) == (
        {},
        "no media server configured",
    )
    assert isinstance(MediaServerError("x"), Exception)


def run_dry(*args):
    out = StringIO()
    call_command("ta_emby_migrate_layout", *args, stdout=out)
    return out.getvalue()


CMD = "config.management.commands.ta_emby_migrate_layout."


@pytest.mark.parametrize(
    "history, expected",
    [
        (({"David": ["playlists/x.mp4"]}, None), "lost for David: 1 items"),
        (({}, "can't reach"), "watch history not read: can't reach"),
        (({}, None), "no watch history on removed items"),
    ],
)
def test_migration_dry_run_lists_the_shows(
    library, settings_env, monkeypatch, history, expected
):
    # pylint: disable=unused-argument
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    monkeypatch.setattr(CMD + "sync_all", lambda paths, dry_run=False: REPORT)
    monkeypatch.setattr(CMD + "watch_history_at_risk", lambda media: history)

    output = run_dry("--dry-run")

    assert "[dry-run] show removed: playlists/PL [PL1] (1 files)" in output
    assert "[dry-run] show removed: channels/Gone [UC1] (1 files)" in output
    assert (
        "playlist shows removed 1, channel shows moved to Other videos 1, "
        "episodes created in Other videos 1" in output
    )
    assert expected in output
    assert "rolled back" in output


def test_migration_dry_run_compares_visibility(
    server, library, settings_env, monkeypatch
):
    # pylint: disable=unused-argument
    monkeypatch.setenv(settings.KEY_ENV, "k")
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    monkeypatch.setattr(CMD + "sync_all", lambda paths, dry_run=False: REPORT)
    monkeypatch.setattr(
        CMD + "watch_history_at_risk", lambda media: ({}, None)
    )
    monkeypatch.setattr(
        CMD + "before_state",
        lambda client, tags, files: {"David": ({"v1"}, 2)},
    )

    output = run_dry("--dry-run")

    assert (
        "[dry-run] visible to David: 1 videos now, 0 after, lost 1, gained 0, "
        "2 Emby items outside" in output
    )
    assert "[dry-run]   LOST v1" in output


def test_migration_visibility_needs_the_server(
    server, library, settings_env, monkeypatch
):
    # pylint: disable=unused-argument
    monkeypatch.setenv(settings.KEY_ENV, "k")
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    monkeypatch.setattr(CMD + "sync_all", lambda paths, dry_run=False: REPORT)
    monkeypatch.setattr(
        CMD + "watch_history_at_risk", lambda media: ({}, None)
    )

    def down(client, tags, files):
        raise MediaServerError("down")

    monkeypatch.setattr(CMD + "before_state", down)

    assert "visibility not compared: down" in run_dry("--dry-run")


def test_migration_lists_watch_history_on_renamed_files(
    library, settings_env, monkeypatch
):
    # pylint: disable=unused-argument
    ConfigDocument.objects.create(key=LAYOUT_KEY, value={"version": 2})
    report = SyncReport(renamed=[("c/A/S1/old.mp4", "c/A/S1/new.mp4")])
    monkeypatch.setattr(CMD + "sync_all", lambda paths, dry_run=False: report)
    calls = []

    def history(media):
        calls.append(media)
        return ({"David": ["c/A/S1/old.mp4"]}, None) if media else ({}, None)

    monkeypatch.setattr(CMD + "watch_history_at_risk", history)

    output = run_dry("--dry-run")

    assert calls[-1] == ["c/A/S1/old.mp4"]
    assert "renamed files with watch history" in output
    assert "[dry-run]   David: 1 items" in output
    assert "no watch history on removed items" in output

    # unreadable server: the renamed section stays out
    monkeypatch.setattr(
        CMD + "watch_history_at_risk", lambda media: ({}, "down")
    )
    assert "renamed files with" not in run_dry("--dry-run")


def test_migration_tells_the_media_server(
    server, library, settings_env, monkeypatch
):
    """the run that writes new tags into existing items must queue the
    notify and the refresh itself; no job does it for a command"""
    # pylint: disable=unused-argument
    from common.src.ta_redis import RedisBase  # pylint: disable=C0415
    from jobs.models import Job  # pylint: disable=C0415

    monkeypatch.setenv(settings.KEY_ENV, "k")
    RedisBase().conn.delete(
        RedisBase.NAME_SPACE + "mediaserver:refresh_pending"
    )
    sync_all(library)
    from channel.models import Channel  # pylint: disable=C0415

    Channel.objects.update(emby_tags=["David"])
    ConfigDocument.objects.filter(key=LAYOUT_KEY).update(value={"version": 2})
    monkeypatch.setattr(CMD + "before_state", lambda c, t, f: None)
    monkeypatch.setattr(
        CMD + "watch_history_at_risk", lambda media: ({}, None)
    )

    run_dry()

    assert Job.objects.get(type="media_notify").created_by == "user"
    pending = RedisBase().conn.smembers(
        RedisBase.NAME_SPACE + "mediaserver:refresh_pending"
    )
    assert any(item.startswith("series:channels/") for item in pending)

"""what the layout 3 migration does to the shows, and who loses watch state"""

from io import StringIO

import pytest
from appsettings.models import ConfigDocument
from common.src.env_settings import EnvironmentSettings
from django.core.management import call_command
from emby.src import shows_change
from emby.src.sync import LAYOUT_KEY
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

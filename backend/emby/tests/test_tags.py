"""Emby access tags: cleaning, storage, NFO, import from Emby, the command"""

import xml.etree.ElementTree as ET
from io import StringIO

import pytest
from channel.models import Channel
from common.tests.docs import channel_doc, playlist_doc
from django.core.management import call_command
from django.core.management.base import CommandError
from emby.src.nfo import SeriesInfo, render_tvshow
from emby.src.sync import sync_all
from emby.src.tag_import import apply_import, plan_import
from emby.src.tags import clean_tags, known_tags, set_tags
from mediaserver.src import settings
from mediaserver.src.client import MediaServerError
from playlist.models import Playlist
from rest_framework.test import APIClient
from user.models import Account

pytestmark = pytest.mark.django_db

CH = channel_doc()["channel_id"]


def test_clean_tags():
    assert clean_tags([" David  ", "david", "Kids", "", "  "]) == [
        "David",
        "Kids",
    ]
    assert clean_tags(["b", "A"]) == ["A", "b"]
    assert len(clean_tags([f"t{i}" for i in range(50)])) == 20
    assert clean_tags(["x" * 100]) == ["x" * 64]


def test_set_tags_and_known_tags():
    Channel.save_doc(channel_doc())
    Playlist.save_doc(playlist_doc("PL1", []))
    assert known_tags() == ["David"]

    assert set_tags("channel", [CH, "missing"], ["Kids", "kids"]) == [CH]
    set_tags("playlist", ["PL1"], ["Zed"])

    assert Channel.objects.get().emby_tags == ["Kids"]
    assert known_tags() == ["David", "Kids", "Zed"]
    settings.update({"known_tags": ["Alice"]})
    assert known_tags() == ["Alice", "Kids", "Zed"]


def test_tvshow_nfo_tags():
    info = SeriesInfo(series_id="x", title="T", tags=("b", "a", "a"))
    root = ET.fromstring(render_tvshow(info))
    assert [t.text for t in root.findall("tag")] == ["a", "b"]
    assert (
        ET.fromstring(render_tvshow(SeriesInfo("x", "T"))).find("tag") is None
    )


def test_sync_writes_tags(library):
    Channel.objects.update(emby_tags=["David"])

    sync_all(library)

    tags = {}
    for path in library.output_root.rglob("tvshow.nfo"):
        root = ET.parse(path).getroot()
        tags[path.parent.name] = [t.text for t in root.findall("tag")]
    assert list(tags.values()) == [["David"]]

    # a tag change is an update of the tvshow.nfo, nothing else
    Channel.objects.update(emby_tags=["David", "Kids"])
    report = sync_all(library)
    assert [p for p in report.updated if p.endswith("tvshow.nfo")]
    assert not report.created and not report.deleted


def test_tags_endpoint():
    user = Account.objects.create_superuser(name="admin", password="test")
    client = APIClient(SERVER_NAME="localhost")
    client.force_authenticate(user)
    Channel.save_doc(channel_doc())
    set_tags("channel", [CH], ["Kids"])

    response = client.get("/api/emby/tags/")

    assert response.status_code == 200
    assert response.json() == {"tags": ["David", "Kids"]}


# import from Emby


class FakeClient:
    """MediaServerClient with the shows of the tags"""

    def __init__(self, shows):
        self.shows = shows

    def series_with_tag(self, tag):
        return [s for s in self.shows if tag in s["tags"]]


def show(name, path="/x", tags=("David",), **ids):
    return {
        "name": name,
        "path": path,
        "provider_ids": ids,
        "tags": list(tags),
    }


def test_plan_and_apply_import():
    Channel.save_doc(channel_doc())  # id CH, name from the doc
    name = Channel.objects.get().name
    Playlist.save_doc(playlist_doc("PLa", [], playlist_name="Bible Stories"))
    Playlist.save_doc(playlist_doc("PLb", [], playlist_name="Twin"))
    Playlist.save_doc(playlist_doc("PLc", [], playlist_name="Twin"))
    shows = [
        show("By provider", "/y/a [zzz]", youtube=CH),
        show("Folder", "/y/playlists/F [PLa]/"),
        show("bible stories", "/y/YT-Approved/bible"),
        show(name, f"/y/YT-Approved/{name}"),
        show("Twin", "/y/twin"),
        show("Nobody", "/y/nobody"),
        show("Manual", "/y/m"),
        show("Bad manual", "/y/bm"),
        show("Other tag", "/y/o", tags=("Kids",)),
    ]
    manual = {"Manual": "PLb", "Bad manual": "nope"}

    plan = plan_import(FakeClient(shows), ["David"], manual)

    by_name = {item.emby_name: item for item in plan}
    assert "Other tag" not in by_name
    assert (by_name["By provider"].kind, by_name["By provider"].how) == (
        "channel",
        "provider id",
    )
    assert by_name["Folder"].target_id == "PLa"
    assert by_name["Folder"].how == "folder id"
    assert (
        by_name["bible stories"].target_id,
        by_name["bible stories"].how,
    ) == (
        "PLa",
        "name",
    )
    assert by_name[name].target_id == CH
    assert by_name["Twin"].how == "several matches by name"
    assert by_name["Nobody"].how == "no match by name"
    assert by_name["Manual"].target_id == "PLb"
    assert by_name["Bad manual"].how == "manual id unknown"
    assert by_name["Nobody"].kind is None

    changed = apply_import(plan)

    assert changed == {
        f"channel/{CH}": ["David"],
        "playlist/PLa": ["David"],
        "playlist/PLb": ["David"],
    }
    assert Channel.objects.get().emby_tags == ["David"]
    assert apply_import(plan) == {}  # nothing left to change


@pytest.fixture(name="emby_server")
def fixture_server(monkeypatch, tmp_path):
    """a configured server whose client is FakeClient"""
    from common.src.env_settings import EnvironmentSettings

    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("jobs.src.handlers.paths_from_env", lambda: None)
    shows = [show("Chan", f"/y/Chan [{CH}]"), show("Lost", "/y/lost")]
    fake = FakeClient(shows)
    monkeypatch.setattr(
        "config.management.commands.ta_emby_import_tags.MediaServerClient",
        lambda conn: fake,
    )
    return fake


def run_command(*args):
    out = StringIO()
    call_command("ta_emby_import_tags", *args, stdout=out)
    return out.getvalue()


def test_command(emby_server):
    # pylint: disable=unused-argument
    with pytest.raises(CommandError, match="no media server"):
        run_command()
    settings.update({"type": "emby", "url": "http://e:8096", "api_key": "k"})
    Channel.save_doc(channel_doc())

    with pytest.raises(CommandError, match="NAME=ID"):
        run_command("--map", "broken")

    preview = run_command("--dry-run")
    assert "Chan [David] -> channel" in preview
    assert "Lost [David] -> NOT MAPPED" in preview
    assert "1 mapped, 1 not mapped" in preview
    assert Channel.objects.get().emby_tags == []

    done = run_command("--map", "Lost=nope")
    assert f"channel/{CH}: David" in done
    assert "1 channels/playlists updated" in done
    assert Channel.objects.get().emby_tags == ["David"]
    assert "0 channels/playlists updated" in run_command()


def test_command_server_error(monkeypatch, emby_server):
    # pylint: disable=unused-argument
    settings.update({"type": "emby", "url": "http://e:8096", "api_key": "k"})

    def fail(tag):
        raise MediaServerError("down")

    emby_server.series_with_tag = fail
    with pytest.raises(CommandError, match="down"):
        run_command()

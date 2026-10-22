"""who sees what before and after, the numbers the dry runs print"""

import io

import pytest
from channel.models import Channel
from common.src.env_settings import EnvironmentSettings
from django.core.management import call_command
from emby.src import visibility
from emby.src.sync import sync_all
from emby.src.writer import Manifest
from mediaserver.src import settings
from playlist.models import Playlist
from video.models import Video

pytestmark = pytest.mark.django_db

SERVER = "/mnt/emby"


class FakeClient:
    """shows with a tag and their episode paths"""

    def __init__(self, shows):
        self.shows = shows  # {tag: [(id, [server paths])]}

    def series_with_tag(self, tag):
        return [
            {"id": show_id, "name": show_id, "path": "", "provider_ids": {}}
            for show_id, _ in self.shows.get(tag, [])
        ]

    def series_episode_paths(self, series_id):
        for shows in self.shows.values():
            for show_id, paths in shows:
                if show_id == series_id:
                    return paths
        return []


@pytest.fixture(autouse=True)
def fixture_cache(monkeypatch, tmp_path):
    """the API key is encrypted with a key file in the cache dir"""
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))


def configure():
    settings.update(
        {"type": "emby", "url": "http://e:8096", "api_key": "k"}
        | {"path_to": SERVER}
    )


def media_files(library):
    return Manifest(library.output_root, dry_run=True).files


def server_path(files, youtube_id):
    relpath = next(
        r
        for r, e in files.items()
        if e["role"] == "media" and e["owner"].endswith(f"/{youtube_id}")
    )
    return f"{SERVER}/{relpath}"


def test_ta_visible(library):
    assert visibility.ta_visible("David") == set()
    Playlist.objects.update(emby_tags=["david"])  # holds v3, v1
    assert visibility.ta_visible("David") == {"v1", "v3"}

    Channel.objects.update(emby_tags=["David"])
    assert visibility.ta_visible("David") == {"v1", "v2", "v3"}
    Video.objects.filter(pk="v2").update(active=False)
    assert visibility.ta_visible("David") == {"v1", "v3"}
    assert visibility.ta_visible("Kids") == set()


def test_compare_before_and_after(library):
    configure()
    sync_all(library)
    files = media_files(library)
    # Emby shows v1, v2 and one item outside the tree for David today
    client = FakeClient(
        {
            "David": [
                (
                    "s1",
                    [
                        server_path(files, "v1"),
                        server_path(files, "v2"),
                        f"{SERVER}/handmade/x.mkv",
                    ],
                )
            ]
        }
    )
    before = visibility.before_state(client, ["David", "Kids"], files)
    assert before["David"] == ({"v1", "v2"}, 1)
    assert before["Kids"] == (set(), 0)

    Playlist.objects.update(emby_tags=["David"])  # v3, v1 after
    results = visibility.compare(before)

    david = results[0]
    assert (david.before, david.after) == (2, 2)
    assert david.lost == ["v2"] and david.gained == 1 and david.outside == 1
    assert "lost 1, gained 1" in david.line()
    assert results[1].lost == []


def test_write_report(library):
    lines = []
    results = [
        visibility.Visibility("David", 3, 1, lost=["v1", "zz"], gained=0)
    ]

    visibility.write_report(results, lines.append, "[x] ", limit=1)

    assert lines[0].startswith("[x] (now = the shows carrying the tag;")
    assert "not counted" in lines[0]
    assert lines[1].startswith("[x] visible to David: 3 videos now, 1 after")
    assert lines[2] == "[x]   LOST v1 " + Video.objects.get(pk="v1").title
    assert lines[3] == "[x]   ... and 1 more"
    visibility.write_report(results, lines.append)
    assert lines[-1].endswith("LOST zz ")
    count = len(lines)
    visibility.write_report([], lines.append)
    assert len(lines) == count  # no tags, no note


def test_media_ids():
    files = {
        "a/e.mp4": {"role": "media", "owner": "channels/UC1/v1"},
        "a/e.nfo": {"role": "nfo", "owner": "channels/UC1/v1"},
    }
    assert visibility.media_ids(files) == {"a/e.mp4": "v1"}


def run(*args):
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def test_import_dry_run_shows_who_loses(library, monkeypatch):
    configure()
    monkeypatch.setattr(
        EnvironmentSettings, "EMBY_ROOT", str(library.output_root)
    )
    monkeypatch.setattr(
        EnvironmentSettings, "MEDIA_DIR", str(library.media_root)
    )
    sync_all(library)
    files = media_files(library)
    shows = {
        "David": [("s1", [server_path(files, "v1"), server_path(files, "v2")])]
    }
    client = FakeClient(shows)
    client.series_with_tag = lambda tag, real=client.series_with_tag: [
        {**s, "name": "Test Playlist", "path": "/x/PL [PL1]", "tags": [tag]}
        for s in real(tag)
    ]
    monkeypatch.setattr(
        "config.management.commands.ta_emby_import_tags.MediaServerClient",
        lambda conn: client,
    )

    output = run("ta_emby_import_tags", "--dry-run")

    # the tag goes to playlist PL1 (v3, v1), v2 is lost for David
    assert (
        "visible to David: 2 videos now, 2 after, lost 1, gained 1" in output
    )
    assert "LOST v2" in output
    assert Playlist.objects.get().emby_tags == []  # rolled back

    real = run("ta_emby_import_tags")
    assert "LOST v2" in real
    assert Playlist.objects.get().emby_tags == ["David"]

"""media server settings, client, notify jobs and API; HTTP mocked"""

import os
from types import SimpleNamespace

import pytest
import requests
from appsettings.models import ConfigDocument
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisBase
from django.utils import timezone
from emby.src.writer import SyncReport
from jobs.models import Job, JobStatus
from jobs.src.context import JobContext
from jobs.src.engine import enqueue
from jobs.src.registry import JobError
from mediaserver.src import client as client_mod
from mediaserver.src import jobs as ms_jobs
from mediaserver.src import pending, settings
from mediaserver.src.client import MediaServerClient, MediaServerError
from mediaserver.src.settings import Connection

pytestmark = pytest.mark.django_db

SECRET = "s3cret-api-key-0123456789"
ROOT = "/youtube/.emby"


@pytest.fixture(autouse=True)
def fixture_env(monkeypatch, tmp_path):
    """cache dir in tmp, emby root set, no key in the env, no pending"""
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(EnvironmentSettings, "EMBY_ROOT", ROOT)
    monkeypatch.delenv(settings.KEY_ENV, raising=False)
    conn = RedisBase().conn
    keys = [
        RedisBase.NAME_SPACE + ms_jobs.PENDING_KEY,
        RedisBase.NAME_SPACE + ms_jobs.REFRESH_KEY,
        RedisBase.NAME_SPACE + pending.SINCE_KEY,
    ]
    conn.delete(*keys)
    yield
    conn.delete(*keys)


class FakeHttp:
    """requests.request stand in: responses by (method, path)"""

    def __init__(self):
        self.calls = []
        self.responses = {}
        self.raise_error = None

    def __call__(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append((method, url, headers, kwargs))
        if self.raise_error:
            raise self.raise_error
        path = url.split("8096", 1)[1]
        status, body = self.responses.get((method, path), (204, None))
        return SimpleNamespace(status_code=status, json=lambda: body)


@pytest.fixture(name="http")
def fixture_http(monkeypatch):
    """fake HTTP"""
    fake = FakeHttp()
    monkeypatch.setattr(client_mod.requests, "request", fake)
    return fake


def configure(**overrides):
    """a configured Emby server"""
    data = {
        "type": "emby",
        "url": "http://emby.test:8096/",
        "api_key": SECRET,
        "path_to": "/mnt/media/.emby",
    }
    data.update(overrides)
    return settings.update(data)


# settings


def test_key_encrypted_at_rest(tmp_path):
    public = configure()

    assert public["api_key_set"] and public["api_key_source"] == "db"
    assert "api_key" not in public and SECRET not in str(public)
    stored = ConfigDocument.objects.get(key="mediaserver").value
    assert SECRET not in str(stored)
    key_file = tmp_path / ".secrets" / "mediaserver.key"
    assert oct(key_file.stat().st_mode)[-3:] == "600"
    assert settings.api_key() == (SECRET, "db")
    assert public["path_from"] == ROOT

    # omitted keeps the key, empty clears it
    assert settings.update({"url": "http://other:8096"})["api_key_set"]
    assert not settings.update({"api_key": ""})["api_key_set"]


def test_key_from_env_wins(monkeypatch):
    configure()
    monkeypatch.setenv(settings.KEY_ENV, "from-env")
    assert settings.api_key() == ("from-env", "env")
    assert settings.public()["api_key_source"] == "env"


def test_key_file_replaced(tmp_path):
    configure()
    (tmp_path / ".secrets" / "mediaserver.key").unlink()
    assert settings.api_key() == (None, None)
    assert settings.decrypt(None) is None


def test_connection_and_paths():
    assert settings.connection() is None
    configure()
    conn = settings.connection()
    assert (conn.type, conn.url, conn.api_key) == (
        "emby",
        "http://emby.test:8096",
        SECRET,
    )
    assert SECRET not in repr(conn)
    form = settings.connection(
        {"type": "jellyfin", "url": "http://j:8096", "api_key": "k"}
    )
    assert (form.type, form.api_key) == ("jellyfin", "k")

    assert (
        settings.to_server_path("channels/A") == "/mnt/media/.emby/channels/A"
    )
    settings.update({"path_to": ""})
    assert settings.to_server_path("/x") == f"{ROOT}/x"
    assert settings.from_ta_path(f"{ROOT}/channels/A") == "channels/A"
    assert settings.from_ta_path(ROOT) == ""
    with pytest.raises(ValueError):
        settings.from_ta_path("/youtube/other")


def test_from_ta_path_without_root(monkeypatch):
    monkeypatch.setattr(EnvironmentSettings, "EMBY_ROOT", "")
    with pytest.raises(ValueError):
        settings.from_ta_path("/x")


# client


def client(server_type="emby"):
    """client for a fake server"""
    return MediaServerClient(
        Connection(type=server_type, url="http://emby.test:8096", api_key="k")
    )


def test_client_auth_headers(http):
    http.responses[("GET", "/System/Info")] = (200, {})
    client().info()
    client("jellyfin").info()
    assert http.calls[0][2] == {"X-Emby-Token": "k"}
    assert http.calls[1][2] == {"Authorization": 'MediaBrowser Token="k"'}


def test_client_info_and_libraries(http):
    http.responses[("GET", "/System/Info")] = (
        200,
        {"ServerName": "nas", "Version": "4.10.1.0"},
    )
    http.responses[("GET", "/Library/VirtualFolders")] = (
        200,
        [
            {
                "Name": "YouTube",
                "ItemId": "12",
                "CollectionType": "tvshows",
                "Locations": ["/mnt/media/.emby/channels"],
            },
            {"Name": "Odd", "Id": 7},
        ],
    )
    assert client().info() == {
        "server_name": "nas",
        "product": "Emby Server",
        "version": "4.10.1.0",
    }
    assert client("jellyfin").info()["product"] == "Jellyfin"
    assert client().libraries() == [
        {
            "id": "12",
            "name": "YouTube",
            "collection_type": "tvshows",
            "paths": ["/mnt/media/.emby/channels"],
        },
        {"id": "7", "name": "Odd", "collection_type": None, "paths": []},
    ]


@pytest.mark.parametrize(
    "status, message",
    [(401, "rejected the API key"), (403, "rejected"), (500, "HTTP 500")],
)
def test_client_errors(http, status, message):
    http.responses[("GET", "/System/Info")] = (status, None)
    with pytest.raises(MediaServerError, match=message):
        client().info()


def test_client_unreachable_hides_key(http):
    http.raise_error = requests.ConnectionError("refused, token=k")
    with pytest.raises(MediaServerError) as err:
        client().info()
    assert str(err.value) == (
        "can't reach http://emby.test:8096: ConnectionError"
    )


def test_media_updated_chunks_and_refresh(http, monkeypatch):
    monkeypatch.setattr(client_mod, "CHUNK", 2)
    updates = [{"Path": f"/p{i}", "UpdateType": "Modified"} for i in range(5)]
    assert client().media_updated(updates) == 204
    posts = [c for c in http.calls if c[1].endswith("/Library/Media/Updated")]
    assert [len(c[3]["json"]["Updates"]) for c in posts] == [2, 2, 1]

    client().refresh("12")
    client().refresh()
    assert http.calls[-2][1].endswith("/Items/12/Refresh")
    assert http.calls[-2][3]["params"] == {"Recursive": "true"}
    assert http.calls[-1][1].endswith("/Library/Refresh")


# notify


def test_changed_folders():
    report = SyncReport(
        created=["channels/A/Season 2025/e1.nfo"],
        updated=["channels/A/tvshow.nfo"],
        renamed=[
            ("channels/A/Season 2024/x.mp4", "channels/B/Season 1/x.mp4")
        ],
        deleted=["channels/C/", ""],
    )
    assert ms_jobs.changed_folders(report) == {
        "channels/A/Season 2025",
        "channels/A",
        "channels/A/Season 2024",
        "channels/B/Season 1",
        "channels",
    }


def test_queue_notify_debounced():
    assert ms_jobs.queue_notify({"channels/A"}) is None  # not configured
    configure()
    first = ms_jobs.queue_notify({"channels/A"})
    second = ms_jobs.queue_notify({"channels/B", "channels/A"})

    assert first.pk == second.pk  # merged into one waiting job
    first.refresh_from_db()
    assert first.not_before is not None and first.merged == 1
    settings.update({"notify_enabled": False})
    assert ms_jobs.queue_notify({"channels/C"}) is None
    assert ms_jobs.queue_notify(set()) is None


def run(job_type, **params):
    """run a media server job handler"""
    job, _ = enqueue(job_type, params=params, dispatch=False)
    Job.objects.filter(pk=job.pk).update(status=JobStatus.RUNNING)
    job.refresh_from_db()
    return getattr(ms_jobs, job_type)(JobContext(job)), job


def test_media_notify(http):
    result, _ = run("media_notify")
    assert result == {"skipped": "no media server configured"}

    configure()
    assert run("media_notify")[0] == {"paths": 0}

    ms_jobs.queue_notify({"channels/B", "channels/A"})
    result, job = run("media_notify")

    assert result == {"status_code": 204, "paths": 2}
    body = http.calls[-1][3]["json"]
    assert body == {
        "Updates": [
            {"Path": "/mnt/media/.emby/channels/A", "UpdateType": "Modified"},
            {"Path": "/mnt/media/.emby/channels/B", "UpdateType": "Modified"},
        ]
    }
    last = settings.public()["last_notify"]
    assert (last["status_code"], last["paths"], last["job_id"]) == (
        204,
        2,
        job.pk,
    )


def test_media_notify_failure_keeps_paths(http):
    configure()
    ms_jobs.queue_notify({"channels/A"})
    http.responses[("POST", "/Library/Media/Updated")] = (500, None)

    with pytest.raises(JobError, match="HTTP 500"):
        run("media_notify")

    assert settings.public()["last_notify"]["error"].endswith("HTTP 500")
    del http.responses[("POST", "/Library/Media/Updated")]
    assert run("media_notify")[0]["paths"] == 1


def test_media_refresh(http):
    with pytest.raises(JobError, match="no media server"):
        run("media_refresh")
    configure(library_id="12")
    assert run("media_refresh")[0] == {"status_code": 204, "library_id": "12"}
    assert http.calls[-1][1].endswith("/Items/12/Refresh")
    settings.update({"library_id": ""})
    assert run("media_refresh")[0]["library_id"] == "all"

    http.responses[("POST", "/Library/Refresh")] = (401, None)
    with pytest.raises(JobError, match="rejected"):
        run("media_refresh")


def test_series_with_tag_and_find_item(http):
    configure()
    http.responses[("GET", "/Items")] = (
        200,
        {
            "Items": [
                {
                    "Name": "Show",
                    "Path": "/x/Show [UC1]",
                    "ProviderIds": {"youtube": "UC1"},
                    "TagItems": [{"Name": "David"}, {"Name": None}],
                    "Tags": ["Kids"],
                },
                {"Name": None, "Path": None},
            ]
        },
    )
    shows = MediaServerClient(
        Connection("emby", "http://emby.test:8096", SECRET)
    ).series_with_tag("David")

    assert shows == [
        {
            "name": "Show",
            "path": "/x/Show [UC1]",
            "provider_ids": {"youtube": "UC1"},
            "tags": ["David", "Kids"],
        },
        {"name": "", "path": "", "provider_ids": {}, "tags": []},
    ]
    query = http.calls[-1][3]["params"]
    assert query["Tags"] == "David" and query["IncludeItemTypes"] == "Series"
    assert http.calls[-1][0] == "GET"


def test_media_by_stem():
    files = {
        "c/A/S1/e.mp4": {"role": "media"},
        "c/A/S1/e.nfo": {"role": "nfo"},
        "c/A/S1/e-thumb.jpg": {"role": "thumb"},
    }
    assert ms_jobs.media_by_stem(files) == {"c/A/S1/e": "c/A/S1/e.mp4"}


def test_changed_items():
    media = {"c/A/Season 1/e": "c/A/Season 1/e.mp4"}
    report = SyncReport(
        created=["channels/N/tvshow.nfo"],
        updated=[
            "channels/A/tvshow.nfo",
            "c/A/Season 1/e.nfo",
            "c/A/Season 1/unknown.nfo",
            "c/A/Season 1/e-thumb.jpg",
        ],
        # a renamed file is a new item for the server: no refresh
        renamed=[("playlists/P/tvshow.nfo", "channels/B/tvshow.nfo")],
    )
    assert ms_jobs.changed_items(report, media) == {
        "series:channels/A",
        "episode:c/A/Season 1/e.mp4",
    }


def test_many_changed_episodes_stay_single_refreshes():
    media = {f"c/A/S1/e{n}": f"c/A/S1/e{n}.mp4" for n in range(300)}
    report = SyncReport(updated=[f"c/A/S1/e{n}.nfo" for n in range(300)])
    items = ms_jobs.changed_items(report, media)
    assert len(items) == 300 and all(i.startswith("episode:") for i in items)


def test_queue_refresh_debounced():
    assert queue({"series:A"}) is None  # not configured
    configure()
    first = queue({"series:A"})
    second = queue({"series:B"})

    assert first.pk == second.pk
    settings.update({"notify_enabled": False})
    assert queue({"series:C"}) is None
    configure(notify_enabled=True)
    # whoever put them there, waiting items get a job; none waiting, none
    assert ms_jobs.queue_item_refresh() is not None
    for entry in pending.entries():
        pending.done(entry)
    assert queue(set()) is None


def queue(entries):
    """put items in the pending set and queue the refresh job"""
    for entry in entries:
        pending.add(entry)
    return ms_jobs.queue_item_refresh()


def found(path, item_id=42):
    return (200, {"Items": [{"Id": item_id, "Path": path}]})


def test_media_refresh_items(http):
    assert run("media_refresh_items")[0] == {
        "skipped": "no media server configured"
    }
    configure()
    assert run("media_refresh_items")[0] == {"items": 0}

    queue({"episode:o/e.mp4", "series:channels/A [x]"})
    paths = {"channels/A [x]": "Series", "o/e.mp4": "Episode"}
    seen = []

    def lookup(method, url, headers=None, timeout=None, **kwargs):
        # answer by the path asked for, like Emby
        params = kwargs.get("params") or {}
        seen.append((method, url, params))
        if method == "GET":
            wanted = params["Path"]
            return SimpleNamespace(
                status_code=200, json=lambda: found(wanted)[1]
            )
        return SimpleNamespace(status_code=204, json=lambda: None)

    client_mod.requests.request = lookup
    result, _ = run("media_refresh_items")

    assert result == {"refreshed": 2, "unknown": 0, "left": 0}
    asked = [
        (p["Path"], p["IncludeItemTypes"]) for m, u, p in seen if m == "GET"
    ]
    # shows before episodes
    assert asked == [
        (f"/mnt/media/.emby/{k}", v)
        for k, v in sorted(paths.items(), key=lambda i: i[1] != "Series")
    ]
    posts = [(u, p) for m, u, p in seen if m == "POST"]
    assert all(u.endswith("/Items/42/Refresh") for u, _ in posts)
    # a removed or replaced tag only goes if the NFO replaces what Emby holds
    assert all(p["MetadataRefreshMode"] == "FullRefresh" for _, p in posts)
    assert all(p["ReplaceAllMetadata"] == "true" for _, p in posts)
    assert all(p["ReplaceAllImages"] == "false" for _, p in posts)
    assert all(p["Recursive"] == "false" for _, p in posts)
    assert not Job.objects.filter(type="media_refresh_items", status="queued")


def test_media_refresh_items_is_paced(http):
    configure(refresh_batch_size=2, refresh_pause_seconds=120)
    queue({"series:s2", "series:s1", "episode:e1.mp4", "episode:e2.mp4"})
    Job.objects.all().delete()
    http.responses[("GET", "/Items")] = (200, {"Items": []})

    result, job = run("media_refresh_items")

    # the two shows first, then a follow-up run for the episodes
    assert result == {"refreshed": 0, "unknown": 2, "left": 2}
    asked = [c[3]["params"]["Path"] for c in http.calls if c[0] == "GET"]
    assert asked == ["/mnt/media/.emby/s1", "/mnt/media/.emby/s2"]
    follow = Job.objects.get(type="media_refresh_items", status="queued")
    assert follow.created_by == job.created_by
    waits = (follow.not_before - timezone.now()).total_seconds()
    assert 100 < waits <= 120

    conn = RedisBase().conn
    key = RedisBase.NAME_SPACE + ms_jobs.REFRESH_KEY
    assert conn.smembers(key) == {"episode:e1.mp4", "episode:e2.mp4"}
    Job.objects.filter(pk=follow.pk).delete()
    result, _ = run("media_refresh_items")
    assert result["left"] == 0 and not conn.smembers(key)


def test_media_refresh_items_unknown_and_failure(http):
    configure()
    queue({"series:channels/A"})
    http.responses[("GET", "/Items")] = (200, {"Items": []})
    assert run("media_refresh_items")[0] == {
        "refreshed": 0,
        "unknown": 1,
        "left": 0,
    }

    # the server ignores the Path filter and answers with another item
    queue({"series:channels/A"})
    http.responses[("GET", "/Items")] = found("/somewhere/else")
    assert run("media_refresh_items")[0] == {
        "refreshed": 0,
        "unknown": 1,
        "left": 0,
    }

    queue({"series:channels/A", "series:channels/B"})
    http.responses[("GET", "/Items")] = (500, None)
    with pytest.raises(JobError, match="HTTP 500"):
        run("media_refresh_items")

    # nothing was lost
    http.responses[("GET", "/Items")] = (200, {"Items": []})
    assert run("media_refresh_items")[0]["unknown"] == 2


def test_emby_write_queues_notify(monkeypatch, http):
    # pylint: disable=import-outside-toplevel,unused-argument
    from jobs.src import handlers

    configure()
    report = SyncReport(created=["channels/A/Season 1/e.nfo"])
    monkeypatch.setattr(handlers, "paths_from_env", lambda: "paths")
    monkeypatch.setattr(handlers, "sync_all", lambda paths: report)
    job, _ = enqueue("emby_write", dispatch=False)

    handlers.emby_write(JobContext(job))

    notify = Job.objects.get(type="media_notify")
    assert notify.parent_id == job.pk
    conn = RedisBase().conn
    members = conn.smembers(RedisBase.NAME_SPACE + ms_jobs.PENDING_KEY)
    assert members == {"channels/A/Season 1"}


# API


@pytest.fixture(name="api")
def fixture_api(db):
    """DRF client as staff"""
    # pylint: disable=import-outside-toplevel,unused-argument
    from rest_framework.test import APIClient
    from user.models import Account

    api = APIClient(SERVER_NAME="localhost")
    api.force_authenticate(Account.objects.create_superuser("a", "pw"))
    return api


def test_settings_api(api, capsys):
    assert api.get("/api/mediaserver/").json()["type"] is None

    response = api.put(
        "/api/mediaserver/",
        {
            "type": "jellyfin",
            "url": "http://j.test:8096",
            "api_key": SECRET,
            "debounce_seconds": 10,
        },
        format="json",
    )

    data = response.json()
    assert response.status_code == 200
    assert (data["type"], data["api_key_set"], data["debounce_seconds"]) == (
        "jellyfin",
        True,
        10,
    )
    assert "api_key" not in data
    assert SECRET not in response.content.decode()
    assert SECRET not in api.get("/api/mediaserver/").content.decode()
    assert SECRET not in capsys.readouterr().out


def test_test_api(api, http):
    result = api.post("/api/mediaserver/test/", {}, format="json").json()
    assert (result["ok"], result["error"]) == (
        False,
        "type, url and API key needed",
    )
    http.responses[("GET", "/System/Info")] = (200, {"ServerName": "nas"})
    form = {"type": "emby", "url": "http://emby.test:8096", "api_key": "x"}
    ok = api.post("/api/mediaserver/test/", form, format="json").json()
    assert (ok["ok"], ok["server_name"]) == (True, "nas")
    http.responses[("GET", "/System/Info")] = (401, None)
    bad = api.post("/api/mediaserver/test/", form, format="json").json()
    assert (bad["ok"], bad["error"]) == (
        False,
        "the server rejected the API key",
    )


def test_libraries_api(api, http):
    assert api.get("/api/mediaserver/libraries/").status_code == 409
    configure()
    http.responses[("GET", "/Library/VirtualFolders")] = (
        200,
        [
            {"Name": "YT", "ItemId": "1", "Locations": ["/mnt/media/.emby/c"]},
            {"Name": "Root", "ItemId": "2", "Locations": ["/mnt/media/.emby"]},
            {"Name": "Movies", "ItemId": "3", "Locations": ["/mnt/movies"]},
        ],
    )
    data = api.get("/api/mediaserver/libraries/").json()["data"]
    assert [i["contains_ta"] for i in data] == [True, True, False]
    http.responses[("GET", "/Library/VirtualFolders")] = (500, None)
    assert api.get("/api/mediaserver/libraries/").status_code == 502


def test_notify_and_refresh_api(api):
    url = "/api/mediaserver/notify/"
    paths = {"paths": [f"{ROOT}/channels/A"]}
    assert api.post(url, paths, format="json").status_code == 409
    assert api.post("/api/mediaserver/refresh/", {}).status_code == 409

    configure()
    created = api.post(url, paths, format="json")
    assert created.status_code == 201
    assert created.json()["job"]["type"] == "media_notify"
    outside = api.post(url, {"paths": ["/etc"]}, format="json")
    assert outside.status_code == 400
    everything = api.post(url, {"all": True}, format="json")
    assert everything.json()["job"]["type"] == "media_refresh"

    refresh = api.post(
        "/api/mediaserver/refresh/", {"library_id": "9"}, format="json"
    )
    assert refresh.status_code == 201
    assert refresh.json()["job"]["type"] == "media_refresh"
    assert Job.objects.filter(type="media_refresh").count() == 1  # merged


def test_secret_never_in_env_dump():
    configure()
    assert SECRET not in str(os.environ)


def test_media_refresh_items_keeps_what_it_did_not_reach(http):
    configure()
    queue({"series:s1", "series:s2"})
    answers = iter([found("/mnt/media/.emby/s1"), (500, None)])

    def flaky(method, url, headers=None, timeout=None, **kwargs):
        if method == "POST":
            return SimpleNamespace(status_code=204, json=lambda: None)
        status, body = next(answers)
        return SimpleNamespace(status_code=status, json=lambda: body)

    client_mod.requests.request = flaky
    with pytest.raises(JobError, match="HTTP 500"):
        run("media_refresh_items")

    key = RedisBase.NAME_SPACE + ms_jobs.REFRESH_KEY
    assert RedisBase().conn.smembers(key) == {"series:s2"}


def test_pending_set_remembers_since_when():
    assert pending.since() is None and pending.count() == 0
    pending.add("series:a")
    first = pending.since()
    pending.add("episode:b")
    assert pending.since() == first and pending.count() == 2
    assert pending.entries() == ["episode:b", "series:a"]

    pending.done("series:a")
    assert pending.since() == first
    pending.done("episode:b")
    assert pending.since() is None


def test_from_server_path():
    configure()
    assert settings.from_server_path("/mnt/media/.emby/c/e.mkv") == "c/e.mkv"
    assert settings.from_server_path("/elsewhere/e.mkv") is None

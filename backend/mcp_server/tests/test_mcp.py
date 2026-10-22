"""MCP server: protocol, auth and every tool; YouTube faked"""

import pytest
from channel.models import Channel
from common.tests.docs import (
    channel_doc,
    download_doc,
    playlist_doc,
    video_doc,
)
from download.models import Download, DownloadRule, VideoOverride
from download.tests.conftest import (  # noqa: F401
    fixture_app_config,
    fixture_redis_ns,
)
from download.tests.test_src.test_add import (  # noqa: F401
    channel_meta,
    fixture_youtube,
)
from download.tests.test_src.test_queue import (  # noqa: F401
    fixture_yt,
    flat_entry,
)
from jobs.models import Job, JobStatus
from jobs.src import handlers
from jobs.src.engine import enqueue, execute
from mediaserver.src import settings as ms_settings
from playlist.models import Playlist
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from task.tests.helpers import fixture_no_broker  # noqa: F401
from user.models import Account
from video.models import Video

pytestmark = pytest.mark.django_db

CHANNEL_ID = "UCchannel000000000000001"


@pytest.fixture(name="mcp")
def fixture_mcp():
    """call(method, params) and tool(name, **args) as an admin token"""
    admin = Account.objects.create_superuser("admin", "pw")
    token = Token.objects.create(user=admin)
    client = APIClient(SERVER_NAME="localhost")
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.key}")
    counter = iter(range(1, 10000))

    def call(method, params=None):
        body = {"jsonrpc": "2.0", "id": next(counter), "method": method}
        if params is not None:
            body["params"] = params
        response = client.post("/api/mcp", body, format="json")
        assert response.status_code == 200, response.content
        return response.json()

    def tool(name, **args):
        result = call("tools/call", {"name": name, "arguments": args})
        return result["result"]

    call.tool = tool
    call.client = client
    call.token = token.key
    return call


def test_initialize_and_list(mcp):
    init = mcp("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "tubearchivist"
    assert "tools" in init["capabilities"]
    newest = mcp("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert newest["protocolVersion"] == "2025-11-25"

    tools = mcp("tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    for name in [
        "preview_url",
        "add_url",
        "set_rule",
        "set_numbering",
        "set_emby_tags",
        "list_queue",
        "download",
        "ignore",
        "move_to_top",
        "activity",
        "stop_task",
        "search_library",
        "get_video",
        "notify_media_server",
    ]:
        assert name in names
    preview = next(t for t in tools if t["name"] == "preview_url")
    assert preview["annotations"]["readOnlyHint"] is True
    assert preview["inputSchema"]["required"] == ["url"]
    assert "handler" not in preview
    assert mcp("ping")["result"] == {}


def test_protocol_errors(mcp):
    assert mcp("nope")["error"]["code"] == -32601
    unknown = mcp("tools/call", {"name": "nope"})
    assert unknown["error"]["code"] == -32602
    client = mcp.client
    bad = client.post("/api/mcp", {"id": 1}, format="json").json()
    assert bad["error"]["code"] == -32600
    note = client.post(
        "/api/mcp",
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        format="json",
    )
    assert note.status_code == 202
    batch = client.post(
        "/api/mcp",
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ],
        format="json",
    )
    assert batch.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]
    only_notes = client.post(
        "/api/mcp",
        [{"jsonrpc": "2.0", "method": "notifications/initialized"}],
        format="json",
    )
    assert only_notes.status_code == 202
    assert client.get("/api/mcp").status_code == 405
    assert (
        client.post(
            "/api/mcp/",
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            format="json",
        ).status_code
        == 200
    )


def test_auth(mcp):
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    anonymous = APIClient(SERVER_NAME="localhost")
    assert anonymous.post("/api/mcp", body, format="json").status_code in (
        401,
        403,
    )
    token = APIClient(SERVER_NAME="localhost")
    token.credentials(HTTP_AUTHORIZATION=f"Token {mcp.token}")
    assert token.post("/api/mcp", body, format="json").status_code == 200

    viewer = Account.objects.create_user("viewer", "pw")
    user = APIClient(SERVER_NAME="localhost")
    user.credentials(
        HTTP_AUTHORIZATION=f"Bearer {Token.objects.create(user=viewer).key}"
    )
    assert user.post("/api/mcp", body, format="json").status_code == 403

    evil = mcp.client.post(
        "/api/mcp", body, format="json", HTTP_ORIGIN="https://evil.example"
    )
    assert evil.status_code == 403


def test_add_link_download_over_an_hour(
    mcp, youtube, yt, no_broker, monkeypatch
):
    """
    'add this link and download anything over an hour':
    preview_url, add_url(mode=once, rule), then activity
    """
    # pylint: disable=unused-argument,too-many-locals
    metas, listings = youtube
    metas[CHANNEL_ID] = channel_meta()
    videos = [
        flat_entry("talk", duration=5400),
        flat_entry("clip", duration=300),
        flat_entry("vlog", duration=1200),
    ]
    listings[CHANNEL_ID] = [{**v, "vid_type": "videos"} for v in videos]
    yt.channel_videos = videos
    over_an_hour = {"min_duration": 3600}

    preview = mcp.tool("preview_url", url=CHANNEL_ID, rule=over_an_hour)
    verdict = preview["structuredContent"]["items"][0]["verdict"]
    assert (verdict["total"], verdict["download"], verdict["skipped"]) == (
        3,
        1,
        2,
    )
    assert verdict["reasons"] == [
        {"key": "too_short", "label": "<1 h", "count": 2}
    ]

    added = mcp.tool(
        "add_url", url=CHANNEL_ID, mode="once", rule=over_an_hour
    )["structuredContent"]
    check = added["jobs"][0]
    assert (check["type"], check["created_by"]) == ("check_channel", "agent")
    assert check["priority"] == 100

    # the worker runs the check: only the long video gets a download job
    Job.objects.filter(pk=check["id"]).update(status=JobStatus.RUNNING)
    monkeypatch.setattr(
        "jobs.src.handlers.AppConfig",
        type(
            "Config",
            (),
            {"config": property(lambda self: _flat_config())},
        ),
    )
    assert execute(check["id"]) == JobStatus.DONE
    downloads = Job.objects.filter(type="download_video")
    assert [d.target_id for d in downloads] == ["talk"]
    assert Download.objects.get(pk="clip").status == "ignore"

    activity = mcp.tool("activity")["structuredContent"]
    waiting = activity["running"] + activity["next"]
    assert [j["target"]["id"] for j in waiting] == ["talk"]
    assert activity["summary"]["queued"] + activity["summary"]["running"] >= 1

    class Done:
        """the download succeeds"""

        def __init__(self, task):
            self.task = task

        def download_one(self, video_data):
            Download.objects.filter(pk=video_data["youtube_id"]).delete()
            return True

    monkeypatch.setattr(handlers, "VideoDownloader", Done)
    talk = downloads.get()
    Job.objects.filter(pk=talk.pk).update(status=JobStatus.RUNNING)
    assert execute(talk.pk) == JobStatus.DONE
    history = mcp.tool("list_queue", status="history")["structuredContent"]
    assert {(j["type"], j["status"]) for j in history["jobs"]} == {
        ("check_channel", "done"),
        ("download_video", "done"),
    }


def _flat_config():
    # pylint: disable=import-outside-toplevel
    import copy

    from appsettings.src.config import AppConfig

    config = copy.deepcopy(AppConfig.CONFIG_DEFAULTS)
    config["subscriptions"]["extract_flat"] = True
    return config


def test_tool_errors(mcp, app_config):
    # pylint: disable=unused-argument
    cases = [
        ("preview_url", {}, "give url"),
        ("add_url", {"url": "x", "mode": "odd"}, "mode is"),
        ("add_url", {"url": "x", "mode": "newest_n"}, "needs newest_n"),
        ("add_url", {"url": "x", "numbering": "odd"}, "numbering is"),
        ("add_url", {}, "give url"),
        ("add_url", {"url": "x", "presets": ["odd"]}, "unknown preset"),
        ("add_url", {"url": "x", "rule": {"min_duration": -1}}, "invalid"),
        ("set_rule", {"scope": "video"}, "give target_id"),
        (
            "set_rule",
            {"scope": "video", "target_id": "v", "override": "odd"},
            "override is",
        ),
        ("set_rule", {"scope": "odd"}, "scope is"),
        ("set_rule", {"scope": "channel", "target_id": "UCx"}, "not in"),
        ("set_rule", {"scope": "default"}, "give rule"),
        (
            "set_rule",
            {"scope": "default", "auto_download": True},
            "per channel or playlist",
        ),
        (
            "set_rule",
            {"scope": "video", "target_id": "v", "auto_download": False},
            "per channel or playlist",
        ),
        ("set_numbering", {"kind": "odd"}, "give kind"),
        ("set_numbering", {"kind": "channel", "id": "x"}, "scheme is"),
        (
            "set_numbering",
            {"kind": "channel", "id": "UCx", "scheme": "date"},
            "not in the archive",
        ),
        ("list_queue", {"status": "odd"}, "unknown status"),
        ("list_found", {"tab": "odd"}, "tab is"),
        ("download", {}, "give ids"),
        ("move_to_top", {}, "give ids"),
        ("stop_task", {"id": 999}, "no job"),
        ("search_library", {"query": " "}, "give query"),
        ("get_video", {"youtube_id": "nope"}, "neither"),
        ("notify_media_server", {"all": True}, "no media server"),
    ]
    for name, args, message in cases:
        result = mcp.tool(name, **args)
        assert result["isError"], (name, args)
        assert message in result["content"][0]["text"], (name, result)


def test_rules_and_numbering_tools(mcp, app_config, monkeypatch):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    Video.save_doc(video_doc(youtube_id="vid1"))
    Download.upsert_doc(download_doc(youtube_id="f1"))

    default = mcp.tool(
        "set_rule",
        scope="default",
        presets=["no_shorts"],
        rule={"max_duration": 7200},
    )["structuredContent"]["rule"]
    assert default["summary"] == "No Shorts · <2 h"
    channel = mcp.tool(
        "set_rule", scope="channel", target_id=CHANNEL_ID, rule=None
    )["structuredContent"]
    assert channel == {"rule": None}
    Playlist.save_doc(playlist_doc("PL1", []))
    playlist = mcp.tool(
        "set_rule", scope="playlist", target_id="PL1", rule={"min_duration": 1}
    )["structuredContent"]
    assert playlist["rule"]["min_duration"] == 1

    anyway = mcp.tool(
        "set_rule", scope="video", target_id="f1", override="download"
    )["structuredContent"]
    assert anyway["override"] == "download" and anyway["job_ids"]
    cleared = mcp.tool(
        "set_rule", scope="video", target_id="f1", override=None
    )["structuredContent"]
    assert cleared == {"deleted": True}
    assert not VideoOverride.objects.exists()

    dry = mcp.tool(
        "set_numbering",
        kind="channel",
        id=CHANNEL_ID,
        scheme="absolute_frozen",
        dry_run=True,
    )["structuredContent"]
    assert dry["renumbers"] == 1
    assert Channel.objects.get().emby_numbering == "date"
    done = mcp.tool(
        "set_numbering",
        kind="channel",
        id=CHANNEL_ID,
        scheme="absolute_frozen",
    )["structuredContent"]
    assert done["renumbered"] == 1
    wrong = mcp.tool(
        "set_numbering",
        kind="channel",
        id=CHANNEL_ID,
        scheme="playlist_position",
    )
    assert wrong["isError"]


def test_queue_and_found_tools(mcp, app_config):
    # pylint: disable=unused-argument
    Download.upsert_doc(download_doc(youtube_id="a", title="Alpha"))
    Download.upsert_doc(download_doc(youtube_id="b", title="Beta"))

    found = mcp.tool("list_found", limit=1)["structuredContent"]
    assert (found["total"], found["has_more"], len(found["videos"])) == (
        2,
        True,
        1,
    )
    queued = mcp.tool("download", ids=["a", "b"], now=False)[
        "structuredContent"
    ]
    assert queued["changed"] == 2
    jobs = mcp.tool("list_queue", limit=1)["structuredContent"]
    assert (jobs["total"], jobs["has_more"]) == (2, True)

    second = Job.objects.get(target_id="b")
    moved = mcp.tool("move_to_top", ids=["b", 99999])["structuredContent"]
    assert moved["moved"] == [second.pk]
    assert "99999" in moved["skipped"]

    stopped = mcp.tool("stop_task", id=second.pk)["structuredContent"]
    assert stopped["status"] == "cancelled"
    ended = mcp.tool("stop_task", id=second.pk)
    assert ended["isError"] and "already ended" in ended["content"][0]["text"]
    running = Job.objects.get(target_id="a")
    Job.objects.filter(pk=running.pk).update(status=JobStatus.RUNNING)
    stopping = mcp.tool("stop_task", id=running.pk)["structuredContent"]
    assert stopping["status"] == "running" and stopping["actions"] == []

    ignored = mcp.tool("ignore", ids=["a"], always=True)["structuredContent"]
    assert ignored["changed"] == 1
    plain = mcp.tool("ignore", ids=["b"])["structuredContent"]
    assert plain["changed"] == 1
    item = mcp.tool("get_video", youtube_id="a")["structuredContent"]
    assert item["verdict"]["override"] == "ignore"

    failed = enqueue("backup", dispatch=False)[0]
    Job.objects.filter(pk=failed.pk).update(
        status=JobStatus.FAILED,
        error="disk full",
        finished_at=failed.created_at,
    )
    activity = mcp.tool("activity")["structuredContent"]
    assert activity["recent_errors"][0]["error"] == "disk full"


def test_library_tools(mcp, app_config, monkeypatch, tmp_path):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc(channel_name="Coaster Channel"))
    Video.save_doc(video_doc(youtube_id="vid1", title="Riding the coaster"))

    results = mcp.tool("search_library", query="coaster")["structuredContent"]
    assert results["videos"][0]["youtube_id"] == "vid1"
    assert results["channels"][0]["name"] == "Coaster Channel"
    assert results["playlists"] == []

    video = mcp.tool("get_video", youtube_id="vid1")["structuredContent"]
    assert (video["state"], video["channel_id"]) == ("downloaded", CHANNEL_ID)
    assert video["numbering"][0]["series_kind"] == "channel"

    # pylint: disable=import-outside-toplevel
    from common.src.env_settings import EnvironmentSettings

    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(EnvironmentSettings, "EMBY_ROOT", "/youtube/.emby")
    ms_settings.update(
        {"type": "emby", "url": "http://emby.test:8096", "api_key": "k"}
    )
    outside = mcp.tool("notify_media_server", paths=["/etc"])
    assert (
        outside["isError"] and "TA_EMBY_ROOT" in outside["content"][0]["text"]
    )
    queued = mcp.tool(
        "notify_media_server", paths=["/youtube/.emby/channels"]
    )["structuredContent"]
    assert queued["type"] == "media_notify"
    full = mcp.tool("notify_media_server", all=True)["structuredContent"]
    assert full["type"] == "media_refresh"


def test_default_rule_tool_unchanged_rule_count(mcp, app_config):
    # pylint: disable=unused-argument
    mcp.tool("set_rule", scope="default", rule=None)
    assert not DownloadRule.objects.exists()


def test_preview_without_any_rule(mcp, youtube):
    metas, listings = youtube
    metas[CHANNEL_ID] = channel_meta()
    listings[CHANNEL_ID] = [
        {**flat_entry("short", duration=10), "vid_type": "shorts"}
    ]
    DownloadRule.objects.create(channel=None, vid_types=["videos"])

    stored = mcp.tool("preview_url", url=CHANNEL_ID)["structuredContent"]
    assert stored["items"][0]["verdict"]["skipped"] == 1
    none = mcp.tool("preview_url", url=CHANNEL_ID, rule=None)
    assert none["structuredContent"]["items"][0]["verdict"]["download"] == 1


def test_auto_download_tools(mcp, app_config, no_broker):
    """set_rule switches auto download; add_url can opt in on subscribe"""
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    Playlist.save_doc(playlist_doc("PL1", []))

    only = mcp.tool(
        "set_rule", scope="channel", target_id=CHANNEL_ID, auto_download=True
    )["structuredContent"]
    assert only == {"auto_download": True}
    assert Channel.objects.get().auto_download is True
    both = mcp.tool(
        "set_rule",
        scope="playlist",
        target_id="PL1",
        presets=["no_shorts"],
        auto_download=True,
    )["structuredContent"]
    assert both["auto_download"] is True
    assert both["rule"]["summary"] == "No Shorts"
    assert Playlist.objects.get().auto_download is True
    mcp.tool(
        "set_rule", scope="channel", target_id=CHANNEL_ID, auto_download=False
    )
    assert Channel.objects.get().auto_download is False

    added = mcp.tool(
        "add_url", url=CHANNEL_ID, mode="subscribe", auto_download=True
    )["structuredContent"]
    job = Job.objects.get(pk=added["jobs"][0]["id"])
    assert job.params["auto_download"] is True
    assert job.params["subscribe"] is True
    tools = {t["name"]: t for t in mcp("tools/list")["result"]["tools"]}
    for name in ("set_rule", "add_url"):
        schema = tools[name]["inputSchema"]["properties"]
        assert schema["auto_download"]["type"] == "boolean"


def test_set_emby_tags(mcp, app_config):
    # pylint: disable=unused-argument
    Channel.save_doc(channel_doc())
    done = mcp.tool(
        "set_emby_tags", kind="channel", id=CHANNEL_ID, tags=["David", "david"]
    )["structuredContent"]
    assert done == {
        "kind": "channel",
        "id": CHANNEL_ID,
        "emby_tags": ["David"],
    }
    assert Channel.objects.get().emby_tags == ["David"]
    assert (
        mcp.tool("set_emby_tags", kind="channel", id=CHANNEL_ID, tags=[])[
            "structuredContent"
        ]["emby_tags"]
        == []
    )

    for args, message in [
        ({"kind": "odd", "id": "x", "tags": []}, "give kind"),
        ({"kind": "channel", "id": "x", "tags": "David"}, "give tags"),
        ({"kind": "channel", "id": "nope", "tags": []}, "not in the archive"),
    ]:
        result = mcp.tool("set_emby_tags", **args)
        assert result["isError"] and message in str(result)

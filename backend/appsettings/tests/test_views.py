"""tests for the appsettings API, celery, cookies and network mocked"""

import json
import zipfile

import pytest
from appsettings import views, views_mb
from appsettings.src import snapshot as snapshot_module
from appsettings.src.backup import DatabaseBackup
from appsettings.src.config import AppConfig
from appsettings.src.membership import Membership
from appsettings.src.snapshot import DbSnapshot
from common.src.ta_redis import RedisArchivist
from django.urls import reverse
from requests.exceptions import JSONDecodeError
from rest_framework import serializers
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from user.models import Account

pytestmark = pytest.mark.django_db


@pytest.fixture(name="admin")
def fixture_admin(app_config, redis_clean):
    """api client logged in as staff"""
    # pylint: disable=unused-argument
    user = Account.objects.create_superuser("admin", "pw")
    client = APIClient()
    client.force_authenticate(user=user)
    client.user = user
    return client


@pytest.fixture(name="viewer")
def fixture_viewer(app_config):
    """api client logged in as a regular user"""
    # pylint: disable=unused-argument
    client = APIClient()
    client.force_authenticate(user=Account.objects.create_user("v", "pw"))
    return client


@pytest.fixture(name="started")
def fixture_started(monkeypatch):
    """record TaskCommand.start and celery .delay calls"""
    calls = []

    def fake_start(self, task_name, kwargs=None):
        # pylint: disable=unused-argument
        calls.append((task_name, kwargs))
        return {"task_id": "t-1", "status": "PENDING", "task_name": task_name}

    class FakeAsync:
        """celery AsyncResult stand in"""

        id = "t-2"

    def fake_delay(filename):
        calls.append(("restore_backup", filename))
        return FakeAsync()

    monkeypatch.setattr(views.TaskCommand, "start", fake_start)
    monkeypatch.setattr(views.run_restore_backup, "delay", fake_delay)
    return calls


# permissions


def test_anonymous_rejected(db):
    # pylint: disable=unused-argument
    response = APIClient().get(reverse("api-config"))
    assert response.status_code == 403


def test_viewer_reads_config_but_not_admin_views(viewer):
    assert viewer.get(reverse("api-config")).status_code == 200
    assert viewer.post(reverse("api-config"), {}).status_code == 403
    assert viewer.get(reverse("api-backup-list")).status_code == 403


# config


def test_config_get_and_update(admin):
    response = admin.get(reverse("api-config"))
    assert response.json() == AppConfig.CONFIG_DEFAULTS

    response = admin.post(
        reverse("api-config"),
        {
            "downloads": {
                **AppConfig.CONFIG_DEFAULTS["downloads"],
                "limit_speed": 9,
            }
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["downloads"]["limit_speed"] == 9
    assert AppConfig().config["downloads"]["limit_speed"] == 9


def test_config_partial_update_of_section(admin):
    response = admin.post(
        reverse("api-config"),
        {"subscriptions": {"channel_size": 5}},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["subscriptions"]["channel_size"] == 5
    assert response.json()["subscriptions"]["playlist_size"] == 50


def test_config_rejects_unknown(admin):
    response = admin.post(
        reverse("api-config"), {"bogus": {"x": 1}}, format="json"
    )
    assert response.status_code == 400
    assert "Unknown fields" in json.dumps(response.json())


# backups


@pytest.fixture(name="backup_dir")
def fixture_backup_dir(tmp_path, monkeypatch):
    """scratch backup dir with one zip"""
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    with zipfile.ZipFile(backup_dir / "ta_backup-20260101-auto.zip", "w"):
        pass
    monkeypatch.setattr(DatabaseBackup, "BACKUP_DIR", str(backup_dir))
    return backup_dir


def test_backup_list_and_start(admin, backup_dir, started):
    # pylint: disable=unused-argument
    response = admin.get(reverse("api-backup-list"))
    assert [f["reason"] for f in response.json()] == ["auto"]

    response = admin.post(reverse("api-backup-list"))
    assert response.json() == {
        "message": "backup task started",
        "task_id": "t-1",
    }
    assert started == [("run_backup", None)]


def test_backup_single(admin, backup_dir, started):
    url = reverse("api-backup", args=["ta_backup-20260101-auto.zip"])
    missing = reverse("api-backup", args=["ta_backup-20990101-auto.zip"])

    assert admin.get(url).json()["timestamp"] == "20260101"
    assert admin.get(missing).status_code == 404
    assert admin.post(missing).json() == {"error": "file not found"}

    response = admin.post(url)
    assert response.json() == {
        "message": "backup restore task started",
        "filename": "ta_backup-20260101-auto.zip",
        "task_id": "t-2",
    }
    assert started == [("restore_backup", "ta_backup-20260101-auto.zip")]

    assert admin.delete(url).status_code == 204
    assert not list(backup_dir.iterdir())
    assert admin.delete(url).status_code == 404


# cookie


@pytest.fixture(name="cookie")
def fixture_cookie(monkeypatch):
    """fake CookieHandler writing validation like the real one"""
    state = {"valid": True, "calls": []}

    class FakeCookieHandler:
        """cookie handler stand in"""

        def __init__(self, config):
            assert "downloads" in config

        @staticmethod
        def set_cookie(cookie):
            """record"""
            state["calls"].append(("set", cookie))

        @staticmethod
        def validate():
            """store a validation result"""
            state["calls"].append(("validate",))
            RedisArchivist().set_message(
                "cookie:valid",
                {
                    "status": state["valid"],
                    "validated": 1,
                    "validated_str": "x",
                },
            )
            return state["valid"]

        @staticmethod
        def revoke():
            """record"""
            state["calls"].append(("revoke",))

    monkeypatch.setattr(views, "CookieHandler", FakeCookieHandler)
    return state


def test_cookie_get(admin, cookie):
    # pylint: disable=unused-argument
    assert admin.get(reverse("api-cookie")).json() == {"cookie_enabled": False}


def test_cookie_validate(admin, cookie):
    response = admin.post(reverse("api-cookie"))

    assert response.json()["status"] is True
    assert cookie["calls"] == [("validate",)]


def test_cookie_put_valid(admin, cookie, settings):
    settings.DEBUG = True

    response = admin.put(
        reverse("api-cookie"), {"cookie": "# Netscape"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["validated"] == 1
    assert cookie["calls"] == [("set", "# Netscape"), ("validate",)]


def test_cookie_put_invalid(admin, cookie):
    cookie["valid"] = False

    response = admin.put(
        reverse("api-cookie"), {"cookie": "bad"}, format="json"
    )

    assert response.status_code == 400
    assert response.json() == {"error": "[cookie]: import failed, not valid"}
    assert cookie["calls"][-1] == ("revoke",)


def test_cookie_put_missing(admin, cookie, monkeypatch):
    # pylint: disable=unused-argument
    assert (
        admin.put(reverse("api-cookie"), {}, format="json").status_code == 400
    )

    class EmptyCookie(views.CookieUpdateSerializer):
        """serializer that lets an empty cookie through"""

        cookie = serializers.CharField(allow_blank=True)

    monkeypatch.setattr(views, "CookieUpdateSerializer", EmptyCookie)
    response = admin.put(reverse("api-cookie"), {"cookie": ""}, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": "missing cookie key in request data"}


def test_cookie_delete(admin, cookie):
    assert admin.delete(reverse("api-cookie")).status_code == 204
    assert cookie["calls"] == [("revoke",)]


# snapshots


@pytest.fixture(name="snapshots")
def fixture_snapshots(tmp_path, monkeypatch):
    """DbSnapshot on a scratch dir, pg tools faked"""
    snapshot_dir = tmp_path / "snapshots"
    monkeypatch.setattr(views, "DbSnapshot", lambda: DbSnapshot(snapshot_dir))
    runs = []

    class Done:
        """successful process"""

        returncode = 0

    def fake_run(args, **kwargs):
        # pylint: disable=unused-argument
        runs.append(args)
        if args[0] == "pg_dump":
            with open(args[2].removeprefix("--file="), "wb") as f:
                f.write(b"PGDMP")
        return Done()

    monkeypatch.setattr(snapshot_module.subprocess, "run", fake_run)
    return runs


def test_snapshot_views(admin, snapshots):
    response = admin.get(reverse("api-snapshot-list"))
    assert response.json()["snapshots"] == []

    name = admin.post(reverse("api-snapshot-list")).json()["snapshot_name"]
    url = reverse("api-snapshot", args=[name])

    listed = admin.get(reverse("api-snapshot-list")).json()["snapshots"]
    assert [s["id"] for s in listed] == [name]
    assert admin.get(url).json()["id"] == name
    assert admin.post(url).json() == {"accepted": True}
    assert snapshots[-1][0] == "pg_restore"
    assert admin.delete(url).status_code == 204
    assert admin.get(url).status_code == 404
    assert admin.post(url).json() == {"error": "failed to restore snapshot"}
    assert admin.delete(url).json() == {"error": "failed to delete snapshot"}


# tasks


@pytest.mark.parametrize(
    "url_name, task_name",
    [
        ("api-rescan-filesystem", "rescan_filesystem"),
        ("api-manual-import", "manual_import"),
    ],
)
def test_task_views(admin, started, url_name, task_name):
    body = {"ignore_error": True, "prefer_local": False}

    response = admin.post(reverse(url_name), body, format="json")

    assert response.json() == {
        "task_id": "t-1",
        "status": "PENDING",
    }
    assert started == [(task_name, body)]
    assert admin.post(reverse(url_name), {}, format="json").status_code == 400


# api token


def test_token(admin):
    token = admin.get(reverse("api-token")).json()["token"]

    assert Token.objects.get(user=admin.user).key == token
    assert admin.delete(reverse("api-token")).status_code == 204
    assert not Token.objects.exists()
    assert admin.delete(reverse("api-token")).status_code == 204


# membership


class FakeResponse:
    """requests response stand in"""

    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self.data = data

    def json(self):
        """payload or decode error"""
        if self.data is None:
            raise JSONDecodeError("Expecting value", "", 0)
        return self.data


PROFILE = {
    "id": 1,
    "user": {"id": 2, "username": "matt"},
    "sponsor_tier": {
        "tier_id": 3,
        "name": "gold",
        "description": "shiny",
        "max_subs": 100,
    },
    "subscription_count": 4,
    "subscription_is_max": False,
    "is_connected": True,
}


@pytest.fixture(name="remote")
def fixture_remote(monkeypatch):
    """set the response of the membership server"""
    state = {}

    def fake_get_profile(self):
        # pylint: disable=unused-argument
        self._get_headers()  # pylint: disable=protected-access
        return state["response"]

    def fake_sync(self):
        # pylint: disable=unused-argument
        return state["response"]

    monkeypatch.setattr(Membership, "get_profile", fake_get_profile)
    monkeypatch.setattr(Membership, "sync_subs", fake_sync)
    return state


def test_membership_token(admin, monkeypatch):
    saves = []
    monkeypatch.setattr(
        views_mb.RedisArchivist, "bg_save", lambda self: saves.append(1)
    )
    url = reverse("api-membership-token")

    assert admin.get(url).json() == {"token": None}
    assert admin.post(url, {"token": "abc"}, format="json").json() == {
        "token": "abc"
    }
    assert saves == [1]
    assert admin.get(url).json() == {"token": "abc"}
    assert admin.delete(url).status_code == 204
    assert admin.get(url).json() == {"token": None}


def test_membership_profile_without_token(admin, remote):
    # pylint: disable=unused-argument
    response = admin.get(reverse("api-membership-profile"))

    assert response.status_code == 400
    assert response.json() == {"message": "expected MB_API_KEY"}


@pytest.mark.parametrize(
    "response, status, body",
    [
        (FakeResponse(200, PROFILE), 200, PROFILE),
        (
            FakeResponse(502),
            400,
            {"message": "Connection to remote server failed: 502"},
        ),
        (
            FakeResponse(403, {"detail": "bad token"}),
            400,
            {"message": "bad token"},
        ),
        (FakeResponse(403, {}), 400, {"message": "undefined error"}),
    ],
)
def test_membership_profile(admin, remote, response, status, body):
    RedisArchivist().set_message(Membership.REDIS_KEY, {"token": "abc"})
    remote["response"] = response

    result = admin.get(reverse("api-membership-profile"))

    assert result.status_code == status
    assert result.json() == body


@pytest.mark.parametrize(
    "response, status, body",
    [
        (FakeResponse(200, {}), 204, None),
        (FakeResponse(400, {"detail": "too many"}), 400, "too many"),
        (FakeResponse(400, {}), 400, "undefined error"),
        (
            FakeResponse(500),
            400,
            "Connection to remote server failed: 500",
        ),
    ],
)
def test_membership_sync(admin, remote, response, status, body):
    remote["response"] = response

    result = admin.post(reverse("api-membership-sync"))

    assert result.status_code == status
    if body:
        assert result.json() == {"message": body}

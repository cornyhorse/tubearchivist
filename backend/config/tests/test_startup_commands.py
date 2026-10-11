"""startup management commands: connection, envcheck, startup, misc"""

import io
import sqlite3
from types import SimpleNamespace

import pytest
from appsettings.models import ConfigDocument
from appsettings.src.config import AppConfig, ReleaseVersion
from appsettings.src.snapshot import DbSnapshot
from common.src.env_settings import EnvironmentSettings
from common.src.ta_redis import RedisArchivist, TaskRedis
from config.management.commands import (
    ta_connection,
    ta_envcheck,
    ta_startup,
    ta_stop_on_error,
)
from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import OperationalError
from django_celery_beat.models import CrontabSchedule
from jobs.models import Job
from jobs.src.engine import enqueue
from task.models import CustomPeriodicTask
from user.models import Account


def run(*args, **kwargs) -> str:
    """call a command, return its stdout"""
    out = io.StringIO()
    call_command(*args, stdout=out, **kwargs)
    return out.getvalue()


@pytest.fixture(name="slept")
def fixture_slept(monkeypatch):
    """no real sleeping in retry loops, record instead"""
    slept = []
    for module in (ta_connection, ta_envcheck, ta_stop_on_error):
        monkeypatch.setattr(module, "sleep", slept.append)
    return slept


# ta_connection


class FakeRedisConn:
    """redis connection with scripted PING replies and config"""

    def __init__(self, pings, timeout="0"):
        self.pings = list(pings)
        self.timeout = timeout
        self.config = {}

    def execute_command(self, command):
        """PING"""
        assert command == "PING"
        reply = self.pings.pop(0) if self.pings else True
        if isinstance(reply, Exception):
            raise reply

        return reply

    def config_get(self, key):
        """read config"""
        return {key: self.timeout}

    def config_set(self, key, value):
        """record config"""
        self.config[key] = value


def fake_redis(monkeypatch, conn):
    """RedisArchivist in ta_connection returns conn"""
    monkeypatch.setattr(
        ta_connection, "RedisArchivist", lambda: SimpleNamespace(conn=conn)
    )


class FakeConnection:
    """django db connection stand in"""

    def __init__(self, failures=0, pg_version=170002):
        self.failures = failures
        self.pg_version = pg_version
        self.closed = 0

    def ensure_connection(self):
        """fail a number of times"""
        if self.failures:
            self.failures -= 1
            raise OperationalError("connection refused")

    def close(self):
        """count closes"""
        self.closed += 1


def command():
    """connection command writing to a buffer"""
    cmd = ta_connection.Command(stdout=io.StringIO())
    return cmd, cmd.stdout


@pytest.mark.django_db
def test_connection_handle(monkeypatch, slept):
    conn = FakeRedisConn([True], timeout="0")
    fake_redis(monkeypatch, conn)
    monkeypatch.setattr(ta_connection.shutil, "which", lambda name: None)

    output = run("ta_connection")

    assert "✓ Redis connection verified" in output
    assert conn.config == {"timeout": 3600}
    assert "✓ Postgres connection established" in output
    assert "check passed" in output
    assert "pg_dump missing can't dump Postgres" in output
    assert not slept


def test_redis_real_ping():
    cmd, out = command()

    cmd._redis_connection_check()

    assert "✓ Redis connection verified" in out.getvalue()


def test_redis_config_already_set(monkeypatch):
    conn = FakeRedisConn([], timeout="300")
    fake_redis(monkeypatch, conn)
    cmd, _ = command()

    cmd._redis_config_set()

    assert not conn.config


def test_redis_retry_then_ok(monkeypatch, slept):
    fake_redis(monkeypatch, FakeRedisConn([ConnectionError(), None, True]))
    cmd, out = command()

    cmd._redis_connection_check()

    assert "retry Redis connection" in out.getvalue()
    assert slept == [2]


def test_redis_fails(monkeypatch, slept):
    error = ConnectionError("refused")
    fake_redis(monkeypatch, FakeRedisConn([error] * 6))
    cmd, out = command()

    with pytest.raises(CommandError, match="ConnectionError: refused"):
        cmd._redis_connection_check()

    assert slept == [2] * 5 + [60]
    assert "Redis connection failed" in out.getvalue()


def test_redis_never_answers(monkeypatch, slept):
    fake_redis(monkeypatch, FakeRedisConn([None] * 5))
    cmd, _ = command()

    with pytest.raises(CommandError, match="Redis connection failed"):
        cmd._redis_connection_check()

    assert slept == [60]


def test_db_retry_then_ok(monkeypatch, slept):
    connection = FakeConnection(failures=2)
    monkeypatch.setattr(ta_connection, "connection", connection)
    cmd, out = command()

    cmd._db_connection_check()

    assert slept == [5, 5]
    assert connection.closed == 2
    assert "waiting for Postgres [1/24]" in out.getvalue()


def test_db_fails(monkeypatch, slept):
    monkeypatch.setattr(ta_connection, "connection", FakeConnection(99))
    cmd, out = command()

    with pytest.raises(CommandError, match="Postgres connection failed"):
        cmd._db_connection_check()

    assert slept == [5] * 24 + [60]
    assert "error message: connection refused" in out.getvalue()


def test_db_version_too_old(monkeypatch, slept):
    connection = FakeConnection(pg_version=140010)
    monkeypatch.setattr(ta_connection, "connection", connection)
    cmd, _ = command()

    with pytest.raises(CommandError, match="Expected 15 or later but got 14"):
        cmd._db_version_check()

    assert slept == [60]


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("pg_dump (PostgreSQL) 17.4\n", "✓ pg_dump 17 can dump Postgres 17"),
        ("pg_dump (PostgreSQL) 16.1\n", "pg_dump 16 can't dump Postgres 17"),
        ("garbage", "pg_dump missing can't dump Postgres 17"),
    ],
)
def test_pg_dump_check(monkeypatch, stdout, expected):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(ta_connection, "connection", FakeConnection())
    monkeypatch.setattr(ta_connection.shutil, "which", lambda name: "/x/pg")
    monkeypatch.setattr(ta_connection.subprocess, "run", fake_run)
    cmd, out = command()

    cmd._pg_dump_check()

    assert calls == [["/x/pg", "--version"]]
    assert expected in out.getvalue()


# ta_envcheck

NGINX_CONF = """server {
    listen 8000;
    location /cache/videos/ {
        auth_request /api/ping/;
    }
    location /api {
        proxy_pass http://localhost:8080;
    }
}
"""


@pytest.fixture(name="envcheck")
def fixture_envcheck(monkeypatch, tmp_path, slept):
    """
    env check against a tmp nginx file, fresh module level var lists
    and a minimal valid environment
    """
    # pylint: disable=unused-argument
    nginx = tmp_path / "nginx.conf"
    nginx.write_text(NGINX_CONF)
    monkeypatch.setattr(ta_envcheck, "NGINX", str(nginx))
    monkeypatch.setattr(
        ta_envcheck, "EXPECTED_ENV_VARS", list(ta_envcheck.EXPECTED_ENV_VARS)
    )
    monkeypatch.setattr(
        ta_envcheck,
        "UNEXPECTED_ENV_VARS",
        dict(ta_envcheck.UNEXPECTED_ENV_VARS),
    )
    removed = list(ta_envcheck.UNEXPECTED_ENV_VARS)
    removed += ta_envcheck.OBSOLETE_ENV_VARS
    removed += ["TA_LOGIN_AUTH_MODE", "TA_LDAP", "TA_ENABLE_AUTH_PROXY"]
    for var in removed:
        monkeypatch.delenv(var, raising=False)
    for var in ta_envcheck.EXPECTED_ENV_VARS:
        monkeypatch.setenv(var, "set")
    for var in ("TA_PORT", "TA_BACKEND_PORT", "DISABLE_STATIC_AUTH"):
        monkeypatch.setattr(EnvironmentSettings, var, False)

    return SimpleNamespace(nginx=nginx, env=monkeypatch, slept=slept)


@pytest.mark.django_db
def test_envcheck_defaults(envcheck):
    output = run("ta_envcheck")
    again = run("ta_envcheck")

    assert "✓ all expected env vars are set" in output
    assert "✓ no unexpected env vars found" in output
    assert f"✓ database {EnvironmentSettings.TA_DB_NAME} at" in output
    assert "TA_PORT is not set" in output
    assert "TA_BACKEND_PORT is not set" in output
    assert "DISABLE_STATIC_AUTH is not set" in output
    assert "✓ new superuser with name" in output
    assert "superuser already created" in again
    assert Account.objects.get(is_superuser=True).name == (
        EnvironmentSettings.TA_USERNAME
    )
    assert envcheck.nginx.read_text() == NGINX_CONF


@pytest.mark.django_db
def test_envcheck_nginx_overwrites(envcheck):
    envcheck.env.setattr(EnvironmentSettings, "TA_PORT", 9000)
    envcheck.env.setattr(EnvironmentSettings, "TA_BACKEND_PORT", 9090)
    envcheck.env.setattr(EnvironmentSettings, "DISABLE_STATIC_AUTH", True)
    envcheck.env.setenv("ES_URL", "http://es:9200")

    first = run("ta_envcheck")
    second = run("ta_envcheck")

    conf = envcheck.nginx.read_text()
    assert "listen 9000;" in conf
    assert "proxy_pass http://localhost:9090;" in conf
    assert "auth_request" not in conf
    assert "✓ TA_PORT changed to 9000" in first
    assert "✓ TA_BACKEND_PORT changed to 9090" in first
    assert "✓ process nginx to disable static auth" in first
    assert "ES_URL is set but unused" in first
    assert "✓ TA_PORT already set to 9000" in second
    assert "✓ TA_BACKEND_PORT already set to 9090" in second
    assert "✓ static auth is already disabled in nginx" in second


def test_envcheck_missing_var(envcheck):
    envcheck.env.delenv("TA_HOST")
    cmd = ta_envcheck.Command(stdout=io.StringIO())

    with pytest.raises(CommandError, match="expected env var TA_HOST"):
        cmd._expected_vars()

    assert envcheck.slept == [60]


def test_envcheck_password_file(envcheck):
    envcheck.env.delenv("TA_PASSWORD")
    envcheck.env.setenv("TA_PASSWORD_FILE", "/run/secrets/ta")
    cmd = ta_envcheck.Command(stdout=io.StringIO())

    cmd._expected_vars()

    assert "all expected env vars are set" in cmd.stdout.getvalue()


def test_envcheck_unexpected_var(envcheck):
    envcheck.env.setenv("REDIS_HOST", "redis")
    cmd = ta_envcheck.Command(stdout=io.StringIO())

    with pytest.raises(CommandError, match="unexpected env var REDIS_HOST"):
        cmd._unexpected_vars()

    assert envcheck.slept == [60]


@pytest.mark.parametrize(
    "variables, ldap_expected, unexpected",
    [
        ({}, False, set()),
        (
            {"TA_LOGIN_AUTH_MODE": "local"},
            False,
            {"TA_LDAP", "TA_ENABLE_AUTH_PROXY"},
        ),
        ({"TA_LOGIN_AUTH_MODE": "ldap"}, True, {"TA_ENABLE_AUTH_PROXY"}),
        ({"TA_LOGIN_AUTH_MODE": "forwardauth"}, False, {"TA_LDAP"}),
        (
            {"TA_LOGIN_AUTH_MODE": "ldap_local"},
            True,
            {"TA_ENABLE_AUTH_PROXY"},
        ),
        ({"TA_LDAP": "1"}, True, {"TA_ENABLE_AUTH_PROXY"}),
        ({"TA_ENABLE_AUTH_PROXY": "1"}, False, {"TA_LDAP"}),
    ],
)
def test_envcheck_auth_modes(envcheck, variables, ldap_expected, unexpected):
    for key, value in variables.items():
        envcheck.env.setenv(key, value)
    before = set(ta_envcheck.UNEXPECTED_ENV_VARS)

    ta_envcheck.Command()._additional_auth_vars_expectations()

    expected = ta_envcheck.EXPECTED_ENV_VARS
    assert ("TA_LDAP_SERVER_URI" in expected) is ldap_expected
    assert set(ta_envcheck.UNEXPECTED_ENV_VARS) - before == unexpected


# ta_startup


@pytest.fixture(name="startup")
def fixture_startup(monkeypatch, tmp_path, redis_ns, no_broker):
    """startup against tmp cache and test redis, snapshots recorded"""
    cache = tmp_path / "cache"
    (cache / "download").mkdir(parents=True)
    monkeypatch.setattr(EnvironmentSettings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(EnvironmentSettings, "TZ", "UTC")
    snapshots = []
    monkeypatch.setattr(
        DbSnapshot, "setup", lambda self: snapshots.append("setup")
    )
    return SimpleNamespace(
        cache=cache, redis=redis_ns, sent=no_broker, snapshots=snapshots
    )


@pytest.mark.django_db
def test_startup_fresh(startup):
    output = run("ta_startup")

    for folder in ("backup", "download", "import", "ytdlp"):
        assert (startup.cache / folder).is_dir()
    assert "no keys found" in output
    assert "no files found" in output
    assert "no new update found" in output
    assert "✓ all default schedules created" in output
    assert "all schedules have correct TZ" in output
    assert "✓ Created default appsettings." in output
    assert startup.snapshots == ["setup"]
    stamp = RedisArchivist().get_message_str("STARTTIMESTAMP")
    assert f"✓ set timestamp to {stamp}." in output
    assert CustomPeriodicTask.objects.get(
        name="check_reindex"
    ).task_config == {"days": 90}
    assert startup.sent == []


@pytest.mark.django_db
def test_startup_existing_install(startup):
    run("ta_startup")
    redis = RedisArchivist()
    redis.set_message("dl_queue", "1")
    redis.set_message("message:download:abc", {"status": "x"})
    redis.set_message(ReleaseVersion.NEW_KEY, {"version": "v0.0.1"})
    TaskRedis().set_key("t1", {"status": "PENDING", "task_id": "t1"})
    lost, _ = enqueue("thumbnail_check", dispatch=False)
    Job.objects.filter(pk=lost.pk).update(status="running", attempts=1)
    CustomPeriodicTask.objects.update(kwargs="{}")
    (startup.cache / "download" / "leftover.part").write_text("x")
    CustomPeriodicTask.objects.filter(name="version_check").update(
        last_run_at=None
    )
    CrontabSchedule.objects.update(timezone="Europe/Berlin")
    config = AppConfig().config
    config["application"]["old_key"] = True
    config["subscriptions"].pop("auto_start")
    config["application"]["enable_snapshot"] = False
    ConfigDocument.objects.filter(key=AppConfig.KEY).update(value=config)

    output = run("ta_startup")

    assert "✓ cleared key dl_queue" in output
    assert "✓ cleared 1 messages" in output
    assert TaskRedis().get_single("t1")["status"] == "FAILED"
    assert "✓ requeued 1 jobs" in output
    assert Job.objects.get(pk=lost.pk).status == "queued"
    assert set(
        CustomPeriodicTask.objects.values_list("kwargs", flat=True)
    ) == {'{"created_by": "schedule"}'}
    assert "✓ cleared 1 files" in output
    assert f"✓ update to {settings.TA_VERSION} completed" in output
    assert "✓ send initial version check task" in output
    assert startup.sent == [("version_check", (), {})]
    assert "schedule init already done, skipping..." in output
    assert "schedules to UTC." in output
    assert not CrontabSchedule.objects.exclude(timezone="UTC").exists()
    assert "added new default: {'subscriptions': {'auto_start'" in output
    assert "removed old key: application.old_key" in output
    assert "snapshots disabled" in output
    assert startup.snapshots == ["setup"]


@pytest.mark.django_db
def test_startup_version_task_already_ran(startup):
    run("ta_startup")
    CustomPeriodicTask.objects.filter(name="version_check").delete()
    CustomPeriodicTask.objects.create(
        name="version_check",
        task="version_check",
        crontab=CrontabSchedule.objects.first(),
        last_run_at=None,
    )
    cmd = ta_startup.Command(stdout=io.StringIO())
    cmd._version_check()
    CustomPeriodicTask.objects.filter(name="version_check").update(
        last_run_at="2026-10-10T00:00:00Z"
    )
    cmd._version_check()

    assert startup.sent == [("version_check", (), {})]


@pytest.mark.django_db
def test_startup_no_version_task(startup):
    cmd = ta_startup.Command(stdout=io.StringIO())

    cmd._version_check()

    assert not startup.sent


@pytest.mark.django_db
def test_startup_config_in_sync(startup):
    run("ta_startup")
    cmd = ta_startup.Command(stdout=io.StringIO())

    cmd._init_app_config()

    output = cmd.stdout.getvalue()
    assert "skip completed appsettings init" in output
    assert "added new default" not in output
    assert "removed old key" not in output
    assert startup.cache.is_dir()


# small commands


@pytest.mark.django_db
def test_stop_on_error_ok():
    assert "✓ local DB is up-to-date." in run("ta_stop_on_error")


def test_stop_on_error_old_layout(monkeypatch, slept):
    monkeypatch.setattr(
        ta_stop_on_error.Command,
        "list_tables",
        staticmethod(lambda: ["user_account", "home_account"]),
    )

    with pytest.raises(CommandError, match="Database is incompatible"):
        run("ta_stop_on_error")

    assert slept == [60]


@pytest.mark.django_db
def test_change_password():
    Account.objects.create_user("viewer", "old-pw")

    output = run("ta_change_password", "viewer", "new-pw")

    assert "✓ updated password for user 'viewer'" in output
    assert Account.objects.get(name="viewer").check_password("new-pw")


@pytest.mark.django_db
def test_change_password_unknown_user():
    Account.objects.create_user("viewer", "old-pw")

    with pytest.raises(CommandError, match="are:\nviewer"):
        run("ta_change_password", "nobody", "new-pw")


@pytest.mark.django_db
def test_import_sqlite(tmp_path):
    path = tmp_path / "db.sqlite3"
    sqlite3.connect(path).close()

    output = run("ta_import_sqlite", str(path))

    assert "skipped: user_account: not in sqlite file" in output


def test_import_sqlite_missing(tmp_path):
    with pytest.raises(CommandError, match="no such file"):
        run("ta_import_sqlite", str(tmp_path / "nope.sqlite3"))


@pytest.mark.django_db
def test_startup_schedules_emby_health(startup, monkeypatch):
    # pylint: disable=unused-argument
    monkeypatch.setattr(ta_startup, "paths_from_env", lambda: "paths")
    output = run("ta_startup")
    assert "scheduled the hourly Emby check" in output
    task = CustomPeriodicTask.objects.get(name="emby_health")
    assert task.schedule_parsed == "30 * *"
    assert "hourly Emby check" not in run("ta_startup")

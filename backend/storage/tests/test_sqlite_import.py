"""tests for importing users, tokens and schedules from stock TA sqlite"""

import json
import sqlite3

import pytest
from django.contrib.auth.models import Group
from django.db.backends.sqlite3.base import DatabaseWrapper
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework.authtoken.models import Token
from storage.sqlite_import import MODELS, SqliteImporter
from task.models import CustomPeriodicTask
from user.models import Account

pytestmark = pytest.mark.django_db(transaction=True)


def make_sqlite(path):
    """empty sqlite with the same tables stock TA has"""
    wrapper = DatabaseWrapper(
        {
            "NAME": str(path),
            "ENGINE": "django.db.backends.sqlite3",
            "OPTIONS": {},
            "TIME_ZONE": None,
            "AUTOCOMMIT": True,
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "ATOMIC_REQUESTS": False,
            "TEST": {},
        }
    )
    with wrapper.schema_editor() as editor:
        for model in MODELS:
            editor.create_model(model)
    wrapper.close()


@pytest.fixture(name="legacy")
def fixture_legacy(tmp_path):
    """sqlite with an admin, a group, a token and a custom schedule"""
    path = tmp_path / "db.sqlite3"
    make_sqlite(path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO user_account (id, password, last_login, is_superuser, "
        "name, is_staff) VALUES (7, 'pbkdf2_sha256$x', "
        "'2026-08-23 10:00:00', 1, 'matt', 1)"
    )
    con.execute("INSERT INTO auth_group (id, name) VALUES (3, 'admin')")
    con.execute(
        "INSERT INTO user_account_groups (id, account_id, group_id) "
        "VALUES (1, 7, 3)"
    )
    con.execute(
        "INSERT INTO authtoken_token (key, user_id, created) "
        "VALUES ('abc123', 7, '2026-08-23 10:00:00')"
    )
    con.execute(
        "INSERT INTO django_celery_beat_crontabschedule (id, minute, hour, "
        "day_of_week, day_of_month, month_of_year, timezone) "
        "VALUES (5, '0', '8', '*', '*', '*', 'America/New_York')"
    )
    con.execute(
        "INSERT INTO django_celery_beat_periodictask (id, name, task, args, "
        "kwargs, queue, exchange, routing_key, headers, priority, expires, "
        "expire_seconds, one_off, start_time, enabled, last_run_at, "
        "total_run_count, date_changed, description, crontab_id) VALUES "
        "(9, 'update_subscribed', 'update_subscribed', '[]', '{}', NULL, "
        "NULL, NULL, '{}', NULL, NULL, NULL, 0, NULL, 1, NULL, 3, "
        "'2026-08-23 10:00:00', '', 5)"
    )
    con.execute(
        "INSERT INTO task_customperiodictask (periodictask_ptr_id, "
        "task_config) VALUES (9, ?)",
        (json.dumps({"days": 90}),),
    )
    con.commit()
    con.close()
    return path


def test_import(legacy):
    report = SqliteImporter(legacy).run()

    user = Account.objects.get(pk=7)
    assert user.name == "matt" and user.is_staff and user.is_superuser
    assert user.last_login.tzinfo is not None
    assert list(user.groups.values_list("name", flat=True)) == ["admin"]
    assert Token.objects.get(key="abc123").user_id == 7
    task = CustomPeriodicTask.objects.get(pk=9)
    assert task.task_config == {"days": 90}
    assert task.crontab.timezone.key == "America/New_York"
    assert report.counts["user_account"] == 1
    assert "user_account: 1" in report.lines()


def test_import_is_idempotent_and_resets_sequences(legacy):
    SqliteImporter(legacy).run()
    PeriodicTask.objects.filter(pk=9).update(total_run_count=50)

    SqliteImporter(legacy).run()

    assert PeriodicTask.objects.get(pk=9).total_run_count == 3
    assert CustomPeriodicTask.objects.count() == 1
    assert Account.objects.create_user("new", "pw-for-tests").pk > 7
    assert Group.objects.create(name="other").pk > 3
    assert CrontabSchedule.objects.create(minute="1").pk > 5


def test_missing_tables_are_reported(tmp_path):
    path = tmp_path / "empty.sqlite3"
    sqlite3.connect(path).close()

    report = SqliteImporter(path).run()

    assert "skipped: user_account: not in sqlite file" in report.lines()
    assert not Account.objects.exists()


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        SqliteImporter(tmp_path / "nope.sqlite3")



def test_convert_keeps_aware_datetimes():
    # pylint: disable=import-outside-toplevel,protected-access
    from storage.sqlite_import import _convert

    field = Account._meta.get_field("last_login")
    value = _convert(field, "2026-08-23T10:00:00+02:00")
    assert value.utcoffset().total_seconds() == 7200
    assert _convert(field, None) is None

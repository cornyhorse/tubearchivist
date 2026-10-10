"""schedules stored as CustomPeriodicTask, task model, celery app"""

import pytest
from common.src.env_settings import EnvironmentSettings
from task import celery
from task.models import CustomPeriodicTask
from task.src.config_schedule import CrontabValidator, ScheduleBuilder


@pytest.mark.django_db
def test_update_schedule_auto_with_config():
    task = ScheduleBuilder().update_schedule(
        "check_reindex", "auto", {"days": 30}
    )

    stored = CustomPeriodicTask.objects.get(name="check_reindex")
    assert task.pk == stored.pk
    assert stored.description == "Reindex Documents"
    assert stored.schedule_parsed == "0 12 *"
    assert stored.crontab.timezone.key == EnvironmentSettings.TZ
    assert stored.task_config == {"days": 30}
    assert stored.last_run_at is not None


@pytest.mark.django_db
def test_update_existing_schedule():
    builder = ScheduleBuilder()
    builder.update_schedule("run_backup", "5 4 0", None)
    builder.update_schedule("run_backup", "15 3,4 1-3", {})

    stored = CustomPeriodicTask.objects.get(name="run_backup")
    assert CustomPeriodicTask.objects.count() == 1
    assert stored.schedule_parsed == "15 3,4 1-3"
    assert stored.task_config == {}


@pytest.mark.django_db
def test_get_task_without_schedule():
    task = ScheduleBuilder().get_set_task("thumbnail_check")

    assert task.pk is None
    assert task.description == "Check your Thumbnails"
    assert not CustomPeriodicTask.objects.exists()


@pytest.mark.parametrize("expression", [None, "", "auto"])
def test_validate_cron_skips(expression):
    assert CrontabValidator().validate_cron(expression) is None


def test_validate_cron_ok():
    assert CrontabValidator().validate_cron("30 2 *") is None


def test_validate_config():
    validator = CrontabValidator()

    assert validator.validate_config("check_reindex", {}) is None
    assert validator.validate_config("check_reindex", {"days": 1}) is None
    with pytest.raises(ValueError, match="doesn't take config"):
        validator.validate_config("thumbnail_check", {"days": 1})
    with pytest.raises(ValueError, match="invalid config key"):
        validator.validate_config("run_backup", {"days": 1})


@pytest.mark.parametrize(
    "con, expected",
    [
        ("redis://host:6379", "redis://host:6379"),
        ("unix:///run/redis.sock", "redis+socket:///run/redis.sock"),
    ],
)
def test_con_parser(monkeypatch, con, expected):
    monkeypatch.setattr(EnvironmentSettings, "REDIS_CON", con)

    assert celery.con_parser() == expected

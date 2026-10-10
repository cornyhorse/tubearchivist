"""tests for app config and release version checks"""

import copy

import pytest
from appsettings.models import ConfigDocument
from appsettings.src import config as config_module
from appsettings.src.config import AppConfig, ReleaseVersion
from common.src.ta_redis import RedisArchivist

pytestmark = pytest.mark.django_db


def stored() -> dict:
    """config value as saved in the database"""
    return ConfigDocument.objects.get(key=AppConfig.KEY).value


def test_missing_config_raises():
    with pytest.raises(ValueError, match="no config found"):
        AppConfig()


def test_sync_defaults_and_read(app_config):
    assert app_config.config == AppConfig.CONFIG_DEFAULTS
    assert str(ConfigDocument.objects.get(key="appsettings")) == "appsettings"


def test_update_config_merges_nested_and_replaces_plain(app_config):
    updated = app_config.update_config(
        {"downloads": {"limit_speed": 500}, "new_flat": 3}
    )

    assert updated["downloads"]["limit_speed"] == 500
    assert updated["downloads"]["sleep_interval"] == 10
    assert updated["new_flat"] == 3
    assert stored() == updated


def test_update_config_keeps_defaults_untouched(app_config):
    defaults = copy.deepcopy(AppConfig.CONFIG_DEFAULTS)
    app_config.config.pop("application")
    app_config.add_new_defaults()

    app_config.update_config({"application": {"enable_cast": True}})

    assert AppConfig.CONFIG_DEFAULTS == defaults
    assert stored()["application"]["enable_cast"] is True


def test_post_process_updated_runs_snapshot_setup(app_config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        config_module.DbSnapshot, "setup", lambda self: calls.append(self)
    )

    app_config.post_process_updated(
        [
            ("application.enable_snapshot", False),
            ("downloads.limit_speed", 4),
            ("application.enable_snapshot", True),
        ]
    )

    assert len(calls) == 1


def test_fail_message(redis_clean):
    AppConfig._fail_message("cookie broken")  # pylint: disable=W0212

    message = RedisArchivist().get_message_dict("message:setting")
    assert message["messages"] == ["cookie broken"]
    assert message["level"] == "error"


def test_add_new_defaults(app_config):
    app_config.config.pop("application")
    app_config.config["downloads"].pop("format")
    app_config._save(app_config.config)  # pylint: disable=W0212

    updated = AppConfig().add_new_defaults()

    assert len(updated) == 2
    assert stored() == AppConfig.CONFIG_DEFAULTS


def test_add_new_defaults_nothing_new(app_config):
    assert not app_config.add_new_defaults()


def test_clear_old_keys(app_config):
    app_config.update_config(
        {"legacy": {"x": 1}, "downloads": {"old_key": True}}
    )

    cleared = AppConfig().clear_old_keys()

    assert cleared == ["{'legacy': {'x': 1}}", "downloads.old_key"]
    assert stored() == AppConfig.CONFIG_DEFAULTS


def test_clear_old_keys_nothing_to_clear(app_config):
    assert not app_config.clear_old_keys()


class FakeResponse:
    """minimal requests response"""

    def __init__(self, data):
        self.data = data

    def json(self):
        """return payload"""
        return self.data


@pytest.fixture(name="release")
def fixture_release(monkeypatch, settings, redis_clean):
    """ReleaseVersion on a fixed local version, no sleeping"""
    # pylint: disable=unused-argument
    settings.TA_VERSION = "v0.5.12"
    monkeypatch.setattr(config_module, "sleep", lambda secs: None)

    def set_remote(version, breaking=False):
        def fake_get(url, timeout):
            assert url == ReleaseVersion.REMOTE_URL
            assert timeout == 20
            return FakeResponse(
                {"release_version": version, "breaking_changes": breaking}
            )

        monkeypatch.setattr(config_module.requests, "get", fake_get)

    return set_remote


def test_check_finds_new_version(release):
    release("v0.6.0", breaking=True)

    ReleaseVersion().check()

    assert ReleaseVersion().get_update() == {
        "status": True,
        "version": "v0.6.0",
        "is_breaking": True,
    }


def test_check_no_update(release):
    release("v0.5.12")
    checker = ReleaseVersion()

    checker.check()

    assert checker.get_local_version() == "v0.5.12"
    assert checker.get_update() is None


def test_unstable_same_version_is_update(release, settings):
    release("v0.5.12")
    settings.TA_VERSION = "v0.5.12-unstable"
    checker = ReleaseVersion()

    checker.get_remote_version()

    assert checker._has_update() == "v0.5.12"  # pylint: disable=W0212


@pytest.mark.parametrize(
    "version, expected", [("v0.5.12", (0, 5, 12)), ("1.2.3-unstable", (1, 2, 3))]
)
def test_parse_version(version, expected):
    assert ReleaseVersion._parse_version(version) == expected  # noqa


def test_is_updated_without_message(release):
    # pylint: disable=unused-argument
    assert ReleaseVersion().is_updated() is False


def test_is_updated_after_upgrade(release, settings):
    RedisArchivist().set_message(
        ReleaseVersion.NEW_KEY, {"status": True, "version": "v0.5.12"}
    )

    assert ReleaseVersion().is_updated() == settings.TA_VERSION
    assert ReleaseVersion().get_update() is None


def test_is_updated_still_pending(release):
    # pylint: disable=unused-argument
    RedisArchivist().set_message(
        ReleaseVersion.NEW_KEY, {"status": True, "version": "v0.6.0"}
    )

    assert ReleaseVersion().is_updated() is False
    assert ReleaseVersion().get_update()["version"] == "v0.6.0"

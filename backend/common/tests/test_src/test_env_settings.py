"""tests for environment settings"""

import importlib.util
import sys

from common.src import env_settings
from common.src.env_settings import EnvironmentSettings, get_password_from_file


def test_password_from_env(monkeypatch):
    monkeypatch.setenv("TEST_TA_SECRET", "from-env")
    monkeypatch.setenv("TEST_TA_SECRET_FILE", "/nonexistent")
    assert get_password_from_file("TEST_TA_SECRET") == "from-env"


def test_password_from_file(monkeypatch, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("  from-file\n", encoding="utf-8")
    monkeypatch.delenv("TEST_TA_SECRET", raising=False)
    monkeypatch.setenv("TEST_TA_SECRET_FILE", str(secret))
    assert get_password_from_file("TEST_TA_SECRET") == "from-file"


def test_password_file_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TEST_TA_SECRET", raising=False)
    monkeypatch.setenv("TEST_TA_SECRET_FILE", str(tmp_path / "missing"))
    assert get_password_from_file("TEST_TA_SECRET") == ""
    assert "is not a path" in capsys.readouterr().out


def test_password_unset(monkeypatch, capsys):
    monkeypatch.delenv("TEST_TA_SECRET", raising=False)
    monkeypatch.delenv("TEST_TA_SECRET_FILE", raising=False)
    assert get_password_from_file("TEST_TA_SECRET") == ""
    assert "must be set" in capsys.readouterr().out


def test_roots_absolute_and_relative():
    settings = EnvironmentSettings()
    settings.CACHE_DIR = "/cache"
    settings.MEDIA_DIR = "/youtube"
    assert settings.get_cache_root() == "/cache"
    assert settings.get_media_root() == "/youtube"

    settings.CACHE_DIR = "cache"
    settings.MEDIA_DIR = "media/youtube"
    assert settings.get_cache_root() == "/cache"
    assert settings.get_media_root() == "/media/youtube"


def test_print_all_masks_passwords(capsys):
    EnvironmentSettings().print_all()
    out = capsys.readouterr().out

    for name in (
        "HOST_UID",
        "MEDIA_DIR",
        "REDIS_NAME_SPACE",
        "TA_DB_HOST",
        "TA_DB_USER",
    ):
        assert f"{name}: " in out
    assert "TA_PASSWORD: *****" in out
    assert "TA_DB_PASSWORD: *****" in out
    for secret in (
        EnvironmentSettings.TA_PASSWORD,
        EnvironmentSettings.TA_DB_PASSWORD,
    ):
        if secret:
            assert secret not in out


def test_import_without_dotenv(monkeypatch, capsys):
    """settings load from the plain environment when dotenv is missing"""
    monkeypatch.setitem(sys.modules, "dotenv", None)
    spec = importlib.util.spec_from_file_location(
        "env_settings_without_dotenv", env_settings.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "loading local dotenv" not in capsys.readouterr().out
    assert module.EnvironmentSettings.TZ == EnvironmentSettings.TZ

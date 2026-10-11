"""accounts, per user config and the user api"""

import importlib

import pytest
from django.test import override_settings
from rest_framework.test import APIClient
from user.models import Account
from user.models import UserConfig as UserConfigModel
from user.src.user_config import UserConfig

pytestmark = pytest.mark.django_db


def test_create_user_needs_name_and_password():
    with pytest.raises(ValueError, match="password value must be set"):
        Account.objects.create_user("someone", "")
    with pytest.raises(ValueError, match="name value must be set"):
        Account.objects.create_user("", "pw")
    assert not Account.objects.exists()


def test_create_superuser():
    user = Account.objects.create_superuser("root", "pw")

    assert user.is_staff and user.is_superuser
    assert user.check_password("pw")


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"is_staff": False}, "is_staff=True"),
        ({"is_superuser": False}, "is_superuser=True"),
    ],
)
def test_create_superuser_flags(extra, message):
    with pytest.raises(ValueError, match=message):
        Account.objects.create_superuser("root", "pw", **extra)


def test_user_config_defaults_and_values(capsys):
    user = Account.objects.create_user("viewer", "pw")
    config = UserConfig(user.id)

    assert config.get_value("page_size") == 25
    with pytest.raises(KeyError, match="unknown key 'nope'"):
        config.get_value("nope")

    config.set_value("page_size", 50)

    assert config.get_value("page_size") == 50
    assert UserConfig(user.id).get_value("page_size") == 50
    assert "set default config for user" in capsys.readouterr().out


def test_user_config_needs_user():
    with pytest.raises(ValueError, match="no user_id passed"):
        UserConfig(None)


def test_user_config_syncs_new_defaults():
    user = Account.objects.create_user("viewer", "pw")
    UserConfigModel.objects.create(user=user, config={"page_size": 10})

    config = UserConfig(user.id)

    assert config.get_value("page_size") == 10
    assert config.get_value("grid_items") == 3
    stored = UserConfigModel.objects.get(user=user).config
    assert stored["show_help_text"] is True
    assert stored["page_size"] == 10


def test_account_view(user_client):
    response = user_client.get("/api/user/account/")

    assert response.status_code == 200
    assert response.json()["name"] == "viewer"
    assert response.json()["is_staff"] is False


def test_me_view(user_client):
    url = "/api/user/me/"

    initial = user_client.get(url).json()
    updated = user_client.post(url, {"page_size": 40}, format="json")
    invalid = user_client.post(url, {"grid_items": 9}, format="json")

    assert initial["page_size"] == 25
    assert updated.json()["page_size"] == 40
    assert updated.json()["stylesheet"] == "device"
    assert invalid.status_code == 400


@pytest.mark.parametrize(
    "theme", ["light", "dark", "paper", "contrast", "colourblind"]
)
def test_set_and_clear_theme(user_client, theme):
    url = "/api/user/me/"

    chosen = user_client.post(url, {"stylesheet": theme}, format="json")
    cleared = user_client.post(url, {"stylesheet": "device"}, format="json")

    assert chosen.status_code == 200
    assert chosen.json()["stylesheet"] == theme
    assert cleared.json()["stylesheet"] == "device"


@pytest.mark.parametrize(
    "value", ["neon", "dark.css", "../custom.css", "", None, 3]
)
def test_unknown_theme_is_rejected(user_client, value):
    url = "/api/user/me/"
    user_client.post(url, {"stylesheet": "paper"}, format="json")

    response = user_client.post(url, {"stylesheet": value}, format="json")

    assert response.status_code == 400
    assert user_client.get(url).json()["stylesheet"] == "paper"


@pytest.mark.parametrize(
    "stored, expected",
    [
        ("dark.css", "dark"),
        ("light.css", "light"),
        ("matrix.css", "dark"),
        ("custom.css", "device"),
        ("<script>", "device"),
    ],
)
def test_legacy_or_tampered_theme_is_migrated(stored, expected):
    user = Account.objects.create_user("legacy", "pw")
    UserConfigModel.objects.create(user=user, config={"stylesheet": stored})

    assert UserConfig(user.id).get_value("stylesheet") == expected
    stored_now = UserConfigModel.objects.get(user=user).config
    assert stored_now["stylesheet"] == expected


def test_anonymous_is_rejected():
    client = APIClient(SERVER_NAME="localhost")

    assert client.get("/api/user/me/").status_code == 403


@pytest.mark.parametrize(
    "remember_me, expiry", [("on", 60 * 60 * 24 * 365), (None, 60 * 60 * 48)]
)
def test_login_and_logout(remember_me, expiry):
    Account.objects.create_user("viewer", "secret-pw")
    client = APIClient(SERVER_NAME="localhost")
    data = {"username": "viewer", "password": "secret-pw"}
    if remember_me:
        data["remember_me"] = remember_me

    response = client.post("/api/user/login/", data, format="json")

    assert response.status_code == 204
    assert client.session.get_expiry_age() == expiry
    assert client.get("/api/user/account/").json()["name"] == "viewer"

    assert client.post("/api/user/logout/").status_code == 204
    assert client.get("/api/user/account/").status_code == 403


def test_login_invalid():
    client = APIClient(SERVER_NAME="localhost")
    data = {"username": "viewer", "password": "wrong"}

    response = client.post("/api/user/login/", data, format="json")

    assert response.status_code == 400
    assert response.json() == {"error": "Invalid credentials"}


def test_remote_user_middleware_header():
    with override_settings(TA_AUTH_PROXY_USERNAME_HEADER="HTTP_X_USER"):
        module = importlib.import_module("user.src.remote_user_auth")
        module = importlib.reload(module)

    assert module.HttpRemoteUserMiddleware.header == "HTTP_X_USER"


def test_admin_registration():
    # pylint: disable=import-outside-toplevel
    from django.contrib import admin
    from django_celery_beat.models import PeriodicTask
    from user.admin import HomeAdmin

    assert isinstance(admin.site._registry[Account], HomeAdmin)
    assert PeriodicTask not in admin.site._registry


@pytest.mark.django_db
def test_table_views_and_home_layout(user_client):
    url = "/api/user/me/"
    defaults = user_client.get(url).json()
    assert defaults["view_style_channel"] == "table"
    assert defaults["home_layout"] == "default"

    changed = user_client.post(
        url,
        {"view_style_downloads": "table", "home_layout": "shelves"},
        format="json",
    )
    assert changed.json()["view_style_downloads"] == "table"
    assert changed.json()["home_layout"] == "shelves"
    bad = user_client.post(url, {"home_layout": "magic"}, format="json")
    assert bad.status_code == 400

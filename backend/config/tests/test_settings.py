"""settings variants by environment, wsgi/asgi entry points, manage.py"""

import importlib
import io
import runpy
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import config
import django_auth_ldap.backend
import ldap
import pytest
from common.src.ta_redis import RedisArchivist

BACKEND = Path(__file__).resolve().parents[2]
AUTH_ENV = [
    "TA_LOGIN_AUTH_MODE",
    "TA_LDAP",
    "TA_ENABLE_AUTH_PROXY",
    "DISABLE_CORS",
    "TA_AUTH_PROXY_USERNAME_HEADER",
    "TA_AUTH_PROXY_TRUSTED_IPS",
    "TA_AUTH_PROXY_CREATE_USERS",
    "TA_LDAP_DISABLE_CERT_CHECK",
    "TA_LDAP_PROMOTE_USERNAMES_TO_SUPERUSER",
    "TA_LDAP_PROMOTE_USERNAMES_TO_STAFF",
    "TA_LDAP_USER_ATTR_MAP_USERNAME",
]
LDAP_ENV = {
    "TA_LDAP_SERVER_URI": "ldap://ldap.invalid",
    "TA_LDAP_BIND_DN": "cn=ta",
    "TA_LDAP_BIND_PASSWORD": "not-a-secret",
    "TA_LDAP_USER_BASE": "ou=users",
    "TA_LDAP_USER_FILTER": "(objectClass=person)",
}
MODEL = "django.contrib.auth.backends.ModelBackend"
LDAP = "django_auth_ldap.backend.LDAPBackend"
REMOTE = "django.contrib.auth.backends.RemoteUserBackend"
REMOTE_MIDDLEWARE = "user.src.remote_user_auth.HttpRemoteUserMiddleware"
TRUSTED = "user.src.remote_user_auth.TrustedRemoteUserBackend"
TRUSTED_MIDDLEWARE = "user.src.remote_user_auth.TrustedRemoteUserMiddleware"


SETTINGS_MODULES = ["settings", "ldap_settings", "fwd_auth_settings"]


@pytest.fixture(name="env")
def fixture_env(monkeypatch):
    """
    controlled env for fresh imports of the settings modules,
    the original modules are put back afterwards
    """
    for key in AUTH_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in LDAP_ENV.items():
        monkeypatch.setenv(key, value)
    for name in SETTINGS_MODULES:
        monkeypatch.delitem(sys.modules, f"config.{name}", raising=False)
        if hasattr(config, name):
            monkeypatch.setattr(config, name, getattr(config, name))

    receivers = list(django_auth_ldap.backend.populate_user.receivers)
    yield monkeypatch
    django_auth_ldap.backend.populate_user.receivers = receivers
    django_auth_ldap.backend.populate_user.sender_receivers_cache.clear()


def load(name, env=None, **variables):
    """import a fresh copy of config.<name> with env variables set"""
    for key, value in variables.items():
        env.setenv(key, value)

    sys.modules.pop(f"config.{name}", None)
    return importlib.import_module(f"config.{name}")


def reload_settings(env, **variables):
    """fresh settings module"""
    return load("settings", env, **variables)


@pytest.mark.parametrize(
    "variables, backends, remote",
    [
        ({"TA_LOGIN_AUTH_MODE": "local"}, (MODEL,), False),
        ({"TA_LOGIN_AUTH_MODE": "LDAP"}, (LDAP,), False),
        ({"TA_LOGIN_AUTH_MODE": "forwardauth"}, (REMOTE,), True),
        ({"TA_LOGIN_AUTH_MODE": "ldap_local"}, (LDAP, MODEL), False),
        ({"TA_LDAP": "1"}, (LDAP,), False),
        ({"TA_ENABLE_AUTH_PROXY": "1"}, (REMOTE,), True),
    ],
)
def test_auth_modes(env, variables, backends, remote):
    module = reload_settings(env, **variables)

    assert module.AUTHENTICATION_BACKENDS == backends
    assert (REMOTE_MIDDLEWARE in module.MIDDLEWARE) is remote


def test_forward_auth_local_mode(env):
    module = reload_settings(env, TA_LOGIN_AUTH_MODE="forwardauth_local")

    assert module.AUTHENTICATION_BACKENDS == (TRUSTED, MODEL)
    assert TRUSTED_MIDDLEWARE in module.MIDDLEWARE
    assert REMOTE_MIDDLEWARE not in module.MIDDLEWARE
    assert module.TA_AUTH_PROXY_TRUSTED_IPS == ""
    assert module.TA_AUTH_PROXY_CREATE_USERS is False


def test_forward_auth_local_options(env):
    module = reload_settings(
        env,
        TA_LOGIN_AUTH_MODE="forwardauth_local",
        TA_AUTH_PROXY_TRUSTED_IPS="192.168.17.16/32",
        TA_AUTH_PROXY_CREATE_USERS="1",
    )

    assert module.TA_AUTH_PROXY_TRUSTED_IPS == "192.168.17.16/32"
    assert module.TA_AUTH_PROXY_CREATE_USERS is True


def test_single_mode_defaults(env):
    module = reload_settings(env, DISABLE_CORS="1")

    assert not hasattr(module, "AUTHENTICATION_BACKENDS")
    assert module.CORS_ALLOW_ALL_ORIGINS is True
    assert REMOTE_MIDDLEWARE not in module.MIDDLEWARE


def test_forward_auth_header(env):
    module = reload_settings(
        env,
        TA_LOGIN_AUTH_MODE="forwardauth",
        TA_AUTH_PROXY_USERNAME_HEADER="HTTP_X_USER",
    )

    assert module.TA_AUTH_PROXY_USERNAME_HEADER == "HTTP_X_USER"


def test_without_dotenv_and_redis(env):
    def no_redis(self, key):
        raise ValueError("no redis in unittest bootstrap")

    env.setitem(sys.modules, "dotenv", None)
    env.setattr(RedisArchivist, "get_message_str", no_redis)

    module = reload_settings(env)

    assert module.TA_START is None
    assert module.CORS_ALLOWED_ORIGIN_REGEXES


def test_ldap_defaults(env):
    module = load("ldap_settings")

    assert module.AUTH_LDAP_USER_ATTR_MAP == {
        "username": "uid",
        "first_name": "givenName",
        "last_name": "sn",
        "email": "mail",
    }
    assert module.AUTH_LDAP_USER_SEARCH.filterstr == (
        "(&(uid=%(user)s)(objectClass=person))"
    )
    assert not hasattr(module, "create_user")
    assert not hasattr(module, "AUTH_LDAP_GLOBAL_OPTIONS")
    assert env  # restores the module


def test_ldap_options(env):
    env.setenv("TA_LDAP_DISABLE_CERT_CHECK", "1")
    env.setenv("TA_LDAP_USER_ATTR_MAP_USERNAME", "cn")
    env.setenv("TA_LDAP_PROMOTE_USERNAMES_TO_SUPERUSER", "boss, chief")
    env.setenv("TA_LDAP_PROMOTE_USERNAMES_TO_STAFF", "helper")

    module = load("ldap_settings")

    assert module.AUTH_LDAP_GLOBAL_OPTIONS == {
        ldap.OPT_X_TLS_REQUIRE_CERT: ldap.OPT_X_TLS_NEVER
    }
    assert module.AUTH_LDAP_USER_ATTR_MAP["username"] == "cn"
    assert module._ldap_superuser_usernames == ["boss", "chief"]
    assert module._ldap_staff_usernames == ["helper"]


def test_ldap_staff_only(env):
    env.setenv("TA_LDAP_PROMOTE_USERNAMES_TO_STAFF", "helper")

    module = load("ldap_settings")

    assert module._ldap_superuser_usernames == []
    assert hasattr(module, "create_user")


def ldap_user(name, staff=False, superuser=False):
    """the attributes create_user reads, save counted"""
    user = SimpleNamespace(
        ldap_username=name, is_staff=staff, is_superuser=superuser, saved=0
    )

    def save():
        user.saved += 1

    user.save = save
    return user


@pytest.mark.parametrize(
    "user, staff, superuser, saved",
    [
        (ldap_user("boss"), True, True, 1),
        (ldap_user("boss", staff=True, superuser=True), True, True, 0),
        (ldap_user("boss", staff=True), True, True, 1),
        (ldap_user("helper"), True, False, 1),
        (ldap_user("helper", staff=True), True, False, 0),
        (ldap_user("nobody"), False, False, 0),
    ],
)
def test_ldap_promote(env, user, staff, superuser, saved):
    env.setenv("TA_LDAP_PROMOTE_USERNAMES_TO_SUPERUSER", "boss")
    env.setenv("TA_LDAP_PROMOTE_USERNAMES_TO_STAFF", "helper")
    module = load("ldap_settings")

    module.create_user(sender=None, user=user)

    assert (user.is_staff, user.is_superuser) == (staff, superuser)
    assert user.saved == saved


def test_wsgi_asgi():
    wsgi = importlib.import_module("config.wsgi")
    asgi = importlib.import_module("config.asgi")

    assert callable(wsgi.application)
    assert callable(asgi.application)


def test_manage_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["manage.py", "version"])
    out = io.StringIO()

    with redirect_stdout(out):
        runpy.run_path(str(BACKEND / "manage.py"), run_name="__main__")

    assert out.getvalue().strip()[0].isdigit()


def test_manage_without_django(monkeypatch):
    manage = importlib.import_module("manage")
    monkeypatch.setitem(sys.modules, "django.core.management", None)

    with pytest.raises(ImportError, match="Couldn't import Django"):
        manage.main()

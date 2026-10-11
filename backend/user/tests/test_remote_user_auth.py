"""forwardauth_local: the proxy's user header, trusted proxies only"""

import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from user.models import Account
from user.src.remote_user_auth import (
    TrustedRemoteUserMiddleware,
    address_in,
    meta_key,
    parse_networks,
)

pytestmark = pytest.mark.django_db

BACKENDS = (
    "user.src.remote_user_auth.TrustedRemoteUserBackend",
    "django.contrib.auth.backends.ModelBackend",
)
NAS = "192.168.17.16"


def ok(_request):
    return "response"


def make_request(
    header=None, real_ip=NAS, remote_addr="127.0.0.1", session=None
):
    """a request as TA's nginx forwards it, with session and request.user"""
    meta = {"REMOTE_ADDR": remote_addr}
    if real_ip is not None:
        meta["HTTP_X_REAL_IP"] = real_ip
    if header is not None:
        meta["HTTP_X_AUTHENTIK_USERNAME"] = header
    request = RequestFactory().get("/api/user/account/", **meta)
    SessionMiddleware(ok).process_request(request)
    if session is not None:
        for key, value in session.items():
            request.session[key] = value
    AuthenticationMiddleware(ok).process_request(request)
    return request


@pytest.fixture(name="mode", autouse=True)
def fixture_mode(settings):
    """forwardauth_local settings for every test; tests change them"""
    settings.AUTHENTICATION_BACKENDS = BACKENDS
    settings.TA_AUTH_PROXY_USERNAME_HEADER = "X-Authentik-Username"
    settings.TA_AUTH_PROXY_TRUSTED_IPS = f"{NAS}/32, 10.0.0.0/8"
    settings.TA_AUTH_PROXY_CREATE_USERS = False
    return settings


def run(request):
    response = TrustedRemoteUserMiddleware(ok)(request)
    assert response == "response"
    return request


@pytest.fixture(name="matt")
def fixture_matt():
    return Account.objects.create_user("matt", "local-password")


def test_trusted_proxy_header_signs_in(matt):
    request = run(make_request(header="matt"))

    assert request.user == matt
    assert request.session["_auth_user_id"] == str(matt.pk)


@pytest.mark.parametrize(
    "real_ip, remote_addr",
    [
        ("192.168.17.99", "127.0.0.1"),  # not a trusted proxy
        (None, "127.0.0.1"),  # no X-Real-IP from TA's nginx
        ("not-an-address", "127.0.0.1"),
        (NAS, "192.168.17.99"),  # straight to Django, not through nginx
    ],
)
def test_untrusted_header_is_ignored(matt, real_ip, remote_addr):
    request = run(
        make_request(header="matt", real_ip=real_ip, remote_addr=remote_addr)
    )

    assert not request.user.is_authenticated


def test_no_trusted_ips_trusts_nobody(matt, mode):
    mode.TA_AUTH_PROXY_TRUSTED_IPS = ""
    request = run(make_request(header="matt"))

    assert not request.user.is_authenticated


def test_without_header_password_login_still_works(matt):
    request = run(make_request())

    assert not request.user.is_authenticated
    assert (
        authenticate(request, name="matt", password="local-password") == matt
    )
    assert authenticate(request, name="matt", password="wrong") is None


def test_missing_header_keeps_existing_session(matt):
    signed_in = run(make_request(header="matt"))
    session = dict(signed_in.session.items())

    request = run(make_request(session=session))

    assert request.user == matt


def test_same_user_is_left_alone(matt):
    signed_in = run(make_request(header="matt"))
    session = dict(signed_in.session.items())

    request = run(make_request(header="matt", session=session))

    assert request.user == matt
    assert request.session["_auth_user_id"] == str(matt.pk)


def test_different_header_user_switches_session(matt):
    other = Account.objects.create_user("david", "pw")
    signed_in = run(make_request(header="matt"))
    session = dict(signed_in.session.items())

    request = run(make_request(header="david", session=session))

    assert request.user == other


def test_unknown_user_is_not_created_by_default(matt):
    request = run(make_request(header="stranger"))

    assert not request.user.is_authenticated
    assert not Account.objects.filter(name="stranger").exists()


def test_unknown_user_created_when_asked(matt, mode):
    mode.TA_AUTH_PROXY_CREATE_USERS = True
    request = run(make_request(header="newcomer"))

    assert request.user.is_authenticated
    assert request.user.get_username() == "newcomer"
    assert not request.user.is_staff


def test_blank_header_is_ignored(matt):
    request = run(make_request(header="   "))

    assert not request.user.is_authenticated


@pytest.mark.parametrize(
    "header, key",
    [
        ("X-Authentik-Username", "HTTP_X_AUTHENTIK_USERNAME"),
        ("HTTP_REMOTE_USER", "HTTP_REMOTE_USER"),
        (" remote-user ", "HTTP_REMOTE_USER"),
    ],
)
def test_meta_key(header, key):
    assert meta_key(header) == key


def test_parse_networks_and_address_in():
    networks = parse_networks(" 192.168.17.16 ,, 10.0.0.0/8,::1 ")

    assert len(networks) == 3
    assert address_in(networks, "10.1.2.3")
    assert address_in(networks, "::1")
    assert not address_in(networks, "192.168.17.17")
    assert not address_in(networks, "")

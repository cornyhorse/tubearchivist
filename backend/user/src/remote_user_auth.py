"""authentication by a user header set by a reverse proxy (forward auth)"""

from ipaddress import ip_address, ip_network

from django.conf import settings
from django.contrib import auth
from django.contrib.auth.backends import RemoteUserBackend
from django.contrib.auth.middleware import PersistentRemoteUserMiddleware

LOOPBACK = (ip_network("127.0.0.0/8"), ip_network("::1/128"))


class HttpRemoteUserMiddleware(PersistentRemoteUserMiddleware):
    """This class allows authentication via HTTP_REMOTE_USER which is set for
    example by certain SSO applications.
    """

    header = getattr(
        settings, "TA_AUTH_PROXY_USERNAME_HEADER", "HTTP_REMOTE_USER"
    )


def meta_key(header):
    """request.META key for X-Remote-User or HTTP_X_REMOTE_USER"""
    key = header.strip().upper().replace("-", "_")
    return key if key.startswith("HTTP_") else f"HTTP_{key}"


def parse_networks(value):
    """comma separated addresses and networks"""
    return [
        ip_network(part.strip(), strict=False)
        for part in value.split(",")
        if part.strip()
    ]


def address_in(networks, address):
    """is the address in one of the networks, False for a non-address"""
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


class TrustedRemoteUserBackend(RemoteUserBackend):
    """forwardauth_local: creates unknown users only when asked to"""

    @property
    def create_unknown_user(self):
        return getattr(settings, "TA_AUTH_PROXY_CREATE_USERS", False)


class TrustedRemoteUserMiddleware:
    """
    forwardauth_local: sign in the user named in the proxy's header, but only
    for requests that came through TA's own nginx (REMOTE_ADDR is loopback)
    from a trusted proxy (the X-Real-IP that nginx sets is in
    TA_AUTH_PROXY_TRUSTED_IPS). Every other request is left to the session
    and the username/password login, and a missing header never signs anyone
    out, so the login form keeps working beside forward auth.

    The proxy must set or clear the header on every request it forwards:
    a request that skips its auth check must not carry a client's header.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.key = meta_key(
            getattr(
                settings, "TA_AUTH_PROXY_USERNAME_HEADER", "HTTP_REMOTE_USER"
            )
        )
        self.trusted = parse_networks(
            getattr(settings, "TA_AUTH_PROXY_TRUSTED_IPS", "")
        )

    def __call__(self, request):
        self.process_request(request)
        return self.get_response(request)

    def from_trusted_proxy(self, request):
        """did the request come through TA's nginx from a trusted proxy"""
        meta = request.META
        return address_in(LOOPBACK, meta.get("REMOTE_ADDR", "")) and (
            address_in(self.trusted, meta.get("HTTP_X_REAL_IP", ""))
        )

    def process_request(self, request):
        """sign in the header user when the header can be trusted"""
        username = request.META.get(self.key, "").strip()
        if not username or not self.from_trusted_proxy(request):
            return

        if request.user.is_authenticated:
            if request.user.get_username() == username:
                return
            auth.logout(request)

        user = auth.authenticate(request, remote_user=username)
        if user:
            request.user = user
            auth.login(request, user)

# Forward auth with the password login kept (`forwardauth_local`)

Matt's homelab puts Authentik (`portal.mk-bt.com`) in front of TubeArchivist with nginx forward auth, so a signed-in
user opens TA without typing a password. Every app must keep its own password login beside SSO (the plan:
MattsTools `docs/architecture/authentik-sso-rollout-plan.md`). Upstream's modes cannot do both:

| Mode | Header sign-in | Password login | Who may send the header |
| --- | --- | --- | --- |
| upstream `forwardauth` | yes | **no** (only `RemoteUserBackend`) | **anyone** who reaches TA |
| fork `forwardauth_local` | yes | yes (`ModelBackend` too) | only trusted proxies, through TA's own nginx |

Upstream's mode is also marked unmaintained, and under uvicorn (ASGI) Django's `RemoteUserMiddleware` looks up
`"HTTP_" + header`, so the default `HTTP_REMOTE_USER` never matches there.

## How it works

`TA_LOGIN_AUTH_MODE=forwardauth_local` sets:

- `AUTHENTICATION_BACKENDS = (TrustedRemoteUserBackend, ModelBackend)`
- `TrustedRemoteUserMiddleware` after Django's `AuthenticationMiddleware`

For each request the middleware reads the header named by `TA_AUTH_PROXY_USERNAME_HEADER` (given as `X-Authentik-Username`
or `HTTP_X_AUTHENTIK_USERNAME`; it is normalised either way) and signs that user in **only if**:

1. `REMOTE_ADDR` is loopback, so the request came through TA's own nginx (`docker_assets/nginx.conf`), and
2. `X-Real-IP`, which that nginx sets from the real peer address (`proxy_params`) and a client cannot override, is in
   `TA_AUTH_PROXY_TRUSTED_IPS`.

Otherwise the header is ignored and the session and the login form work as in `local` mode. A missing header never
signs anyone out. A header naming a different user than the session switches to that user. A user TA does not know
is not created unless `TA_AUTH_PROXY_CREATE_USERS` is set (created users get no staff rights).

| Variable | Default | |
| --- | --- | --- |
| `TA_LOGIN_AUTH_MODE` | `single` | `forwardauth_local` |
| `TA_AUTH_PROXY_USERNAME_HEADER` | `HTTP_REMOTE_USER` | `X-Authentik-Username` for Authentik |
| `TA_AUTH_PROXY_TRUSTED_IPS` | empty (trusts nobody) | the fronting nginx as TA sees it, e.g. `192.168.17.16/32`; required by `ta_envcheck` |
| `TA_AUTH_PROXY_CREATE_USERS` | off | any value turns it on |

## What the proxy must do

The fronting nginx (the NAS for `ta.mk-bt.com`) must **set or clear the header on every request** it forwards to TA.
A location that skips the Authentik check must clear it (`proxy_set_header X-Authentik-Username "";`): otherwise a
client could send the header through that location, and TA would trust it because it came from the NAS.

Anything that can send requests from a trusted address can sign in as any user. On the NAS that includes its Docker
containers, whose traffic leaves with the NAS's address; keep `TA_AUTH_PROXY_TRUSTED_IPS` to the one nginx host.

API clients (the browser extension, scripts) send `Authorization: Token …` and do not go through Authentik. Route only
those requests around the check (match on the header, not on `/api/`), since the web app itself also lives under
`/api/` and needs the header to sign in.

## Checking it on the host

- The address TA's nginx sees for the NAS: `docker exec tubearchivist tail -n 5 /var/log/nginx/access.log` while
  loading the page through `ta.mk-bt.com`. It must be the NAS (`192.168.17.16`), not a Docker gateway; if it is a
  gateway, that is what goes in `TA_AUTH_PROXY_TRUSTED_IPS`, and nothing else on that network may reach port 8000.
- Through Authentik: the page opens signed in. Directly on `http://192.168.17.157:8000` with a forged header: still
  the login form.

Tests: `backend/user/tests/test_remote_user_auth.py`, plus the mode rows in `backend/config/tests/test_settings.py`
and `test_startup_commands.py`.

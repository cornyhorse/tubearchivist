from os import environ

TA_AUTH_PROXY_USERNAME_HEADER = (
    environ.get("TA_AUTH_PROXY_USERNAME_HEADER") or "HTTP_REMOTE_USER"
)
TA_AUTH_PROXY_LOGOUT_URL = environ.get("TA_AUTH_PROXY_LOGOUT_URL")

# forwardauth_local only (TrustedRemoteUserMiddleware in
# user.src.remote_user_auth): comma separated addresses or networks of the
# reverse proxies whose user header is trusted, as TA's own nginx sees them
# (X-Real-IP). Empty trusts none.
TA_AUTH_PROXY_TRUSTED_IPS = environ.get("TA_AUTH_PROXY_TRUSTED_IPS") or ""
# create an account for a header user TA does not know (off: such a request
# stays anonymous and can still use the login form)
TA_AUTH_PROXY_CREATE_USERS = bool(environ.get("TA_AUTH_PROXY_CREATE_USERS"))

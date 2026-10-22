"""
Functionality:
- media server settings, stored as the 'mediaserver' config document
- the API key: TA_MEDIASERVER_API_KEY wins, else stored encrypted with a
  key file in the cache dir, so database dumps never hold it in clear
- the key is never returned, printed or logged
"""

import os
from dataclasses import dataclass
from pathlib import Path

from appsettings.models import ConfigDocument
from common.src.env_settings import EnvironmentSettings
from cryptography.fernet import Fernet, InvalidToken
from django.db import transaction

CONFIG_KEY = "mediaserver"
KEY_ENV = "TA_MEDIASERVER_API_KEY"
DEFAULTS = {
    "type": None,
    "url": "",
    "api_key_encrypted": None,
    "library_id": "",
    "path_to": "",
    "notify_enabled": True,
    "debounce_seconds": 30,
    "known_tags": ["David"],
    "last_notify": None,
}
EDITABLE = [
    "type",
    "url",
    "library_id",
    "path_to",
    "notify_enabled",
    "debounce_seconds",
    "known_tags",
]


def _key_file() -> Path:
    return Path(EnvironmentSettings.CACHE_DIR) / ".secrets" / "mediaserver.key"


def _fernet() -> Fernet:
    """the encryption key, created on first use, readable by owner only"""
    path = _key_file()
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def encrypt(secret: str) -> str:
    """encrypt for storage"""
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """stored key, None if missing or the key file changed"""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


@dataclass(frozen=True)
class Connection:
    """what the client needs, the key included"""

    type: str
    url: str
    api_key: str

    def __repr__(self):
        return f"Connection(type={self.type!r}, url={self.url!r})"


def load() -> dict:
    """stored settings with defaults"""
    document = ConfigDocument.objects.filter(key=CONFIG_KEY).first()
    return {**DEFAULTS, **(document.value if document else {})}


def api_key() -> tuple[str | None, str | None]:
    """(key, source): env wins over the stored key"""
    from_env = os.environ.get(KEY_ENV)
    if from_env:
        return from_env, "env"
    stored = decrypt(load()["api_key_encrypted"])
    return (stored, "db") if stored else (None, None)


def public() -> dict:
    """settings as the API returns them, without the key"""
    stored = load()
    key, source = api_key()
    return {
        "type": stored["type"],
        "url": stored["url"],
        "api_key_set": bool(key),
        "api_key_source": source,
        "library_id": stored["library_id"],
        "path_from": EnvironmentSettings.EMBY_ROOT,
        "path_to": stored["path_to"],
        "notify_enabled": stored["notify_enabled"],
        "debounce_seconds": stored["debounce_seconds"],
        "known_tags": stored["known_tags"],
        "last_notify": stored["last_notify"],
    }


def update(data: dict) -> dict:
    """
    change settings; api_key: absent keeps it, None or '' clears it,
    anything else is stored encrypted
    """
    with transaction.atomic():
        document, _ = ConfigDocument.objects.select_for_update().get_or_create(
            key=CONFIG_KEY, defaults={"value": {}}
        )
        value = {**DEFAULTS, **document.value}
        value.update({k: data[k] for k in EDITABLE if k in data})
        if "api_key" in data:
            secret = data["api_key"]
            value["api_key_encrypted"] = encrypt(secret) if secret else None
        document.value = value
        document.save()
    return public()


def record_notify(result: dict) -> None:
    """remember the last notify call"""
    with transaction.atomic():
        document, _ = ConfigDocument.objects.select_for_update().get_or_create(
            key=CONFIG_KEY, defaults={"value": {}}
        )
        document.value = {**document.value, "last_notify": result}
        document.save()


def connection(overrides: dict | None = None) -> Connection | None:
    """
    how to reach the server: stored settings, overridden by unsaved form
    values; None if type, url or key is missing
    """
    stored = load()
    overrides = overrides or {}
    key = overrides.get("api_key") or api_key()[0]
    server_type = overrides.get("type") or stored["type"]
    url = (overrides.get("url") or stored["url"]).rstrip("/")
    if not (server_type and url and key):
        return None
    return Connection(type=server_type, url=url, api_key=key)


def to_server_path(relpath: str) -> str:
    """path below TA's Emby root -> the same file as the server sees it"""
    base = load()["path_to"] or EnvironmentSettings.EMBY_ROOT
    return f"{base.rstrip('/')}/{relpath.lstrip('/')}"


def from_server_path(path: str) -> str | None:
    """the same file as a path below TA's Emby root, None if outside"""
    base = (load()["path_to"] or EnvironmentSettings.EMBY_ROOT).rstrip("/")
    if base and path.startswith(base + "/"):
        return path[len(base) + 1 :]
    return None


def from_ta_path(path: str) -> str:
    """TA-side path under TA_EMBY_ROOT -> relpath; ValueError outside"""
    root = EnvironmentSettings.EMBY_ROOT.rstrip("/")
    if not root or not (path == root or path.startswith(root + "/")):
        raise ValueError(f"{path} is not under TA_EMBY_ROOT")
    return path[len(root) :].lstrip("/")

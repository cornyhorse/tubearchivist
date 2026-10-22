"""
Functionality:
- the set of items whose NFO was rewritten and that the media server must
  read again (access tags are only read on a refresh)
- the Emby writer adds an item the moment it rewrites its NFO, so a sync
  that stops halfway (a CIFS error, a restart) leaves what it did behind in
  the set; an item leaves it only after it was refreshed
- entries: "series:<folder>" for a tvshow.nfo, "episode:<media relpath>"
  for an episode NFO
"""

from datetime import datetime, timezone

from common.src.ta_redis import RedisBase

REFRESH_KEY = "mediaserver:refresh_pending"
SINCE_KEY = "mediaserver:refresh_since"


def _conn():
    return RedisBase().conn


def _key(name: str) -> str:
    return RedisBase.NAME_SPACE + name


def add(entry: str) -> None:
    """remember an item to refresh, and since when something waits"""
    conn = _conn()
    conn.sadd(_key(REFRESH_KEY), entry)
    conn.set(_key(SINCE_KEY), datetime.now(timezone.utc).isoformat(), nx=True)


def entries() -> list[str]:
    """everything waiting, sorted"""
    return sorted(
        e.decode() if isinstance(e, bytes) else e
        for e in _conn().smembers(_key(REFRESH_KEY))
    )


def count() -> int:
    """how many items wait"""
    return _conn().scard(_key(REFRESH_KEY))


def done(entry: str) -> None:
    """an item was refreshed; the clock stops when nothing waits"""
    conn = _conn()
    conn.srem(_key(REFRESH_KEY), entry)
    if not conn.scard(_key(REFRESH_KEY)):
        conn.delete(_key(SINCE_KEY))


def since() -> datetime | None:
    """when the oldest waiting item was added, None if none waits"""
    value = _conn().get(_key(SINCE_KEY))
    if not value:
        return None
    return datetime.fromisoformat(
        value.decode() if isinstance(value, bytes) else value
    )

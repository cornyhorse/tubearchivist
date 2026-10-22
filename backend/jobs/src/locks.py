"""
Functionality:
- emby_lock: one writer of the Emby tree at a time, shared by the
  emby_write and emby_health jobs and the ta_emby_* commands (review T1)
"""

import functools
from contextlib import contextmanager

from common.src.ta_redis import RedisBase
from django.core.management.base import CommandError
from redis.exceptions import LockNotOwnedError

EMBY_LOCK = "lock:emby_sync"
PLAYLIST_LOCK = "lock:emby_playlists"
# a crashed holder frees the lock after this long
LOCK_TIMEOUT = 6 * 3600


class LockBusy(RuntimeError):
    """another Emby writer holds the lock"""


@contextmanager
def _hold(name: str, wait_seconds: float, busy: str):
    """hold a redis lock; LockBusy(busy) if it stays taken"""
    lock = RedisBase().conn.lock(
        RedisBase.NAME_SPACE + name, timeout=LOCK_TIMEOUT
    )
    if wait_seconds:
        acquired = lock.acquire(blocking_timeout=wait_seconds)
    else:
        acquired = lock.acquire(blocking=False)
    if not acquired:
        raise LockBusy(busy)
    try:
        yield
    finally:
        try:
            lock.release()
        except LockNotOwnedError:
            # held longer than LOCK_TIMEOUT, someone else may have it now
            pass


def emby_lock(wait_seconds: float = 0):
    """
    hold the Emby lock; wait up to wait_seconds for it, LockBusy if it
    stays taken
    """
    return _hold(EMBY_LOCK, wait_seconds, "another Emby sync is running")


def playlist_lock(wait_seconds: float = 0):
    """
    one Emby playlist sync at a time, the job or the command: two at once
    could each create a copy of a playlist
    """
    return _hold(
        PLAYLIST_LOCK, wait_seconds, "another Emby playlist sync is running"
    )


# a command waits this long for a running sync to finish
COMMAND_WAIT = 30


def with_emby_lock(handle):
    """decorator for the handle() of a ta_emby_* management command"""

    @functools.wraps(handle)
    def wrapper(self, *args, **options):
        try:
            with emby_lock(wait_seconds=COMMAND_WAIT):
                return handle(self, *args, **options)
        except LockBusy as err:
            raise CommandError(f"{err}, try again when it is done") from err

    return wrapper

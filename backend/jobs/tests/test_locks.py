"""the Emby lock shared by jobs and commands (review T1)"""

import pytest
from django.core.management.base import CommandError
from jobs.src import locks
from jobs.src.locks import LockBusy, emby_lock, with_emby_lock


def test_one_holder_at_a_time():
    with emby_lock():
        with pytest.raises(LockBusy):
            with emby_lock():
                pass
    with emby_lock():
        pass


def test_waits_then_gives_up(monkeypatch):
    with emby_lock():
        with pytest.raises(LockBusy):
            with emby_lock(wait_seconds=0.2):
                pass


def test_expired_lock_released_quietly(monkeypatch):
    monkeypatch.setattr(locks, "LOCK_TIMEOUT", 0.05)
    with emby_lock():
        conn = locks.RedisBase().conn
        conn.delete(locks.RedisBase.NAME_SPACE + locks.EMBY_LOCK)


def test_command_decorator(monkeypatch):
    monkeypatch.setattr(locks, "COMMAND_WAIT", 0.1)
    calls = []

    class Command:
        """stand in management command"""

        @with_emby_lock
        def handle(self, *args, **options):
            """record"""
            calls.append((args, options))
            return "done"

    assert Command().handle(1, x=2) == "done"
    assert calls == [((1,), {"x": 2})]
    with emby_lock():
        with pytest.raises(CommandError, match="another Emby sync"):
            Command().handle()

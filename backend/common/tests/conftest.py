"""test configs"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def change_test_dir(request):
    """change directory to project folder"""
    os.chdir(request.config.rootdir / "backend")


@pytest.fixture(name="redis_clean")
def fixture_redis_clean():
    """delete every key of this test run's redis namespace, before and after"""
    # pylint: disable=import-outside-toplevel
    from common.src.ta_redis import RedisBase

    conn = RedisBase().conn
    pattern = f"{RedisBase.NAME_SPACE}*"

    def wipe():
        for key in conn.scan_iter(match=pattern):
            conn.delete(key)

    wipe()
    yield conn
    wipe()

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


@pytest.fixture(name="app_config")
def fixture_app_config(db):  # pylint: disable=unused-argument
    """default app settings document"""
    # pylint: disable=import-outside-toplevel
    from appsettings.src.config import AppConfig

    AppConfig.__new__(AppConfig).sync_defaults()
    return AppConfig()


@pytest.fixture(name="account")
def fixture_account(db):  # pylint: disable=unused-argument
    """regular user"""
    # pylint: disable=import-outside-toplevel
    from common.src.helper import randomizor
    from user.models import Account

    return Account.objects.create_user("viewer", randomizor(16))


@pytest.fixture(name="admin_account")
def fixture_admin_account(db):  # pylint: disable=unused-argument
    """staff user"""
    # pylint: disable=import-outside-toplevel
    from common.src.helper import randomizor
    from user.models import Account

    return Account.objects.create_superuser("boss", randomizor(16))

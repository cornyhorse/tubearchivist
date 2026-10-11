"""fixtures"""

import pytest

# pylint: disable=unused-import
from download.tests.conftest import fixture_app_config  # noqa: F401
from jobs.src.engine import clear_next_pump
from task.tests.helpers import (  # noqa: F401
    fixture_admin_client,
    fixture_no_broker,
    fixture_redis_ns,
    fixture_user_client,
)


@pytest.fixture(autouse=True)
def fixture_no_pending_wakeup():
    """no wake-up marker left over from another test"""
    clear_next_pump()
    yield
    clear_next_pump()

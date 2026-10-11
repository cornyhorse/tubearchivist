"""notification urls stored in the database, sent through apprise"""

import pytest
from task.src import notify
from task.src.notify import Notifications, get_all_notifications

pytestmark = pytest.mark.django_db


class FakeApprise:
    """stand in for apprise.Apprise, never touches the network"""

    add_result = True
    notify_result = True
    raise_on_notify = False
    sent: list = []

    def __init__(self):
        self.urls = []

    def add(self, url):
        """register url"""
        self.urls.append(url)
        return self.add_result

    def notify(self, body, title):
        """record the notification"""
        if self.raise_on_notify:
            raise RuntimeError("boom")

        self.sent.append((self.urls, title, body))
        return self.notify_result


@pytest.fixture(name="apprise")
def fixture_apprise(monkeypatch):
    """fresh fake apprise per test"""
    fake = type("Apprise", (FakeApprise,), {"sent": []})
    monkeypatch.setattr(notify.apprise, "Apprise", fake)
    return fake


def test_add_and_remove_urls():
    handler = Notifications("download_pending")
    handler.add_url("json://one")
    handler.add_url("json://one")
    handler.add_url("json://two")
    Notifications("run_backup").add_url("json://three")

    assert handler.get_urls() == ["json://one", "json://two"]
    assert get_all_notifications() == {
        "download_pending": {
            "urls": ["json://one", "json://two"],
            "title": "Downloading",
        },
        "run_backup": {"urls": ["json://three"], "title": "Index Backup"},
    }

    handler.remove_url("json://one")
    handler.remove_url("json://unknown")
    assert handler.get_urls() == ["json://two"]

    handler.remove_url("json://two")
    assert "download_pending" not in get_all_notifications()

    Notifications("run_backup").remove_task()
    assert not get_all_notifications()


def test_no_urls_stored():
    assert not get_all_notifications()
    assert Notifications("download_pending").get_urls() == []


def test_notify(apprise):
    handler = Notifications("download_pending")
    handler.notify("[TA] Downloading ended: done", "3 videos")
    assert not apprise.sent

    handler.add_url("json://one")
    handler.notify("[TA] Downloading ended: done", None)
    assert not apprise.sent

    handler.notify("[TA] Downloading ended: done", "3 videos")
    assert apprise.sent == [
        (["json://one"], "[TA] Downloading ended: done", "3 videos")
    ]


@pytest.mark.parametrize(
    "attrs, expected",
    [
        ({}, (True, "Test notification sent successfully")),
        (
            {"add_result": False},
            (False, "Invalid notification URL format: json://x"),
        ),
        ({"notify_result": False}, (False, "Notification failed. ")),
        ({"raise_on_notify": True}, (False, "Notification error: boom")),
    ],
)
def test_test_notification(apprise, attrs, expected):
    for key, value in attrs.items():
        setattr(apprise, key, value)

    success, message = Notifications("manual_test").test("json://x")

    assert success is expected[0]
    assert message.startswith(expected[1])

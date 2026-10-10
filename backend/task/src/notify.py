"""send notifications using apprise"""

import apprise
from appsettings.models import ConfigDocument
from django.db import transaction
from task.src.task_config import TASK_CONFIG
from task.src.task_manager import TaskManager


NOTIFY_KEY = "notify"


class Notifications:
    """store notifications in the database"""


    def __init__(self, task_name: str):
        self.task_name = task_name

    def send(self, task_id: str, task_title: str) -> None:
        """send notifications"""
        apobj = apprise.Apprise()
        urls: list[str] = self.get_urls()
        if not urls:
            return

        title, body = self._build_message(task_id, task_title)

        if not body:
            return

        for url in urls:
            apobj.add(url)

        apobj.notify(body=body, title=title)

    def test(self, url) -> tuple[bool, str]:
        """send test notification"""
        try:
            apobj = apprise.Apprise()

            if not apobj.add(url):
                success = False
                message = f"Invalid notification URL format: {url}"
                return success, message

            title = f"[TA] {self.task_name} process ended with SUCCESS"
            body = "This is a test notification. Task completed successfully."

            result = apobj.notify(body=body, title=title)

            if result:
                success = True
                message = "Test notification sent successfully"
                return success, message

            success = False
            message = (
                "Notification failed. "
                "Please check container logs for more information."
            )
            return success, message

        except Exception as err:  # pylint: disable=broad-exception-caught
            success = False
            message = f"Notification error: {str(err)}"
            return success, message

    def _build_message(
        self, task_id: str, task_title: str
    ) -> tuple[str, str | None]:
        """build message to send notification"""
        task = TaskManager().get_task(task_id)
        status = task.get("status")
        title: str = f"[TA] {task_title} process ended with {status}"
        body: str | None = task.get("result")

        return title, body

    def get_urls(self) -> list[str]:
        """get stored urls for task"""
        return _load().get(self.task_name, [])

    def add_url(self, url: str) -> None:
        """add url to task notification"""
        with transaction.atomic():
            document = _locked()
            urls = document.value.setdefault(self.task_name, [])
            if url not in urls:
                urls.append(url)
                document.save()

    def remove_url(self, url: str) -> None:
        """remove url from task, and the task once it has no urls"""
        with transaction.atomic():
            document = _locked()
            urls = document.value.get(self.task_name, [])
            if url in urls:
                urls.remove(url)
            if not urls:
                document.value.pop(self.task_name, None)
            document.save()

    def remove_task(self) -> None:
        """remove all notifications from task"""
        with transaction.atomic():
            document = _locked()
            document.value.pop(self.task_name, None)
            document.save()


def _load() -> dict:
    document = ConfigDocument.objects.filter(key=NOTIFY_KEY).first()
    return document.value if document else {}


def _locked() -> ConfigDocument:
    document, _ = ConfigDocument.objects.select_for_update().get_or_create(
        key=NOTIFY_KEY, defaults={"value": {}}
    )
    return document


def get_all_notifications() -> dict[str, list[str]]:
    """get all notifications stored"""
    return {
        task_id: {"urls": urls, "title": TASK_CONFIG[task_id]["title"]}
        for task_id, urls in _load().items()
    }

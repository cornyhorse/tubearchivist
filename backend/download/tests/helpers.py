"""helpers for the download tests"""

from appsettings.src.config import AppConfig


class FakeTask:
    """stands in for a celery task: records progress, stops on demand"""

    def __init__(self, stop_after: int | None = None):
        self.messages: list[dict] = []
        self.stop_after = stop_after
        self.stop_checks = 0

    def send_progress(
        self, message_lines, progress=False, level="info", **detail
    ):
        """record a progress message"""
        message = {"lines": message_lines, "progress": progress}
        message["level"] = level
        if detail:
            message["detail"] = detail
        self.messages.append(message)

    def is_stopped(self) -> bool:
        """stopped once is_stopped was called stop_after times"""
        self.stop_checks += 1
        if self.stop_after is None:
            return False
        return self.stop_checks > self.stop_after

    @property
    def lines(self) -> list[str]:
        """all message lines sent"""
        return [line for msg in self.messages for line in msg["lines"]]


def set_config(**sections) -> dict:
    """update parts of the stored config, return the new config"""
    return AppConfig().update_config(sections)

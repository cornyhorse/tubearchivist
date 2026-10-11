"""
Functionality:
- what each job type is: its lane, label, target kind, retries, handler
- the lanes and their defaults
"""

from dataclasses import dataclass
from typing import Callable

# name: (label, description, concurrency, min_interval_seconds)
LANES: dict[str, tuple[str, str, int, int]] = {
    "scan": (
        "Checking YouTube",
        "Channel, playlist and video checks: finds new videos.",
        2,
        0,
    ),
    "download": (
        "Downloading",
        "Downloads one video per job.",
        1,
        10,
    ),
    "postprocess": (
        "After downloads",
        "Playlists, comments, metadata, the Emby library and the media "
        "server.",
        1,
        0,
    ),
    "maintenance": (
        "Maintenance",
        "Backups, refreshes, thumbnail checks and other upkeep.",
        1,
        0,
    ),
}


class JobError(Exception):
    """a job failed in an expected way, the message is shown as is"""


class Defer(Exception):
    """put the job back in the queue, not counted as an attempt"""

    def __init__(self, seconds: int, reason: str):
        super().__init__(reason)
        self.seconds = seconds


@dataclass(frozen=True)
class JobSpec:
    """a job type"""

    type: str
    lane: str
    label: str
    target_kind: str
    handler: Callable
    max_attempts: int = 1
    # task name in TASK_CONFIG, for the old UI's progress messages and for
    # apprise notifications
    legacy_name: str | None = None
    # send apprise notifications when it ends
    notify: bool = True


SPECS: dict[str, JobSpec] = {}


def register(
    job_type: str,
    lane: str,
    label: str,
    target_kind: str = "none",
    max_attempts: int = 1,
    legacy_name: str | None = None,
    notify: bool = True,
) -> Callable:
    """decorator: register handler(ctx) -> result dict for job_type"""

    def wrap(handler: Callable) -> Callable:
        SPECS[job_type] = JobSpec(
            type=job_type,
            lane=lane,
            label=label,
            target_kind=target_kind,
            handler=handler,
            max_attempts=max_attempts,
            legacy_name=legacy_name,
            notify=notify,
        )
        return handler

    return wrap


def get_spec(job_type: str) -> JobSpec:
    """spec of job_type, KeyError if unknown"""
    # handlers register on import, loaded late to avoid import cycles
    # pylint: disable=import-outside-toplevel,unused-import
    import jobs.src.handlers  # noqa: F401
    import mediaserver.src.jobs  # noqa: F401

    return SPECS[job_type]

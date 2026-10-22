"""
Functionality:
- Application startup
"""

import os
from datetime import datetime
from random import randint

from django.core.management.base import BaseCommand
from django.utils import dateformat
from django_celery_beat.models import CrontabSchedule, PeriodicTasks

from appsettings.models import ConfigDocument
from appsettings.src.config import AppConfig, ReleaseVersion
from appsettings.src.snapshot import DbSnapshot
from common.src.env_settings import EnvironmentSettings
from common.src.helper import clear_dl_cache
from common.src.ta_redis import RedisArchivist
from emby.src.sync import paths_from_env
from jobs.src.engine import recover_after_restart
from task.models import CustomPeriodicTask
from task.src.config_schedule import SCHEDULE_KWARGS, ScheduleBuilder
from task.src.task_manager import TaskManager
from task.tasks import version_check

TOPIC = """

#######################
#  Application Start  #
#######################

"""


class Command(BaseCommand):
    """command framework"""

    def handle(self, *args, **options):
        """run all commands"""
        self.stdout.write(TOPIC)
        self._make_folders()
        self._clear_redis_keys()
        self._clear_tasks()
        self._clear_dl_cache()
        self._version_check()
        self._create_default_schedules()
        self._ensure_emby_health_schedule()
        self._update_schedule_tz()
        self._init_app_config()
        self._snapshot_check()
        self._set_ta_startup_time()

    def _make_folders(self):
        """make expected cache folders"""
        self.stdout.write("[1] create expected cache folders")
        folders = [
            "backup",
            "channels",
            "download",
            "import",
            "playlists",
            "videos",
            "ytdlp",
        ]
        cache_dir = EnvironmentSettings.CACHE_DIR
        for folder in folders:
            folder_path = os.path.join(cache_dir, folder)
            os.makedirs(folder_path, exist_ok=True)

        self.stdout.write(self.style.SUCCESS("    ✓ expected folders created"))

    def _clear_redis_keys(self):
        """make sure there are no leftover locks or keys set in redis"""
        self.stdout.write("[2] clear leftover keys in redis")
        all_keys = [
            "dl_queue_id",
            "dl_queue",
            "downloading",
            "manual_import",
            "reindex",
            "rescan",
            "run_backup",
            "startup_check",
            "reindex:ta_video",
            "reindex:ta_channel",
            "reindex:ta_playlist",
        ]

        redis_con = RedisArchivist()
        has_changed = False
        for key in all_keys:
            if redis_con.del_message(key):
                self.stdout.write(
                    self.style.SUCCESS(f"    ✓ cleared key {key}")
                )
                has_changed = True

        if not has_changed:
            self.stdout.write(self.style.SUCCESS("    no keys found"))

    def _clear_tasks(self):
        """clear tasks and messages"""
        self.stdout.write("[3] clear task leftovers")
        TaskManager().fail_pending()
        requeued = recover_after_restart()
        if requeued:
            self.stdout.write(
                self.style.SUCCESS(f"    ✓ requeued {requeued} jobs")
            )
        CustomPeriodicTask.objects.update(kwargs=SCHEDULE_KWARGS)
        redis_con = RedisArchivist()
        to_delete = redis_con.list_keys("message:")
        if to_delete:
            for key in to_delete:
                redis_con.del_message(key)

            self.stdout.write(
                self.style.SUCCESS(f"    ✓ cleared {len(to_delete)} messages")
            )

    def _clear_dl_cache(self):
        """clear leftover files from dl cache"""
        self.stdout.write("[4] clear leftover files from dl cache")
        leftover_files = clear_dl_cache(EnvironmentSettings.CACHE_DIR)
        if leftover_files:
            self.stdout.write(
                self.style.SUCCESS(f"    ✓ cleared {leftover_files} files")
            )
        else:
            self.stdout.write(self.style.SUCCESS("    no files found"))

    def _version_check(self):
        """remove new release key if updated now"""
        self.stdout.write("[5] check for first run after update")
        new_version = ReleaseVersion().is_updated()
        if new_version:
            self.stdout.write(
                self.style.SUCCESS(f"    ✓ update to {new_version} completed")
            )
        else:
            self.stdout.write(self.style.SUCCESS("    no new update found"))

        version_task = CustomPeriodicTask.objects.filter(name="version_check")
        if not version_task.exists():
            return

        if not version_task.first().last_run_at:
            self.stdout.write(
                self.style.SUCCESS("    ✓ send initial version check task")
            )
            version_check.delay()

    def _ensure_emby_health_schedule(self) -> None:
        """hourly Emby sync health check, when the Emby writer is on"""
        if not paths_from_env():
            return
        if CustomPeriodicTask.objects.filter(name="emby_health").exists():
            return
        builder = ScheduleBuilder()
        builder.get_set_task(
            "emby_health", schedule=builder.SCHEDULES["emby_health"]
        )
        self.stdout.write(
            self.style.SUCCESS("    ✓ scheduled the hourly Emby check")
        )

    def _create_default_schedules(self) -> None:
        """create default schedules for new installations"""
        self.stdout.write("[6] create initial schedules")
        init_has_run = CustomPeriodicTask.objects.filter(
            name="version_check"
        ).exists()

        if init_has_run:
            self.stdout.write(
                self.style.SUCCESS(
                    "    schedule init already done, skipping..."
                )
            )
            return

        builder = ScheduleBuilder()
        check_reindex = builder.get_set_task(
            "check_reindex", schedule=builder.SCHEDULES["check_reindex"]
        )
        check_reindex.task_config.update({"days": 90})
        check_reindex.last_run_at = dateformat.make_aware(datetime.now())
        check_reindex.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"    ✓ created new default schedule: {check_reindex}"
            )
        )

        thumbnail_check = builder.get_set_task(
            "thumbnail_check", schedule=builder.SCHEDULES["thumbnail_check"]
        )
        thumbnail_check.last_run_at = dateformat.make_aware(datetime.now())
        thumbnail_check.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"    ✓ created new default schedule: {thumbnail_check}"
            )
        )
        daily_random = f"{randint(0, 59)} {randint(0, 23)} *"
        version_check_task = builder.get_set_task(
            "version_check", schedule=daily_random
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"    ✓ created new default schedule: {version_check_task}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS("    ✓ all default schedules created")
        )

    def _update_schedule_tz(self) -> None:
        """update timezone for Schedule instances"""
        self.stdout.write("[7] validate schedules TZ")
        tz = EnvironmentSettings.TZ
        to_update = CrontabSchedule.objects.exclude(timezone=tz)

        if not to_update.exists():
            self.stdout.write(
                self.style.SUCCESS("    all schedules have correct TZ")
            )
            return

        updated = to_update.update(timezone=tz)
        self.stdout.write(
            self.style.SUCCESS(f"    ✓ updated {updated} schedules to {tz}.")
        )
        PeriodicTasks.update_changed()

    def _init_app_config(self) -> None:
        """create default app config, or sync it with current defaults"""
        self.stdout.write("[8] Check AppConfig")
        if not ConfigDocument.objects.filter(key=AppConfig.KEY).exists():
            handler = AppConfig.__new__(AppConfig)
            handler.sync_defaults()
            self.stdout.write(
                self.style.SUCCESS("    ✓ Created default appsettings.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS("    skip completed appsettings init")
        )
        updated_defaults = AppConfig().add_new_defaults()
        for new_default in updated_defaults:
            self.stdout.write(
                self.style.SUCCESS(f"    added new default: {new_default}")
            )

        cleared = AppConfig().clear_old_keys()
        for removed_key in cleared:
            self.stdout.write(
                self.style.SUCCESS(f"    removed old key: {removed_key}")
            )

    def _snapshot_check(self):
        """schedule daily database snapshots if enabled"""
        self.stdout.write("[9] setup snapshots")
        if AppConfig().config["application"]["enable_snapshot"]:
            DbSnapshot().setup()
        else:
            DbSnapshot.remove_schedule()
            self.stdout.write(self.style.SUCCESS("    snapshots disabled"))

    def _set_ta_startup_time(self) -> None:
        """set startup time to trigger frontend refresh, threadsafe"""
        self.stdout.write("[10] Set startup timestamp")
        message = str(int(datetime.now().timestamp() // 10 * 10))
        RedisArchivist().set_message(
            "STARTTIMESTAMP", message=message, save=True
        )
        self.stdout.write(
            self.style.SUCCESS(f"    ✓ set timestamp to {message}.")
        )

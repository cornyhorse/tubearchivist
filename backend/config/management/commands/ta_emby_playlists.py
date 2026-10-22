"""sync the Emby playlists now, or show what a sync would do"""

from django.core.management.base import BaseCommand, CommandError
from emby.src.playlist_sync import (
    media_paths,
    record_run,
    sync_playlists,
)
from emby.src.sync import paths_from_env
from emby.src.writer import Manifest
from jobs.src.locks import (
    COMMAND_WAIT,
    LockBusy,
    emby_lock,
    playlist_lock,
)
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError


class Command(BaseCommand):
    """create, update and remove the playlists TA keeps in Emby"""

    help = "Keep Emby playlists in step with the TA playlists (Emby only)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-large-removal",
            action="store_true",
            help="also do a change that removes most of a playlist",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="list what would change, change nothing in Emby",
        )

    def _write(self, result, prefix: str) -> None:
        for verb in ("created", "changed", "deleted"):
            for label in getattr(result, verb):
                self.stdout.write(f"{prefix}{verb} {label}")
        for line in result.warnings:
            self.stdout.write(f"warning: {line}")
        for line in result.errors:
            self.stdout.write(f"error: {line}")
        if result.skipped:
            self.stdout.write(f"{prefix}held back: {result.skipped}")
        self.stdout.write(f"{prefix}{result.summary()}")

    def handle(self, *args, **options):
        conn = settings.connection()
        paths = paths_from_env()
        if not conn or conn.type != "emby":
            raise CommandError("an Emby server needs to be configured")
        if not paths:
            raise CommandError("TA_EMBY_ROOT is not set")
        dry_run = options["dry_run"]
        try:
            with playlist_lock(wait_seconds=COMMAND_WAIT):
                with emby_lock(wait_seconds=COMMAND_WAIT):
                    media = media_paths(
                        Manifest(paths.output_root, dry_run=True)
                    )
                result = sync_playlists(
                    MediaServerClient(conn),
                    media,
                    dry_run,
                    options["allow_large_removal"],
                )
        except LockBusy as err:
            raise CommandError(f"{err}, try again when it is done") from err
        except MediaServerError as err:
            raise CommandError(str(err)) from err
        if not dry_run and not result.skipped:
            record_run(result=result)
        self._write(result, "[dry-run] " if dry_run else "")
        if result.errors:
            raise CommandError(f"{len(result.errors)} playlists failed")

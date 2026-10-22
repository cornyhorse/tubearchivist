"""sync the Emby playlists now, or show what a sync would do"""

from django.core.management.base import BaseCommand, CommandError
from emby.src.playlist_sync import (
    media_paths,
    record_run,
    sync_playlists,
)
from emby.src.sync import paths_from_env
from emby.src.writer import Manifest
from jobs.src.locks import with_emby_lock
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError


class Command(BaseCommand):
    """create, update and remove the playlists TA keeps in Emby"""

    help = "Keep Emby playlists in step with the TA playlists (Emby only)"

    def add_arguments(self, parser):
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
        self.stdout.write(f"{prefix}{result.summary()}")

    @with_emby_lock
    def handle(self, *args, **options):
        conn = settings.connection()
        paths = paths_from_env()
        if not conn or conn.type != "emby":
            raise CommandError("an Emby server needs to be configured")
        if not paths:
            raise CommandError("TA_EMBY_ROOT is not set")
        media = media_paths(Manifest(paths.output_root, dry_run=True))
        dry_run = options["dry_run"]
        prefix = "[dry-run] " if dry_run else ""
        try:
            result = sync_playlists(MediaServerClient(conn), media, dry_run)
        except MediaServerError as err:
            raise CommandError(str(err)) from err
        if not dry_run:
            record_run(result=result)
        self._write(result, prefix)
        if result.errors:
            raise CommandError(f"{len(result.errors)} playlists failed")

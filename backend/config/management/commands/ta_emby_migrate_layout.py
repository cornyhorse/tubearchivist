"""
one-time Emby layout migration: frozen names, MMDD*100+n dates and
frozen playlists, then the single sync that renames the files
see docs/fork/emby-layout-migration.md
"""

from common.src.env_settings import EnvironmentSettings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from emby.src.migrate_layout import AlreadyMigrated, migrate_layout
from emby.src.sync import paths_from_env, sync_all


class _DryRun(Exception):
    """rolls the dry run back"""


class Command(BaseCommand):
    """convert stored Emby numbers and names, then sync once"""

    help = (
        "Freeze Emby file names, give the date scheme 100 slots per day "
        "and freeze playlist numbering, then rename the tree in one sync"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="show the database changes and the renames, change nothing",
        )
        parser.add_argument(
            "--keep-playlist-position",
            nargs="*",
            default=[],
            metavar="PLAYLIST_ID",
            help="playlists that keep following the playlist order",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="convert the database only, the next emby_sync renames",
        )
        parser.add_argument("--verbose-changes", action="store_true")

    def handle(self, *args, **options):
        # TODO: take the emby_sync lock (review T1) once it exists
        paths = paths_from_env()
        if not paths and not (options["no_sync"] or options["dry_run"]):
            raise CommandError("TA_EMBY_ROOT is not set; use --no-sync")
        try:
            if options["dry_run"]:
                self._dry_run(paths, options)
                return
            summary = migrate_layout(
                EnvironmentSettings.TZ, set(options["keep_playlist_position"])
            )
        except AlreadyMigrated as err:
            raise CommandError(str(err)) from err

        self._write_summary(summary, "")
        if options["no_sync"]:
            self.stdout.write("database migrated, not synced")
            return
        self._write_report(sync_all(paths), "", options)

    def _dry_run(self, paths, options) -> None:
        prefix = "[dry-run] "
        try:
            with transaction.atomic():
                summary = migrate_layout(
                    EnvironmentSettings.TZ,
                    set(options["keep_playlist_position"]),
                )
                self._write_summary(summary, prefix)
                if paths:
                    report = sync_all(paths, dry_run=True)
                    self._write_report(report, prefix, options)
                raise _DryRun
        except _DryRun:
            self.stdout.write(f"{prefix}rolled back, nothing changed")

    def _write_summary(self, summary, prefix: str) -> None:
        for youtube_id in summary.moved_to_own_day:
            self.stdout.write(f"{prefix}back to its own day: {youtube_id}")
        self.stdout.write(
            f"{prefix}date numbers {summary.date_numbers}, "
            f"moved to own day {len(summary.moved_to_own_day)}, "
            f"playlists frozen {len(summary.playlists_frozen)}, "
            f"playlists kept on position {len(summary.playlists_kept)}, "
            f"names frozen {summary.names_frozen}"
        )

    def _write_report(self, report, prefix: str, options) -> None:
        if options["verbose_changes"]:
            for old, new in report.renamed:
                self.stdout.write(f"{prefix}rename {old} -> {new}")
            for path in report.created:
                self.stdout.write(f"{prefix}create {path}")
            for path in report.deleted:
                self.stdout.write(f"{prefix}delete {path}")
        for line in report.errors:
            self.stdout.write(f"error: {line}")
        for line in report.conflicts + report.warnings[:50]:
            self.stdout.write(f"warning: {line}")
        self.stdout.write(
            f"{prefix}renamed {len(report.renamed)}, "
            f"created {len(report.created)}, updated {len(report.updated)}, "
            f"deleted {len(report.deleted)}, unchanged {report.unchanged}, "
            f"errors {len(report.errors)}"
        )

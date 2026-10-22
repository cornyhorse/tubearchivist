"""
one-time Emby layout migration: frozen names, MMDD*100+n dates and
frozen playlists, then shows = channels with an "Other videos" show, then
the single sync that renames the files
see docs/fork/emby-layout-migration.md
"""

from common.src.env_settings import EnvironmentSettings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from emby.src.migrate_layout import AlreadyMigrated, migrate_layout
from emby.src.shows_change import describe, watch_history_at_risk
from emby.src.sync import paths_from_env, preflight, sync_all
from emby.src.tags import known_tags
from emby.src.visibility import before_state, compare, write_report
from emby.src.writer import EmbyRootError, Manifest
from jobs.src.locks import with_emby_lock
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError


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

    @with_emby_lock
    def handle(self, *args, **options):
        paths = paths_from_env()
        if not paths and not (options["no_sync"] or options["dry_run"]):
            raise CommandError("TA_EMBY_ROOT is not set; use --no-sync")
        before = self._visibility_before(paths)
        try:
            if paths and not (options["no_sync"] and not options["dry_run"]):
                # before the database changes: a root that can't take the
                # renames must not leave the database migrated, and the
                # dry run checks exactly what the real run checks
                preflight(paths)
            if options["dry_run"]:
                self._dry_run(paths, options, before)
                return
            summary = migrate_layout(
                EnvironmentSettings.TZ, set(options["keep_playlist_position"])
            )
        except (AlreadyMigrated, EmbyRootError) as err:
            raise CommandError(str(err)) from err

        self._write_summary(summary, "")
        self._compare(before, "")
        if options["no_sync"]:
            self.stdout.write("database migrated, not synced")
            return
        self._write_report(sync_all(paths), "", options)

    def _visibility_before(self, paths):
        """
        what each Emby tag opens now (read only), taken before anything
        changes because a real sync rewrites the manifest it is matched by
        """
        conn = settings.connection()
        if not (paths and conn):
            return None
        try:
            files = Manifest(paths.output_root, dry_run=True).files
            return before_state(MediaServerClient(conn), known_tags(), files)
        except MediaServerError as err:
            self.stdout.write(f"visibility not compared: {err}")
            return None

    def _compare(self, before, prefix: str) -> None:
        """who sees what, now against after the change"""
        if before is not None:
            write_report(compare(before), self.stdout.write, prefix)

    def _dry_run(self, paths, options, before) -> None:
        prefix = "[dry-run] "
        try:
            with transaction.atomic():
                summary = migrate_layout(
                    EnvironmentSettings.TZ,
                    set(options["keep_playlist_position"]),
                )
                self._write_summary(summary, prefix)
                self._compare(before, prefix)
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

    def _write_shows(self, report, prefix: str) -> None:
        """the shows that go, what is created, and who loses watch state"""
        change = describe(report)
        for folder, count in change.removed_shows.items():
            self.stdout.write(
                f"{prefix}show removed: {folder} ({count} files)"
            )
        self.stdout.write(
            f"{prefix}playlist shows removed {change.removed_playlist_shows},"
            f" channel shows moved to Other videos "
            f"{change.removed_channel_shows}, episodes created in Other "
            f"videos {change.other_created}"
        )
        at_risk, error = watch_history_at_risk(change.removed_media)
        if error:
            self.stdout.write(f"{prefix}watch history not read: {error}")
        for user, paths in at_risk.items():
            self.stdout.write(
                f"{prefix}watch history lost for {user}: {len(paths)} items"
            )
            for path in paths:
                self.stdout.write(f"{prefix}  {path}")
        if not error and not at_risk:
            self.stdout.write(f"{prefix}no watch history on removed items")
        self._write_renamed(change, prefix)

    def _write_renamed(self, change, prefix: str) -> None:
        """watch state on files that only move: kept if Emby sees the move"""
        at_risk, error = watch_history_at_risk(change.renamed_media)
        if error or not at_risk:
            return
        self.stdout.write(
            f"{prefix}renamed files with watch history (Emby normally keeps "
            "it when a file is renamed; check a few after the run):"
        )
        for user, paths in at_risk.items():
            self.stdout.write(f"{prefix}  {user}: {len(paths)} items")

    def _write_report(self, report, prefix: str, options) -> None:
        self._write_shows(report, prefix)
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

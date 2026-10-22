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
from emby.src.visibility import (
    before_state,
    compare,
    hand_set_metadata,
    write_report,
)
from emby.src.writer import EmbyRootError, Manifest
from jobs.src.handlers import after_sync
from jobs.src.locks import with_emby_lock
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError
from mediaserver.src.jobs import changed_items, media_by_stem


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
        parser.add_argument(
            "--accept-lost",
            action="store_true",
            help="run even if someone would lose access to videos they "
            "can see now, or Emby can't be read to check",
        )

    @with_emby_lock
    def handle(self, *args, **options):
        paths = paths_from_env()
        if not paths and not (options["no_sync"] or options["dry_run"]):
            raise CommandError("TA_EMBY_ROOT is not set; use --no-sync")
        before, unreadable = self._visibility_before(paths)
        self._guard(before, unreadable, options)
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

        self._write_summary(summary, "", options["verbose_changes"])
        self._compare(before, "")
        self._hand_set(paths, "")
        if options["no_sync"]:
            self.stdout.write("database migrated, not synced")
            return
        report = sync_all(paths)
        self._write_report(report, "", options, paths)
        # the run that first writes new episode tags into existing items:
        # Emby reads those only when told to
        after_sync(report, paths)

    def _visibility_before(self, paths):
        """
        (what each Emby tag opens now, why that couldn't be read): taken
        before anything changes because a real sync rewrites the manifest
        it is matched by; (None, None) without a media server to protect
        """
        conn = settings.connection()
        if not (paths and conn):
            return None, None
        try:
            files = Manifest(paths.output_root, dry_run=True).files
            state = before_state(MediaServerClient(conn), known_tags(), files)
        except MediaServerError as err:
            self.stdout.write(f"visibility not compared: {err}")
            return None, str(err)
        return state, None

    def _guard(self, before, unreadable, options) -> None:
        """
        a real run refuses, before the database changes, when someone would
        lose access they have now, or when that can't be checked: a
        refresh that replaces metadata sets a show's tags to exactly what
        TA writes, so a tag nobody imported into TA would be taken away
        """
        if options["dry_run"] or options["accept_lost"]:
            return
        if unreadable:
            raise CommandError(
                f"can't check who would lose access ({unreadable}); fix "
                "the media server settings, or --accept-lost"
            )
        if before is None:
            return
        results = compare(before)
        lost = sum(len(result.lost) for result in results)
        if lost:
            write_report(results, self.stdout.write)
            raise CommandError(
                f"{lost} videos would lose access they have now: tag their "
                "channels or playlists (ta_emby_import_tags), or "
                "--accept-lost. Nothing was changed."
            )

    def _compare(self, before, prefix: str) -> None:
        """who sees what, now against after the change"""
        if before is None:
            return
        results = compare(before)
        write_report(results, self.stdout.write, prefix)
        lost = sum(len(result.lost) for result in results)
        if lost and prefix:
            self.stdout.write(
                f"{prefix}a real run would refuse: {lost} videos lose "
                "access (--accept-lost to go on)"
            )

    def _hand_set(self, paths, prefix: str) -> None:
        """metadata set by hand on TA's items, which a refresh clears"""
        conn = settings.connection()
        if not (paths and conn):
            return
        try:
            count, lines = hand_set_metadata(MediaServerClient(conn))
        except MediaServerError as err:
            self.stdout.write(f"{prefix}hand-set metadata not read: {err}")
            return
        self.stdout.write(
            f"{prefix}hand-set metadata on {count} items that the item "
            "refresh after the run clears (ratings, genres, tags TA does "
            "not use):"
        )
        for line in lines:
            self.stdout.write(f"{prefix}  {line}")

    def _dry_run(self, paths, options, before) -> None:
        prefix = "[dry-run] "
        try:
            with transaction.atomic():
                summary = migrate_layout(
                    EnvironmentSettings.TZ,
                    set(options["keep_playlist_position"]),
                )
                self._write_summary(
                    summary, prefix, options["verbose_changes"]
                )
                self._compare(before, prefix)
                self._hand_set(paths, prefix)
                if paths:
                    report = sync_all(paths, dry_run=True)
                    self._write_report(report, prefix, options, paths)
                raise _DryRun
        except _DryRun:
            self.stdout.write(f"{prefix}rolled back, nothing changed")

    def _write_summary(
        self, summary, prefix: str, verbose: bool = False
    ) -> None:
        self.stdout.write(
            f"{prefix}UTC dates: {len(summary.utc_renumbered)} episodes "
            f"renumbered, {summary.utc_season_changes} of them change season"
        )
        if verbose:
            for item in summary.utc_renumbered:
                self.stdout.write(f"{prefix}  UTC date: {item}")
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

    def _write_refresh(self, report, paths, prefix: str) -> None:
        """how many item refreshes the media server will be asked for"""
        items = changed_items(
            report, media_by_stem(Manifest(paths.output_root).files)
        )
        shows = sum(1 for item in items if item.startswith("series:"))
        stored = settings.load()
        runs = -(-len(items) // stored["refresh_batch_size"])
        self.stdout.write(
            f"{prefix}item refreshes queued: {len(items)} ({shows} shows, "
            f"{len(items) - shows} episodes), {stored['refresh_batch_size']}"
            f" per run, {runs} runs {stored['refresh_pause_seconds']} s "
            "apart; Emby re-reads each item's files"
        )

    def _write_report(self, report, prefix: str, options, paths) -> None:
        self._write_shows(report, prefix)
        self._write_refresh(report, paths, prefix)
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

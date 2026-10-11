"""write the native Emby library tree"""

from pathlib import Path

from common.src.env_settings import EnvironmentSettings
from django.core.management.base import BaseCommand, CommandError
from emby.src.sync import (
    EmbyLayoutOutdated,
    EmbyPaths,
    sync_all,
    sync_channel,
    sync_playlist,
    validate_output_root,
)
from emby.src.writer import EmbyRootError


class Command(BaseCommand):
    """sync channels and playlists into an Emby library root"""

    help = "Write <episodedetails> NFOs, thumbs and media links for Emby"

    def add_arguments(self, parser):
        parser.add_argument("output_root", type=Path)
        parser.add_argument("--channel", help="only this channel id")
        parser.add_argument("--playlist", help="only this playlist id")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--verbose-changes", action="store_true")

    def _write_changes(self, report, prefix: str) -> None:
        """one line per filesystem change"""
        changes = (
            [f"create {path}" for path in report.created]
            + [f"update {path}" for path in report.updated]
            + [f"rename {old} -> {new}" for old, new in report.renamed]
            + [f"delete {path}" for path in report.deleted]
        )
        for change in changes:
            self.stdout.write(f"{prefix}{change}")

    def handle(self, *args, **options):
        output_root = options["output_root"].resolve()
        media_root = Path(EnvironmentSettings.MEDIA_DIR).resolve()
        try:
            validate_output_root(media_root, output_root)
        except ValueError as err:
            raise CommandError(str(err)) from err

        paths = EmbyPaths(
            media_root=media_root,
            cache_root=Path(EnvironmentSettings.CACHE_DIR),
            output_root=output_root,
            timezone=EnvironmentSettings.TZ,
        )
        dry_run = options["dry_run"]
        try:
            report = self._sync(paths, options)
        except (EmbyLayoutOutdated, EmbyRootError) as err:
            raise CommandError(str(err)) from err

        prefix = "[dry-run] " if dry_run else ""
        if options["verbose_changes"]:
            self._write_changes(report, prefix)
        for line in report.errors:
            self.stdout.write(f"error: {line}")
        for line in report.conflicts + report.warnings[:50]:
            self.stdout.write(f"warning: {line}")

        self.stdout.write(
            f"{prefix}created {len(report.created)}, "
            f"updated {len(report.updated)}, renamed {len(report.renamed)}, "
            f"deleted {len(report.deleted)}, unchanged {report.unchanged}, "
            f"conflicts {len(report.conflicts)}, "
            f"warnings {len(report.warnings)}, errors {len(report.errors)}"
        )
        if report.errors:
            raise CommandError(f"{len(report.errors)} series failed")

    @staticmethod
    def _sync(paths, options):
        dry_run = options["dry_run"]
        if options["channel"]:
            return sync_channel(options["channel"], paths, dry_run)
        if options["playlist"]:
            return sync_playlist(options["playlist"], paths, dry_run)
        return sync_all(paths, dry_run)

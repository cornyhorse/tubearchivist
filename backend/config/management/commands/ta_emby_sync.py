"""write the native Emby library tree"""

from pathlib import Path

from common.src.env_settings import EnvironmentSettings
from django.core.management.base import BaseCommand, CommandError
from emby.src.sync import EmbyPaths, sync_all, sync_channel, sync_playlist


class Command(BaseCommand):
    """sync channels and playlists into an Emby library root"""

    help = "Write <episodedetails> NFOs, thumbs and media links for Emby"

    def add_arguments(self, parser):
        parser.add_argument("output_root", type=Path)
        parser.add_argument("--channel", help="only this channel id")
        parser.add_argument("--playlist", help="only this playlist id")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--verbose-changes", action="store_true")

    def handle(self, *args, **options):
        output_root = options["output_root"].resolve()
        media_root = Path(EnvironmentSettings.MEDIA_DIR).resolve()
        if output_root == media_root or media_root in output_root.parents:
            raise CommandError("output root must be outside the media dir")

        paths = EmbyPaths(
            media_root=media_root,
            cache_root=Path(EnvironmentSettings.CACHE_DIR),
            output_root=output_root,
            timezone=EnvironmentSettings.TZ,
        )
        dry_run = options["dry_run"]
        if options["channel"]:
            report = sync_channel(options["channel"], paths, dry_run)
        elif options["playlist"]:
            report = sync_playlist(options["playlist"], paths, dry_run)
        else:
            report = sync_all(paths, dry_run)

        prefix = "[dry-run] " if dry_run else ""
        if options["verbose_changes"]:
            for path in report.created:
                self.stdout.write(f"{prefix}create {path}")
            for path in report.updated:
                self.stdout.write(f"{prefix}update {path}")
            for old, new in report.renamed:
                self.stdout.write(f"{prefix}rename {old} -> {new}")
            for path in report.deleted:
                self.stdout.write(f"{prefix}delete {path}")
        for line in report.conflicts + report.warnings[:50]:
            self.stdout.write(f"warning: {line}")

        self.stdout.write(
            f"{prefix}created {len(report.created)}, "
            f"updated {len(report.updated)}, renamed {len(report.renamed)}, "
            f"deleted {len(report.deleted)}, unchanged {report.unchanged}, "
            f"conflicts {len(report.conflicts)}, "
            f"warnings {len(report.warnings)}"
        )

"""import a folder of media files as an archived channel"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from jobs.models import CreatedBy
from jobs.src.engine import enqueue
from jobs.src.handlers import request_emby_write
from video.src.archive_import import MODES, ArchiveImporter, ArchiveImportError


class Command(BaseCommand):
    """index old YouTube archive folders that have no YouTube metadata"""

    help = (
        "Index the media files of a folder as videos of an archived channel, "
        "without any network access. Safe to run again: only new files are "
        "added. See docs/fork/archive-import.md"
    )

    def add_arguments(self, parser):
        parser.add_argument("root", type=Path, help="folder of media files")
        parser.add_argument(
            "--channel-name",
            help="name of the archived channel, default: tvshow.nfo title "
            "or the folder name",
        )
        parser.add_argument(
            "--channel-id",
            help="add to this indexed channel (YouTube or archived) instead",
        )
        parser.add_argument(
            "--mode",
            choices=MODES,
            default="auto",
            help="auto: hardlink, copy across filesystems; move removes "
            "the file from the folder",
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--queue",
            action="store_true",
            help="run as a job in the maintenance lane, not in this process",
        )
        parser.add_argument(
            "--no-emby",
            action="store_true",
            help="don't queue an Emby library write afterwards",
        )

    def handle(self, *args, **options):
        root = options["root"].resolve()
        params = {
            "root": str(root),
            "channel_name": options["channel_name"],
            "channel_id": options["channel_id"],
            "mode": options["mode"],
        }
        if options["queue"]:
            job, _ = enqueue(
                "archive_import",
                params=params,
                dedupe_key=f"archive_import:{root}",
                created_by=CreatedBy.USER,
            )
            self.stdout.write(f"queued job {job.pk}")
            return

        importer = ArchiveImporter(dry_run=options["dry_run"], **params)
        try:
            report = importer.run()
        except ArchiveImportError as err:
            raise CommandError(str(err)) from err

        verb = "would import" if options["dry_run"] else "imported"
        for name in report.imported:
            self.stdout.write(f"{verb} {name}")
        for name, reason in report.failed:
            self.stderr.write(f"failed {name}: {reason}")
        self.stdout.write(report.message())
        if (
            report.imported
            and not options["dry_run"]
            and not options["no_emby"]
        ):
            request_emby_write()
        if report.failed:
            raise CommandError(f"{len(report.failed)} files failed")

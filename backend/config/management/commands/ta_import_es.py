"""import an Elasticsearch export into postgres"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from storage.importer import EsImporter


class Command(BaseCommand):
    """
    one-shot ES -> Postgres migration
    export first with: python -m storage.es_export <dir>
    """

    help = "Import an ES export (storage.es_export) into Postgres"

    def add_arguments(self, parser):
        parser.add_argument("export_dir", type=Path)
        parser.add_argument("--sample-size", type=int, default=50)

    def handle(self, *args, **options):
        export_dir = options["export_dir"]
        if not (export_dir / "manifest.json").exists():
            raise CommandError(f"no manifest.json in {export_dir}")

        report = EsImporter(export_dir, options["sample_size"]).run()
        for line in report.lines():
            self.stdout.write(line)

        if not report.ok:
            raise CommandError("import verification failed")

"""import users, tokens and schedules from a stock TA db.sqlite3"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from storage.sqlite_import import SqliteImporter


class Command(BaseCommand):
    """run before ta_import_es so user settings find their users"""

    help = "Copy users, groups, API tokens and schedules from db.sqlite3"

    def add_arguments(self, parser):
        parser.add_argument("sqlite_path", type=Path)

    def handle(self, *args, **options):
        try:
            report = SqliteImporter(options["sqlite_path"]).run()
        except FileNotFoundError as err:
            raise CommandError(f"no such file: {err}") from err

        for line in report.lines():
            self.stdout.write(line)

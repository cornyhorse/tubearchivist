"""check the Emby sync and repair what is safe, see emby/src/health.py"""

from django.core.management.base import BaseCommand, CommandError
from emby.src.health import check_emby_health
from emby.src.sync import paths_from_env
from jobs.src.locks import with_emby_lock


class Command(BaseCommand):
    """detect a stuck or failing Emby sync, repair the safe cases"""

    help = "Check the Emby sync; exits non-zero when a problem remains"

    def add_arguments(self, parser):
        parser.add_argument("--no-repair", action="store_true")
        parser.add_argument("--max-age-hours", type=float, default=6)
        parser.add_argument("--failing-runs", type=int, default=3)

    @with_emby_lock
    def handle(self, *args, **options):
        paths = paths_from_env()
        if not paths:
            raise CommandError("TA_EMBY_ROOT is not set")
        report = check_emby_health(
            paths,
            max_age_hours=options["max_age_hours"],
            failing_runs=options["failing_runs"],
            repair=not options["no_repair"],
        )
        self.stdout.write(report.message())
        if report.rerun_sync:
            self.stdout.write("run ta_emby_sync (or emby_sync) to finish")
        if not report.healthy:
            raise CommandError(f"{len(report.problems)} problems")

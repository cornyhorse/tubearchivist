"""
one-time import of the Emby tags set by hand (David) onto TA channels and
playlists; reads Emby, never writes to it
see docs/fork/emby-v050-design.md
"""

from django.core.management.base import BaseCommand, CommandError
from emby.src.tag_import import apply_import, plan_import
from jobs.src.handlers import request_emby_write
from mediaserver.src import settings
from mediaserver.src.client import MediaServerClient, MediaServerError


class Command(BaseCommand):
    """map tagged Emby shows onto channels and playlists"""

    help = (
        "Read the Emby shows carrying a tag and add the tag to the matching "
        "TA channel or playlist; --dry-run only lists the mapping"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tag",
            nargs="+",
            default=["David"],
            help="Emby tags to import (default: David)",
        )
        parser.add_argument(
            "--map",
            nargs="*",
            default=[],
            metavar="EMBY_NAME=ID",
            help="decide a show by hand: its Emby name and the TA id",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="list the mapping, change nothing",
        )

    def handle(self, *args, **options):
        conn = settings.connection()
        if not conn:
            raise CommandError("no media server configured")
        manual = {}
        for pair in options["map"]:
            name, _, target = pair.rpartition("=")
            if not name or not target:
                raise CommandError(f"--map expects NAME=ID, got {pair!r}")
            manual[name] = target
        try:
            plan = plan_import(MediaServerClient(conn), options["tag"], manual)
        except MediaServerError as err:
            raise CommandError(str(err)) from err
        for item in plan:
            where = (
                f"{item.kind} {item.target_id} ({item.target_name})"
                if item.kind
                else "NOT MAPPED"
            )
            self.stdout.write(
                f"{item.emby_name} [{', '.join(item.tags)}] -> {where}"
                f" ({item.how})"
            )
        unmapped = [item for item in plan if not item.kind]
        self.stdout.write(
            f"{len(plan) - len(unmapped)} mapped, {len(unmapped)} not mapped"
        )
        if options["dry_run"]:
            self.stdout.write("dry run: nothing changed")
            return
        changed = apply_import(plan)
        for target, tags in changed.items():
            self.stdout.write(f"{target}: {', '.join(tags)}")
        if changed:
            request_emby_write()
        self.stdout.write(f"{len(changed)} channels/playlists updated")

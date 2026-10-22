"""
Functionality:
- check that all connections are working
"""

import re
import shutil
import subprocess
from time import sleep

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, connection

from common.src.ta_redis import RedisArchivist

TOPIC = """

#######################
#  Connection check   #
#######################

"""


class Command(BaseCommand):
    """command framework"""

    TIMEOUT = 120
    # Django 6 supports PostgreSQL 15 and later
    MIN_MAJOR = 15

    # pylint: disable=no-member
    help = "Check connections"

    def handle(self, *args, **options):
        """run all commands"""
        self.stdout.write(TOPIC)
        self._redis_connection_check()
        self._redis_config_set()
        self._db_connection_check()
        self._db_version_check()
        self._pg_dump_check()

    def _redis_connection_check(self):
        """check ir redis connection is established"""
        self.stdout.write("[1] connect to Redis")
        redis_conn = RedisArchivist().conn
        for _ in range(5):
            try:
                pong = redis_conn.execute_command("PING")
                if pong:
                    self.stdout.write(
                        self.style.SUCCESS("    ✓ Redis connection verified")
                    )
                    return

            except Exception:  # pylint: disable=broad-except
                self.stdout.write("    ... retry Redis connection")
                sleep(2)

        message = "    🗙 Redis connection failed"
        self.stdout.write(self.style.ERROR(f"{message}"))
        try:
            redis_conn.execute_command("PING")
        except Exception as err:  # pylint: disable=broad-except
            message = f"    🗙 {type(err).__name__}: {err}"
            self.stdout.write(self.style.ERROR(f"{message}"))

        sleep(60)
        raise CommandError(message)

    def _redis_config_set(self):
        """set config for redis if not set already"""
        self.stdout.write("[2] set Redis config")
        redis_conn = RedisArchivist().conn
        timeout_is = int(redis_conn.config_get("timeout").get("timeout"))
        if not timeout_is:
            redis_conn.config_set("timeout", 3600)

        self.stdout.write(self.style.SUCCESS("    ✓ Redis config set"))

    def _db_connection_check(self):
        """wait for postgres"""
        self.stdout.write("[3] connect to Postgres")
        total = self.TIMEOUT // 5
        error = None
        for i in range(total):
            try:
                connection.ensure_connection()
                self.stdout.write(
                    self.style.SUCCESS("    ✓ Postgres connection established")
                )
                return
            except OperationalError as err:
                error = err
                self.stdout.write(
                    f"    ... waiting for Postgres [{i}/{total}]"
                )
                connection.close()
                sleep(5)

        message = "    🗙 Postgres connection failed"
        self.stdout.write(self.style.ERROR(message))
        self.stdout.write(f"    error message: {error}")
        sleep(60)
        raise CommandError(message)

    def _db_version_check(self):
        """check for minimal postgres version"""
        self.stdout.write("[4] Postgres version check")
        major = connection.pg_version // 10000
        if major >= self.MIN_MAJOR:
            self.stdout.write(
                self.style.SUCCESS(f"    ✓ Postgres {major} check passed")
            )
            return

        message = (
            "    🗙 Postgres version check failed. "
            + f"Expected {self.MIN_MAJOR} or later but got {major}"
        )
        self.stdout.write(self.style.ERROR(message))
        sleep(60)
        raise CommandError(message)

    def _pg_dump_check(self):
        """snapshots need a pg_dump at least as new as the server"""
        self.stdout.write("[5] pg_dump check")
        server = connection.pg_version // 10000
        binary = shutil.which("pg_dump")
        client = 0
        if binary:
            output = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            if found := re.search(r"(\d+)\.", output):
                client = int(found.group(1))

        if client >= server:
            self.stdout.write(
                self.style.SUCCESS(
                    f"    ✓ pg_dump {client} can dump Postgres {server}"
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"    pg_dump {client or 'missing'} can't dump Postgres "
                + f"{server}, database snapshots will fail"
            )
        )

"""
Functionality:
- copy what stock TA keeps in its SQLite database (users, groups, API
  tokens, task schedules) into Postgres, keeping primary keys
- run before ta_import_es, so per user settings find their user
- idempotent: rows are upserted by primary key
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth.models import Group
from django.core.management.color import no_style
from django.db import connection, transaction
from django_celery_beat.models import (
    ClockedSchedule,
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    SolarSchedule,
)
from rest_framework.authtoken.models import Token
from task.models import CustomPeriodicTask
from user.models import Account

# dependency order
MODELS = [
    Group,
    Account,
    Token,
    CrontabSchedule,
    IntervalSchedule,
    ClockedSchedule,
    SolarSchedule,
    PeriodicTask,
    CustomPeriodicTask,
]
# m2m tables copied as they are: (model, field name)
M2M = [(Account, "groups")]


@dataclass
class SqliteReport:
    """rows copied per table"""

    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """human readable report"""
        out = [f"{table}: {count}" for table, count in self.counts.items()]
        out.extend(f"skipped: {reason}" for reason in self.skipped)
        return out


class SqliteImporter:
    """copy rows from a stock TA db.sqlite3"""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.report = SqliteReport()

    def _rows(self, legacy, table: str) -> list[dict]:
        exists = legacy.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            self.report.skipped.append(f"{table}: not in sqlite file")
            return []
        cursor = legacy.execute(f'SELECT * FROM "{table}"')  # noqa: S608
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def run(self) -> SqliteReport:
        """copy everything in one transaction, then fix sequences"""
        legacy = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            with transaction.atomic():
                for model in MODELS:
                    self._copy_model(legacy, model)
                for model, name in M2M:
                    self._copy_m2m(legacy, model, name)
                self._reset_sequences()
        finally:
            legacy.close()
        return self.report

    def _copy_model(self, legacy, model) -> None:
        table = model._meta.db_table
        rows = self._rows(legacy, table)
        local = model._meta.local_concrete_fields
        values = [
            {f.attname: _convert(f, row[f.column]) for f in local if f.column in row}
            for row in rows
        ]
        if values and model._meta.parents:
            self._upsert_child(model, values)
        elif values:
            update_fields = [f.name for f in local if not f.primary_key]
            model.objects.bulk_create(
                [model(**v) for v in values],
                update_conflicts=True,
                unique_fields=[model._meta.pk.name],
                update_fields=update_fields,
            )
        self.report.counts[table] = len(values)

    @staticmethod
    def _upsert_child(model, values: list[dict]) -> None:
        """
        child table of multi table inheritance: the ORM would touch the
        parent table too, so upsert only this table's columns
        """
        fields = model._meta.local_concrete_fields
        pk = model._meta.pk
        columns = ", ".join(f'"{f.column}"' for f in fields)
        placeholders = ", ".join(["%s"] * len(fields))
        updates = ", ".join(
            f'"{f.column}" = EXCLUDED."{f.column}"'
            for f in fields
            if not f.primary_key
        )
        sql = (
            f'INSERT INTO "{model._meta.db_table}" ({columns}) '
            f"VALUES ({placeholders}) "
            f'ON CONFLICT ("{pk.column}") DO UPDATE SET {updates}'
        )
        with connection.cursor() as cursor:
            cursor.executemany(
                sql,
                [
                    [f.get_db_prep_save(v[f.attname], connection) for f in fields]
                    for v in values
                ],
            )

    def _copy_m2m(self, legacy, model, name: str) -> None:
        relation = model._meta.get_field(name)
        through = relation.remote_field.through
        table = through._meta.db_table
        rows = self._rows(legacy, table)
        objects = [
            through(**{f.attname: row[f.column] for f in through._meta.local_fields})
            for row in rows
        ]
        through.objects.bulk_create(objects, ignore_conflicts=True)
        self.report.counts[table] = len(objects)

    @staticmethod
    def _reset_sequences() -> None:
        """move id sequences past the copied primary keys"""
        statements = connection.ops.sequence_reset_sql(no_style(), MODELS)
        with connection.cursor() as cursor:
            for sql in statements:
                cursor.execute(sql)


def _convert(model_field, value):
    """sqlite storage -> python value for model_field"""
    if value is None:
        return None
    kind = model_field.get_internal_type()
    if kind == "JSONField" and isinstance(value, str):
        return json.loads(value)
    if kind == "DateTimeField" and isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    if kind == "BooleanField":
        return bool(value)
    return value

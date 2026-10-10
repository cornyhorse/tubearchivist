"""
Functionality:
- shared Postgres helpers for models: search vector expressions,
  epoch <-> datetime conversion for the legacy ES document format
"""

from datetime import datetime, timezone

from django.contrib.postgres.search import SearchVectorField
from django.db import models

# Postgres' array_to_string is only STABLE, so generated columns can't use
# it. ta_array_text() is an IMMUTABLE wrapper for text[], created in
# common/migrations/0001_initial.py.
CREATE_ARRAY_TEXT_FN = """
CREATE OR REPLACE FUNCTION ta_array_text(text[]) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT coalesce(array_to_string($1, ' '), '') $$;
"""
DROP_ARRAY_TEXT_FN = "DROP FUNCTION IF EXISTS ta_array_text(text[]);"


class WeightedSearchVector(models.Func):
    """
    immutable tsvector over columns of this table, for GeneratedField
    parts: list of (column, weight, is_array)
    """

    output_field = SearchVectorField()

    def __init__(self, *parts: tuple[str, str, bool]):
        self.parts = parts
        super().__init__()

    def as_sql(self, compiler, connection, **extra_context):
        sql_parts = []
        for column, weight, is_array in self.parts:
            quoted = connection.ops.quote_name(column)
            text = (
                f"ta_array_text({quoted})"
                if is_array
                else f"coalesce({quoted}, '')"
            )
            sql_parts.append(
                f"setweight(to_tsvector('simple'::regconfig, {text}), "
                f"'{weight}')"
            )
        return " || ".join(sql_parts), []

    def deconstruct(self):
        path, _, kwargs = super().deconstruct()
        return path, list(self.parts), kwargs


def from_epoch(value) -> datetime | None:
    """ES epoch seconds (or ISO string) -> aware datetime"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_epoch(value: datetime | None) -> int | None:
    """aware datetime -> epoch seconds"""
    if value is None:
        return None
    return int(value.timestamp())

"""tests for shared database helpers"""

from datetime import datetime, timedelta, timezone

import pytest
from common.src.db import WeightedSearchVector, from_epoch, to_epoch

UTC_2026 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        (UTC_2026, UTC_2026),
        (int(UTC_2026.timestamp()), UTC_2026),
        (float(UTC_2026.timestamp()), UTC_2026),
        (str(int(UTC_2026.timestamp())), UTC_2026),
        ("2026-01-02T03:04:05", UTC_2026),
        ("2026-01-02T04:04:05+01:00", UTC_2026),
    ],
)
def test_from_epoch(value, expected):
    assert from_epoch(value) == expected


def test_from_epoch_keeps_offset():
    value = from_epoch("2026-01-02T04:04:05+01:00")
    assert value.utcoffset() == timedelta(hours=1)


def test_to_epoch():
    assert to_epoch(None) is None
    assert to_epoch(UTC_2026) == int(UTC_2026.timestamp())


def test_search_vector_deconstruct_round_trips():
    expr = WeightedSearchVector(("title", "A", False), ("tags", "B", True))

    path, args, kwargs = expr.deconstruct()

    assert path.endswith("WeightedSearchVector")
    assert WeightedSearchVector(*args, **kwargs).parts == expr.parts

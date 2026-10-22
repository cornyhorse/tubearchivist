"""tests for episode numbering schemes"""

from datetime import date

import pytest

from emby.src.numbering import NumberingError, NumberingItem, assign_numbers


def item(youtube_id, day, idx=None):
    """numbering item from an ISO date"""
    return NumberingItem(youtube_id, date.fromisoformat(day), idx)


def test_date_scheme():
    items = [
        item("b", "2025-03-14"),
        item("a", "2025-03-14"),
        item("c", "2024-12-31"),
    ]

    numbers, warnings = assign_numbers("date", items)

    assert numbers == {
        "a": (2025, 31400),
        "b": (2025, 31401),
        "c": (2024, 123100),
    }
    assert not warnings


def test_date_scheme_frozen_on_backfill():
    first, _ = assign_numbers("date", [item("b", "2025-03-14")])
    # "a" sorts before "b" but arrives later: b keeps its slot
    numbers, _ = assign_numbers(
        "date", [item("a", "2025-03-14"), item("b", "2025-03-14")], first
    )

    assert numbers == {"b": (2025, 31400), "a": (2025, 31401)}


def test_date_scheme_has_100_slots_per_day():
    """E8: an 11th upload stays in its own day"""
    items = [item(f"v{i:03d}", "2025-03-14") for i in range(100)]
    items.append(item("next", "2025-03-15"))

    numbers, warnings = assign_numbers("date", items)

    assert numbers["v010"] == (2025, 31410)
    assert numbers["v099"] == (2025, 31499)
    assert numbers["next"] == (2025, 31500)
    assert not warnings


def test_date_scheme_overflow_stays_unique():
    items = [item(f"v{i:03d}", "2025-03-14") for i in range(102)]

    numbers, warnings = assign_numbers("date", items)

    assert len(set(numbers.values())) == 102
    assert numbers["v099"] == (2025, 31499)
    assert numbers["v100"] == (2025, 31500)
    assert len(warnings) == 2


def test_year_frozen_backfill_gets_next_number():
    first, _ = assign_numbers(
        "year_frozen", [item("a", "2025-01-01"), item("b", "2025-06-01")]
    )
    numbers, _ = assign_numbers(
        "year_frozen",
        [
            item("old", "2025-02-01"),
            item("a", "2025-01-01"),
            item("b", "2025-06-01"),
        ],
        first,
    )

    assert numbers == {"a": (2025, 1), "b": (2025, 2), "old": (2025, 3)}


def test_year_frozen_never_reuses_numbers_of_removed_videos():
    first, _ = assign_numbers(
        "year_frozen", [item("a", "2025-01-01"), item("b", "2025-06-01")]
    )
    numbers, _ = assign_numbers(
        "year_frozen", [item("c", "2025-07-01")], first
    )

    assert numbers["c"] == (2025, 3)


def test_absolute_frozen():
    numbers, _ = assign_numbers(
        "absolute_frozen", [item("b", "2025-01-01"), item("a", "2024-01-01")]
    )

    assert numbers == {"a": (1, 1), "b": (1, 2)}


def test_playlist_position_follows_playlist():
    numbers, _ = assign_numbers(
        "playlist_position",
        [item("a", "2025-01-01", 1), item("b", "2024-01-01", 0)],
        {"a": (1, 7)},
    )

    assert numbers == {"a": (1, 2), "b": (1, 1)}


def test_errors():
    with pytest.raises(NumberingError):
        assign_numbers("nope", [])
    with pytest.raises(NumberingError):
        assign_numbers(
            "date", [item("a", "2025-01-01"), item("a", "2025-01-02")]
        )
    with pytest.raises(NumberingError):
        assign_numbers("playlist_position", [item("a", "2025-01-01")])
    with pytest.raises(NumberingError):
        assign_numbers("date", [], {"a": (2025, 1), "b": (2025, 1)})


def test_absolute_frozen_keeps_existing():
    numbers, _ = assign_numbers(
        "absolute_frozen",
        [item("new", "2020-01-01"), item("a", "2024-01-01")],
        {"a": (1, 1)},
    )

    assert numbers == {"a": (1, 1), "new": (1, 2)}


def test_playlist_position_duplicates():
    with pytest.raises(NumberingError):
        assign_numbers(
            "playlist_position",
            [item("a", "2025-01-01", 0), item("b", "2025-01-01", 0)],
        )


def test_playlist_frozen_appends_and_never_renumbers():
    """E7: the playlist default numbers in playlist order, then freezes"""
    first, _ = assign_numbers(
        "playlist_frozen",
        [item("b", "2025-01-01", 1), item("a", "2025-06-01", 0)],
    )
    assert first == {"a": (1, 1), "b": (1, 2)}

    # reordered, with a new video at the front and "a" removed
    numbers, warnings = assign_numbers(
        "playlist_frozen",
        [item("new", "2024-01-01", 0), item("b", "2025-01-01", 1)],
        first,
    )

    assert numbers == {"a": (1, 1), "b": (1, 2), "new": (1, 3)}
    assert not warnings


def test_playlist_frozen_needs_positions():
    with pytest.raises(NumberingError, match="no playlist position"):
        assign_numbers("playlist_frozen", [item("a", "2025-01-01")])

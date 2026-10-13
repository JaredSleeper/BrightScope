from datetime import UTC, datetime

from brightscope import sync
from brightscope.db import ROUND_COLUMN_NAMES


def test_rounds_sql_selects_every_round_column_in_order():
    sql = sync.rounds_sql()
    positions = [
        sql.index(f" as {c}") if f" as {c}" in sql else sql.index(c) for c in ROUND_COLUMN_NAMES
    ]
    assert positions == sorted(positions)
    assert "investment_type in ('series_a')" in sql
    assert "try_element_at" in sql


def test_next_run_is_today_or_tomorrow():
    early = datetime(2026, 1, 1, 3, tzinfo=UTC)
    late = datetime(2026, 1, 1, 23, tzinfo=UTC)
    assert sync.next_run_at(early).day == 1
    assert sync.next_run_at(late).day == 2


def test_round_row_converts_category_arrays():
    row = tuple(None for _ in ROUND_COLUMN_NAMES)
    row = row[:11] + (("AI", "SaaS"),) + row[12:]
    out = sync._round_row(row)
    assert out[11] == ["AI", "SaaS"]


def test_arrays_returned_as_json_strings_are_parsed():
    assert sync._arr('["AI", "Fintech"]') == ["AI", "Fintech"]
    assert sync._arr(None) is None

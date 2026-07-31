"""Table builders and constants shared by the datasweep engine tests.

A uniquely named module rather than ``conftest`` helpers: every project in the
workspace has a ``tests/`` directory on ``sys.path`` during a full run.
"""

from __future__ import annotations

from datetime import UTC, datetime

from datasweep.engine.models import Policy, RawTable

CONTENT_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
ENGINE_VERSION = "0.1.0-test"
FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def table(headers: list[str], rows: list[list[str | None]]) -> RawTable:
    return RawTable(headers=headers, rows=[list(row) for row in rows])


def column_table(name: str, values: list[str | None]) -> RawTable:
    """A one-column table, the usual unit under test for a detector."""
    return RawTable(headers=[name], rows=[[value] for value in values])


def repeat(values: list[str], times: int) -> list[str]:
    """Repeat a label list until a column is long enough to be categorical.

    ``categorical`` needs distinct-ratio ≤ 0.10 (FR-5), so most detector tests
    need at least ten rows per distinct label.
    """
    return [value for _ in range(times) for value in values]


def clean(table_in: RawTable, policy_in: Policy | None = None):
    from datasweep.engine.pipeline import clean_table

    return clean_table(
        table_in,
        policy_in or Policy(),
        content_sha256=CONTENT_SHA,
        engine_version=ENGINE_VERSION,
    )

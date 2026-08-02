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


def _sample_rows() -> list[str]:
    """One defect per auto class plus exactly one review-tier proposal.

    Sized so the `country` column clears FR-5's categorical thresholds and the
    lone `Slovenia` clears D10b's rarity guard (ratio ≤ 0.05, majority ≥ 20) —
    otherwise the surface tests would have no review queue to work with.
    """
    rows: list[str] = []
    for index in range(40):
        name = {0: " Ana ", 1: "JosÃ©"}.get(index, f"Name{index:02d}")
        country = "Slovenia" if index == 7 else "Slovakia"
        # RFC 4180 quoting: the thousands separator is a comma, so the cell has
        # to be quoted or the reader would (correctly) see a ragged long row.
        amount = '"1,234.56"' if index == 2 else f"{(index + 1) * 3}.50"
        signup = {3: "25/03/2023", 4: "07/04/2023"}.get(index, f"2023-01-{index % 28 + 1:02d}")
        rows.append(f"{name},{country},{amount},{signup}")
    rows.append(rows[5])  # an exact duplicate row (DUP, auto)
    return rows


#: A small CSV carrying WS, ENC, TYPE, DATE and DUP defects at auto tier plus a
#: single `fix.label_merge_nn` proposal at review tier.
SAMPLE_CSV = "name,country,amount,signup\n" + "".join(row + "\n" for row in _sample_rows())
SAMPLE_ROWS = 41


def write_sample(directory, name: str = "sales.csv", text: str = SAMPLE_CSV):
    """Write ``text`` into ``directory`` and return the path as a string."""
    from pathlib import Path

    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def service_for(directory, **kwargs):
    """A hermetic service: in-memory store, fixed clock, no notifications."""
    from datasweep.adapters.clock import FixedClock
    from datasweep.adapters.notifier import NullNotifier
    from datasweep.services import DatasweepService
    from datasweep.store.memory import InMemoryRepository

    kwargs.setdefault("clock", FixedClock(FIXED_NOW, step_seconds=1))
    kwargs.setdefault("notifier", NullNotifier())
    kwargs.setdefault("engine_version", ENGINE_VERSION)
    return DatasweepService(kwargs.pop("repository", None) or InMemoryRepository(), **kwargs)

"""``RawTable`` operations and the structural (``STR``) repairs of FR-3.

The *parsed original* keeps the file's shape exactly as it was — ragged rows,
duplicate header names and all.  This module turns those anomalies into planned
fixes so that the STR pipeline stage repairs them under audit, which is what
makes ``revert`` reproduce the parsed original cell-for-cell (FR-9 / D4).
"""

from __future__ import annotations

import json

from .models import AuditKind, Policy, Proposal, RawTable

OVERFLOW_COLUMN = "_overflow"


def dedupe_headers(headers: list[str]) -> list[tuple[int, str, str]]:
    """Return ``(col, before, after)`` for every header that needs renaming.

    Duplicates get ``_2``, ``_3`` … suffixes (FR-3); the first occurrence keeps
    its name.  Suffixing loops until the new name is genuinely free, so
    ``[a, a, a_2]`` cannot collide into another duplicate.
    """
    seen: dict[str, int] = {}
    taken: set[str] = set()
    renames: list[tuple[int, str, str]] = []
    for col, name in enumerate(headers):
        count = seen.get(name, 0) + 1
        seen[name] = count
        if count == 1 and name not in taken:
            taken.add(name)
            continue
        suffix = count
        candidate = f"{name}_{suffix}"
        while candidate in taken or candidate in headers[:col]:
            suffix += 1
            candidate = f"{name}_{suffix}"
        taken.add(candidate)
        renames.append((col, name, candidate))
    return renames


def overflow_cells(table: RawTable) -> list[str | None]:
    """JSON-encoded surplus values per row, ``None`` where a row is not long."""
    width = table.n_cols
    cells: list[str | None] = []
    for row in table.rows:
        if len(row) > width:
            cells.append(json.dumps(row[width:], ensure_ascii=False, separators=(",", ":")))
        else:
            cells.append(None)
    return cells


def structural_proposals(table: RawTable, policy: Policy) -> list[Proposal]:
    """Every ``STR`` repair the parsed table needs (FR-3).

    * short rows → padded with nulls (auto);
    * long rows  → surplus values JSON-encoded into one ``_overflow`` column;
    * duplicate header names → ``_2``/``_3`` suffixes (auto).
    """
    proposals: list[Proposal] = []
    width = table.n_cols
    cells = overflow_cells(table)
    long_rows = [index for index, cell in enumerate(cells) if cell is not None]

    for col, before, after in dedupe_headers(table.headers):
        if not policy.rule_enabled("fix.dedupe_header"):
            break
        proposals.append(
            Proposal(
                rule="fix.dedupe_header",
                kind=AuditKind.HEADER_RENAME,
                col=col,
                before=before,
                after=after,
                evidence={"reason": "duplicate header name"},
            )
        )

    # A long row forces padding regardless of the override: the surplus column
    # can only line up on a rectangular table.
    if policy.rule_enabled("fix.pad_row") or long_rows:
        for row_index, row in enumerate(table.rows):
            if len(row) < width:
                proposals.append(
                    Proposal(
                        rule="fix.pad_row",
                        kind=AuditKind.ROW_PAD,
                        row=row_index,
                        cols=list(range(len(row), width)),
                        evidence={"from_width": len(row), "to_width": width},
                    )
                )

    if long_rows and policy.rule_enabled("fix.overflow_column"):
        proposals.append(
            Proposal(
                rule="fix.overflow_column",
                kind=AuditKind.COLUMN_ADD,
                col=width,
                column_name=OVERFLOW_COLUMN,
                column_cells=cells,
                evidence={
                    "overflow": True,
                    "rows": long_rows[:10],
                    "n_rows": len(long_rows),
                    "max_width": table.width,
                },
            )
        )
    return proposals


def row_signature(row: list[str | None]) -> tuple[str | None, ...]:
    """Row identity used by the DUP detector: exact, after normalization."""
    return tuple(row)

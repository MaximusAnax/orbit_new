"""Fix application and the constructive inverse (SCOPE.md FR-8 / FR-9).

``apply_plan`` is the single source of truth for what a revision contains: the
auto plan, plus the mandatory structural repairs, plus exactly the accepted
review items — always recomputed from the parsed original, never as a delta on
the previous revision (FR-11).  ``revert`` undoes the resulting audit and must
reproduce the parsed original cell-for-cell (D4).
"""

from __future__ import annotations

import json

from .models import (
    AuditEntry,
    AuditKind,
    Fix,
    FixPlan,
    RawTable,
    select_applicable,
    stricter,
)

__all__ = [
    "apply_plan",
    "audit_diff_mismatches",
    "dropped_row_indices",
    "revert",
    "row_map_for",
]


def _entry_from_fix(fix: Fix, before_row: list[str | None] | None = None) -> AuditEntry:
    return AuditEntry(
        kind=fix.kind,
        rule=fix.rule,
        tier=fix.tier,
        confidence=fix.confidence,
        row=fix.row,
        col=fix.col,
        col_name=fix.col_name,
        before=fix.before,
        after=fix.after,
        before_row=before_row if fix.kind is AuditKind.ROW_DROP else None,
        cols=fix.cols,
        column_name=fix.column_name,
        column_cells=fix.column_cells,
        evidence=fix.evidence,
    )


def apply_plan(
    original: RawTable,
    plan: FixPlan | list[Fix],
    *,
    accepted: frozenset[str] = frozenset(),
) -> tuple[RawTable, list[AuditEntry]]:
    """Apply a plan to the parsed original, returning (cleaned, audit).

    Entries come out in application order — (pipeline stage, kind, col, row) —
    which is what makes the reverse fold in :func:`revert` mechanical.

    Changes on rows that a later duplicate-drop removes are *not* audited: the
    row disappears from the cleaned table, so such an entry would correspond to
    no diff (FR-8) and the dropped row's verbatim original content is preserved
    whole in the ``row_drop`` entry anyway.
    """
    fixes = select_applicable(plan.fixes if isinstance(plan, FixPlan) else list(plan), accepted)
    headers = list(original.headers)
    rows: list[list[str | None]] = [list(row) for row in original.rows]
    drop_rows = {fix.row for fix in fixes if fix.kind is AuditKind.ROW_DROP and fix.row is not None}

    entries: list[AuditEntry] = []
    for fix in fixes:
        if fix.kind is AuditKind.ROW_DROP:
            if fix.row is None or not 0 <= fix.row < len(original.rows):
                continue
            entries.append(_entry_from_fix(fix, before_row=list(original.rows[fix.row])))
            continue
        if fix.row is not None and fix.row in drop_rows:
            continue

        if fix.kind is AuditKind.CELL_CHANGE:
            if fix.row is None or fix.col is None:
                continue
            row = rows[fix.row]
            if fix.col >= len(row):
                continue
            if row[fix.col] == fix.after:
                continue  # already there: never record an identity change
            row[fix.col] = fix.after
        elif fix.kind is AuditKind.ROW_PAD:
            if fix.row is None or not fix.cols:
                continue
            row = rows[fix.row]
            target = max(fix.cols) + 1
            row.extend([None] * (target - len(row)))
        elif fix.kind is AuditKind.HEADER_RENAME:
            if fix.col is None or fix.after is None:
                continue
            headers[fix.col] = fix.after
        elif fix.kind is AuditKind.COLUMN_ADD:
            if fix.col is None or fix.column_cells is None:
                continue
            width = fix.col
            for index, row in enumerate(rows):
                cell = fix.column_cells[index] if index < len(fix.column_cells) else None
                if len(row) > width:
                    del row[width:]
                elif len(row) < width:
                    row.extend([None] * (width - len(row)))
                row.append(cell)
            headers.append(fix.column_name or f"col_{fix.col}")
        entries.append(_entry_from_fix(fix))

    if drop_rows:
        rows = [row for index, row in enumerate(rows) if index not in drop_rows]

    return RawTable(headers=headers, rows=rows), _merge_cell_changes(entries)


def _merge_cell_changes(entries: list[AuditEntry]) -> list[AuditEntry]:
    """Collapse per-stage edits of one cell into a single audit entry (US-2).

    A cell that needed a mojibake repair *and* a trim is one change from the
    user's point of view — "every cell that differs is covered by exactly one
    entry" — and one entry is also what keeps an intermediate value from being
    read as a finished fix.  The merged entry keeps the earliest stage's
    position and rule, the original ``before``, the final ``after``, the
    strictest tier and the lowest confidence.
    """
    first_at: dict[tuple[int, int], int] = {}
    merged: list[AuditEntry | None] = list(entries)
    for index, entry in enumerate(entries):
        if entry.kind is not AuditKind.CELL_CHANGE or entry.row is None or entry.col is None:
            continue
        key = (entry.row, entry.col)
        anchor = first_at.get(key)
        if anchor is None:
            first_at[key] = index
            continue
        head = merged[anchor]
        assert head is not None
        applied = list(head.evidence.get("also_applied", []))
        applied.append(
            {"rule": entry.rule, "before": entry.before, "after": entry.after, **entry.evidence}
        )
        merged[anchor] = head.model_copy(
            update={
                "after": entry.after,
                "tier": stricter(head.tier, entry.tier),
                "confidence": min(head.confidence, entry.confidence),
                "evidence": {**head.evidence, "also_applied": applied},
            }
        )
        merged[index] = None
    kept = [entry for entry in merged if entry is not None]
    return [
        entry
        for entry in kept
        if entry.kind is not AuditKind.CELL_CHANGE or entry.before != entry.after
    ]


def dropped_row_indices(audit: list[AuditEntry]) -> set[int]:
    return {
        entry.row for entry in audit if entry.kind is AuditKind.ROW_DROP and entry.row is not None
    }


def row_map_for(n_rows: int, audit: list[AuditEntry]) -> list[int]:
    """Working row index → original row index after the audit was applied."""
    dropped = dropped_row_indices(audit)
    return [index for index in range(n_rows) if index not in dropped]


def revert(cleaned: RawTable, audit: list[AuditEntry]) -> RawTable:
    """Undo an audit, reconstructing the parsed original exactly (FR-9).

    Dropped rows are re-inserted first, in ascending original index, so that
    every remaining entry's original coordinates address the row it meant;
    everything else is undone in reverse application order.
    """
    drops = {
        entry.row: list(entry.before_row or [])
        for entry in audit
        if entry.kind is AuditKind.ROW_DROP and entry.row is not None
    }
    rows: list[list[str | None]] = [list(row) for row in cleaned.rows]
    for row_index in sorted(drops):
        insert_at = min(row_index, len(rows))
        rows.insert(insert_at, list(drops[row_index]))
    headers = list(cleaned.headers)

    for entry in reversed(audit):
        if entry.kind is AuditKind.ROW_DROP:
            continue
        if entry.kind is AuditKind.CELL_CHANGE:
            if entry.row is None or entry.col is None:
                continue
            if entry.row < len(rows) and entry.col < len(rows[entry.row]):
                rows[entry.row][entry.col] = entry.before
        elif entry.kind is AuditKind.ROW_PAD:
            if entry.row is None or not entry.cols:
                continue
            row = rows[entry.row]
            for col in sorted(entry.cols, reverse=True):
                if col < len(row):
                    del row[col]
        elif entry.kind is AuditKind.HEADER_RENAME:
            if entry.col is not None and entry.col < len(headers):
                headers[entry.col] = entry.before or headers[entry.col]
        elif entry.kind is AuditKind.COLUMN_ADD:
            if entry.col is None:
                continue
            overflow = bool(entry.evidence.get("overflow"))
            for index, row in enumerate(rows):
                if index in drops or entry.col >= len(row):
                    continue
                value = row.pop(entry.col)
                if overflow and value is not None:
                    row.extend(json.loads(value))
            if entry.col < len(headers):
                headers.pop(entry.col)

    return RawTable(headers=headers, rows=rows)


def audit_diff_mismatches(
    original: RawTable, cleaned: RawTable, audit: list[AuditEntry]
) -> list[str]:
    """Check FR-8's completeness invariant; returns human-readable violations.

    The diff between the parsed original and the cleaned table must be exactly
    the set of audit entries, and no entry may be an identity change.
    """
    problems: list[str] = []
    dropped = dropped_row_indices(audit)
    added_columns = {
        entry.col for entry in audit if entry.kind is AuditKind.COLUMN_ADD and entry.col is not None
    }
    changed: dict[tuple[int, int], AuditEntry] = {}
    for entry in audit:
        if entry.kind is AuditKind.CELL_CHANGE:
            key = (entry.row or 0, entry.col or 0)
            if key in changed:
                problems.append(f"cell {key} has more than one audit entry")
            changed[key] = entry
            if entry.before == entry.after:
                problems.append(f"identity change audited at {key}")
        elif entry.kind is AuditKind.ROW_PAD and not entry.cols:
            problems.append(f"row_pad with no padded columns at row {entry.row}")
        elif entry.kind is AuditKind.HEADER_RENAME and entry.before == entry.after:
            problems.append(f"identity header rename at col {entry.col}")

    kept_rows = [index for index in range(original.n_rows) if index not in dropped]
    if len(kept_rows) != cleaned.n_rows:
        problems.append(f"cleaned has {cleaned.n_rows} rows, audit accounts for {len(kept_rows)}")
        return problems

    for cleaned_index, original_index in enumerate(kept_rows):
        for col in range(cleaned.n_cols):
            after = (
                cleaned.rows[cleaned_index][col] if col < len(cleaned.rows[cleaned_index]) else None
            )
            if col in added_columns:
                continue
            before = original.cell(original_index, col)
            padded = col >= len(original.rows[original_index])
            entry = changed.get((original_index, col))
            if before == after:
                if entry is not None:
                    problems.append(f"audited change at ({original_index},{col}) is a no-op")
                continue
            if entry is None and not padded:
                problems.append(
                    f"unaudited change at ({original_index},{col}): {before!r} → {after!r}"
                )
            elif entry is not None and entry.after != after:
                problems.append(
                    f"audit at ({original_index},{col}) says {entry.after!r}, table has {after!r}"
                )
    return problems

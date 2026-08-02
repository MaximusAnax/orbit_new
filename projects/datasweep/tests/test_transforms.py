"""FR-8 audit completeness and pipeline order, FR-9 reversibility."""

from __future__ import annotations

import json

import pytest
from datasweep.engine.models import AuditKind, Policy, RawTable, Tier
from datasweep.engine.pipeline import apply_revision, clean_table
from datasweep.engine.planner import plan_fixes
from datasweep.engine.table import structural_proposals
from datasweep.engine.transforms import apply_plan, audit_diff_mismatches, revert
from support_datasweep import CONTENT_SHA, ENGINE_VERSION, clean, table

POLICY = Policy()


def _structural(raw: RawTable):
    return plan_fixes(structural_proposals(raw, POLICY), POLICY, raw.headers)


# --------------------------------------------------------------------------
# FR-8 — application and audit
# --------------------------------------------------------------------------


def test_fr8_audit_entries_follow_the_pipeline_order() -> None:
    raw = table(
        ["name", "amount", "when"],
        [
            ["  JosÃ© ", "1,234.56", "31/01/2023"],
            ["Ana", "9.99", "03/04/2021"],
            ["Ana", "9.99", "03/04/2021"],
        ],
    )
    result = clean(raw)
    stages = [entry.rule for entry in result.audit]
    assert stages[0] == "fix.mojibake"  # ENC first
    assert stages[-1] == "fix.drop_duplicate_row"  # DUP last
    assert [entry.tier for entry in result.audit] == [Tier.AUTO] * len(result.audit)


def test_fr8_diff_equals_the_audit_entries() -> None:
    raw = table(
        ["name", "amount"],
        [["  Ana  ", "1,234.56"], ["Bo", "9.99"], ["Cy", "19,99"]],
    )
    result = clean(raw)
    assert audit_diff_mismatches(result.original, result.cleaned, result.audit) == []
    changed = sum(
        1
        for row in range(raw.n_rows)
        for col in range(raw.n_cols)
        if raw.cell(row, col) != result.cleaned.cell(row, col)
    )
    assert changed == len(result.audit)


def test_fr8_no_entry_is_an_identity_change() -> None:
    result = clean(table(["a"], [["  x  "], ["y"], ["z"]]))
    for entry in result.audit:
        if entry.kind is AuditKind.CELL_CHANGE:
            assert entry.before != entry.after


def test_fr8_clean_table_produces_no_entries_at_all() -> None:
    raw = table(["a", "b"], [["1", "x"], ["2", "y"], ["3", "z"]])
    result = clean(raw)
    assert result.audit == []
    assert result.cleaned.rows == raw.rows


def test_fr8_changes_on_dropped_rows_are_not_audited() -> None:
    """A dropped row's content is preserved whole in its row_drop entry."""
    raw = table(["a"], [["  x  "], ["  x  "]])
    result = clean(raw)
    assert audit_diff_mismatches(result.original, result.cleaned, result.audit) == []
    drops = [entry for entry in result.audit if entry.kind is AuditKind.ROW_DROP]
    assert len(drops) == 1
    assert drops[0].before_row == ["  x  "]


def test_fr8_row_pad_and_header_dedupe_are_audited_structural_repairs() -> None:
    raw = RawTable(headers=["a", "a", "b"], rows=[["1", "2", "3"], ["4"]])
    fixes = _structural(raw)
    cleaned, audit = apply_plan(raw, fixes)
    kinds = [entry.kind for entry in audit]
    assert AuditKind.HEADER_RENAME in kinds
    assert AuditKind.ROW_PAD in kinds
    assert cleaned.headers == ["a", "a_2", "b"]
    assert cleaned.rows[1] == ["4", None, None]
    assert cleaned.is_rectangular


def test_fr3_long_rows_go_into_one_overflow_column() -> None:
    raw = RawTable(headers=["a", "b"], rows=[["1", "2"], ["3", "4", "5", "6"]])
    fixes = _structural(raw)
    cleaned, audit = apply_plan(raw, fixes)
    assert cleaned.headers == ["a", "b", "_overflow"]
    assert cleaned.rows[0] == ["1", "2", None]
    assert json.loads(cleaned.rows[1][2]) == ["5", "6"]
    column_add = [entry for entry in audit if entry.kind is AuditKind.COLUMN_ADD]
    assert len(column_add) == 1
    assert column_add[0].tier is Tier.REVIEW


# --------------------------------------------------------------------------
# FR-9 — reversibility
# --------------------------------------------------------------------------


def test_fr9_revert_restores_cell_changes() -> None:
    raw = table(["name", "amount"], [["  Ana  ", "1,234.56"], ["Bo", "19,99"]])
    result = clean(raw)
    assert revert(result.cleaned, result.audit).rows == raw.rows


def test_fr9_revert_row_drop_and_headers() -> None:
    raw = RawTable(
        headers=["a", "a", "b"],
        rows=[
            ["1", "2", " x "],
            ["9", "9", "y"],
            ["1", "2", " x "],
            ["4"],
        ],
    )
    result = clean(raw)
    restored = revert(result.cleaned, result.audit)
    assert restored.headers == raw.headers
    assert restored.rows == raw.rows


def test_fr9_revert_drops_the_overflow_column_and_restores_surplus() -> None:
    raw = RawTable(headers=["a", "b"], rows=[["1", "2"], ["3", "4", "5", "6"], ["7", "8"]])
    fixes = _structural(raw)
    cleaned, audit = apply_plan(raw, fixes)
    restored = revert(cleaned, audit)
    assert restored.headers == raw.headers
    assert restored.rows == raw.rows


def test_fr9_revert_is_exact_for_a_file_with_every_class_of_defect() -> None:
    raw = RawTable(
        headers=["name", "country", "amount", "when", "zip"],
        rows=[
            [" JosÃ© ", "USA", "1,234.56", "31/01/2023", "02134"],
            ["Ana", "U.S.A.", "N/A", "03/04/2021", "'01001"],
            ["Bo", " usa ", "€12.30", "2021-01-07", "90210"],
            ["Bo", " usa ", "€12.30", "2021-01-07", "90210"],
            ["Cy", "USA", "19,99", "07/01/2021", "12345"],
        ],
    )
    result = clean(raw)
    assert audit_diff_mismatches(result.original, result.cleaned, result.audit) == []
    restored = revert(result.cleaned, result.audit)
    assert restored.headers == raw.headers
    assert restored.rows == raw.rows


def test_fr9_revert_of_an_empty_audit_is_the_identity() -> None:
    raw = table(["a"], [["1"], ["2"]])
    assert revert(raw, []).rows == raw.rows


# --------------------------------------------------------------------------
# FR-11 — revisions are complete, not deltas
# --------------------------------------------------------------------------


def test_fr11_revision_is_recomputed_from_the_parsed_original() -> None:
    values = ["Mississippi"] * 40 + ["Missisippi"]
    raw = RawTable(
        headers=["state", "id"], rows=[[value, str(index)] for index, value in enumerate(values)]
    )
    result = clean_table(raw, POLICY, content_sha256=CONTENT_SHA, engine_version=ENGINE_VERSION)
    item = next(item for item in result.review_items if item.rule == "fix.label_merge_nn")

    cleaned_r2, audit_r2 = apply_revision(result, [item.id])
    assert cleaned_r2.rows[40][0] == "Mississippi"
    assert any(entry.rule == "fix.label_merge_nn" for entry in audit_r2)
    # complete audit against the parsed original, so the invariant is uniform
    assert audit_diff_mismatches(result.original, cleaned_r2, audit_r2) == []
    assert revert(cleaned_r2, audit_r2).rows == raw.rows


def test_fr11_revision_one_contains_only_the_auto_plan() -> None:
    values = ["Mississippi"] * 40 + ["Missisippi"]
    raw = RawTable(
        headers=["state", "id"], rows=[[value, str(index)] for index, value in enumerate(values)]
    )
    result = clean(raw)
    assert result.cleaned.rows[40][0] == "Missisippi"
    assert all(entry.tier is Tier.AUTO for entry in result.audit)


def test_fr11_rejecting_an_item_leaves_the_table_untouched() -> None:
    values = ["Mississippi"] * 40 + ["Missisippi"]
    raw = RawTable(
        headers=["state", "id"], rows=[[value, str(index)] for index, value in enumerate(values)]
    )
    result = clean(raw)
    cleaned, audit = apply_revision(result, [])
    assert cleaned.rows == result.cleaned.rows
    assert len(audit) == len(result.audit)


@pytest.mark.parametrize("accepted", [[], ["nonexistent"]])
def test_fr11_unknown_accepted_ids_are_ignored(accepted: list[str]) -> None:
    raw = table(["a"], [["  x  "], ["y"]])
    result = clean(raw)
    cleaned, _ = apply_revision(result, accepted)
    assert cleaned.rows == result.cleaned.rows

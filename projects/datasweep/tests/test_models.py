"""Domain model invariants (DATA_MODEL §2) and artifact record shapes (§3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from datasweep.engine.models import (
    RULES,
    AuditEntry,
    AuditKind,
    Issue,
    IssueClass,
    Policy,
    RawTable,
    Stage,
    Tier,
    WatchedFolder,
    rule_spec,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# RawTable (D3)
# --------------------------------------------------------------------------


def test_raw_table_cells_are_strings_or_the_canonical_null() -> None:
    raw = RawTable(headers=["a", "b"], rows=[["1", None]])
    assert raw.cell(0, 0) == "1"
    assert raw.cell(0, 1) is None
    assert raw.cell(9, 9) is None  # out of range reads as null, never raises


def test_raw_table_tracks_raggedness() -> None:
    raw = RawTable(headers=["a", "b", "c"], rows=[["1", "2"], ["3", "4", "5", "6"]])
    assert raw.is_rectangular is False
    assert raw.width == 4
    assert raw.n_cols == 3
    assert raw.column(2) == [None, "5"]
    padded = raw.padded()
    assert padded.rows == [["1", "2", None], ["3", "4", "5"]]
    assert padded.is_rectangular


def test_raw_table_is_frozen() -> None:
    raw = RawTable(headers=["a"], rows=[["1"]])
    with pytest.raises(ValueError):
        raw.headers = ["b"]


def test_raw_table_header_helper_survives_added_columns() -> None:
    raw = RawTable(headers=["a"], rows=[["1"]])
    assert raw.header(0) == "a"
    assert raw.header(5) == "col_5"


# --------------------------------------------------------------------------
# tiers, stages, rules
# --------------------------------------------------------------------------


def test_pipeline_stage_order_is_the_documented_one() -> None:
    assert [stage.name for stage in Stage] == [
        "ENC",
        "STR",
        "WS",
        "MISS",
        "TYPE",
        "DATE",
        "CAT",
        "DUP",
        "OUT",
    ]
    assert Stage.ENC < Stage.STR < Stage.WS < Stage.OUT


def test_every_rule_declares_a_class_stage_and_cap() -> None:
    for rule, spec in RULES.items():
        assert spec.rule == rule
        assert isinstance(spec.klass, IssueClass)
        assert isinstance(spec.stage, Stage)
        assert isinstance(spec.cap, Tier)
        if spec.detect_only:
            assert spec.cap is Tier.REPORT or spec.fixed_tier is not None


def test_unknown_rule_lookup_is_loud() -> None:
    with pytest.raises(KeyError):
        rule_spec("fix.imagination")


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------


def test_policy_defaults_match_the_documented_thresholds() -> None:
    policy = Policy()
    assert policy.general.settle_seconds == 5
    assert policy.general.poll_interval_seconds == 5
    assert policy.general.max_file_mb == 100
    assert policy.general.max_rows == 500_000
    assert policy.thresholds.type_majority == 0.90
    assert policy.thresholds.auto_confidence == 0.95
    assert policy.thresholds.convention_agree == 0.95
    assert policy.thresholds.convention_min_decisive == 3
    assert policy.thresholds.merge_dominance == 0.80
    assert policy.thresholds.nn_max_ratio == 0.05
    assert policy.thresholds.nn_min_majority == 20
    assert policy.thresholds.outlier_iqr_k == 3.0
    assert policy.thresholds.outlier_mad_z == 3.5
    assert policy.thresholds.outlier_min_n == 20


def test_policy_merge_is_a_section_wise_overlay() -> None:
    merged = Policy().merged({"thresholds": {"type_majority": 0.8}})
    assert merged.thresholds.type_majority == 0.8
    assert merged.thresholds.auto_confidence == 0.95
    assert merged.general.settle_seconds == 5


def test_policy_canonical_json_is_sorted_and_stable() -> None:
    payload = Policy().canonical_json()
    assert payload.startswith('{"detectors"')
    assert payload == Policy().canonical_json()


def test_policy_rejects_out_of_range_thresholds() -> None:
    with pytest.raises(ValueError):
        Policy.model_validate({"thresholds": {"type_majority": 1.5}})


# --------------------------------------------------------------------------
# artifact records (DATA_MODEL §3)
# --------------------------------------------------------------------------


def test_cell_change_entry_serializes_the_documented_fields() -> None:
    entry = AuditEntry(
        kind=AuditKind.CELL_CHANGE,
        rule="fix.date_canon",
        tier=Tier.AUTO,
        confidence=1.0,
        row=17,
        col=4,
        col_name="order_date",
        before="31/01/2023",
        after="2023-01-31",
        evidence={"format_from": "%d/%m/%Y"},
    )
    payload = entry.to_json_obj()
    assert payload == {
        "kind": "cell_change",
        "rule": "fix.date_canon",
        "tier": "auto",
        "confidence": 1.0,
        "row": 17,
        "col": 4,
        "col_name": "order_date",
        "before": "31/01/2023",
        "after": "2023-01-31",
        "evidence": {"format_from": "%d/%m/%Y"},
    }


def test_row_drop_entry_keeps_the_whole_row_for_reversibility() -> None:
    entry = AuditEntry(
        kind=AuditKind.ROW_DROP,
        rule="fix.drop_duplicate_row",
        tier=Tier.AUTO,
        confidence=1.0,
        row=58,
        before_row=["1042", "2023-01-31", "19.99"],
        evidence={"first_occurrence_row": 31},
    )
    payload = entry.to_json_obj()
    assert payload["before_row"] == ["1042", "2023-01-31", "19.99"]
    assert "before" not in payload


def test_finding_line_carries_the_documented_fields() -> None:
    issue = Issue(
        klass=IssueClass.OUT,
        rule="detect.outlier",
        tier=Tier.REPORT,
        row=204,
        col=2,
        col_name="amount",
        value="48200.00",
        evidence={"q1": 12.5},
    )
    payload = issue.to_json_obj()
    assert list(payload) == ["klass", "rule", "tier", "row", "col", "col_name", "value", "evidence"]
    assert payload["klass"] == "OUT"
    assert "item_id" not in payload


def test_finding_line_links_to_its_review_item_when_there_is_one() -> None:
    issue = Issue(
        klass=IssueClass.DATE,
        rule="fix.date_canon_ambiguous",
        tier=Tier.REVIEW,
        row=0,
        col=4,
        col_name="order_date",
        value="03/04/2021",
        item_id="a3f1c2d9",
    )
    assert issue.to_json_obj()["item_id"] == "a3f1c2d9"


def test_findings_sort_by_class_then_column_then_row() -> None:
    issues = [
        Issue(klass=IssueClass.WS, rule="fix.trim", tier=Tier.AUTO, row=5, col=1),
        Issue(klass=IssueClass.ENC, rule="fix.mojibake", tier=Tier.AUTO, row=9, col=0),
        Issue(klass=IssueClass.WS, rule="fix.trim", tier=Tier.AUTO, row=2, col=1),
    ]
    ordered = sorted(issues, key=lambda issue: issue.sort_key)
    assert [(issue.klass.value, issue.row) for issue in ordered] == [
        ("ENC", 9),
        ("WS", 2),
        ("WS", 5),
    ]


# --------------------------------------------------------------------------
# watched folders
# --------------------------------------------------------------------------


def test_watched_folder_paths_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        WatchedFolder(id="f", path="relative/dir", output_dir="/tmp/out", created_at=NOW)


def test_watched_folder_defaults() -> None:
    folder = WatchedFolder(id="f", path="/data/x/", output_dir="/data/x/.datasweep", created_at=NOW)
    assert folder.path == "/data/x"
    assert folder.recursive is True
    assert folder.enabled is True
    assert folder.include == ["*.csv", "*.tsv", "*.xlsx", "*.jsonl"]

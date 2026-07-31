"""End-to-end engine behaviour: the safety contract (D1) and FR-16 determinism.

These are the unit-level analogues of the eval gates: a clean file must pass
through silently, hand-vetted ambiguity must never be auto-touched *and* must
actually surface, and the artifacts must be a pure function of the inputs.
"""

from __future__ import annotations

import pytest
from datasweep.adapters.artifacts import serialize_audit, serialize_findings, serialize_table
from datasweep.engine.models import ColumnType, FileFormat, Policy, RawTable, Tier
from datasweep.engine.report import render_report
from support_datasweep import CONTENT_SHA, ENGINE_VERSION, clean, table

# --------------------------------------------------------------------------
# a clean file must be silent (M3_clean / M3_clean_findings)
# --------------------------------------------------------------------------


def golden_table() -> RawTable:
    rows: list[list[str | None]] = []
    for index in range(40):
        rows.append(
            [
                f"person {index}",
                ["USA", "FRA", "DEU", "ESP"][index % 4],
                f"{10 + index}.{index % 10:02d}",
                f"2023-01-{(index % 28) + 1:02d}",
                f"{index:05d}",
                None if index % 9 == 0 else str(20 + index % 50),
            ]
        )
    return RawTable(headers=["name", "country", "amount", "when", "zip", "age"], rows=rows)


def test_fr7_a_clean_file_produces_no_changes_and_no_findings() -> None:
    result = clean(golden_table())
    assert result.audit == []
    assert result.issues == []
    assert result.review_items == []
    assert result.issue_counts == {}
    assert result.cleaned.rows == golden_table().rows


def test_fr6_seeded_empty_cells_are_data_not_defects() -> None:
    result = clean(golden_table())
    age = next(profile for profile in result.profiles if profile.name == "age")
    assert age.null_count == 5
    assert age.non_null + age.null_count == 40
    assert not [issue for issue in result.issues if issue.klass.value == "MISS"]


def test_fr5_profiles_describe_the_cleaned_table() -> None:
    result = clean(golden_table())
    types = {profile.name: profile.inferred_type for profile in result.profiles}
    assert types["country"] is ColumnType.CATEGORICAL
    assert types["amount"] is ColumnType.FLOAT
    assert types["when"] is ColumnType.DATE
    assert types["zip"] is ColumnType.DIGITS
    assert types["age"] is ColumnType.INTEGER


# --------------------------------------------------------------------------
# traps: nothing ambiguous may be auto-applied, and it must still surface
# --------------------------------------------------------------------------


def test_us3_ambiguous_dates_are_never_auto_fixed_and_carry_both_readings() -> None:
    dates = ["03/04/2021", "07/01/2021", "05/05/2021", "11/12/2021", "02/03/2021"]
    raw = RawTable(headers=["id", "event_date"], rows=[[str(i), d] for i, d in enumerate(dates)])
    result = clean(raw)

    assert result.audit == []
    assert result.cleaned.rows == raw.rows
    item = next(item for item in result.review_items if item.rule == "fix.date_canon_ambiguous")
    assert item.proposal["formats"] == ["%d/%m/%Y", "%m/%d/%Y"]
    assert item.proposal["recommended"] == "%d/%m/%Y"
    assert item.affected_cells == 5
    assert item.confidence == pytest.approx(0.2)  # only 05/05 reads the same both ways


def test_us3_ambiguous_decimals_are_never_auto_fixed_and_carry_both_readings() -> None:
    raw = RawTable(
        headers=["id", "value"],
        rows=[[str(i), v] for i, v in enumerate(["1.234", "2.345", "3.456", "4.567"])],
    )
    result = clean(raw)
    assert result.audit == []
    item = next(item for item in result.review_items if item.rule == "fix.number_canon_ambiguous")
    assert item.proposal["readings"] == ["COMMA", "DOT"]
    assert item.affected_cells == 4


def test_us3_soft_sentinel_words_go_to_review_not_to_null() -> None:
    """`None` in a medication column is a real category (D8)."""
    meds = [
        "aspirin",
        "ibuprofen",
        "None",
        "paracetamol",
        "None",
        "metformin",
        "statin",
        "insulin",
        "None",
        "warfarin",
        "aspirin",
        "None",
    ]
    raw = RawTable(
        headers=["patient", "medication"], rows=[[str(i), m] for i, m in enumerate(meds)]
    )
    result = clean(raw)
    assert result.audit == []
    assert [item.rule for item in result.review_items] == ["fix.sentinel_null_soft"]
    assert result.cleaned.rows == raw.rows


def test_us3_numeric_sentinels_are_reported_never_nulled() -> None:
    values = [str(19 + index % 5) for index in range(20)] + ["-9999"] * 4
    raw = RawTable(
        headers=["id", "temperature_c"], rows=[[str(i), v] for i, v in enumerate(values)]
    )
    result = clean(raw)
    assert result.audit == []
    sentinels = [issue for issue in result.issues if issue.rule == "detect.numeric_sentinel"]
    assert len(sentinels) == 4
    assert all(issue.tier is Tier.REPORT for issue in sentinels)
    assert result.cleaned.rows == raw.rows


def test_us8_leading_zero_codes_are_never_numerically_coerced() -> None:
    rows = [[f"{index:05d}", f"{index:08d}"] for index in range(30)]
    raw = RawTable(headers=["zip", "sku"], rows=rows)
    result = clean(raw)
    assert result.audit == []
    assert all(profile.inferred_type is ColumnType.DIGITS for profile in result.profiles)


def test_fr6_near_duplicate_rows_are_never_dropped() -> None:
    raw = RawTable(
        headers=["id", "ts", "value"],
        rows=[
            ["1", "2023-01-01T00:00:00", "10"],
            ["1", "2023-01-01T00:00:01", "10"],
            ["2", "2023-01-01T00:00:02", "11"],
        ],
    )
    result = clean(raw)
    assert result.cleaned.n_rows == 3
    assert not [issue for issue in result.issues if issue.klass.value == "DUP"]


def test_fr6_frequent_near_labels_are_never_proposed() -> None:
    labels = ["Iran"] * 25 + ["Iraq"] * 20 + ["Slovakia"] * 22 + ["Slovenia"] * 18
    raw = RawTable(
        headers=["id", "country"], rows=[[str(i), label] for i, label in enumerate(labels)]
    )
    result = clean(raw)
    assert not [issue for issue in result.issues if issue.klass.value == "CAT"]
    assert result.review_items == []


# --------------------------------------------------------------------------
# bookkeeping invariants
# --------------------------------------------------------------------------


def dirty_table() -> RawTable:
    return RawTable(
        headers=["name", "country", "amount", "when", "zip"],
        rows=[
            [" JosÃ© ", "USA", "1,234.56", "31/01/2023", "02134"],
            ["Ana", "U.S.A.", "N/A", "03/04/2021", "'01001"],
            ["Bo", " usa ", "€12.30", "2021-01-07", "90210"],
            ["Bo", " usa ", "€12.30", "2021-01-07", "90210"],
            ["Cy", "USA", "19,99", "07/01/2021", "12345"],
        ],
    )


def test_fr12_issue_counts_match_the_findings_log() -> None:
    result = clean(dirty_table())
    for klass, count in result.issue_counts.items():
        assert count == len([issue for issue in result.issues if issue.klass.value == klass])
    assert sum(result.issue_counts.values()) == len(result.issues)


def test_fr8_every_auto_change_has_a_finding_at_the_same_cell() -> None:
    result = clean(dirty_table())
    findings = {(issue.row, issue.col) for issue in result.issues}
    for entry in result.audit:
        if entry.kind.value == "cell_change":
            assert (entry.row, entry.col) in findings


def test_fr11_every_review_cell_is_a_reported_finding() -> None:
    values = ["Mississippi"] * 40 + ["Missisippi"]
    raw = RawTable(headers=["state", "id"], rows=[[v, str(i)] for i, v in enumerate(values)])
    result = clean(raw)
    findings = {(issue.row, issue.col, issue.klass.value) for issue in result.issues}
    for item in result.review_items:
        klass = "CAT"
        for cell in item.proposal["cells"]:
            assert (cell["row"], cell["col"], klass) in findings
        assert all(
            issue.item_id == item.id
            for issue in result.issues
            if issue.rule == item.rule and issue.tier is Tier.REVIEW
        )


def test_fr12_change_counts_split_applied_from_proposed() -> None:
    result = clean(dirty_table())
    assert result.change_counts["auto"] == len(result.audit)
    assert result.change_counts["review"] == len(
        [fix for fix in result.plan.fixes if fix.tier is Tier.REVIEW and not fix.detect_only]
    )
    assert result.change_counts["report"] == len(
        [issue for issue in result.issues if issue.tier is Tier.REPORT]
    )


# --------------------------------------------------------------------------
# FR-16 determinism
# --------------------------------------------------------------------------


def _artifacts(raw: RawTable, policy: Policy) -> tuple[str, str, str, str]:
    from datasweep.engine.pipeline import clean_table

    result = clean_table(raw, policy, content_sha256=CONTENT_SHA, engine_version=ENGINE_VERSION)
    header = {
        "source_name": "sample.csv",
        "content_sha256": CONTENT_SHA,
        "policy_hash": policy.policy_hash(),
        "engine_version": ENGINE_VERSION,
        "revision": 1,
        "n_rows": result.original.n_rows,
        "n_cols": result.original.n_cols,
    }
    return (
        serialize_table(result.cleaned, FileFormat.CSV),
        serialize_audit(result.audit, header),
        serialize_findings(result.issues),
        render_report(
            result,
            source_name="sample.csv",
            content_sha256=CONTENT_SHA,
            policy_hash=policy.policy_hash(),
            engine_version=ENGINE_VERSION,
        ),
    )


def test_fr16_artifacts_are_a_pure_function_of_the_inputs() -> None:
    policy = Policy()
    assert _artifacts(dirty_table(), policy) == _artifacts(dirty_table(), policy)


def test_fr16_no_ids_timestamps_or_paths_in_the_artifacts() -> None:
    policy = Policy()
    cleaned, audit, findings, report = _artifacts(dirty_table(), policy)
    for blob in (cleaned, audit, findings, report):
        assert "2026-" not in blob.replace(CONTENT_SHA, "")
        assert "/home/" not in blob
        assert "run_id" not in blob


def test_fr16_policy_hash_changes_with_the_policy() -> None:
    default = Policy()
    tuned = Policy.model_validate({"tiers": {"fix.drop_duplicate_row": "off"}})
    assert default.policy_hash() != tuned.policy_hash()
    assert len(default.policy_hash()) == 16
    assert default.policy_hash() == Policy().policy_hash()


# --------------------------------------------------------------------------
# US-6 policy tuning
# --------------------------------------------------------------------------


def test_us6_disabling_duplicate_dropping_keeps_the_rows() -> None:
    policy = Policy.model_validate({"tiers": {"fix.drop_duplicate_row": "off"}})
    result = clean(dirty_table(), policy)
    assert result.cleaned.n_rows == 5
    assert not [entry for entry in result.audit if entry.kind.value == "row_drop"]
    assert result.plan.disabled_rules == ["fix.drop_duplicate_row"]
    report = render_report(
        result,
        source_name="sample.csv",
        content_sha256=CONTENT_SHA,
        policy_hash=policy.policy_hash(),
        engine_version=ENGINE_VERSION,
    )
    assert "fix.drop_duplicate_row" in report
    assert "disabled by policy" in report


def test_us6_disabling_a_detector_family_silences_its_findings() -> None:
    policy = Policy.model_validate({"detectors": {"cat": False, "enc": False}})
    result = clean(dirty_table(), policy)
    classes = {issue.klass.value for issue in result.issues}
    assert "CAT" not in classes
    assert "ENC" not in classes


def test_policy_toml_round_trip() -> None:
    document = """
[general]
settle_seconds = 9

[thresholds]
type_majority = 0.8

[tiers]
"fix.label_merge_nn" = "report"
"""
    policy = Policy.from_toml(document)
    assert policy.general.settle_seconds == 9
    assert policy.thresholds.type_majority == 0.8
    assert policy.tiers["fix.label_merge_nn"] == "report"
    assert policy.general.poll_interval_seconds == 5  # default preserved


def test_pipeline_handles_an_empty_table() -> None:
    result = clean(table([], []))
    assert result.cleaned.rows == []
    assert result.issues == []

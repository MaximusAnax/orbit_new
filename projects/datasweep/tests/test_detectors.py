"""FR-6: the eight detector families, one group per class."""

from __future__ import annotations

import pytest
from datasweep.engine.detectors import (
    damerau_levenshtein,
    detect_cat,
    detect_coercion_failures,
    detect_date,
    detect_dup,
    detect_enc,
    detect_miss,
    detect_out,
    detect_type,
    detect_ws,
    fingerprint,
    is_round_sentinel,
    mojibake_indicator_count,
    normalize_whitespace,
    repair_mojibake,
)
from datasweep.engine.inference import profile_table
from datasweep.engine.models import ColumnType, Policy, RawTable
from datasweep.engine.stats import summarize
from support_datasweep import column_table, repeat, table


def detect(fn, raw: RawTable, policy: Policy | None = None):
    policy = policy or Policy()
    return fn(raw, profile_table(raw, policy), policy)


# --------------------------------------------------------------------------
# ENC (FR-4 / D6)
# --------------------------------------------------------------------------


def test_fr6_enc_repairs_mojibake_by_round_trip() -> None:
    assert repair_mojibake("JosÃ©") == "José"
    assert repair_mojibake("caffÃ¨ latte") == "caffè latte"


def test_fr6_enc_leaves_legitimate_accented_text_alone() -> None:
    """The strict UTF-8 decode is what protects Åsa and château."""
    for legitimate in ["Åsa", "château", "Ana", "Zürich"]:
        assert repair_mojibake(legitimate) is None


def test_fr6_enc_requires_a_strict_indicator_reduction() -> None:
    assert mojibake_indicator_count("JosÃ©") == 1
    assert mojibake_indicator_count("José") == 0
    assert repair_mojibake("plain ascii") is None


def test_fr6_enc_confidence_is_the_column_repair_share() -> None:
    raw = column_table("name", ["JosÃ©", "Åsa", "Ana"])
    proposals = detect(detect_enc, raw)
    assert len(proposals) == 1
    assert proposals[0].evidence["share"] == pytest.approx(0.5)
    assert proposals[0].after == "José"


# --------------------------------------------------------------------------
# WS
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("  Ana  ", "Ana", "leading"),
        ("Ana\u00a0Lee", "Ana Lee", "nbsp"),
        ("An\u200ba", "Ana", "zero_width"),
        ("Ana  Lee", "Ana Lee", "internal_run"),
        ("Cafe\u0301", "Caf\u00e9", "nfc"),
    ],
)
def test_fr6_ws_normalizations(before: str, after: str, kind: str) -> None:
    result = normalize_whitespace(before, Policy())
    assert result is not None
    assert result.value == after
    assert kind in result.kinds


def test_fr6_ws_clean_cell_produces_nothing() -> None:
    assert normalize_whitespace("Ana Lee", Policy()) is None


def test_fr6_ws_one_proposal_per_cell_even_with_several_sub_rules() -> None:
    raw = column_table("name", ["\u00a0  Ana  Lee "])
    proposals = detect(detect_ws, raw)
    assert len(proposals) == 1
    assert proposals[0].after == "Ana Lee"
    assert set(proposals[0].evidence["rules"]) == {"fix.nbsp", "fix.trim", "fix.collapse_spaces"}


# --------------------------------------------------------------------------
# MISS (D8)
# --------------------------------------------------------------------------


def test_fr6_empty_cell_is_not_an_issue() -> None:
    """Absence of data is data (FR-6): MISS detects representations only."""
    raw = column_table("age", [None, "31", None, "42"])
    assert detect(detect_miss, raw) == []


def test_fr6_miss_hard_and_soft_lists() -> None:
    values = [str(30 + index) for index in range(18)] + ["N/A", "unknown"]
    raw = column_table("age", values)
    proposals = detect(detect_miss, raw)
    assert [proposal.rule for proposal in proposals] == [
        "fix.sentinel_null_hard",
        "fix.sentinel_null_soft",
    ]


def test_fr6_miss_targets_the_canonical_null() -> None:
    raw = column_table("age", ["31", "NaN", "42", "44", "45", "46", "47", "48", "49", "50"])
    proposal = detect(detect_miss, raw)[0]
    assert proposal.after is None


def test_fr6_miss_in_a_text_column_is_never_hard() -> None:
    values = [f"comment number {index}" for index in range(12)] + ["NA"]
    raw = column_table("comment", values)
    proposals = detect(detect_miss, raw)
    assert [proposal.rule for proposal in proposals] == ["fix.sentinel_null_soft"]


def test_fr6_numeric_sentinel_is_report_only_and_needs_three_repeats() -> None:
    values = [str(20 + index) for index in range(20)] + ["-9999"] * 4
    raw = column_table("temperature_c", values)
    proposals = [
        proposal
        for proposal in detect(detect_miss, raw)
        if proposal.rule == "detect.numeric_sentinel"
    ]
    assert len(proposals) == 4
    assert all(proposal.before == proposal.after for proposal in proposals)
    assert proposals[0].evidence["occurrences"] == 4


def test_fr6_numeric_sentinel_ignores_a_lone_extreme_value() -> None:
    values = [str(20 + index) for index in range(20)] + ["-9999"]
    raw = column_table("temperature_c", values)
    assert not [
        proposal
        for proposal in detect(detect_miss, raw)
        if proposal.rule == "detect.numeric_sentinel"
    ]


@pytest.mark.parametrize("value", ["-9999", "9999", "-999", "-99900"])
def test_fr6_round_sentinel_shapes(value: str) -> None:
    assert is_round_sentinel(value)


@pytest.mark.parametrize("value", ["-1", "42", "1234.56", "0"])
def test_fr6_ordinary_values_are_not_round_sentinels(value: str) -> None:
    assert not is_round_sentinel(value)


# --------------------------------------------------------------------------
# TYPE (D7)
# --------------------------------------------------------------------------


def test_fr6_type_canonicalizes_a_decisive_cell_from_its_own_reading() -> None:
    raw = column_table("amount", ["1,234.56", "9.99", "12.50", "19,99"])
    fixes = {proposal.row: proposal for proposal in detect(detect_type, raw)}
    assert fixes[0].after == "1234.56"
    assert fixes[3].after == "19.99"
    assert fixes[3].evidence["convention"] == "COMMA"
    assert 1 not in fixes and 2 not in fixes  # already canonical: no change, no issue


def test_fr6_mixed_convention_advisory() -> None:
    raw = column_table("amount", ["1,234.56", "9.99", "12.50", "19,99"])
    advisories = [
        proposal
        for proposal in detect(detect_type, raw)
        if proposal.rule == "detect.mixed_number_conventions"
    ]
    assert len(advisories) == 1
    assert advisories[0].row is None
    assert advisories[0].evidence["scope"] == "column"
    assert advisories[0].evidence["agree"] == pytest.approx(0.75)


def test_fr6_no_advisory_when_the_column_agrees() -> None:
    raw = column_table("amount", ["1,234.56", "9.99", "12.50", "19.99"])
    assert not [
        proposal
        for proposal in detect(detect_type, raw)
        if proposal.rule == "detect.mixed_number_conventions"
    ]


def test_fr6_digits_column_is_never_numerically_coerced() -> None:
    raw = column_table("zip", ["02134", "'01001", "90210", "12345"])
    profile = profile_table(raw, Policy())
    assert profile.type_of(0) is ColumnType.DIGITS
    proposals = detect(detect_type, raw)
    assert len(proposals) == 1
    assert proposals[0].rule == "fix.strip_apostrophe"
    assert proposals[0].after == "01001"


def test_fr6_currency_strip_and_apostrophe() -> None:
    raw = column_table("amount", ["€12.30", "'42.00", "9.99", "10.00"])
    by_row = {proposal.row: proposal for proposal in detect(detect_type, raw)}
    assert by_row[0].after == "12.30"
    assert by_row[1].after == "42.00"
    assert by_row[1].rule == "fix.strip_apostrophe"


def test_fr6_coercion_failures_are_reported_never_guessed() -> None:
    """A cell that fits nothing and that no rule explains is a report finding."""
    values = [str(10 + index) for index in range(18)] + ["not-a-number", "99"]
    raw = column_table("amount", values)
    proposals = detect_coercion_failures(
        raw,
        profile_table(raw, Policy()),
        Policy(),
        covered=set(),
        row_map=list(range(raw.n_rows)),
    )
    assert [proposal.row for proposal in proposals] == [18]
    assert proposals[0].before == proposals[0].after == "not-a-number"
    assert proposals[0].evidence["expected_type"] == "integer"


def test_fr6_a_cell_with_a_planned_fix_is_not_a_coercion_failure() -> None:
    values = [str(10 + index) for index in range(18)] + ["not-a-number", "99"]
    raw = column_table("amount", values)
    proposals = detect_coercion_failures(
        raw,
        profile_table(raw, Policy()),
        Policy(),
        covered={(18, 0)},
        row_map=list(range(raw.n_rows)),
    )
    assert proposals == []


def test_fr6_mixed_currency_symbols_block_stripping() -> None:
    raw = column_table("amount", ["€12.30", "$9.99", "10.00", "11.00"])
    rules = {proposal.rule for proposal in detect(detect_type, raw)}
    assert rules == {"fix.currency_mixed"}


# --------------------------------------------------------------------------
# DATE (D9)
# --------------------------------------------------------------------------


def test_fr6_date_proven_column_fixes_every_cell() -> None:
    raw = column_table("event_date", ["31/01/2023", "03/04/2021", "2021-01-07"])
    proposals = detect(detect_date, raw)
    assert {proposal.row for proposal in proposals} == {0, 1}
    assert all(proposal.rule == "fix.date_canon" for proposal in proposals)
    assert proposals[1].after == "2021-04-03"
    assert proposals[1].evidence["proof_row"] == 0


def test_fr6_date_ambiguous_column_yields_one_column_scope_group() -> None:
    raw = column_table("event_date", ["03/04/2021", "07/01/2021", "05/05/2021"])
    proposals = detect(detect_date, raw)
    assert {proposal.rule for proposal in proposals} == {"fix.date_canon_ambiguous"}
    assert {proposal.group for proposal in proposals} == {"0:date_ambiguous"}
    assert proposals[0].evidence["scope"] == "column"
    assert proposals[0].evidence["candidates"] == ["%d/%m/%Y", "%m/%d/%Y"]


def test_fr6_date_two_digit_year_is_its_own_rule() -> None:
    raw = column_table("event_date", ["31/01/23", "03/04/21", "2021-01-07"])
    rules = {proposal.rule for proposal in detect(detect_date, raw)}
    assert rules == {"fix.two_digit_year"}


def test_fr6_excel_serial_needs_other_date_evidence() -> None:
    dates = [f"2023-02-{day:02d}" for day in range(1, 20)]
    dated = column_table("restock_date", [*dates, "44927"])
    proposals = [p for p in detect(detect_date, dated) if p.rule == "fix.excel_serial"]
    assert len(proposals) == 1
    assert proposals[0].after == "2023-01-01"

    plain = column_table("code", ["44927", "31002", "58001", "22222"])
    assert not [p for p in detect(detect_date, plain) if p.rule == "fix.excel_serial"]


def test_fr6_datetime_column_is_canonicalized_to_rfc3339() -> None:
    raw = column_table("ts", ["2023-01-05 09:30:00", "2023-01-06T10:00:00Z"])
    proposals = detect(detect_date, raw)
    assert [proposal.after for proposal in proposals] == ["2023-01-05T09:30:00"]


# --------------------------------------------------------------------------
# CAT (D10)
# --------------------------------------------------------------------------


def test_fr6_fingerprint_normalizes_case_punctuation_and_spacing() -> None:
    assert fingerprint(" U.S.A. ") == fingerprint("usa") == "usa"
    assert fingerprint("New  York") == fingerprint("new york") == "new york"
    assert fingerprint("Smith John") != fingerprint("John Smith")  # order preserved (D10)


def test_fr6_cat_fingerprint_cluster_merges_to_the_most_frequent_form() -> None:
    values = ["USA"] * 40 + ["U.S.A."] + [" usa "]
    raw = column_table("country", values)
    proposals = detect(detect_cat, raw)
    assert {proposal.after for proposal in proposals} == {"USA"}
    assert len(proposals) == 2
    assert proposals[0].evidence["dominance"] == pytest.approx(40 / 42)


def test_fr6_cat_nearest_neighbour_proposes_only_rare_minorities() -> None:
    values = ["Mississippi"] * 40 + ["Missisippi"]
    raw = column_table("state", values)
    proposals = [p for p in detect(detect_cat, raw) if p.rule == "fix.label_merge_nn"]
    assert len(proposals) == 1
    assert proposals[0].after == "Mississippi"
    assert proposals[0].evidence["ratio"] == pytest.approx(1 / 41, abs=1e-6)


def test_fr6_cat_frequent_near_labels_are_left_alone() -> None:
    """Iran/Iraq and Slovakia/Slovenia are legitimate pairs, not typos."""
    values = ["Iran"] * 25 + ["Iraq"] * 20 + ["Slovakia"] * 22 + ["Slovenia"] * 18
    raw = column_table("country", values)
    assert detect(detect_cat, raw) == []


def test_fr6_cat_ignores_free_text_columns() -> None:
    values = [f"free text number {index}" for index in range(30)] + ["free text number 0 "]
    raw = column_table("comment", values)
    assert detect(detect_cat, raw) == []


@pytest.mark.parametrize(
    ("left", "right", "distance"),
    [
        ("Iran", "Iraq", 1),
        ("Missisippi", "Mississippi", 1),
        ("Slovakia", "Slovenia", 2),
        ("ab", "ba", 1),
    ],
)
def test_fr6_damerau_levenshtein(left: str, right: str, distance: int) -> None:
    assert damerau_levenshtein(left, right) == distance
    assert damerau_levenshtein(left, left) == 0


# --------------------------------------------------------------------------
# DUP
# --------------------------------------------------------------------------


def test_fr6_dup_keeps_the_first_occurrence() -> None:
    raw = table(["a", "b"], [["1", "x"], ["2", "y"], ["1", "x"], ["1", "x"]])
    proposals = detect(detect_dup, raw)
    assert [proposal.row for proposal in proposals] == [2, 3]
    assert all(proposal.evidence["first_occurrence_row"] == 0 for proposal in proposals)
    assert proposals[0].before_row == ["1", "x"]


def test_fr6_dup_ignores_near_duplicate_rows() -> None:
    raw = table(
        ["id", "ts"],
        [["1", "2023-01-01T00:00:00"], ["1", "2023-01-01T00:00:01"]],
    )
    assert detect(detect_dup, raw) == []


# --------------------------------------------------------------------------
# OUT (D11)
# --------------------------------------------------------------------------


def test_fr6_out_needs_both_tukey_and_modified_z() -> None:
    values = [str(value) for value in range(10, 34)] + ["5000"]
    raw = column_table("amount", values)
    proposals = detect(detect_out, raw)
    assert len(proposals) == 1
    assert proposals[0].before == "5000"
    assert proposals[0].evidence["mad_z"] > 3.5


def test_fr6_out_is_silent_below_the_minimum_sample() -> None:
    values = [str(value) for value in range(10, 25)] + ["5000"]
    raw = column_table("amount", values)
    assert detect(detect_out, raw) == []


def test_fr6_out_never_modifies_anything() -> None:
    values = [str(value) for value in range(10, 34)] + ["5000"]
    raw = column_table("amount", values)
    proposal = detect(detect_out, raw)[0]
    assert proposal.before == proposal.after


def test_d11_fence_math_on_a_known_array() -> None:
    summary = summarize([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], iqr_k=3.0)
    assert summary.q1 == pytest.approx(3.0)
    assert summary.median == pytest.approx(5.0)
    assert summary.q3 == pytest.approx(7.0)
    assert summary.iqr == pytest.approx(4.0)
    assert summary.lower_fence == pytest.approx(-9.0)
    assert summary.upper_fence == pytest.approx(19.0)
    assert summary.mad == pytest.approx(2.0)
    assert summary.modified_z(9.0) == pytest.approx(0.6745 * 4 / 2)


def test_d11_modified_z_falls_back_when_mad_is_zero() -> None:
    summary = summarize([5.0] * 10 + [9.0], iqr_k=3.0)
    assert summary.mad == pytest.approx(0.0)
    assert summary.modified_z(9.0) > 0


def test_detectors_respect_the_family_switches() -> None:
    policy = Policy.model_validate({"detectors": {"ws": False}})
    raw = column_table("name", ["  Ana  "])
    assert detect(detect_ws, raw, policy) == []


def test_repeat_helper_builds_categorical_columns() -> None:
    labels = repeat(["a", "b"], 10)
    assert len(labels) == 20

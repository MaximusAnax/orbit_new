"""FR-5 type inference, D7 number conventions, D9 dates."""

from __future__ import annotations

import pytest
from datasweep.engine.inference import (
    COMMA,
    DOT,
    analyze_dates,
    analyze_numbers,
    cell_types,
    date_candidates,
    excel_serial_to_iso,
    infer_column_type,
    is_convention_bearing,
    looks_headerless,
    number_readings,
    parse_bool,
    parse_datetime_cell,
    parse_number,
    profile_table,
    sentinel_kind,
    split_affixes,
)
from datasweep.engine.models import ColumnType, Policy, RawTable
from support_datasweep import column_table, repeat, table

THRESHOLDS = Policy().thresholds


# --------------------------------------------------------------------------
# micro-parsers
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["true", "FALSE", "Yes", "no", "y", "N", "t", "F", "  true  "])
def test_fr5_bool_token_vocabulary(token: str) -> None:
    assert parse_bool(token) is not None


@pytest.mark.parametrize("token", ["0", "1", "2", "oui", "on", "off", ""])
def test_fr5_zero_and_one_are_not_boolean(token: str) -> None:
    assert parse_bool(token) is None


def test_fr5_numeric_flag_column_types_as_integer_not_bool() -> None:
    vote = infer_column_type(["0", "1", "1", "0", "1", "0"], THRESHOLDS)
    assert vote.column_type is ColumnType.INTEGER


def test_fr5_digits_wins_on_leading_zero() -> None:
    vote = infer_column_type(["02134", "01001", "90210", "12345"], THRESHOLDS)
    assert vote.column_type is ColumnType.DIGITS


def test_fr5_digits_wins_on_fixed_width_with_high_distinct_ratio() -> None:
    values = [f"{4000 + index}" for index in range(40)]
    assert infer_column_type(values, THRESHOLDS).column_type is ColumnType.DIGITS


def test_fr5_short_numbers_stay_integer() -> None:
    ages = [str(age) for age in range(18, 91)]
    assert infer_column_type(ages, THRESHOLDS).column_type is ColumnType.INTEGER


def test_fr5_majority_vote_absorbs_a_dirty_minority() -> None:
    values = [str(number) for number in range(9)] + ["oops"]
    vote = infer_column_type(values, THRESHOLDS)
    assert vote.column_type is ColumnType.INTEGER
    assert vote.coverage == pytest.approx(0.9)


def test_fr5_majority_vote_falls_through_below_the_threshold() -> None:
    values = [str(number) for number in range(8)] + ["oops", "nope"]
    assert infer_column_type(values, THRESHOLDS).column_type is ColumnType.TEXT


def test_fr5_hard_sentinels_do_not_pollute_the_type_vote() -> None:
    values = ["1", "2", "3", "N/A", "NA", "4", "5", "6", "7", "8"]
    vote = infer_column_type(values, THRESHOLDS)
    assert vote.column_type is ColumnType.INTEGER
    assert vote.coverage == pytest.approx(1.0)


def test_fr5_categorical_needs_low_distinct_ratio() -> None:
    labels = repeat(["USA", "FRA", "DEU"], 12)
    assert infer_column_type(labels, THRESHOLDS).column_type is ColumnType.CATEGORICAL
    assert infer_column_type(["USA", "FRA", "DEU"], THRESHOLDS).column_type is ColumnType.TEXT


def test_fr5_float_beats_integer_on_a_mixed_numeric_column() -> None:
    values = ["1", "2.5", "3", "4.25", "5", "6.75", "7", "8.5", "9", "10.5"]
    assert infer_column_type(values, THRESHOLDS).column_type is ColumnType.FLOAT


def test_fr5_thousands_separators_still_type_as_numeric() -> None:
    values = ["1,234.56", "19,99", "€12.30", "'42.00", "7.25", "1234"]
    assert infer_column_type(values, THRESHOLDS).column_type is ColumnType.FLOAT


def test_fr5_date_and_datetime_types() -> None:
    dates = ["2023-01-31", "31/01/2023", "01.02.2023", "Jan 05, 2023"]
    assert infer_column_type(dates, THRESHOLDS).column_type is ColumnType.DATE
    stamps = ["2023-01-31T10:00:00Z", "2023-02-01 09:30:00", "2023-02-02T00:00:00+01:00"]
    assert infer_column_type(stamps, THRESHOLDS).column_type is ColumnType.DATETIME


def test_fr5_empty_column_is_text() -> None:
    vote = infer_column_type([None, None], THRESHOLDS)
    assert vote.column_type is ColumnType.TEXT
    assert vote.null_count == 2
    assert vote.non_null == 0


def test_fr5_headers_do_not_influence_type() -> None:
    """Header names are never admissible evidence (FR-5, EVALS M1)."""
    original = table(
        ["zip", "signup_date", "amount", "comment"],
        [
            ["02134", "2023-01-05", "12.50", "hello"],
            ["01001", "2023-02-06", "9.99", "there"],
            ["90210", "2023-03-07", "112.00", "again"],
            ["12345", "2023-04-08", "8.25", "words"],
        ],
    )
    renamed = RawTable(
        headers=[f"c{index}" for index in range(original.n_cols)], rows=original.rows
    )
    policy = Policy()
    left = [profile.inferred_type for profile in profile_table(original, policy).columns]
    right = [profile.inferred_type for profile in profile_table(renamed, policy).columns]
    assert left == right
    assert left[0] is ColumnType.DIGITS


def test_fr5_cell_types_are_a_set_per_cell() -> None:
    assert ColumnType.INTEGER in cell_types("42")
    assert ColumnType.FLOAT in cell_types("42")
    assert ColumnType.DIGITS in cell_types("42")
    assert ColumnType.INTEGER not in cell_types("4.2")
    assert cell_types("hello") == frozenset({ColumnType.TEXT, ColumnType.CATEGORICAL})


# --------------------------------------------------------------------------
# D7 — number conventions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1.234.567", {COMMA: "1234567"}),
        ("1,234.56", {DOT: "1234.56"}),
        ("19,99", {COMMA: "19.99"}),
        ("19.99", {DOT: "19.99"}),
        ("1,234", {DOT: "1234", COMMA: "1.234"}),
        ("1.234", {DOT: "1.234", COMMA: "1234"}),
    ],
)
def test_d7_worked_examples_from_the_spec(cell: str, expected: dict[str, str]) -> None:
    assert number_readings(cell) == expected


def test_d7_grouping_runs_must_be_exactly_three_digits() -> None:
    assert parse_number("1,23,456", DOT) is None
    assert parse_number("1234,567", DOT) is None
    assert parse_number("12,345", DOT) == "12345"


def test_d7_at_most_one_decimal_separator() -> None:
    assert parse_number("1.2.3", DOT) is None


def test_d7_separator_free_cells_are_already_canonical() -> None:
    assert not is_convention_bearing("1234")
    assert number_readings("1234") == {DOT: "1234", COMMA: "1234"}


def test_d7_affix_stripping() -> None:
    assert split_affixes(" €1.234,56 ").currency == "€"
    assert split_affixes("'0123").apostrophe is True
    assert split_affixes("1234 USD").currency == "USD"
    assert split_affixes("plain").currency is None


def test_d7_column_agreement_and_majority() -> None:
    analysis = analyze_numbers(["1,234.56", "9.99", "12.50", "19,99"])
    assert analysis.decisive_counts == {COMMA: 1, DOT: 3}
    assert analysis.majority == DOT
    assert analysis.agree == pytest.approx(0.75)
    assert analysis.mixed is True


def test_d7_ties_resolve_to_dot() -> None:
    analysis = analyze_numbers(["1.234.567", "1,234.56"])
    assert analysis.majority == DOT


def test_d7_currency_coverage_is_recorded() -> None:
    analysis = analyze_numbers(["€10.00", "12.00", "€14.00", "16.00"])
    assert analysis.single_currency == "€"
    assert analysis.currency_coverage() == pytest.approx(0.5)


# --------------------------------------------------------------------------
# D9 — dates
# --------------------------------------------------------------------------


def test_d9_iso_and_named_month_formats() -> None:
    assert [c.iso for c in date_candidates("2023-01-31")] == ["2023-01-31"]
    assert [c.iso for c in date_candidates("Jan 05, 2023")] == ["2023-01-05"]
    assert [c.iso for c in date_candidates("05 Jan 2023")] == ["2023-01-05"]
    assert [c.iso for c in date_candidates("January 5, 2023")] == ["2023-01-05"]


def test_d9_invalid_calendar_dates_do_not_parse() -> None:
    assert date_candidates("2023-02-30") == []
    assert date_candidates("31/31/2023") == []


def test_d9_two_way_cell_offers_both_readings() -> None:
    specs = {candidate.spec for candidate in date_candidates("03/04/2021")}
    assert specs == {"%d/%m/%Y", "%m/%d/%Y"}


def test_d9_proven_day_first_column() -> None:
    analysis = analyze_dates(["31/01/2023", "03/04/2021", "07/01/2021"])
    assert analysis.order == "day-first"
    assert analysis.proof_row == 0
    assert analysis.is_ambiguous is False
    assert analysis.chosen(1).iso == "2021-04-03"


def test_d9_proven_month_first_column() -> None:
    analysis = analyze_dates(["01/31/2023", "03/04/2021"])
    assert analysis.order == "month-first"
    assert analysis.chosen(1).iso == "2021-03-04"


def test_d9_ambiguous_column_has_no_order_and_keeps_both_readings() -> None:
    analysis = analyze_dates(["03/04/2021", "07/01/2021", "05/05/2021"])
    assert analysis.order is None
    assert analysis.is_ambiguous is True
    assert analysis.ambiguous_rows == [0, 1, 2]
    # 05/05/2021 reads the same either way: 1 of 3 cells is interpretation-invariant.
    assert analysis.invariant_share == pytest.approx(1 / 3)


def test_d9_conflicting_proofs_are_treated_as_ambiguous() -> None:
    analysis = analyze_dates(["31/01/2023", "01/31/2023", "03/04/2021"])
    assert analysis.conflict is True
    assert analysis.order is None
    assert analysis.is_ambiguous is True


def test_d9_excel_serial_epoch() -> None:
    assert excel_serial_to_iso(44927) == "2023-01-01"


def test_d9_rfc3339_canonicalization() -> None:
    assert parse_datetime_cell("2023-01-05 09:30:00") == "2023-01-05T09:30:00"
    assert parse_datetime_cell("2023-01-05T09:30:00z") == "2023-01-05T09:30:00Z"
    assert parse_datetime_cell("2023-01-05T09:30:00+0100") == "2023-01-05T09:30:00+01:00"
    assert parse_datetime_cell("2023-01-05T25:30:00") is None


# --------------------------------------------------------------------------
# D8 sentinels + FR-3 headerless heuristic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["NA", "n/a", "NULL", "NaN", "#N/A", "#VALUE!", ""])
def test_d8_hard_sentinel_vocabulary(token: str) -> None:
    assert sentinel_kind(token) == "hard"


@pytest.mark.parametrize("token", ["-", ".", "?", "None", "missing", "UNKNOWN"])
def test_d8_soft_sentinel_vocabulary(token: str) -> None:
    assert sentinel_kind(token) == "soft"


def test_d8_a_real_value_is_not_a_sentinel() -> None:
    assert sentinel_kind("aspirin") is None
    assert sentinel_kind(None) is None


def test_fr3_normal_header_row_never_looks_headerless() -> None:
    headers = ["zip", "signup_date", "age"]
    rows: list[list[str | None]] = [
        ["02134", "2023-01-05", "44"],
        ["01001", "2023-02-06", "51"],
    ]
    assert looks_headerless(headers, rows, Policy()) is False


def test_fr3_headerless_file_is_detected() -> None:
    headers = ["02134", "2023-01-05", "44"]
    rows: list[list[str | None]] = [
        ["01001", "2023-02-06", "51"],
        ["90210", "2023-03-07", "29"],
    ]
    assert looks_headerless(headers, rows, Policy()) is True


def test_fr3_all_text_body_is_never_headerless() -> None:
    headers = ["alpha", "beta"]
    rows: list[list[str | None]] = [["gamma", "delta"], ["epsilon", "zeta"]]
    assert looks_headerless(headers, rows, Policy()) is False


def test_profile_reports_nulls_without_calling_them_issues() -> None:
    profile = profile_table(column_table("age", ["31", None, "42", None]), Policy())
    column = profile.columns[0]
    assert column.null_count == 2
    assert column.non_null == 2
    assert column.non_null + column.null_count == 4

"""FR-3 defensive reading and FR-4 encoding detection."""

from __future__ import annotations

import io
import json

import pytest
from datasweep.adapters.readers import (
    CsvReader,
    JsonlReader,
    XlsxReader,
    render_excel_value,
    sniff_delimiter,
)
from datasweep.engine.models import FileFormat, Policy
from datasweep.errors import FileTooLargeError, UnsupportedFormatError

POLICY = Policy()


def read_csv(text: str, *, path: str = "sample.csv", encoding: str = "utf-8"):
    reader = CsvReader()
    data = text.encode(encoding)
    meta = reader.sniff(path, data)
    return reader.read(data, meta, POLICY)


# --------------------------------------------------------------------------
# FR-3 — dialects
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a,b,c\n1,2,3\n4,5,6\n", ","),
        ("a\tb\tc\n1\t2\t3\n4\t5\t6\n", "\t"),
        ("a;b;c\n1;2;3\n4;5;6\n", ";"),
        ("a|b|c\n1|2|3\n4|5|6\n", "|"),
    ],
)
def test_fr3_delimiter_sniffing(text: str, expected: str) -> None:
    assert sniff_delimiter(text, ",") == expected


def test_fr3_tsv_keeps_tabs_and_reports_its_format() -> None:
    reader = CsvReader()
    data = b"a\tb\n1\t2\n"
    meta = reader.sniff("sample.tsv", data)
    assert meta.format is FileFormat.TSV
    assert meta.delimiter == "\t"


def test_fr3_quoted_fields_with_embedded_newlines() -> None:
    result = read_csv('name,note\n"Ana","line one\nline two"\n')
    assert result.table.rows == [["Ana", "line one\nline two"]]


def test_fr3_empty_fields_read_as_the_canonical_null() -> None:
    result = read_csv("a,b\n1,\n,2\n")
    assert result.table.rows == [["1", None], [None, "2"]]


def test_fr3_ragged_rows_are_preserved_by_the_reader() -> None:
    """The parsed original keeps the file's shape; STR repairs it under audit."""
    result = read_csv("a,b,c\n1,2\n3,4,5,6\n")
    assert result.table.rows == [["1", "2"], ["3", "4", "5", "6"]]
    assert result.table.is_rectangular is False


def test_fr3_duplicate_headers_are_preserved_by_the_reader() -> None:
    result = read_csv("a,a,b\n1,2,3\n")
    assert result.table.headers == ["a", "a", "b"]


def test_fr3_blank_lines_carry_no_cells() -> None:
    result = read_csv("a,b\n1,2\n\n3,4\n")
    assert result.table.rows == [["1", "2"], ["3", "4"]]


def test_fr3_headerless_file_gets_synthetic_headers_and_a_review_issue() -> None:
    text = "02134,2023-01-05,44\n01001,2023-02-06,51\n90210,2023-03-07,29\n"
    result = read_csv(text)
    assert result.table.headers == ["col_0", "col_1", "col_2"]
    assert result.table.n_rows == 3
    assert result.meta.has_header is False
    assert [issue.rule for issue in result.issues] == ["fix.synthetic_headers"]
    assert result.issues[0].tier.value == "review"


def test_fr3_normal_file_keeps_its_header_row() -> None:
    result = read_csv("zip,when,age\n02134,2023-01-05,44\n01001,2023-02-06,51\n")
    assert result.table.headers == ["zip", "when", "age"]
    assert result.meta.has_header is True
    assert result.issues == []


def test_fr3_empty_file_is_an_empty_table() -> None:
    result = read_csv("")
    assert result.table.headers == []
    assert result.table.rows == []


def test_fr3_row_cap_is_enforced() -> None:
    policy = Policy.model_validate({"general": {"max_rows": 2}})
    reader = CsvReader()
    data = b"a\n1\n2\n3\n"
    with pytest.raises(FileTooLargeError) as excinfo:
        reader.read(data, reader.sniff("x.csv", data), policy)
    assert excinfo.value.code == "file_too_large"


# --------------------------------------------------------------------------
# FR-3 — JSONL
# --------------------------------------------------------------------------


def read_jsonl(text: str):
    reader = JsonlReader()
    data = text.encode()
    return reader.read(data, reader.sniff("sample.jsonl", data), POLICY)


def test_fr3_jsonl_key_union_in_first_seen_order() -> None:
    result = read_jsonl('{"b": 1, "a": 2}\n{"c": 3, "a": 4}\n')
    assert result.table.headers == ["b", "a", "c"]
    assert result.table.rows == [["1", "2", None], [None, "4", "3"]]


def test_fr3_jsonl_numbers_keep_their_source_text() -> None:
    result = read_jsonl('{"x": 0.1, "y": 1e3, "z": 100}\n')
    assert result.table.rows == [["0.1", "1e3", "100"]]


def test_fr3_jsonl_null_is_the_canonical_null_and_bools_are_tokens() -> None:
    result = read_jsonl('{"a": null, "b": true, "c": false}\n')
    assert result.table.rows == [[None, "true", "false"]]


def test_fr3_jsonl_nested_values_are_serialized_and_flagged() -> None:
    result = read_jsonl('{"a": {"k": 1}, "b": [1, 2]}\n')
    assert json.loads(result.table.rows[0][0]) == {"k": 1}
    assert [issue.rule for issue in result.issues] == [
        "detect.nested_value",
        "detect.nested_value",
    ]
    assert all(issue.klass.value == "STR" for issue in result.issues)


def test_fr3_jsonl_non_object_line_is_a_clear_error() -> None:
    with pytest.raises(UnsupportedFormatError):
        read_jsonl("[1, 2, 3]\n")
    with pytest.raises(UnsupportedFormatError):
        read_jsonl("{not json}\n")


# --------------------------------------------------------------------------
# FR-3 / US-8 — XLSX
# --------------------------------------------------------------------------


def workbook_bytes(
    rows: list[list[object]], sheets: dict[str, list[list[object]]] | None = None
) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    for row in rows:
        sheet.append(row)
    for name, extra in (sheets or {}).items():
        other = workbook.create_sheet(name)
        for row in extra:
            other.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def read_xlsx(data: bytes, policy: Policy = POLICY, sheet: str | int | None = None):
    reader = XlsxReader()
    meta = reader.sniff("book.xlsx", data)
    if sheet is not None:
        meta = meta.model_copy(update={"sheet": sheet})
    return reader.read(data, meta, policy)


def test_us8_xlsx_values_are_read_as_displayed_strings() -> None:
    from datetime import date

    data = workbook_bytes(
        [
            ["sku", "qty", "price", "restock_date", "flag"],
            ["00123", 4, 12.5, date(2023, 1, 1), True],
            ["00124", 10, 3.0, date(2023, 2, 1), False],
        ]
    )
    result = read_xlsx(data)
    assert result.table.headers == ["sku", "qty", "price", "restock_date", "flag"]
    assert result.table.rows[0] == ["00123", "4", "12.5", "2023-01-01", "TRUE"]
    assert result.table.rows[1][2] == "3"  # no float artifact, no trailing .0


def test_us8_xlsx_serial_number_dates_survive_as_integers() -> None:
    data = workbook_bytes([["restock_date"], [44927], [44928]])
    result = read_xlsx(data)
    assert result.table.rows == [["44927"], ["44928"]]


def test_fr3_xlsx_sheet_selection_by_index_and_name() -> None:
    data = workbook_bytes([["a"], ["1"]], sheets={"second": [["b"], ["2"]]})
    assert read_xlsx(data).table.headers == ["a"]
    assert read_xlsx(data, sheet="second").table.headers == ["b"]
    assert read_xlsx(
        data, policy=Policy.model_validate({"general": {"xlsx_sheet": 2}})
    ).table.headers == ["b"]
    with pytest.raises(UnsupportedFormatError):
        read_xlsx(data, sheet="missing")


def test_fr3_xlsx_trailing_empty_rows_and_columns_are_trimmed() -> None:
    data = workbook_bytes([["a", "b", None], ["1", "2", None], [None, None, None]])
    result = read_xlsx(data)
    assert result.table.headers == ["a", "b"]
    assert result.table.rows == [["1", "2"]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (True, "TRUE"), (3, "3"), (3.0, "3"), (0.1, "0.1"), (1e-5, "1e-05")],
)
def test_fr3_excel_value_rendering(value: object, expected: str | None) -> None:
    assert render_excel_value(value) == expected


def test_fr3_unknown_extension_is_unsupported_format() -> None:
    from datasweep.adapters.readers import reader_for

    with pytest.raises(UnsupportedFormatError) as excinfo:
        reader_for("notes.parquet")
    assert excinfo.value.code == "unsupported_format"
    assert reader_for("a.CSV").__class__ is CsvReader

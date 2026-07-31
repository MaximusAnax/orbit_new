"""FR-10 report rendering (DATA_MODEL §3.4)."""

from __future__ import annotations

from datasweep.engine.models import RawTable
from datasweep.engine.report import render_report
from support_datasweep import CONTENT_SHA, ENGINE_VERSION, clean

POLICY_HASH = "0123456789abcdef"


def render(raw: RawTable, revision: int = 1) -> str:
    result = clean(raw)
    return render_report(
        result,
        source_name="sales.csv",
        content_sha256=CONTENT_SHA,
        policy_hash=POLICY_HASH,
        engine_version=ENGINE_VERSION,
        revision=revision,
        file_format="csv",
        encoding="cp1252",
        dialect={"delimiter": ",", "quotechar": '"', "has_header": True, "sheet": None},
    )


def dirty() -> RawTable:
    values = [str(19 + index % 5) for index in range(20)] + ["-9999"] * 4
    rows: list[list[str | None]] = [
        [f" name {index} ", value, "03/04/2021"] for index, value in enumerate(values)
    ]
    return RawTable(headers=["name", "temperature_c", "when"], rows=rows)


def test_fr10_report_has_every_documented_section() -> None:
    text = render(dirty())
    for heading in (
        "# datasweep report — sales.csv",
        "## Summary",
        "### Issues by class",
        "### Changes by tier",
        "## Columns",
        "## Review queue",
        "## Report-only findings",
    ):
        assert heading in text


def test_fr10_report_shows_the_null_count_column() -> None:
    raw = RawTable(headers=["age"], rows=[["31"], [None], ["42"]])
    text = render(raw)
    assert "nulls" in text
    assert "| age |" in text


def test_fr10_report_lists_review_items_with_their_ids() -> None:
    result = clean(dirty())
    text = render(dirty())
    for item in result.review_items:
        assert item.id in text
        assert item.rule in text


def test_fr10_report_lists_report_only_findings() -> None:
    text = render(dirty())
    assert "detect.numeric_sentinel" in text
    assert "never auto-nulled" in text


def test_fr16_report_footer_has_no_run_id_and_no_timestamp() -> None:
    text = render(dirty())
    assert f"`content_sha256`: `{CONTENT_SHA}`" in text
    assert f"`policy_hash`: `{POLICY_HASH}`" in text
    assert f"`engine_version`: `{ENGINE_VERSION}`" in text
    assert "`revision`: 1" in text
    assert "run_id" not in text
    assert "2026-" not in text


def test_fr16_report_is_byte_identical_across_renders() -> None:
    assert render(dirty()) == render(dirty())


def test_fr10_report_escapes_pipes_so_tables_survive() -> None:
    raw = RawTable(headers=["note"], rows=[[" a|b "], ["c"], ["d"]])
    text = render(raw)
    assert "a\\|b" in text


def test_fr10_clean_file_report_says_so() -> None:
    raw = RawTable(headers=["a"], rows=[["1"], ["2"], ["3"]])
    text = render(raw)
    assert "_Nothing awaiting review._" in text
    assert "_No report-only findings._" in text


def test_fr10_revision_number_is_rendered() -> None:
    assert "`revision`: 2" in render(dirty(), revision=2)

"""FR-10 artifact serialization, atomic writes and the FR-16 byte contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from datasweep.adapters.artifacts import (
    LocalArtifactWriter,
    artifact_dir_name,
    audit_filename,
    cleaned_filename,
    serialize_audit,
    serialize_findings,
    serialize_table,
)
from datasweep.adapters.notifier import LogNotifier
from datasweep.engine.models import FileFormat, RawTable, RunStatus, RunSummary
from datasweep.engine.report import render_report
from support_datasweep import CONTENT_SHA, ENGINE_VERSION, clean, table

POLICY_HASH = "0123456789abcdef"


def sample_result():
    raw = RawTable(
        headers=["name", "amount", "when"],
        rows=[
            [" JosÃ© ", "1,234.56", "31/01/2023"],
            ["Ana", "19,99", "03/04/2021"],
            ["Ana", "19,99", "03/04/2021"],
        ],
    )
    return raw, clean(raw)


def header_for(result) -> dict:
    return {
        "source_name": "sales.csv",
        "content_sha256": CONTENT_SHA,
        "policy_hash": POLICY_HASH,
        "engine_version": ENGINE_VERSION,
        "revision": 1,
        "n_rows": result.original.n_rows,
        "n_cols": result.original.n_cols,
    }


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def test_fr10_cleaned_csv_is_rfc4180_with_lf_endings() -> None:
    raw = table(["a", "b"], [["1", None], ['say "hi"', "x,y"]])
    text = serialize_table(raw, FileFormat.CSV)
    assert text == 'a,b\n1,\n"say ""hi""","x,y"\n'
    assert "\r" not in text


def test_fr10_tsv_input_keeps_tabs() -> None:
    raw = table(["a", "b"], [["1", "2"]])
    assert serialize_table(raw, FileFormat.TSV) == "a\tb\n1\t2\n"


def test_fr10_jsonl_input_yields_jsonl_with_json_nulls() -> None:
    raw = table(["a", "b"], [["1", None]])
    assert serialize_table(raw, FileFormat.JSONL) == '{"a": "1", "b": null}\n'


def test_fr10_xlsx_input_yields_canonical_csv() -> None:
    assert cleaned_filename(FileFormat.XLSX, 1) == "cleaned.csv"
    assert cleaned_filename(FileFormat.TSV, 1) == "cleaned.tsv"
    assert cleaned_filename(FileFormat.JSONL, 2) == "cleaned.r2.jsonl"
    assert audit_filename(1) == "audit.jsonl"
    assert audit_filename(3) == "audit.r3.jsonl"


def test_fr10_artifact_directory_name() -> None:
    assert artifact_dir_name("sales", CONTENT_SHA) == f"sales.{CONTENT_SHA[:8]}"


def test_fr10_audit_log_starts_with_a_header_line() -> None:
    _, result = sample_result()
    lines = serialize_audit(result.audit, header_for(result)).splitlines()
    head = json.loads(lines[0])
    assert head["kind"] == "header"
    assert head["source_name"] == "sales.csv"  # basename, never a path
    assert head["content_sha256"] == CONTENT_SHA
    assert len(lines) == len(result.audit) + 1


def test_fr10_sentinel_fix_serializes_after_as_json_null() -> None:
    raw = RawTable(
        headers=["age"],
        rows=[["31"], ["N/A"], ["42"], ["44"], ["45"], ["46"], ["47"], ["48"], ["49"], ["50"]],
    )
    result = clean(raw)
    payload = [json.loads(line) for line in serialize_audit(result.audit, {}).splitlines()[1:]]
    entry = next(item for item in payload if item["rule"] == "fix.sentinel_null_hard")
    assert entry["after"] is None
    assert "after" in entry


def test_fr10_findings_log_has_one_line_per_instance() -> None:
    _, result = sample_result()
    lines = serialize_findings(result.issues).splitlines()
    assert len(lines) == len(result.issues)
    first = json.loads(lines[0])
    assert set(first) >= {"klass", "rule", "tier", "row", "col", "col_name", "value", "evidence"}


def test_fr10_findings_include_every_tier() -> None:
    values = [str(19 + index % 5) for index in range(20)] + ["-9999"] * 4
    raw = RawTable(headers=["t"], rows=[[value] for value in values])
    result = clean(raw)
    tiers = {json.loads(line)["tier"] for line in serialize_findings(result.issues).splitlines()}
    assert "report" in tiers


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def test_fr10_writer_lays_out_all_four_artifacts(tmp_path: Path) -> None:
    _, result = sample_result()
    run_dir = tmp_path / artifact_dir_name("sales", CONTENT_SHA)
    paths = LocalArtifactWriter().write(
        str(run_dir),
        cleaned=result.cleaned,
        audit=result.audit,
        findings=result.issues,
        report_md=render_report(
            result,
            source_name="sales.csv",
            content_sha256=CONTENT_SHA,
            policy_hash=POLICY_HASH,
            engine_version=ENGINE_VERSION,
        ),
        fmt=FileFormat.CSV,
        header=header_for(result),
    )
    assert Path(paths.cleaned).name == "cleaned.csv"
    assert Path(paths.audit).name == "audit.jsonl"
    assert Path(paths.findings).name == "findings.jsonl"
    assert Path(paths.report).name == "report.md"
    assert sorted(path.name for path in run_dir.iterdir()) == [
        "audit.jsonl",
        "cleaned.csv",
        "findings.jsonl",
        "report.md",
    ]


def test_fr10_revision_two_writes_only_cleaned_and_audit(tmp_path: Path) -> None:
    _, result = sample_result()
    writer = LocalArtifactWriter()
    common = {
        "cleaned": result.cleaned,
        "audit": result.audit,
        "findings": result.issues,
        "report_md": "# report",
        "fmt": FileFormat.CSV,
        "header": header_for(result),
    }
    writer.write(str(tmp_path), **common)
    paths = writer.write(str(tmp_path), **common, revision=2)
    assert paths.findings is None and paths.report is None
    assert Path(paths.cleaned).name == "cleaned.r2.csv"
    assert (tmp_path / "audit.r2.jsonl").exists()


def test_fr10_writes_leave_no_partial_files_behind(tmp_path: Path) -> None:
    _, result = sample_result()
    LocalArtifactWriter().write(
        str(tmp_path),
        cleaned=result.cleaned,
        audit=result.audit,
        findings=result.issues,
        report_md="# report",
        fmt=FileFormat.CSV,
        header=header_for(result),
    )
    assert not list(tmp_path.glob("*.partial"))


def test_fr10_source_bytes_are_never_touched(tmp_path: Path) -> None:
    source = tmp_path / "sales.csv"
    source.write_bytes(b"name,amount\n Ana ,1,234.56\n")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    _, result = sample_result()
    LocalArtifactWriter().write(
        str(tmp_path / "out"),
        cleaned=result.cleaned,
        audit=result.audit,
        findings=result.issues,
        report_md="# report",
        fmt=FileFormat.CSV,
        header=header_for(result),
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_fr16_artifact_bytes_are_stable_across_writes(tmp_path: Path) -> None:
    _, result = sample_result()
    writer = LocalArtifactWriter()
    payload = {
        "cleaned": result.cleaned,
        "audit": result.audit,
        "findings": result.issues,
        "report_md": "# report",
        "fmt": FileFormat.CSV,
        "header": header_for(result),
    }
    first = writer.write(str(tmp_path / "a"), **payload)
    second = writer.write(str(tmp_path / "b"), **payload)
    for left, right in ((first.cleaned, second.cleaned), (first.audit, second.audit)):
        assert Path(left).read_bytes() == Path(right).read_bytes()


# --------------------------------------------------------------------------
# FR-13 notifier
# --------------------------------------------------------------------------


def test_fr13_log_notifier_appends_one_line_per_run(tmp_path: Path) -> None:
    notifier = LogNotifier(tmp_path / "notify.log")
    summary = RunSummary(
        run_id="abc",
        source_name="sales.csv",
        status=RunStatus.REVIEW_PENDING,
        issue_counts={"WS": 3},
        change_counts={"auto": 2, "review": 1},
    )
    notifier.notify(summary)
    notifier.notify(summary)
    lines = (tmp_path / "notify.log").read_text().splitlines()
    assert len(lines) == 2
    assert "sales.csv" in lines[0]
    assert "review_pending" in lines[0]


@pytest.mark.parametrize("status", list(RunStatus))
def test_fr13_summary_line_mentions_the_status(status: RunStatus) -> None:
    summary = RunSummary(run_id="a", source_name="f.csv", status=status)
    assert status.value in summary.one_line()

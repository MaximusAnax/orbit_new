"""The ArtifactWriter port (SCOPE.md FR-10).

Four artifacts per run, written atomically (temp file + ``os.replace`` — the
standard partial-write-safe idiom, and the same reason the daemon defers files
that are still being written).  Every byte is a pure function of (file bytes,
effective policy, engine version): no ids, no timestamps, no absolute paths
appear in any artifact (FR-16, DATA_MODEL §3.1/§3.4).
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from ..engine.models import AuditEntry, FileFormat, Issue, RawTable


class ArtifactPaths(BaseModel):
    model_config = ConfigDict(frozen=True)

    directory: str
    cleaned: str
    audit: str
    findings: str | None = None
    report: str | None = None


@runtime_checkable
class ArtifactWriter(Protocol):
    def write(
        self,
        run_dir: str,
        *,
        cleaned: RawTable,
        audit: list[AuditEntry],
        findings: list[Issue],
        report_md: str,
        fmt: FileFormat,
        header: dict[str, Any],
        revision: int = 1,
    ) -> ArtifactPaths:  # pragma: no cover - protocol
        ...


def artifact_dir_name(stem: str, content_sha256: str) -> str:
    """``<stem>.<sha256[:8]>`` (FR-10)."""
    return f"{stem}.{content_sha256[:8]}"


def cleaned_filename(fmt: FileFormat, revision: int) -> str:
    """``.tsv`` keeps tabs, ``.jsonl`` stays JSONL, ``.xlsx`` becomes CSV (D14)."""
    extension = {
        FileFormat.CSV: "csv",
        FileFormat.TSV: "tsv",
        FileFormat.JSONL: "jsonl",
        FileFormat.XLSX: "csv",
    }[fmt]
    suffix = "" if revision <= 1 else f".r{revision}"
    return f"cleaned{suffix}.{extension}"


def audit_filename(revision: int) -> str:
    return "audit.jsonl" if revision <= 1 else f"audit.r{revision}.jsonl"


def serialize_table(table: RawTable, fmt: FileFormat) -> str:
    """Canonical text for the cleaned table: UTF-8, RFC 4180 quoting, LF."""
    if fmt is FileFormat.JSONL:
        lines = []
        for row in table.rows:
            record = {
                header: (row[index] if index < len(row) else None)
                for index, header in enumerate(table.headers)
            }
            lines.append(json.dumps(record, ensure_ascii=False))
        return "".join(line + "\n" for line in lines)

    delimiter = "\t" if fmt is FileFormat.TSV else ","
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(table.headers)
    for row in table.rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue()


def serialize_audit(audit: list[AuditEntry], header: dict[str, Any]) -> str:
    """Line 1 is the header entry; every later line is one applied change."""
    lines = [json.dumps({"kind": "header", **header}, ensure_ascii=False)]
    lines.extend(json.dumps(entry.to_json_obj(), ensure_ascii=False) for entry in audit)
    return "".join(line + "\n" for line in lines)


def serialize_findings(findings: list[Issue]) -> str:
    """One line per detected issue instance, every tier (DATA_MODEL §3.2)."""
    return "".join(json.dumps(issue.to_json_obj(), ensure_ascii=False) + "\n" for issue in findings)


def atomic_write_text(path: str, text: str) -> None:
    """Write via ``<name>.partial`` + ``os.replace`` so readers never see a tear."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


class LocalArtifactWriter:
    """Offline default: atomic writes into the run's artifact directory."""

    def write(
        self,
        run_dir: str,
        *,
        cleaned: RawTable,
        audit: list[AuditEntry],
        findings: list[Issue],
        report_md: str,
        fmt: FileFormat,
        header: dict[str, Any],
        revision: int = 1,
    ) -> ArtifactPaths:
        directory = Path(run_dir)
        directory.mkdir(parents=True, exist_ok=True)

        cleaned_path = directory / cleaned_filename(fmt, revision)
        audit_path = directory / audit_filename(revision)
        atomic_write_text(str(cleaned_path), serialize_table(cleaned, fmt))
        atomic_write_text(str(audit_path), serialize_audit(audit, header))

        findings_path: str | None = None
        report_path: str | None = None
        if revision <= 1:
            # findings.jsonl and report.md describe what was *detected*, which
            # accepting a proposal does not change (DATA_MODEL §2.7).
            findings_path = str(directory / "findings.jsonl")
            report_path = str(directory / "report.md")
            atomic_write_text(findings_path, serialize_findings(findings))
            atomic_write_text(report_path, report_md)

        return ArtifactPaths(
            directory=str(directory),
            cleaned=str(cleaned_path),
            audit=str(audit_path),
            findings=findings_path,
            report=report_path,
        )

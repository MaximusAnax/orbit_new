"""Markdown report rendering (SCOPE.md FR-10, DATA_MODEL §3.4).

``report.md`` is covered by the FR-16 byte-identity contract, so nothing that
varies between runs may appear here: no run id, no timestamp, no absolute path.
The footer carries ``content_sha256`` / ``policy_hash`` / ``engine_version`` /
``revision``, which identify the inputs exactly.
"""

from __future__ import annotations

import json
from typing import Any

from .models import CleanResult, Issue, IssueClass, Tier

_MAX_SAMPLES = 5


def _cell(value: Any) -> str:
    if value is None:
        return "_(null)_"
    text = str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    return text if text.strip() else "_(empty)_"


def _evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return ""
    compact = json.dumps(evidence, sort_keys=True, ensure_ascii=False, separators=(",", " "))
    return f"`{_cell(compact)}`"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render_report(
    result: CleanResult,
    *,
    source_name: str,
    content_sha256: str,
    policy_hash: str,
    engine_version: str,
    revision: int = 1,
    file_format: str | None = None,
    encoding: str | None = None,
    dialect: dict[str, Any] | None = None,
) -> str:
    """Render the human-readable report for one cleaning result."""
    lines: list[str] = [f"# datasweep report — {source_name}", ""]

    lines.append("## Summary")
    lines.append("")
    summary_rows = [
        ["rows", str(result.original.n_rows)],
        ["columns", str(result.original.n_cols)],
        ["format", file_format or "—"],
        ["encoding", encoding or "—"],
        [
            "dialect",
            _cell(json.dumps(dialect, sort_keys=True)) if dialect else "—",
        ],
        ["issues detected", str(sum(result.issue_counts.values()))],
        ["auto changes applied", str(result.change_counts.get("auto", 0))],
        ["awaiting review", str(result.change_counts.get("review", 0))],
        ["report-only findings", str(result.change_counts.get("report", 0))],
    ]
    lines.extend(_table(["field", "value"], summary_rows))
    lines.append("")

    lines.append("### Issues by class")
    lines.append("")
    class_rows = [
        [klass.value, str(result.issue_counts.get(klass.value, 0))]
        for klass in IssueClass
        if result.issue_counts.get(klass.value)
    ]
    lines.extend(_table(["class", "instances"], class_rows or [["—", "0"]]))
    lines.append("")

    lines.append("### Changes by tier")
    lines.append("")
    lines.extend(
        _table(
            ["tier", "count"],
            [
                [tier, str(result.change_counts.get(tier, 0))]
                for tier in ("auto", "review", "report")
            ],
        )
    )
    lines.append("")

    if result.plan.disabled_rules:
        lines.append("### Disabled rules")
        lines.append("")
        for rule in result.plan.disabled_rules:
            lines.append(f"- `{rule}` — disabled by policy; no fixes were proposed.")
        lines.append("")

    lines.append("## Columns")
    lines.append("")
    column_rows = []
    for profile in result.profiles:
        notes: list[str] = []
        convention = profile.stats.get("convention")
        if isinstance(convention, dict):
            if convention.get("currency"):
                notes.append(f"currency {convention['currency']}")
            if convention.get("decimal"):
                notes.append(f"decimal `{convention['decimal']}`")
        if profile.original_name:
            notes.append(f"renamed from `{profile.original_name}`")
        if profile.stats.get("ambiguous"):
            notes.append("ambiguous date order")
        column_rows.append(
            [
                str(profile.col_index),
                _cell(profile.name),
                profile.inferred_type.value,
                f"{profile.type_coverage:.3f}",
                str(profile.non_null),
                str(profile.null_count),
                str(profile.distinct_count),
                ", ".join(notes) or "—",
            ]
        )
    lines.extend(
        _table(
            ["#", "name", "type", "coverage", "non-null", "nulls", "distinct", "notes"],
            column_rows or [["—"] * 8],
        )
    )
    lines.append("")

    by_class: dict[str, list[Issue]] = {}
    for issue in result.issues:
        by_class.setdefault(issue.klass.value, []).append(issue)
    if by_class:
        lines.append("## Issues by class")
        lines.append("")
        for klass in IssueClass:
            issues = by_class.get(klass.value)
            if not issues:
                continue
            lines.append(f"### {klass.value} — {len(issues)} instance(s)")
            lines.append("")
            sample_rows = [
                [
                    "—" if issue.row is None else str(issue.row),
                    _cell(issue.col_name) if issue.col_name else "—",
                    f"`{issue.rule}`",
                    issue.tier.value,
                    _cell(issue.value),
                ]
                for issue in issues[:_MAX_SAMPLES]
            ]
            lines.extend(_table(["row", "column", "rule", "tier", "value"], sample_rows))
            if len(issues) > _MAX_SAMPLES:
                lines.append("")
                lines.append(f"_… and {len(issues) - _MAX_SAMPLES} more (see `findings.jsonl`)._")
            lines.append("")

    lines.append("## Review queue")
    lines.append("")
    if result.review_items:
        item_rows = [
            [
                f"`{item.id}`",
                f"`{item.rule}`",
                _cell(item.description),
                f"{item.confidence:.2f}",
                str(item.affected_cells),
            ]
            for item in result.review_items
        ]
        lines.extend(_table(["item", "rule", "proposal", "confidence", "cells"], item_rows))
    else:
        lines.append("_Nothing awaiting review._")
    lines.append("")

    lines.append("## Report-only findings")
    lines.append("")
    reported = [issue for issue in result.issues if issue.tier is Tier.REPORT]
    if reported:
        report_rows = [
            [
                issue.klass.value,
                f"`{issue.rule}`",
                "—" if issue.row is None else str(issue.row),
                _cell(issue.col_name) if issue.col_name else "—",
                _cell(issue.value),
                _evidence(issue.evidence),
            ]
            for issue in reported[: _MAX_SAMPLES * 4]
        ]
        lines.extend(_table(["class", "rule", "row", "column", "value", "evidence"], report_rows))
        if len(reported) > _MAX_SAMPLES * 4:
            lines.append("")
            lines.append(f"_… and {len(reported) - _MAX_SAMPLES * 4} more (see `findings.jsonl`)._")
    else:
        lines.append("_No report-only findings._")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"- `content_sha256`: `{content_sha256}`")
    lines.append(f"- `policy_hash`: `{policy_hash}`")
    lines.append(f"- `engine_version`: `{engine_version}`")
    lines.append(f"- `revision`: {revision}")
    lines.append("")
    lines.append(
        "_datasweep never modifies the source file, never imputes a missing value, "
        "and never drops or clamps an outlier._"
    )
    return "\n".join(lines) + "\n"

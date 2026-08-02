"""Issues → :class:`FixPlan`: confidence formulas, tier algebra, review items.

SCOPE.md FR-7 and the D12 table.  This is the safety-critical layer: a wrong
auto-fix in a background tool is silent data corruption (D1), so the tier of a
fix is always the *most restrictive* of its rule's safety cap and what its
confidence earns.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import (
    AuditKind,
    Fix,
    Issue,
    Policy,
    Proposal,
    RawTable,
    ReviewItem,
    Tier,
    review_item_id,
    rule_spec,
    stricter,
)

# --------------------------------------------------------------------------
# Confidence (SCOPE.md D12)
# --------------------------------------------------------------------------


def confidence_for(rule: str, evidence: dict[str, Any], policy: Policy) -> float:
    """The rule's documented evidence formula (D12), clamped to [0, 1]."""
    thresholds = policy.thresholds
    if rule in {"fix.trim", "fix.nbsp", "fix.zero_width", "fix.nfc"}:
        value = 1.0
    elif rule == "fix.collapse_spaces":
        value = 1.0 if evidence.get("column_type") != "text" else 0.6
    elif rule == "fix.mojibake":
        value = float(evidence.get("share", 0.0))
    elif rule == "fix.sentinel_null_hard":
        value = 1.0
    elif rule == "fix.sentinel_null_soft":
        value = 0.7
    elif rule in {"fix.number_canon", "fix.currency_strip", "fix.strip_apostrophe"}:
        value = 1.0
    elif rule == "fix.number_canon_ambiguous":
        value = float(evidence.get("agree", 0.0))
    elif rule == "fix.currency_mixed":
        value = 0.4
    elif rule == "fix.date_canon":
        value = 1.0
    elif rule == "fix.date_canon_ambiguous":
        value = float(evidence.get("invariant_share", 0.0))
    elif rule in {"fix.excel_serial", "fix.two_digit_year"}:
        value = 0.6
    elif rule == "fix.label_merge_fingerprint":
        value = float(evidence.get("dominance", 0.0))
    elif rule == "fix.label_merge_nn":
        ratio = float(evidence.get("ratio", 1.0))
        value = 1.0 - ratio / thresholds.nn_max_ratio
    elif rule in {"fix.drop_duplicate_row", "fix.pad_row", "fix.dedupe_header"}:
        value = 1.0
    elif rule in {"fix.overflow_column", "fix.synthetic_headers"}:
        value = 0.7
    else:
        # Report-only findings (outliers, numeric sentinels, coercion failures,
        # mixed conventions): D12 lists their confidence as n/a.  They are
        # certain *as findings*; the tier is fixed at report regardless.
        value = 1.0
    return min(1.0, max(0.0, value))


def conf_tier(confidence: float, auto_threshold: float) -> Tier:
    """≥ auto_threshold → auto, ≥ 0.50 → review, else report (FR-7)."""
    if confidence >= auto_threshold:
        return Tier.AUTO
    if confidence >= 0.50:
        return Tier.REVIEW
    return Tier.REPORT


def resolve_tier(
    rule: str, confidence: float, evidence: dict[str, Any], policy: Policy
) -> Tier | None:
    """``stricter(safety_cap, conf_tier(confidence))``; ``None`` when off.

    Three rules carry a disposition the docs name outright rather than derive
    from confidence, and the code honours the prose over the generic formula:

    * ``fix.number_canon_ambiguous`` — D7.5 makes it auto iff
      ``|D| ≥ convention_min_decisive and agree ≥ convention_agree``, else a
      review item.  A column with no decisive cell has ``agree = 0``, which the
      generic mapping would push to *report* — the trap fixtures require a
      review item with both readings attached.
    * ``fix.date_canon_ambiguous`` — D9 requires "exactly one column-level
      review item" even when no cell is interpretation-invariant.
    * ``fix.currency_mixed`` — D7 requires a review item at confidence 0.4.

    ``fix.label_merge_fingerprint`` additionally needs cluster dominance ≥
    ``merge_dominance`` to be auto-*eligible* (D10a); below that the cap drops
    to review even though the confidence formula is the same number.
    """
    spec = rule_spec(rule)
    if not policy.detectors.enabled(spec.klass):
        return None
    override = policy.tiers.get(rule)
    if override == "off":
        return None
    cap = Tier(override) if override is not None else spec.cap

    if rule == "fix.number_canon_ambiguous":
        decisive_enough = (
            int(evidence.get("n_decisive", 0)) >= policy.thresholds.convention_min_decisive
            and float(evidence.get("agree", 0.0)) >= policy.thresholds.convention_agree
        )
        base = Tier.AUTO if decisive_enough else Tier.REVIEW
    elif rule == "fix.label_merge_fingerprint":
        base = conf_tier(confidence, policy.thresholds.auto_confidence)
        if float(evidence.get("dominance", 0.0)) < policy.thresholds.merge_dominance:
            base = stricter(base, Tier.REVIEW)
    elif spec.fixed_tier is not None:
        base = spec.fixed_tier
    else:
        base = conf_tier(confidence, policy.thresholds.auto_confidence)
    return stricter(cap, base)


def _composite(evidence: dict[str, Any], primary: str, policy: Policy) -> tuple[float, Tier | None]:
    """Confidence/tier for a cell repaired by several sub-rules at once.

    A cell that needs both an NBSP replacement and a trim produces exactly one
    audit entry (US-2), so its confidence is the *minimum* over the sub-rules
    and its tier the strictest — a review-capped sub-rule pulls the whole cell
    out of the auto tier rather than half-applying it.
    """
    rules = list(evidence.get("rules") or [primary])
    if primary not in rules:
        rules.append(primary)
    confidence = 1.0
    tier: Tier | None = None
    for rule in rules:
        rule_confidence = confidence_for(rule, evidence, policy)
        rule_tier = resolve_tier(rule, rule_confidence, evidence, policy)
        if rule_tier is None:
            return 0.0, None
        confidence = min(confidence, rule_confidence)
        tier = rule_tier if tier is None else stricter(tier, rule_tier)
    return confidence, tier


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def plan_fixes(proposals: list[Proposal], policy: Policy, headers: list[str]) -> list[Fix]:
    """Turn detector proposals into tiered fixes, in deterministic order."""
    fixes: list[Fix] = []
    for proposal in proposals:
        spec = rule_spec(proposal.rule)
        confidence, tier = _composite(proposal.evidence, proposal.rule, policy)
        if tier is None:
            continue
        if (
            proposal.kind is AuditKind.CELL_CHANGE
            and not (spec.detect_only or spec.allow_identity)
            and proposal.before == proposal.after
        ):
            continue  # never plan an identity change (FR-8)
        col_name = None
        if proposal.col is not None:
            col_name = (
                headers[proposal.col]
                if proposal.col < len(headers)
                else proposal.column_name or f"col_{proposal.col}"
            )
        fixes.append(
            Fix(
                rule=proposal.rule,
                klass=spec.klass,
                stage=spec.stage,
                kind=proposal.kind,
                tier=tier,
                confidence=round(confidence, 6),
                row=proposal.row,
                col=proposal.col,
                col_name=col_name,
                before=proposal.before,
                after=proposal.after,
                before_row=proposal.before_row,
                cols=proposal.cols,
                column_name=proposal.column_name,
                column_cells=proposal.column_cells,
                mandatory=spec.mandatory,
                detect_only=spec.detect_only,
                group=proposal.group,
                evidence=proposal.evidence,
            )
        )
    return sorted(fixes, key=lambda fix: fix.sort_key)


def issue_from_fix(fix: Fix, original: RawTable) -> Issue:
    """The ``findings.jsonl`` line for a planned fix (DATA_MODEL §3.2)."""
    value: str | None = None
    if fix.kind is AuditKind.CELL_CHANGE and fix.row is not None and fix.col is not None:
        value = original.cell(fix.row, fix.col)
        if value is None:
            value = fix.before
    return Issue(
        klass=fix.klass,
        rule=fix.rule,
        tier=fix.tier,
        row=fix.row,
        col=fix.col,
        col_name=fix.col_name,
        value=value,
        item_id=fix.item_id,
        evidence=fix.evidence,
    )


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda issue: issue.sort_key)


# --------------------------------------------------------------------------
# Review items (FR-11, DATA_MODEL §2.6)
# --------------------------------------------------------------------------


def _group_key(fix: Fix) -> str:
    return fix.group or f"{fix.col}:{fix.rule}"


def _describe(rule: str, col_name: str, cells: int, sample: Fix) -> str:
    evidence = sample.evidence
    if rule == "fix.date_canon_ambiguous":
        candidates = ", ".join(evidence.get("candidates", []))
        return (
            f"{col_name}: {cells} cells match both {candidates}; no cell proves the day/month order"
        )
    if rule == "fix.number_canon_ambiguous":
        return (
            f"{col_name}: {cells} cells parse under both dot- and comma-decimal "
            f"conventions; propose {evidence.get('majority', 'DOT')}"
        )
    if rule in {"fix.label_merge_fingerprint", "fix.label_merge_nn"}:
        return f"{col_name}: merge label {sample.before!r} → {sample.after!r} ({cells} cells)"
    if rule == "fix.sentinel_null_soft":
        return (
            f"{col_name}: treat {evidence.get('token', sample.before)!r} as missing "
            f"({cells} cells) — it may be a real category"
        )
    if rule == "fix.excel_serial":
        return f"{col_name}: read {cells} integers as Excel 1900 date serials"
    if rule == "fix.two_digit_year":
        return f"{col_name}: expand two-digit years in {cells} cells"
    if rule == "fix.currency_mixed":
        symbols = ", ".join(sorted(evidence.get("symbols", {})))
        return f"{col_name}: mixed currency symbols ({symbols}) across {cells} cells"
    if rule == "fix.overflow_column":
        return f"{evidence.get('n_rows', cells)} rows had surplus values → _overflow column"
    if rule == "fix.collapse_spaces":
        return f"{col_name}: collapse internal whitespace runs in {cells} cells"
    return f"{col_name}: {rule} ({cells} cells)"


def _proposal_payload(rule: str, fixes: list[Fix]) -> dict[str, Any]:
    sample = fixes[0]
    cells: list[dict[str, Any]] = []
    for fix in fixes:
        cell: dict[str, Any] = {
            "row": fix.row,
            "col": fix.col,
            "before": fix.before,
            "after": fix.after,
        }
        if rule == "fix.number_canon_ambiguous":
            cell["alternatives"] = fix.evidence.get("readings", {})
        cells.append(cell)
    payload: dict[str, Any] = {"cells": cells}
    evidence = sample.evidence
    if rule == "fix.date_canon_ambiguous":
        payload["candidates"] = [
            {"label": "day-first" if spec.startswith("%d") else "month-first", "format": spec}
            for spec in evidence.get("candidates", [])
        ]
        payload["formats"] = list(evidence.get("candidates", []))
        payload["recommended"] = evidence.get("recommended")
    elif rule == "fix.number_canon_ambiguous":
        payload["candidates"] = [
            {"label": "dot-decimal", "convention": "DOT"},
            {"label": "comma-decimal", "convention": "COMMA"},
        ]
        payload["readings"] = ["COMMA", "DOT"]
        payload["recommended"] = evidence.get("majority")
    elif rule in {"fix.label_merge_fingerprint", "fix.label_merge_nn"}:
        payload["cluster"] = evidence.get("cluster", [sample.before, sample.after])
        payload["canonical"] = sample.after
    elif rule == "fix.excel_serial":
        payload["interpretation"] = evidence.get("interpretation")
        payload["epoch"] = evidence.get("epoch")
    elif rule == "fix.currency_mixed":
        payload["symbols"] = evidence.get("symbols", {})
    return payload


def build_review_items(
    fixes: list[Fix],
    *,
    content_sha256: str,
    policy_hash: str,
    engine_version: str,
) -> tuple[list[ReviewItem], list[Fix]]:
    """Group review-tier fixes into decision units and stamp their item ids.

    Returns the items plus the full fix list with ``item_id`` populated, so
    every review cell can be linked from ``findings.jsonl`` (FR-11's invariant:
    the review queue never contains an un-reported finding).
    """
    groups: dict[str, list[Fix]] = defaultdict(list)
    for fix in fixes:
        if fix.tier is Tier.REVIEW and not fix.detect_only:
            groups[_group_key(fix)].append(fix)

    ordered_keys = sorted(
        groups,
        key=lambda key: (
            groups[key][0].rule,
            -1 if groups[key][0].col is None else groups[key][0].col,
            min(-1 if f.row is None else f.row for f in groups[key]),
        ),
    )

    items: list[ReviewItem] = []
    assigned: dict[str, str] = {}
    used_ids: set[str] = set()
    for key in ordered_keys:
        members = sorted(groups[key], key=lambda fix: fix.sort_key)
        sample = members[0]
        rows = [fix.row for fix in members if fix.row is not None]
        first_row = min(rows) if rows else None
        item_id = review_item_id(
            content_sha256=content_sha256,
            policy_hash=policy_hash,
            engine_version=engine_version,
            rule=sample.rule,
            col_index=sample.col,
            first_cell_row=first_row,
        )
        if item_id in used_ids:  # deterministic collision suffix (§2.6)
            suffix = 2
            while f"{item_id}_{suffix}" in used_ids:
                suffix += 1
            item_id = f"{item_id}_{suffix}"
        used_ids.add(item_id)
        assigned[key] = item_id
        payload = _proposal_payload(sample.rule, members)
        items.append(
            ReviewItem(
                id=item_id,
                rule=sample.rule,
                col_index=sample.col,
                description=_describe(
                    sample.rule, sample.col_name or f"col_{sample.col}", len(members), sample
                ),
                proposal=payload,
                affected_cells=len(payload["cells"]),
                confidence=min(fix.confidence for fix in members),
            )
        )

    stamped = [
        fix.model_copy(update={"item_id": assigned[_group_key(fix)]})
        if fix.tier is Tier.REVIEW and not fix.detect_only and _group_key(fix) in assigned
        else fix
        for fix in fixes
    ]
    return items, stamped

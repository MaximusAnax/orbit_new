"""The fixed cleaning pipeline (SCOPE.md D13) — the engine's entry point.

Order is part of the engine contract and of determinism (FR-16)::

    ENC → STR → WS → MISS → TYPE → DATE → CAT → DUP → OUT

ENC first (later stages must see repaired text); STR second (the table shape
must be rectangular before columns mean anything); WS and MISS before TYPE and
DATE (trimmed cells parse, sentinels do not pollute type votes) — which is why
inference re-runs on the working table after every stage's auto fixes; CAT
before DUP (label consolidation exposes duplicates that differ only by variant
spelling); OUT last, on final canonical numerics.
"""

from __future__ import annotations

from collections import Counter

from . import detectors
from .inference import profile_table
from .models import (
    AuditEntry,
    CleanResult,
    Fix,
    FixPlan,
    Issue,
    IssueClass,
    Policy,
    Proposal,
    RawTable,
    Stage,
    TableProfile,
    Tier,
)
from .planner import build_review_items, issue_from_fix, plan_fixes, sort_issues
from .table import structural_proposals
from .transforms import apply_plan, row_map_for


def _stage_proposals(
    stage: Stage,
    table: RawTable,
    profile: TableProfile,
    policy: Policy,
    *,
    covered: set[tuple[int, int]],
    row_map: list[int],
) -> list[Proposal]:
    if stage is Stage.ENC:
        return detectors.detect_enc(table, profile, policy)
    if stage is Stage.STR:
        return structural_proposals(table, policy)
    if stage is Stage.WS:
        return detectors.detect_ws(table, profile, policy)
    if stage is Stage.MISS:
        return detectors.detect_miss(table, profile, policy)
    if stage is Stage.TYPE:
        return detectors.detect_type(table, profile, policy)
    if stage is Stage.DATE:
        return detectors.detect_date(table, profile, policy)
    if stage is Stage.CAT:
        return detectors.detect_cat(table, profile, policy)
    if stage is Stage.DUP:
        return detectors.detect_dup(table, profile, policy)
    # OUT: robust outlier flagging plus the leftover coercion failures.
    out = detectors.detect_out(table, profile, policy, exclude=covered, row_map=row_map)
    coercion = detectors.detect_coercion_failures(
        table, profile, policy, covered=covered, row_map=row_map
    )
    return out + coercion


def _to_original_rows(proposals: list[Proposal], row_map: list[int]) -> list[Proposal]:
    """Translate working-table row indices back to parsed-original ones."""
    if row_map == list(range(len(row_map))):
        return proposals
    translated: list[Proposal] = []
    for proposal in proposals:
        if proposal.row is None:
            translated.append(proposal)
            continue
        translated.append(proposal.model_copy(update={"row": row_map[proposal.row]}))
    return translated


def clean_table(
    original: RawTable,
    policy: Policy,
    *,
    content_sha256: str,
    engine_version: str,
    policy_hash: str | None = None,
    reader_issues: list[Issue] | None = None,
) -> CleanResult:
    """Profile, detect, plan, apply — the whole engine, deterministically.

    ``content_sha256`` and ``policy_hash`` only feed the content-derived review
    item ids (DATA_MODEL §2.6); no id, path or timestamp reaches an artifact.
    """
    policy_hash = policy_hash or policy.policy_hash()
    fixes: list[Fix] = []
    covered: set[tuple[int, int]] = set()

    for stage in Stage:
        working, audit = apply_plan(original, fixes)
        row_map = row_map_for(original.n_rows, audit)
        profile = profile_table(working, policy, with_stats=False)
        proposals = _stage_proposals(
            stage, working, profile, policy, covered=covered, row_map=row_map
        )
        staged = plan_fixes(_to_original_rows(proposals, row_map), policy, working.headers)
        fixes.extend(staged)
        for fix in staged:
            if fix.row is not None and fix.col is not None:
                covered.add((fix.row, fix.col))

    fixes = sorted(fixes, key=lambda fix: fix.sort_key)
    review_items, fixes = build_review_items(
        fixes,
        content_sha256=content_sha256,
        policy_hash=policy_hash,
        engine_version=engine_version,
    )
    cleaned, audit = apply_plan(original, fixes)

    issues = [issue_from_fix(fix, original) for fix in fixes]
    if reader_issues:
        issues.extend(reader_issues)
    issues = sort_issues(issues)

    profiles = profile_table(cleaned, policy, original_headers=list(original.headers)).columns

    plan = FixPlan(fixes=fixes, issues=issues, disabled_rules=policy.disabled_rules())
    issue_counts = Counter(issue.klass.value for issue in issues)
    change_counts = {
        "auto": sum(1 for entry in audit if entry.tier is Tier.AUTO),
        "review": sum(1 for fix in plan.by_tier(Tier.REVIEW) if not fix.detect_only),
        "report": sum(1 for issue in issues if issue.tier is Tier.REPORT),
    }
    return CleanResult(
        original=original,
        cleaned=cleaned,
        profiles=profiles,
        issues=issues,
        audit=audit,
        review_items=review_items,
        plan=plan,
        issue_counts={
            klass.value: issue_counts.get(klass.value, 0)
            for klass in IssueClass
            if issue_counts.get(klass.value, 0)
        },
        change_counts=change_counts,
    )


def apply_revision(
    result: CleanResult, accepted_item_ids: list[str]
) -> tuple[RawTable, list[AuditEntry]]:
    """Recompute a revision from the parsed original (FR-11).

    Every revision is a *complete* artifact — the auto plan plus **all**
    accepted items applied to the parsed original — never a delta on r-1, so
    ``revert(cleaned.r<N>, audit.r<N>) == parsed original`` holds for every N.
    """
    return apply_plan(result.original, result.plan, accepted=frozenset(accepted_item_ids))

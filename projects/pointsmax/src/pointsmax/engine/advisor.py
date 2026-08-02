"""Orchestration: ``(world, wallet, goal, today, params) -> PlanSet`` (FR-9, FR-16).

Pure and deterministic — no clock, no network, no filesystem.  The same inputs
always produce byte-identical output, which is what lets a saved plan pin the
world it was computed against and be recomputed later.
"""

from __future__ import annotations

from datetime import date

from ..models import (
    DISCLAIMER,
    GoalKind,
    GoalSpec,
    Plan,
    PlanParams,
    PlanSet,
    Verdict,
    Wallet,
    World,
    canonical_json,
)
from .plan import PlanDraft, build_plan, canonical_form_of, choose_verdict, draft_sort_key
from .search import (
    FundingSearch,
    SearchStats,
    cash_drafts,
    cash_goal_has_options,
    enumerate_booking_sets,
    solve_booking_set,
)
from .world import ActiveWorld, active_subgraph


def _drafts_for_goal(
    world: World,
    active: ActiveWorld,
    wallet: Wallet,
    goal: GoalSpec,
    today: date,
    params: PlanParams,
    stats: SearchStats,
    *,
    pruning: bool,
) -> tuple[list[PlanDraft], int]:
    """Solved drafts plus the number of candidate booking sets considered."""
    opening = {p: n for p, n in wallet.balances.items() if n}
    if goal.kind is GoalKind.CASH:
        drafts = cash_drafts(world, active, opening, goal, params, stats)
        candidate_sets = 1 if cash_goal_has_options(active, opening, goal) else 0
        return drafts, candidate_sets

    booking_sets = enumerate_booking_sets(goal, world, active, today)
    search = FundingSearch(world, active, opening, today, params, pruning=pruning, stats=stats)
    drafts = []
    for bookings in booking_sets:
        draft = solve_booking_set(search, bookings)
        if draft is not None:
            drafts.append(draft)
    return drafts, len(booking_sets)


def rank_drafts(world: World, goal: GoalSpec, drafts: list[PlanDraft]) -> list[PlanDraft]:
    """FR-9 total order, with duplicate canonical forms collapsed."""
    decorated = []
    for draft in drafts:
        _steps, canonical = canonical_form_of(world, draft)
        decorated.append((draft_sort_key(draft, goal.kind, canonical), canonical, draft))
    decorated.sort(key=lambda item: item[0])
    seen: set[str] = set()
    ordered: list[PlanDraft] = []
    for _key, canonical, draft in decorated:
        fingerprint = canonical_json(canonical)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        ordered.append(draft)
    return ordered


def select_output(
    ordered: list[PlanDraft], goal: GoalSpec, top_k: int
) -> list[tuple[PlanDraft, bool]]:
    """Top-K plus the best portal comparator when it falls below the cutoff (FR-9)."""
    chosen: list[tuple[PlanDraft, bool]] = [(d, False) for d in ordered[:top_k]]
    if goal.kind is GoalKind.CASH:
        return chosen
    included = {id(d) for d, _ in chosen}
    for draft in ordered:
        if draft.is_portal_only():
            if id(draft) not in included:
                chosen.append((draft, True))
            break
    return chosen


def compute_plan_set(
    *,
    world: World,
    wallet: Wallet,
    goal: GoalSpec,
    today: date,
    params: PlanParams | None = None,
    pruning: bool = True,
    goal_id: int | None = None,
    created_at: str = "",
) -> PlanSet:
    """Compute the full ranked plan set for a goal (FR-7 through FR-10, FR-16)."""
    params = params or PlanParams()
    active = active_subgraph(world, wallet.cards, today)
    stats = SearchStats()

    drafts, candidate_sets = _drafts_for_goal(
        world, active, wallet, goal, today, params, stats, pruning=pruning
    )
    ordered = rank_drafts(world, goal, drafts)
    verdict = choose_verdict(goal.kind, candidate_sets=candidate_sets, feasible_drafts=ordered)

    plans: list[Plan] = []
    if verdict not in (Verdict.INSUFFICIENT_POINTS, Verdict.NO_MATCHING_AWARD):
        for rank, (draft, is_comparator) in enumerate(
            select_output(ordered, goal, params.top_k), start=1
        ):
            plans.append(
                build_plan(
                    world,
                    goal,
                    draft,
                    today,
                    params,
                    rank=rank,
                    is_comparator=is_comparator,
                )
            )

    return PlanSet(
        goal_id=goal_id,
        world_version=world.version.version,
        world_hash=world.version.content_hash,
        today=today,
        params=params,
        expansions=stats.expansions,
        verdict=verdict,
        disclaimer=DISCLAIMER,
        created_at=created_at,
        plans=plans,
    )


def plan_set_fingerprint(plan_set: PlanSet) -> str:
    """Canonical JSON of the parts of a PlanSet the engine determines (D0).

    Excludes database identity and ``created_at`` so two recomputations of the
    same (world, wallet, goal, today, params) compare byte-identically.
    """
    payload = {
        "world_version": plan_set.world_version,
        "world_hash": plan_set.world_hash,
        "today": plan_set.today.isoformat(),
        "params": plan_set.params.as_dict(),
        "verdict": str(plan_set.verdict),
        "disclaimer": plan_set.disclaimer,
        "expansions": plan_set.expansions,
        "plans": [
            {
                "rank": plan.rank,
                "is_comparator": plan.is_comparator,
                "gross_value_cents": plan.gross_value_cents,
                "cash_outlay_cents": plan.cash_outlay_cents,
                "points_cost_cents": plan.points_cost_cents,
                "net_value_cents": plan.net_value_cents,
                "cash_received_cents": plan.cash_received_cents,
                "realized_cpp_milli": plan.realized_cpp_milli,
                "points_spent": plan.points_spent,
                "feasible_in_days": plan.feasible_in_days,
                "signature": plan.signature,
                "caveats": [
                    {"code": str(c.code), "params": c.params, "text": c.text} for c in plan.caveats
                ],
                "steps": [
                    {
                        "seq": step.seq,
                        "kind": str(step.kind),
                        "hop_index": step.hop_index,
                        "from_program": step.from_program,
                        "to_program": step.to_program,
                        "edge_id": step.edge_id,
                        "offer_id": step.offer_id,
                        "cashout_id": step.cashout_id,
                        "points_sent": step.points_sent,
                        "points_delivered": step.points_delivered,
                        "fees_cents": step.fees_cents,
                        "eta_days": step.eta_days,
                        "irreversible": step.irreversible,
                        "explanation": step.explanation,
                    }
                    for step in plan.steps
                ],
            }
            for plan in plan_set.plans
        ],
    }
    return canonical_json(payload)


__all__ = [
    "compute_plan_set",
    "plan_set_fingerprint",
    "rank_drafts",
    "select_output",
]

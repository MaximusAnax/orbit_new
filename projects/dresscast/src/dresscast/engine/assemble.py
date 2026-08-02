"""Outfit assembly under constraints — SCOPE.md FR-8, the hard part.

Filter → bounded enumeration → scalarized scoring → MMR diversity (D12), with
FR-14's relaxation ladder behind it.  The whole path is deterministic: no RNG
is constructed, ``seed`` is accepted and persisted but has no effect, ties
break on (rounded score desc, sorted garment-id tuple asc), and every score is
quantized to ``SCORE_DP`` *before* ranking so a last-ULP difference in
``v**0.16`` cannot reorder outfits (FR-19).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from dresscast.engine import explain
from dresscast.engine.comfort import (
    DayContext,
    TargetTable,
    ThermalEval,
    achievable_band,
    build_day_context,
    day_brief,
    hour_score,
    hourly_plan,
    layer_configs,
    select_plan,
    target_table,
)
from dresscast.engine.models import (
    ACCESSORY_SLOTS,
    CANDIDATE_CAP,
    COVER_ROLES,
    ENGINE_VERSION,
    ICL_INTERCEPT,
    ICL_SLOPE,
    MAX_MIDS,
    MMR_JACCARD_MAX,
    MMR_POOL_SIZE,
    THERMAL_MEAN_WEIGHT,
    THERMAL_WORST_WEIGHT,
    WARDROBE_MAX,
    Band,
    Compromise,
    CoreOutfit,
    DayForecast,
    Garment,
    LayerConfig,
    Note,
    OutfitItem,
    Recommendation,
    RequestParams,
    ScoreBreakdown,
    ScoredOutfit,
    SlotCandidates,
    WearHistory,
    q_score,
    wardrobe_hash,
)
from dresscast.engine.palette import color_score
from dresscast.engine.protection import (
    AccessoryPlan,
    ProtectionEval,
    attach_accessories,
    protect_score,
    select_umbrella,
)
from dresscast.engine.style import style_score
from dresscast.engine.variety import repeats_yesterday, variety_score
from dresscast.errors import InfeasibleWardrobe, WardrobeTooLarge

#: Fractions of the day's required insulation each slot is expected to carry.
#: Used only to rank candidates when a slot has more than ``CANDIDATE_CAP``
#: members (D12); derived from ``bare_feels_c`` so it is configuration
#: independent and computable before any outfit exists.
SLOT_SHARES: dict[str, float] = {
    "base": 0.15,
    "full_body": 0.35,
    "bottom": 0.20,
    "footwear": 0.06,
    "mid": 0.22,
    "outer": 0.30,
    "leg_base": 0.10,
}


@dataclass(frozen=True, slots=True)
class Relaxation:
    """One rung of FR-14's ladder, expressed as the constraints it loosens."""

    drop_hc8: bool = False
    formality_spread: int = 1
    lower_moderate_cover: bool = False
    applied: tuple[Compromise, ...] = ()


_R1 = Compromise(
    rule="HC-8",
    detail="no HC-valid outfit existed; yesterday's core-item set re-admitted",
)
_R2 = Compromise(
    rule="HC-4",
    detail="no HC-valid outfit existed; formality spread widened from 1 to 2",
)
_R3 = Compromise(
    rule="HC-6",
    detail=(
        "no HC-valid outfit existed; moderate-intensity rain hours accept "
        "one-level-lower cover (heavy rain is never relaxed)"
    ),
)

#: The ladder, as lexicographic prefixes (FR-14, EVALS.md M10(b)).
LADDER: tuple[Relaxation, ...] = (
    Relaxation(),
    Relaxation(drop_hc8=True, applied=(_R1,)),
    Relaxation(drop_hc8=True, formality_spread=2, applied=(_R1, _R2)),
    Relaxation(
        drop_hc8=True,
        formality_spread=2,
        lower_moderate_cover=True,
        applied=(_R1, _R2, _R3),
    ),
)


@dataclass(slots=True)
class ScoredCandidate:
    """A complete, HC-valid outfit with its five components and its plan."""

    core: CoreOutfit
    configs: tuple[LayerConfig, ...]
    thermal: ThermalEval
    protection: ProtectionEval
    umbrella: Garment | None
    s_thermal: float
    s_protection: float
    s_color: float
    s_style: float
    s_variety: float
    total: float

    def sort_key(self) -> tuple[float, tuple[str, ...]]:
        return (-self.total, self.core.id_tuple())


# --------------------------------------------------------------------------
# Candidate filtering (HC-2, HC-3, HC-7 and D12's cap)
# --------------------------------------------------------------------------


def _slot_target(role: str, ctx: DayContext) -> float:
    mid_required = (ctx.required_min + ctx.required_max) / 2.0
    clo_sum = max(0.0, (mid_required - ICL_INTERCEPT) / ICL_SLOPE)
    return SLOT_SHARES[role] * clo_sum


def _cap(items: list[Garment], role: str, ctx: DayContext) -> tuple[Garment, ...]:
    ordered = sorted(items, key=lambda g: g.id)
    if len(ordered) <= CANDIDATE_CAP:
        return tuple(ordered)
    target = _slot_target(role, ctx)
    kept = sorted(ordered, key=lambda g: (abs(g.clo - target), g.id))[:CANDIDATE_CAP]
    return tuple(sorted(kept, key=lambda g: g.id))


def build_candidates(
    wardrobe: Sequence[Garment], params: RequestParams, ctx: DayContext
) -> SlotCandidates:
    """Per-slot candidate lists: clean (HC-2), occasion-listing (HC-3), capped."""
    by_role: dict[str, list[Garment]] = {
        r: [] for r in ("base", "mid", "outer", "bottom", "leg_base", "full_body", "footwear")
    }
    accessories: list[Garment] = []
    for g in wardrobe:
        if g.status == "retired":
            continue
        if g.layer_role == "accessory":
            if g.status == "clean":
                accessories.append(g)
            continue
        if g.status != "clean" or params.occasion not in g.occasions:
            continue
        by_role[g.layer_role].append(g)
    leg_base = by_role["leg_base"] if ctx.leg_base_allowed else []
    return SlotCandidates(
        base=_cap(by_role["base"], "base", ctx),
        full_body=_cap(by_role["full_body"], "full_body", ctx),
        bottom=_cap(by_role["bottom"], "bottom", ctx),
        mid=_cap(by_role["mid"], "mid", ctx),
        outer=_cap(by_role["outer"], "outer", ctx),
        leg_base=_cap(leg_base, "leg_base", ctx),
        footwear=_cap(by_role["footwear"], "footwear", ctx),
        accessories=tuple(sorted(accessories, key=lambda g: g.id)),
    )


# --------------------------------------------------------------------------
# Bounded enumeration
# --------------------------------------------------------------------------


def _relaxed_context(ctx: DayContext, relax: Relaxation) -> DayContext:
    """Apply R3: moderate-intensity rain hours accept one-level-lower cover."""
    if not relax.lower_moderate_cover:
        return ctx
    hours = tuple(
        replace(h, needed_cover=max(1, h.needed_cover - 1))
        if (h.hard_rain and h.intensity == "moderate")
        else h
        for h in ctx.hours
    )
    return replace(ctx, hours=hours)


def _thermal_bound(ctx: DayContext, table: TargetTable, lo: float, hi: float) -> float:
    """An admissible upper bound on ``S_thermal`` for any completion.

    Every configuration of any completion of this skeleton has ``Icl`` in
    ``[lo, hi]`` and some windproofness class, so taking, per hour, the best
    score reachable over that interval and over all three classes can only
    over-estimate — which is what makes pruning against it safe.
    """
    per_hour: list[float] = []
    weighted = 0.0
    for hc in ctx.hours:
        best = 0.0
        for target in table.targets[hc.index]:
            reachable = min(hi, max(lo, target))
            score = hour_score(reachable - target)
            if score > best:
                best = score
        per_hour.append(best)
        weighted += best * hc.weight
    return THERMAL_MEAN_WEIGHT * (weighted / ctx.weight_sum) + THERMAL_WORST_WEIGHT * min(per_hour)


def _mid_subsets(mids: Sequence[Garment]) -> list[tuple[Garment, ...]]:
    """0-2 mids, each subset ordered ascending by ``(clo, id)`` (DATA_MODEL §2.6)."""
    ordered = sorted(mids, key=lambda g: (g.clo, g.id))
    subsets: list[tuple[Garment, ...]] = [()]
    subsets.extend((m,) for m in ordered)
    if MAX_MIDS >= 2:
        subsets.extend((a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :])
    return subsets


def _max_cover(garments: Sequence[Garment]) -> int:
    return max(
        (g.waterproofness for g in garments if g.layer_role in COVER_ROLES),
        default=0,
    )


def _hc6_satisfiable(garments: Sequence[Garment], ctx: DayContext, umbrella: bool) -> bool:
    """HC-6: does *some* configuration cover every hard-rain hour?

    Cover is monotone in worn layers, so the fullest configuration maximises
    it; checking that one is exactly equivalent to checking all of them.
    """
    cover = _max_cover(garments)
    for hc in ctx.hours:
        if hc.needed_cover == 0:
            continue
        if cover >= hc.needed_cover:
            continue
        if hc.umbrella_ok and umbrella:
            continue
        return False
    return True


@dataclass(slots=True)
class SearchResult:
    scored: list[ScoredCandidate]
    feasible_count: int


def search(
    ctx: DayContext,
    candidates: SlotCandidates,
    band: Band,
    history: WearHistory,
    params: RequestParams,
    relax: Relaxation,
) -> SearchResult:
    """Enumerate HC-valid outfits, score them, and keep the best pool."""
    weights = params.weights
    spread = relax.formality_spread
    has_rain = any(h.rain_any for h in ctx.hours)
    table = target_table(ctx, band)
    w_thermal = weights["thermal"]
    w_protect = weights["protection"]
    w_color = weights["color"]
    w_style = weights["style"]
    w_variety = weights["variety"]

    body_options: list[tuple[Garment, Garment | None]] = [
        (top, bottom) for top in candidates.base for bottom in candidates.bottom
    ]
    body_options.extend((dress, None) for dress in candidates.full_body)
    mid_subsets = _mid_subsets(candidates.mid)
    outer_options: list[Garment | None] = [None, *candidates.outer]
    leg_options: list[Garment | None] = [None, *candidates.leg_base]

    max_mid_sum = sum(sorted((g.clo for g in candidates.mid), reverse=True)[:MAX_MIDS])
    max_outer = max((g.clo for g in candidates.outer), default=0.0)

    scored: list[ScoredCandidate] = []
    top_scores: list[float] = []  # ascending; the pool's worst is top_scores[0]
    feasible = 0
    # Two outfits whose configuration chains agree on (Icl, windproofness,
    # cover, layer count) and on the relative order of their garment-id tuples
    # produce the same plan and the same thermal/protection numbers — every
    # input FR-7's selection and FR-9's scoring read is in that profile.  A
    # personal wardrobe repeats those profiles heavily (~70% on the fixture
    # wardrobe), so memoising them is what keeps the search inside EVALS.md
    # §6's runtime budget.
    plan_cache: dict[object, tuple[ThermalEval, ProtectionEval]] = {}

    for top, bottom in body_options:
        f_lo0 = min(top.formality, bottom.formality) if bottom else top.formality
        f_hi0 = max(top.formality, bottom.formality) if bottom else top.formality
        if f_hi0 - f_lo0 > spread:
            continue
        clo0 = top.clo + (bottom.clo if bottom else 0.0)
        for foot in candidates.footwear:
            f_lo1 = min(f_lo0, foot.formality)
            f_hi1 = max(f_hi0, foot.formality)
            if f_hi1 - f_lo1 > spread:
                continue
            clo1 = clo0 + foot.clo
            for legb in leg_options:
                if legb is not None:
                    f_lo2 = min(f_lo1, legb.formality)
                    f_hi2 = max(f_hi1, legb.formality)
                    if f_hi2 - f_lo2 > spread:
                        continue
                    clo2 = clo1 + legb.clo
                else:
                    f_lo2, f_hi2, clo2 = f_lo1, f_hi1, clo1

                lo_icl = ICL_SLOPE * clo2 + ICL_INTERCEPT
                hi_icl = ICL_SLOPE * (clo2 + max_mid_sum + max_outer) + ICL_INTERCEPT
                thermal_cap = _thermal_bound(ctx, table, lo_icl, hi_icl)
                if (
                    len(top_scores) >= MMR_POOL_SIZE
                    and (w_thermal * thermal_cap + w_protect + w_color + w_style + w_variety)
                    <= top_scores[0]
                ):
                    continue

                for mids in mid_subsets:
                    f_lo3, f_hi3 = f_lo2, f_hi2
                    for m in mids:
                        f_lo3 = min(f_lo3, m.formality)
                        f_hi3 = max(f_hi3, m.formality)
                    if f_hi3 - f_lo3 > spread:
                        continue
                    for outer in outer_options:
                        if outer is not None and (
                            max(f_hi3, outer.formality) - min(f_lo3, outer.formality) > spread
                        ):
                            continue

                        core = CoreOutfit(
                            base=top,
                            bottom=bottom,
                            mids=mids,
                            outer=outer,
                            leg_base=legb,
                            footwear=foot,
                        )
                        garments = core.garments()
                        core_ids = frozenset(g.id for g in garments)
                        if not relax.drop_hc8 and repeats_yesterday(core_ids, history):
                            continue
                        umbrella = (
                            select_umbrella(candidates.accessories, core, params.occasion)
                            if has_rain
                            else None
                        )
                        if has_rain and not _hc6_satisfiable(garments, ctx, umbrella is not None):
                            continue
                        feasible += 1

                        s_color = q_score(color_score(garments))
                        s_style = q_score(style_score(garments))
                        s_variety = q_score(variety_score(core_ids, history, params.date))
                        if (
                            len(top_scores) >= MMR_POOL_SIZE
                            and (
                                w_thermal * thermal_cap
                                + w_protect
                                + w_color * s_color
                                + w_style * s_style
                                + w_variety * s_variety
                            )
                            <= top_scores[0]
                        ):
                            continue

                        configs = layer_configs(core)
                        cache_key = (
                            umbrella is not None,
                            tuple(
                                (round(c.icl, 12), c.windproofness, c.cover, c.layer_count)
                                for c in configs
                            ),
                            tuple(
                                sorted(
                                    range(len(configs)),
                                    key=lambda i: configs[i].garment_ids,
                                )
                            ),
                        )
                        cached = plan_cache.get(cache_key)
                        if cached is None:
                            thermal = select_plan(configs, ctx, band, umbrella is not None, table)
                            if thermal is None:  # pragma: no cover - HC-6 pre-checked
                                continue
                            protection = protect_score(
                                configs, thermal.chosen, ctx, umbrella is not None
                            )
                            plan_cache[cache_key] = (thermal, protection)
                        else:
                            thermal, protection = cached
                        s_thermal = q_score(thermal.s_thermal)
                        s_protect = q_score(protection.score)
                        total = q_score(
                            w_thermal * s_thermal
                            + w_protect * s_protect
                            + w_color * s_color
                            + w_style * s_style
                            + w_variety * s_variety
                        )
                        scored.append(
                            ScoredCandidate(
                                core=core,
                                configs=configs,
                                thermal=thermal,
                                protection=protection,
                                umbrella=umbrella,
                                s_thermal=s_thermal,
                                s_protection=s_protect,
                                s_color=s_color,
                                s_style=s_style,
                                s_variety=s_variety,
                                total=total,
                            )
                        )
                        bisect.insort(top_scores, total)
                        if len(top_scores) > MMR_POOL_SIZE:
                            top_scores.pop(0)

    scored.sort(key=lambda c: c.sort_key())
    return SearchResult(scored=scored, feasible_count=feasible)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def select_diverse(scored: Sequence[ScoredCandidate], k: int) -> list[ScoredCandidate]:
    """Greedy MMR-style selection with FR-8's pairwise Jaccard ≤ 0.5 cap."""
    selected: list[ScoredCandidate] = []
    for cand in scored:
        if len(selected) >= k:
            break
        ids = cand.core.core_ids()
        if all(jaccard(ids, s.core.core_ids()) <= MMR_JACCARD_MAX for s in selected):
            selected.append(cand)
    return selected


# --------------------------------------------------------------------------
# FR-14 — diagnosis when the ladder is exhausted
# --------------------------------------------------------------------------


def _clock_range(hours: Sequence[int]) -> str:
    return f"{min(hours):02d}:00-{max(hours):02d}:00"


def diagnose(ctx: DayContext, candidates: SlotCandidates, params: RequestParams) -> str:
    """Name the missing capability behind an ``infeasible_wardrobe`` (FR-14)."""
    occ = params.occasion
    if not candidates.footwear:
        return f"no clean footwear listing the {occ!r} occasion"
    if not candidates.full_body and not (candidates.base and candidates.bottom):
        return f"no clean base top plus bottom (or full-body dress) listing the {occ!r} occasion"
    pool = candidates.core_pool()
    for need in (3, 2, 1):
        hours = [
            hc.weather.hour
            for hc in ctx.hours
            if hc.needed_cover == need and not (hc.umbrella_ok and candidates.accessories)
        ]
        if not hours:
            continue
        if not any(g.waterproofness >= need for g in pool):
            classes = {hc.intensity for hc in ctx.hours if hc.needed_cover == need}
            label = "/".join(sorted(classes))
            return (
                f"no waterproofness ≥ {need} garment for the {len(hours)} "
                f"{label}-rain hours {_clock_range(hours)}"
            )
    anchors = [
        f
        for f in range(1, 6)
        if any(g.formality in (f, f + 1) for g in candidates.footwear)
        and (
            any(g.formality in (f, f + 1) for g in candidates.full_body)
            or (
                any(g.formality in (f, f + 1) for g in candidates.base)
                and any(g.formality in (f, f + 1) for g in candidates.bottom)
            )
        )
    ]
    if not anchors:
        return f"no clean {occ!r} garments sit within one formality step of each other (HC-4)"
    return f"no outfit in this wardrobe satisfies the day's constraints for {occ!r}"


# --------------------------------------------------------------------------
# The public entry point
# --------------------------------------------------------------------------


def recommend(
    wardrobe: Sequence[Garment],
    forecast: DayForecast,
    history: WearHistory,
    params: RequestParams,
    *,
    now: datetime,
    engine_version: str = ENGINE_VERSION,
) -> Recommendation:
    """FR-8: the top-k complete outfits for this day, wardrobe and history."""
    if len(wardrobe) > WARDROBE_MAX:
        raise WardrobeTooLarge(
            f"wardrobe holds {len(wardrobe)} garments; the limit is {WARDROBE_MAX}",
            count=len(wardrobe),
            limit=WARDROBE_MAX,
        )
    base_ctx = build_day_context(forecast, params)
    candidates = build_candidates(wardrobe, params, base_ctx)

    chosen: list[ScoredCandidate] = []
    used: Relaxation = LADDER[0]
    result = SearchResult(scored=[], feasible_count=0)
    ctx = base_ctx
    band = achievable_band(base_ctx, candidates, occasion=params.occasion)
    for relax in LADDER:
        ctx = _relaxed_context(base_ctx, relax)
        band = achievable_band(
            ctx,
            candidates,
            formality_spread=relax.formality_spread,
            occasion=params.occasion,
        )
        result = search(ctx, candidates, band, history, params, relax)
        if result.scored:
            used = relax
            chosen = select_diverse(result.scored, params.k)
            break
    if not chosen:
        missing = diagnose(base_ctx, candidates, params)
        raise InfeasibleWardrobe(
            missing,
            missing=missing,
            brief=day_brief(forecast, params).model_dump(mode="json"),
        )

    notes: list[Note] = []
    if len(chosen) < params.k:
        notes.append(
            Note(
                kind="partial_k",
                n=len(chosen),
                k=params.k,
                reason=(
                    f"only {len(chosen)} outfits satisfy HC-1…HC-8 with pairwise "
                    f"core-item Jaccard ≤ {MMR_JACCARD_MAX}"
                ),
            )
        )
    compromises = list(used.applied)

    outfits: list[ScoredOutfit] = []
    for rank, cand in enumerate(chosen, start=1):
        outfits.append(
            _build_outfit(
                rank=rank,
                cand=cand,
                ctx=ctx,
                band=band,
                candidates=candidates,
                params=params,
                history=history,
                notes=notes,
                compromises=compromises,
            )
        )
    return Recommendation(
        date=params.date,
        snapshot_id=forecast.id,
        created_at=now,
        engine_version=engine_version,
        seed=params.seed,
        params=params,
        wardrobe_hash=wardrobe_hash(list(wardrobe)),
        outfits=outfits,
        notes=notes,
        compromises=compromises,
    )


def _build_outfit(
    *,
    rank: int,
    cand: ScoredCandidate,
    ctx: DayContext,
    band: Band,
    candidates: SlotCandidates,
    params: RequestParams,
    history: WearHistory,
    notes: Sequence[Note],
    compromises: Sequence[Compromise],
) -> ScoredOutfit:
    accessories: AccessoryPlan = attach_accessories(
        cand.core, candidates.accessories, ctx, cand.protection, params.occasion
    )
    plan = hourly_plan(
        cand.core,
        ctx,
        band,
        cand.configs,
        cand.thermal,
        cand.protection.hour_scores,
        cand.umbrella is not None,
    )
    items = [OutfitItem(slot=slot, garment_id=g.id) for slot, g in cand.core.slot_items()]
    for i, attachment in enumerate(accessories.attachments):
        items.append(OutfitItem(slot=ACCESSORY_SLOTS[i], garment_id=attachment.garment_id))
    by_id = {g.id: g for g in cand.core.garments()}
    for g in candidates.accessories:
        by_id.setdefault(g.id, g)
    outfit_notes = [
        Note(kind="advisory_gap", accessory_class=cls, trigger=trigger)
        for cls, trigger in accessories.gaps
    ]
    reasoning = explain.build_reasoning(
        explain.ExplainInput(
            core=cand.core,
            garments=by_id,
            ctx=ctx,
            configs=cand.configs,
            thermal=cand.thermal,
            protection=cand.protection,
            plan=plan,
            accessories=accessories,
            history=history,
            params=params,
            notes=list(notes),
            compromises=list(compromises),
        )
    )
    return ScoredOutfit(
        rank=rank,
        score_total=cand.total,
        scores=ScoreBreakdown(
            thermal=cand.s_thermal,
            protection=cand.s_protection,
            color=cand.s_color,
            style=cand.s_style,
            variety=cand.s_variety,
            weights=dict(params.weights),
        ),
        items=items,
        hour_plan=plan,
        reasoning=reasoning,
        accessories=list(accessories.attachments),
        notes=outfit_notes,
        compromises=list(compromises),
    )

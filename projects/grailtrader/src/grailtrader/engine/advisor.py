"""FR-8: the advisor — hard part B, second half.

At week ``t``, for each garment with status ``owned``/``watching``:

* **Stratum selection** — ``s`` is the most specific ancestor of the garment's
  leaf path with a published point within ``stale_max_weeks`` of ``t``.
* **Event matching and scope dilution** — active legs (FR-6) are matched against
  the garment's *leaf* path but applied to ``s``'s index, diluted by
  ``lambda_e`` = the event target subtree's share of ``s``'s trailing
  8-week surviving-sale weights.
* **Baseline** ``B`` — ``s``'s index at the last published week strictly before
  the earliest active event's week.
* **Expected level** — ``ln I_hat(t+H) = ln B + sum_e lambda_e m_e(t + H - t_e)``,
  and ``r_hat_H = I_hat(t+H)/O_t - 1`` against the *observed* index ``O_t``:
  sellers reprice slowly, and the gap between modeled path and observed level is
  the trade (SCOPE D-9).
* **Volatility** ``sigma_w`` from gap-normalised consecutive published points, so
  gappy sparse strata are usable and no sigma is ever fabricated.
* **Horizon** ``H*`` = argmax of ``z_H = |ln(1+r_hat_H)| / (sigma_w sqrt(H))``
  (ties to the smallest H), **confidence** ``clamp(z_term q_index c_event)``, and
  the buy/sell/hold decision against ``theta`` and ``conf_min``.

Every threshold and coefficient is config data (``advisor_config.json``), never a
code constant.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise

from ..ids import advice_id
from ..models import (
    Advice,
    AdviceAction,
    AdviceDecision,
    AdvisorSettings,
    Driver,
    FashionEvent,
    Garment,
    GarmentStatus,
    HoldReason,
    IndexPoint,
    ValuationMethod,
    build_inputs_hash,
)
from ..weeks import add_weeks, weeks_between
from .context import EngineContext
from .frame import render_checked
from .impact import ImpactLeg, active_legs, retirement_age
from .index import IndexView, median
from .strata import is_prefix
from .valuation import value_garment

__all__ = [
    "advise_garment",
    "build_advice",
    "q_index_factor",
    "scope_weight",
    "weekly_volatility",
]

#: Trailing window (weeks) used to estimate a stratum's own weekly volatility.
SIGMA_WINDOW_WEEKS = 52


def weekly_volatility(
    points: Sequence[IndexPoint], *, sigma_min_changes: int
) -> tuple[float | None, int]:
    """``sigma_w = 1.4826 * median|Delta_i|`` over gap-normalised changes (FR-8).

    ``Delta_i = (ln I(t_{i+1}) - ln I(t_i)) / sqrt(g_i)`` with ``g_i`` the gap in
    weeks (random-walk scaling). Returns ``(sigma, n_pairs)``; sigma is ``None``
    when fewer than ``sigma_min_changes`` pairs exist — no fabricated sigma, no
    silent fallback.
    """
    ordered = sorted(points, key=lambda p: p.week)
    deltas: list[float] = []
    for before, after in pairwise(ordered):
        gap = weeks_between(before.week, after.week)
        if gap <= 0:
            continue
        deltas.append((math.log(after.index_value) - math.log(before.index_value)) / math.sqrt(gap))
    if len(deltas) < sigma_min_changes:
        return None, len(deltas)
    return 1.4826 * median([abs(d) for d in deltas]), len(deltas)


def q_index_factor(staleness_weeks: int, settings: AdvisorSettings) -> float:
    """Index-quality damping from the stratum's staleness (FR-8)."""
    for step in settings.q_index:
        if staleness_weeks <= step.max_stale_weeks:
            return step.factor
    return settings.q_index[-1].factor


def scope_weight(index: IndexView, *, stratum: str, target: str, week: str) -> float:
    """``lambda_e`` — the event target subtree's share of ``stratum``'s sale weights.

    ``lambda_e = 1`` when ``stratum`` is at or below the event's target. When
    ``stratum`` is a proper ancestor of the target, the event moves only part of
    it, so its contribution is diluted by the target subtree's share of the
    trailing-window surviving-sale weights — the same weights FR-4 chains parents
    with. Where no sales exist in the window at all, the fallback is an
    equal-weight leaf count (and 1.0 when even that is unknown), so an unknown
    weight never silently zeroes a real event.
    """
    if is_prefix(target, stratum):
        return 1.0
    if not is_prefix(stratum, target):
        return 0.0
    total = index.subtree_weight(stratum, week)
    inside = index.subtree_weight(target, week)
    if total > 0:
        return inside / total
    leaves_total = len(index.observed_leaves(stratum))
    leaves_inside = len(index.observed_leaves(target))
    if leaves_total == 0:
        return 1.0
    return leaves_inside / leaves_total


def _source_factor(leg: ImpactLeg, settings: AdvisorSettings) -> float:
    """``f_e`` — source reliability, liftable toward 1.0 by corroboration, never above."""
    base = settings.source_factor[leg.source]
    steps = min(max(leg.corroboration - 1, 0), settings.corroboration_max_steps)
    return min(1.0, base * (1.0 + settings.corroboration_step * steps))


def _hold(
    garment: Garment,
    *,
    as_of_week: str,
    stratum_id: str,
    reason: HoldReason,
    ctx: EngineContext,
    index: IndexView,
    drivers: tuple[Driver, ...] = (),
    codes: Sequence[str] = (),
    stratum_selected: bool = True,
) -> AdviceDecision:
    valuation = value_garment(garment, index=index, as_of_week=as_of_week, ctx=ctx)
    driver_codes = [f"driver:event:{eid}" for eid in dict.fromkeys(d.event_id for d in drivers)]
    driver_codes += [f"prior:{key}" for key in dict.fromkeys(d.prior_key for d in drivers)]
    rationale = (*driver_codes, *codes, f"hold:{reason.value}")
    return AdviceDecision(
        garment_id=garment.id,
        as_of_week=as_of_week,
        stratum_id=stratum_id,
        action=AdviceAction.HOLD,
        hold_reason=reason,
        is_candidate=False,
        horizon_weeks=ctx.settings.horizons_weeks[0],
        drivers=drivers,
        rationale_codes=rationale,
        stratum_selected=stratum_selected,
        inputs_hash=build_inputs_hash(
            drivers, stratum_id, index.built_as_of, ctx.config.config_version
        ),
        fair_value=valuation.fair_value,
        fair_value_method=valuation.method,
        fair_value_reason=valuation.reason,
        level_usd=valuation.level_usd,
    )


def advise_garment(
    garment: Garment,
    *,
    as_of_week: str,
    index: IndexView,
    events: Iterable[FashionEvent],
    ctx: EngineContext,
) -> AdviceDecision:
    """Produce the pure decision for one garment-week (FR-8)."""
    if garment.deleted_at is not None:
        raise ValueError(f"garment {garment.id} is soft-deleted and leaves the advise pipeline")
    if garment.status is GarmentStatus.SOLD_ARCHIVED:
        raise ValueError(f"garment {garment.id} is archived; advise covers owned/watching only")

    settings = ctx.settings
    stale_max = ctx.index_config.stale_max_weeks
    leaf = garment.stratum_path

    # -- stratum selection ------------------------------------------------- #
    selected = index.most_specific_fresh(leaf, as_of_week, stale_max)
    if selected is None:
        reason = (
            HoldReason.STALE_INDEX if index.has_any_point(leaf, as_of_week) else HoldReason.NO_INDEX
        )
        return _hold(
            garment,
            as_of_week=as_of_week,
            stratum_id=leaf,
            reason=reason,
            ctx=ctx,
            index=index,
            stratum_selected=False,
        )
    stratum, observed, staleness = selected

    # -- active events ------------------------------------------------------ #
    legs = active_legs(
        events,
        leaf=leaf,
        as_of_week=as_of_week,
        gazetteer=ctx.gazetteer,
        priors=ctx.priors,
        settings=settings,
    )
    if not legs:
        return _hold(
            garment,
            as_of_week=as_of_week,
            stratum_id=stratum,
            reason=HoldReason.NO_ACTIVE_EVENTS,
            ctx=ctx,
            index=index,
        )
    lambdas = {
        (leg.event_id, leg.target.stratum): scope_weight(
            index, stratum=stratum, target=leg.target.stratum, week=as_of_week
        )
        for leg in legs
    }

    # -- baseline ----------------------------------------------------------- #
    earliest_week = min(leg.event_week for leg in legs)
    baseline_point = index.latest_before(stratum, earliest_week)
    if baseline_point is None or weeks_between(baseline_point.week, earliest_week) > stale_max:
        return _hold(
            garment,
            as_of_week=as_of_week,
            stratum_id=stratum,
            reason=HoldReason.NO_BASELINE,
            ctx=ctx,
            index=index,
            drivers=_drivers(legs, lambdas, as_of_week, settings, settings.horizons_weeks[0]),
        )

    # -- volatility --------------------------------------------------------- #
    window = index.points_between(stratum, add_weeks(as_of_week, -SIGMA_WINDOW_WEEKS), as_of_week)
    sigma_w, _n_pairs = weekly_volatility(window, sigma_min_changes=settings.sigma_min_changes)
    if sigma_w is None:
        return _hold(
            garment,
            as_of_week=as_of_week,
            stratum_id=stratum,
            reason=HoldReason.INSUFFICIENT_HISTORY,
            ctx=ctx,
            index=index,
            drivers=_drivers(legs, lambdas, as_of_week, settings, settings.horizons_weeks[0]),
        )

    # -- horizon selection --------------------------------------------------- #
    ln_baseline = math.log(baseline_point.index_value)
    ln_observed = math.log(observed.index_value)
    best_horizon = settings.horizons_weeks[0]
    best_z = -math.inf
    best_ln_return = 0.0
    for horizon in settings.horizons_weeks:
        impact = 0.0
        for leg in legs:
            age = max(0, leg.age_at(as_of_week) + horizon)
            impact += lambdas[(leg.event_id, leg.target.stratum)] * leg.m_at(age)
        ln_return = ln_baseline + impact - ln_observed
        if sigma_w > 0:
            z_score = abs(ln_return) / (sigma_w * math.sqrt(horizon))
        else:
            z_score = math.inf if ln_return != 0.0 else 0.0
        if z_score > best_z:
            best_z, best_horizon, best_ln_return = z_score, horizon, ln_return

    expected_return = math.expm1(best_ln_return)
    drivers = _drivers(legs, lambdas, as_of_week, settings, best_horizon)

    # -- confidence ---------------------------------------------------------- #
    z_term = 1.0 - 0.5 ** (best_z / settings.z_half)
    q_index = q_index_factor(staleness, settings)
    c_event = _c_event(legs, lambdas, drivers, settings)
    confidence = min(settings.conf_cap, max(settings.conf_floor, z_term * q_index * c_event))

    # -- action -------------------------------------------------------------- #
    is_candidate = expected_return >= settings.theta_buy or expected_return <= -settings.theta_sell
    hold_reason: HoldReason | None = None
    if expected_return >= settings.theta_buy and confidence >= settings.conf_min:
        action = AdviceAction.BUY
    elif expected_return <= -settings.theta_sell and confidence >= settings.conf_min:
        action = AdviceAction.SELL
    else:
        action = AdviceAction.HOLD
        hold_reason = HoldReason.LOW_CONFIDENCE if is_candidate else HoldReason.BELOW_THRESHOLD

    codes = _rationale_codes(drivers, best_z, best_horizon, c_event, q_index, hold_reason)
    valuation = value_garment(garment, index=index, as_of_week=as_of_week, ctx=ctx)
    return AdviceDecision(
        garment_id=garment.id,
        as_of_week=as_of_week,
        stratum_id=stratum,
        action=action,
        hold_reason=hold_reason,
        is_candidate=is_candidate,
        horizon_weeks=best_horizon,
        expected_return=expected_return,
        confidence=confidence,
        z_score=best_z,
        sigma_w=sigma_w,
        baseline_index=baseline_point.index_value,
        observed_index=observed.index_value,
        q_index=q_index,
        c_event=c_event,
        stratum_staleness_weeks=staleness,
        drivers=drivers,
        rationale_codes=codes,
        inputs_hash=build_inputs_hash(
            drivers, stratum, index.built_as_of, ctx.config.config_version
        ),
        fair_value=valuation.fair_value,
        fair_value_method=valuation.method,
        fair_value_reason=valuation.reason,
        level_usd=valuation.level_usd,
    )


def _drivers(
    legs: Sequence[ImpactLeg],
    lambdas: Mapping[tuple[str, str], float],
    as_of_week: str,
    settings: AdvisorSettings,
    horizon: int,
) -> tuple[Driver, ...]:
    out: list[Driver] = []
    for leg in legs:
        age = leg.age_at(as_of_week)
        out.append(
            Driver(
                event_id=leg.event_id,
                event_type=leg.event_type,
                event_week=leg.event_week,
                target_stratum=leg.target.stratum,
                prior_key=leg.prior_key,
                direction=leg.direction,
                permanent_pct=leg.permanent,
                transient_pct=leg.transient,
                half_life_weeks=leg.half_life,
                base_conf=leg.base_conf,
                source=leg.source,
                corroboration=leg.corroboration,
                age_weeks=age,
                retirement_age_weeks=retirement_age(leg, settings),
                lam=lambdas[(leg.event_id, leg.target.stratum)],
                m_now=leg.m_at(max(0, age)),
                m_at_horizon=leg.m_at(max(0, age + horizon)),
            )
        )
    return tuple(out)


def _c_event(
    legs: Sequence[ImpactLeg],
    lambdas: Mapping[tuple[str, str], float],
    drivers: Sequence[Driver],
    settings: AdvisorSettings,
) -> float:
    """``c_event = sum_e w_e base_conf_e f_e / sum_e w_e``, ``w_e = lambda_e |m_e(t+H*-t_e)|``.

    The source factor appears exactly once (REVIEW D4). When every weight is zero
    — a stack of legs whose modelled move has fully decayed — the weighted mean is
    undefined, so the unweighted mean of the same per-leg terms is used.
    """
    numerator = denominator = 0.0
    terms: list[float] = []
    for leg, driver in zip(legs, drivers, strict=True):
        weight = lambdas[(leg.event_id, leg.target.stratum)] * abs(driver.m_at_horizon)
        term = leg.base_conf * _source_factor(leg, settings)
        terms.append(term)
        numerator += weight * term
        denominator += weight
    if denominator <= 0.0:
        return sum(terms) / len(terms) if terms else 0.0
    return numerator / denominator


def _rationale_codes(
    drivers: Sequence[Driver],
    z_score: float,
    horizon: int,
    c_event: float,
    q_index: float,
    hold_reason: HoldReason | None,
) -> tuple[str, ...]:
    codes: list[str] = []
    for event_id in dict.fromkeys(d.event_id for d in drivers):
        codes.append(f"driver:event:{event_id}")
    for prior_key in dict.fromkeys(d.prior_key for d in drivers):
        codes.append(f"prior:{prior_key}")
    for driver in drivers:
        codes.append(f"mod:lambda={driver.lam:.2f}@{driver.event_id}")
    codes.append(f"mod:z={z_score:.2f}@h{horizon}")
    codes.append(f"mod:conf_event={c_event:.2f}")
    codes.append(f"mod:q_index={q_index:.1f}")
    for source in sorted({d.source.value for d in drivers}):
        codes.append(f"mod:src={source}")
    if hold_reason is not None:
        codes.append(f"hold:{hold_reason.value}")
    return tuple(codes)


def build_advice(
    decision: AdviceDecision,
    *,
    garment: Garment,
    events_by_id: Mapping[str, FashionEvent],
    ctx: EngineContext,
    created_as_of: str,
) -> Advice:
    """Render, frame-check and package a decision as a persistable advice row.

    Raises :class:`~grailtrader.engine.frame.FrameCheckError` before any store
    ever sees the row (FR-9).
    """
    rendered = render_checked(decision, garment=garment, events_by_id=events_by_id, ctx=ctx)
    return Advice(
        id=advice_id(decision.garment_id, decision.as_of_week, decision.inputs_hash),
        garment_id=decision.garment_id,
        as_of_week=decision.as_of_week,
        inputs_hash=decision.inputs_hash,
        config_version=ctx.config.config_version,
        stratum_id=decision.stratum_id,
        action=decision.action,
        is_candidate=decision.is_candidate,
        horizon_weeks=decision.horizon_weeks,
        expected_return=decision.expected_return,
        confidence=decision.confidence,
        fair_value=decision.fair_value,
        fair_value_method=decision.fair_value_method or ValuationMethod.UNAVAILABLE,
        rationale_codes=decision.rationale_codes,
        rendered_text=rendered.text,
        frame_checked=True,
        created_as_of=created_as_of,
    )

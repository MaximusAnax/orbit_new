"""FR-6: the impact model — hard part B, first half.

Each confirmed event resolves, via the committed priors, to one *leg* per target
stratum. A leg's cumulative log-impact at age ``a`` whole weeks is::

    m_e(a) = ln(1 + P_e + T_e * 0.5 ** (a / h_e))          a >= 0

the permanent-plus-decaying-transient form of advertising adstock (Broadbent
1979) applied to a price level. Overlapping legs on the same stratum combine
log-additively.

Retirement is **conjunctive and bounded** (REVIEW D2): with

    A_e = active_max_weeks                                    if |T_e| <= active_transient_min
    A_e = min(active_max_weeks, h_e * log2(|T_e| / active_transient_min))  otherwise

an event is active at week ``t`` iff ``0 <= age_e(t) <= A_e``. Once retired its
permanent component is considered absorbed into the observed index, and FR-8
takes its baseline from a week after every retired event — so it is counted
exactly once.

Note on the SCOPE prose: FR-6's aside that "every prior in the catalog retires at
26 weeks" does not follow from the formula plus the committed prior table (e.g.
``collab_announcement.*`` with T = 0.10, h = 4 gives A_e = 17.3). The formula is
the normative part of the FR and is what this module implements.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..models import (
    TIER_SCALE,
    Acclaim,
    AdvisorSettings,
    CelebrityTier,
    DepartureReason,
    EventSource,
    EventStatus,
    EventType,
    FashionEvent,
    ImpactDirection,
    ImpactPrior,
    RunwayPolarity,
    ScandalSeverity,
    TargetKind,
)
from ..weeks import weeks_between
from .events import EventTarget, target_strata
from .strata import Gazetteer, is_prefix

__all__ = [
    "ImpactLeg",
    "active_legs",
    "combined_log_impact",
    "legs_for_event",
    "prior_key_for",
    "reachable_prior_keys",
    "retirement_age",
]


@dataclass(frozen=True)
class ImpactLeg:
    """One (event, target stratum, prior) triple — the unit FR-6/FR-8 sum over."""

    event_id: str
    event_type: EventType
    event_week: str
    source: EventSource
    corroboration: int
    target: EventTarget
    prior_key: str
    prior: ImpactPrior
    permanent: float
    transient: float
    half_life: float

    @property
    def base_conf(self) -> float:
        return self.prior.base_conf

    @property
    def direction(self) -> ImpactDirection:
        return self.prior.direction

    def m_at(self, age_weeks: float) -> float:
        """``m_e(a)`` — the cumulative log impact at age ``a`` whole weeks."""
        if age_weeks < 0:
            raise ValueError("m_e is only defined for non-negative ages")
        factor = 1.0 + self.permanent + self.transient * 0.5 ** (age_weeks / self.half_life)
        if factor <= 0.0:
            raise ValueError(
                f"prior {self.prior_key} implies a non-positive price factor at age {age_weeks}"
            )
        return math.log(factor)

    def age_at(self, week: str) -> int:
        """Whole weeks between the event's week and ``week`` (negative before the event)."""
        return weeks_between(self.event_week, week)


def prior_key_for(event: FashionEvent, kind: TargetKind) -> str:
    """The ``impact_priors.json`` key for one target of one event (FR-6)."""
    attrs = event.attributes
    if event.event_type is EventType.DESIGNER_DEPARTURE:
        return f"designer_departure.{DepartureReason(attrs['reason']).value}"
    if event.event_type is EventType.DESIGNER_APPOINTMENT:
        if kind is TargetKind.PREDECESSOR_ERA:
            return "designer_appointment.*.predecessor_era"
        return f"designer_appointment.{Acclaim(attrs['acclaim']).value}.brand"
    if event.event_type is EventType.COLLAB_ANNOUNCEMENT:
        return "collab_announcement.*"
    if event.event_type is EventType.CELEBRITY_COSIGN:
        return "celebrity_cosign.*"
    if event.event_type is EventType.RUNWAY_RECEPTION:
        return f"runway_reception.{RunwayPolarity(attrs['polarity']).value}"
    return f"brand_scandal.{ScandalSeverity(attrs['severity']).value}"


def reachable_prior_keys() -> dict[str, TargetKind]:
    """Every (event type x attribute combination) reachable per the FR-5 table (FR-1)."""
    keys: dict[str, TargetKind] = {}
    for reason in DepartureReason:
        keys[f"designer_departure.{reason.value}"] = TargetKind.ERA
    for acclaim in Acclaim:
        keys[f"designer_appointment.{acclaim.value}.brand"] = TargetKind.BRAND
    keys["designer_appointment.*.predecessor_era"] = TargetKind.PREDECESSOR_ERA
    keys["collab_announcement.*"] = TargetKind.BRAND
    keys["celebrity_cosign.*"] = TargetKind.GIVEN
    for polarity in RunwayPolarity:
        keys[f"runway_reception.{polarity.value}"] = TargetKind.BRAND
    for severity in ScandalSeverity:
        keys[f"brand_scandal.{severity.value}"] = TargetKind.BRAND
    return keys


def legs_for_event(
    event: FashionEvent, gazetteer: Gazetteer, priors: Mapping[str, ImpactPrior]
) -> tuple[ImpactLeg, ...]:
    """Resolve an event into its impact legs (one per target stratum)."""
    legs: list[ImpactLeg] = []
    for target in target_strata(event, gazetteer):
        key = prior_key_for(event, target.kind)
        try:
            prior = priors[key]
        except KeyError:
            raise KeyError(f"no impact prior for key {key!r} (event {event.id})") from None
        transient = prior.transient_pct
        if event.event_type is EventType.CELEBRITY_COSIGN:
            transient *= TIER_SCALE[CelebrityTier(event.attributes["tier"])]
        legs.append(
            ImpactLeg(
                event_id=event.id,
                event_type=event.event_type,
                event_week=event.week,
                source=event.source,
                corroboration=event.corroboration,
                target=target,
                prior_key=key,
                prior=prior,
                permanent=prior.permanent_pct,
                transient=transient,
                half_life=prior.half_life_weeks,
            )
        )
    return tuple(legs)


def retirement_age(leg: ImpactLeg, settings: AdvisorSettings) -> float:
    """``A_e`` — the conjunctive, bounded active age limit (FR-6)."""
    magnitude = abs(leg.transient)
    if magnitude <= settings.active_transient_min:
        return float(settings.active_max_weeks)
    decayed = leg.half_life * math.log2(magnitude / settings.active_transient_min)
    return min(float(settings.active_max_weeks), decayed)


def is_active(leg: ImpactLeg, as_of_week: str, settings: AdvisorSettings) -> bool:
    """Active iff the event is young enough *and* its transient is still alive."""
    age = leg.age_at(as_of_week)
    return 0 <= age <= retirement_age(leg, settings)


def active_legs(
    events: Iterable[FashionEvent],
    *,
    leaf: str,
    as_of_week: str,
    gazetteer: Gazetteer,
    priors: Mapping[str, ImpactPrior],
    settings: AdvisorSettings,
) -> tuple[ImpactLeg, ...]:
    """Confirmed, unretired legs whose target is a prefix of ``leaf`` (FR-5 + FR-6).

    Pending and rejected events never contribute (DATA_MODEL FashionEvent
    invariants); the result is sorted by ``(event_week, event_id, target)`` so
    downstream rationale codes are deterministic.
    """
    out: list[ImpactLeg] = []
    for event in events:
        if event.status is not EventStatus.CONFIRMED:
            continue
        for leg in legs_for_event(event, gazetteer, priors):
            if not is_prefix(leg.target.stratum, leaf):
                continue
            if not is_active(leg, as_of_week, settings):
                continue
            out.append(leg)
    out.sort(key=lambda leg: (leg.event_week, leg.event_id, leg.target.stratum))
    return tuple(out)


def combined_log_impact(
    legs: Iterable[ImpactLeg],
    *,
    as_of_week: str,
    horizon_weeks: int = 0,
    scope_weights: Mapping[tuple[str, str], float] | None = None,
) -> float:
    """``sum_e lambda_e * m_e(t + H - t_e)`` — log-additive combination (FR-6/FR-8).

    ``m_e`` is evaluated at the *future* age even when it exceeds ``A_e``: by then
    the transient has decayed to noise and only the permanent term remains, which
    is the intended forward path.
    """
    total = 0.0
    for leg in legs:
        age = leg.age_at(as_of_week) + horizon_weeks
        lam = 1.0
        if scope_weights is not None:
            lam = scope_weights.get((leg.event_id, leg.target.stratum), 1.0)
        total += lam * leg.m_at(max(0, age))
    return total

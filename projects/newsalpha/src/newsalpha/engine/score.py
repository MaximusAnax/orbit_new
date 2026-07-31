"""FR-6: signal scoring from the committed priors table (hard part B).

    prior       = resolve(event_type, role, polarity, asset.kind)   # most-specific-wins
    eff         = prior.stage_overrides[stage] if present else prior
    direction   = eff.direction        (unclear => no signal is emitted)
    horizon     = eff.horizon_bars
    mid_ar      = (eff.expected_ar_lo + eff.expected_ar_hi) / 2

    confidence  = clamp(prior.base_conf
                        * w_tier                       # t1 1.00, t2 0.85, t3 0.60
                        * f_stage                      # confirmed 1.00, rumored 0.50, denied 0.70
                        * (1 + 0.10 * min(corroboration - 1, 3))
                        * extraction_conf * link_conf,
                        0.05, 0.95)
    score       = mid_ar * confidence

Stage is applied exactly once in each channel and the two channels are disjoint:
`f_stage` is the only stage effect on **confidence**, `stage_overrides` the only
stage effect on **direction / magnitude / band / horizon**.  Prior keys never
encode stage, so US-4's "a rumored event scores at exactly half the confidence of
a confirmed one" is literally true.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..datasets import Datasets
from ..models import (
    F_STAGE,
    SIGNAL_ROLES,
    Cluster,
    Direction,
    Event,
    EventLink,
    EventSnapshot,
    LinkRole,
    Magnitude,
    Stage,
)

CONFIDENCE_FLOOR = 0.05
CONFIDENCE_CAP = 0.95
CORROBORATION_STEP = 0.10
CORROBORATION_CAP = 3

NOTE_UNCLEAR_ABSTAIN = "score:unclear_abstain"


@dataclass(frozen=True, slots=True)
class ScoredTuple:
    """One (event, signal-bearing link) scored -- what FR-15 turns into a revision."""

    event_id: str
    event_type: str
    asset_id: str
    role: LinkRole
    direction: Direction
    magnitude: Magnitude
    confidence: float
    horizon_bars: int
    expected_ar_lo: float
    expected_ar_hi: float
    score: float
    prior_key: str
    rationale_codes: tuple[str, ...]
    event_snapshot: EventSnapshot
    observed_at: str
    event_date: str

    def key_tuple(self) -> tuple[str, str, str]:
        return (self.event_type, self.asset_id, self.role.value)

    def scored_tuple(self) -> tuple[str, str, int, float, str]:
        return (
            self.direction.value,
            self.magnitude.value,
            self.horizon_bars,
            round(self.confidence, 4),
            self.prior_key,
        )


def corroboration_factor(corroboration: int) -> float:
    """`1 + 0.10 * min(corroboration - 1, 3)` -- capped at four distinct domains."""
    return 1.0 + CORROBORATION_STEP * min(max(corroboration - 1, 0), CORROBORATION_CAP)


def confidence_for(
    *,
    base_conf: float,
    tier_weight: float,
    stage: Stage,
    corroboration: int,
    extraction_confidence: float,
    link_confidence: float,
) -> float:
    """The FR-6 modifier chain, clamped to [0.05, 0.95] and rounded to 4 decimals.

    Rounding to 4 decimals is the same precision FR-15 compares for idempotency, so
    a stored confidence and a recomputed one can never differ by float noise alone.
    """
    raw = (
        base_conf
        * tier_weight
        * F_STAGE[stage]
        * corroboration_factor(corroboration)
        * extraction_confidence
        * link_confidence
    )
    return round(min(max(raw, CONFIDENCE_FLOOR), CONFIDENCE_CAP), 4)


def _evidence_article_ids(event: Event) -> tuple[str, ...]:
    """Distinct evidence article ids, in the (published_at, article_id) order FR-4 appended."""
    ordered: list[str] = []
    for span in event.evidence:
        if span.article_id not in ordered:
            ordered.append(span.article_id)
    return tuple(ordered)


def score_event(
    event: Event,
    links: list[EventLink],
    cluster: Cluster,
    datasets: Datasets,
) -> tuple[list[ScoredTuple], list[str]]:
    """Score every signal-bearing link of one event. Returns (scored tuples, notes).

    `mentioned` and `venue` links never produce a signal (precision-first, FR-5),
    and an `unclear` resolution emits no signal at all -- it is recorded as an
    abstention note instead, so it stays visible and countable (DATA_MODEL.md).
    """
    scored: list[ScoredTuple] = []
    notes: list[str] = []
    tier_weight = datasets.weight_for(cluster.best_tier)

    for link in sorted(links, key=lambda link: (link.asset_id, link.role.value)):
        if link.role not in SIGNAL_ROLES:
            continue
        asset = datasets.assets.get(link.asset_id)
        if asset is None:  # pragma: no cover - links are built from the gazetteer
            continue
        prior_key = f"{event.event_type.value}.{link.role.value}.{event.polarity}"
        prior = datasets.resolve_prior(prior_key, asset.kind.value)
        if prior is None:
            notes.append(f"score:no_prior:{prior_key}")
            continue
        eff = prior.effective(event.stage)
        if eff.direction is Direction.unclear:
            notes.append(NOTE_UNCLEAR_ABSTAIN)
            continue

        confidence = confidence_for(
            base_conf=prior.base_conf,
            tier_weight=tier_weight,
            stage=event.stage,
            corroboration=cluster.corroboration,
            extraction_confidence=event.extraction_confidence,
            link_confidence=link.link_confidence,
        )
        mid_ar = eff.mid_expected_ar
        codes = [
            f"prior:{prior.key}",
            f"mod:kind={asset.kind.value}",
        ]
        if prior.stage_overrides and event.stage in prior.stage_overrides:
            codes.append(f"mod:stage_override={event.stage.value}")
        codes.extend(
            [
                f"mod:f_stage={F_STAGE[event.stage]:.2f}",
                f"mod:tier={cluster.best_tier.value}",
                f"mod:corroboration={cluster.corroboration}",
                f"mod:extraction={event.extraction_confidence:.2f}",
                f"mod:link={link.link_confidence:.2f}",
            ]
        )
        scored.append(
            ScoredTuple(
                event_id=event.id,
                event_type=event.event_type.value,
                asset_id=link.asset_id,
                role=link.role,
                direction=eff.direction,
                magnitude=eff.magnitude,
                confidence=confidence,
                horizon_bars=eff.horizon_bars,
                expected_ar_lo=eff.expected_ar_lo,
                expected_ar_hi=eff.expected_ar_hi,
                score=round(mid_ar * confidence, 8),
                prior_key=prior.key,
                rationale_codes=tuple(codes),
                event_snapshot=EventSnapshot(
                    event_type=event.event_type,
                    stage=event.stage,
                    attributes=dict(event.attributes),
                    corroboration=cluster.corroboration,
                    best_tier=cluster.best_tier,
                    extraction_confidence=event.extraction_confidence,
                    link_confidence=link.link_confidence,
                    evidence_article_ids=_evidence_article_ids(event),
                    event_date=event.event_date,
                ),
                # `observed_at` is the cluster's *latest* publication, not the latest
                # trigger-bearing one: corroboration and best_tier are cluster-wide
                # inputs to this confidence, so anchoring entry any earlier would let a
                # later article's contribution leak backwards (FR-10/FR-15).
                observed_at=event.observed_at,
                event_date=event.event_date,
            )
        )
    return scored, notes


__all__ = [
    "CONFIDENCE_CAP",
    "CONFIDENCE_FLOOR",
    "NOTE_UNCLEAR_ABSTAIN",
    "ScoredTuple",
    "confidence_for",
    "corroboration_factor",
    "score_event",
]

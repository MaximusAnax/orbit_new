"""FR-15: signal keys, key continuity, revisions and cross-cluster supersession.

Signals are durable and versioned: a recompute never mutates one.  For each
signal key produced by the active-window recompute:

  * no signal exists              -> insert `revision = 1`;
  * the scored tuple is unchanged -> **no-op** (this is what makes re-running
    `ingest` byte-identical, US-1);
  * otherwise                     -> insert `revision = r + 1`, `supersedes` the
    previous revision id.

Key continuity absorbs a late, earlier-dated article that shifts a cluster's
`event_date` by <= 2 days; supersession demotes a stale rumor once a
different-stage signal for the same (event_type, asset_id, role) arrives more
than 2 and at most `supersession_days` later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..datasets import Datasets
from ..models import Signal, SignalKeyAlias
from .normalize import hex16
from .score import ScoredTuple

KEY_CONTINUITY_DAYS = 2
DEFAULT_SUPERSESSION_DAYS = 21


def signal_key(event_type: str, asset_id: str, role: str, event_date: str) -> str:
    """`hex16("sigkey|" + event_type + "|" + asset_id + "|" + role + "|" + event_date)`."""
    return hex16(f"sigkey|{event_type}|{asset_id}|{role}|{event_date}")


def signal_id(key: str, revision: int) -> str:
    """`hex16("signal|" + signal_key + "|" + revision)`."""
    return hex16(f"signal|{key}|{revision}")


@dataclass(frozen=True, slots=True)
class RevisionPlan:
    """What a recompute wants to append. Nothing here mutates an existing row."""

    new_signals: tuple[Signal, ...]
    new_aliases: tuple[SignalKeyAlias, ...]


def _days_between(left: str, right: str) -> int:
    return (date.fromisoformat(left) - date.fromisoformat(right)).days


def _latest_by_key(signals: list[Signal]) -> dict[str, Signal]:
    latest: dict[str, Signal] = {}
    for signal in signals:
        current = latest.get(signal.signal_key)
        if current is None or signal.revision > current.revision:
            latest[signal.signal_key] = signal
    return latest


def latest_revisions(signals: list[Signal]) -> list[Signal]:
    """The `max(revision)` row of every key -- nothing is mutated to flag it."""
    return sorted(_latest_by_key(signals).values(), key=lambda s: (s.signal_key, s.revision))


def _first_revision_by_key(signals: list[Signal]) -> dict[str, Signal]:
    first: dict[str, Signal] = {}
    for signal in signals:
        current = first.get(signal.signal_key)
        if current is None or signal.revision < current.revision:
            first[signal.signal_key] = signal
    return first


def resolve_key(
    scored: ScoredTuple,
    existing_signals: list[Signal],
    aliases: dict[str, str],
) -> tuple[str, str | None]:
    """FR-15 key continuity: reuse an existing key whose event_date is within 2 days.

    The earlier-created key wins, and the absorption is recorded in
    `signal_key_alias` so the mapping survives the next recompute.
    """
    raw_key = signal_key(scored.event_type, scored.asset_id, scored.role.value, scored.event_date)
    if raw_key in aliases:
        return aliases[raw_key], None

    first_rev = _first_revision_by_key(existing_signals)
    candidates: list[tuple[str, str, str]] = []  # (created_as_of, key, event_date)
    for key, signal in first_rev.items():
        if key == raw_key:
            return raw_key, None
        if (
            signal.event_snapshot.event_type.value != scored.event_type
            or signal.asset_id != scored.asset_id
            or signal.role != scored.role
        ):
            continue
        delta = abs(_days_between(scored.event_date, signal.event_snapshot.event_date))
        if delta <= KEY_CONTINUITY_DAYS:
            candidates.append((signal.created_as_of, key, signal.event_snapshot.event_date))
    if not candidates:
        return raw_key, None
    candidates.sort()
    return candidates[0][1], raw_key


def _supersedes_key(
    scored: ScoredTuple,
    resolved_key: str,
    existing_signals: list[Signal],
    datasets: Datasets,
) -> str | None:
    """The older, different-stage signal key this one demotes (FR-15).

    `S2` supersedes `S1` when they share (event_type, asset_id, role),
    `S2.stage != S1.stage`, and `2 days < event_date(S2) - event_date(S1) <=
    supersession_days`.  The relation is recorded on the newer signal only.
    """
    asset = datasets.assets.get(scored.asset_id)
    prior = datasets.resolve_prior(scored.prior_key, asset.kind.value if asset else "*")
    window = prior.supersession_days if prior is not None else DEFAULT_SUPERSESSION_DAYS
    best: tuple[int, str] | None = None
    for signal in latest_revisions(existing_signals):
        if signal.signal_key == resolved_key:
            continue
        snapshot = signal.event_snapshot
        if (
            snapshot.event_type.value != scored.event_type
            or signal.asset_id != scored.asset_id
            or signal.role != scored.role
        ):
            continue
        if snapshot.stage is scored.event_snapshot.stage:
            continue
        delta = _days_between(scored.event_date, snapshot.event_date)
        if KEY_CONTINUITY_DAYS < delta <= window:
            candidate = (delta, signal.signal_key)
            if best is None or candidate < best:
                best = candidate
    return best[1] if best is not None else None


def plan_revisions(
    scored_tuples: list[ScoredTuple],
    existing_signals: list[Signal],
    existing_aliases: list[SignalKeyAlias],
    datasets: Datasets,
    as_of: str,
) -> RevisionPlan:
    """Turn this recompute's scored tuples into appended revisions (FR-15)."""
    aliases = {alias.from_key: alias.to_key for alias in existing_aliases}
    pool = list(existing_signals)
    new_signals: list[Signal] = []
    new_aliases: list[SignalKeyAlias] = []

    ordered = sorted(
        scored_tuples,
        key=lambda s: (s.event_date, s.event_type, s.asset_id, s.role.value, s.event_id),
    )
    for scored in ordered:
        key, aliased_from = resolve_key(scored, pool, aliases)
        if aliased_from is not None:
            recorded = SignalKeyAlias(from_key=aliased_from, to_key=key, created_as_of=as_of)
            new_aliases.append(recorded)
            aliases[recorded.from_key] = recorded.to_key

        latest = _latest_by_key(pool).get(key)
        if latest is not None and latest.scored_tuple() == scored.scored_tuple():
            continue  # idempotent: an unchanged scored tuple writes nothing

        revision = 1 if latest is None else latest.revision + 1
        signal = Signal(
            id=signal_id(key, revision),
            signal_key=key,
            revision=revision,
            supersedes=None if latest is None else latest.id,
            supersedes_key=_supersedes_key(scored, key, pool, datasets),
            event_id=scored.event_id,
            asset_id=scored.asset_id,
            role=scored.role,
            direction=scored.direction,
            magnitude=scored.magnitude,
            confidence=scored.confidence,
            horizon_bars=scored.horizon_bars,
            expected_ar_lo=scored.expected_ar_lo,
            expected_ar_hi=scored.expected_ar_hi,
            score=scored.score,
            prior_key=scored.prior_key,
            rationale_codes=scored.rationale_codes,
            event_snapshot=scored.event_snapshot,
            observed_at=scored.observed_at,
            created_as_of=as_of,
        )
        new_signals.append(signal)
        pool.append(signal)

    return RevisionPlan(new_signals=tuple(new_signals), new_aliases=tuple(new_aliases))


def superseded_keys(signals: list[Signal]) -> set[str]:
    """Keys demoted by a newer, different-stage signal (FR-8 hides these by default)."""
    return {s.supersedes_key for s in signals if s.supersedes_key is not None}


__all__ = [
    "DEFAULT_SUPERSESSION_DAYS",
    "KEY_CONTINUITY_DAYS",
    "RevisionPlan",
    "latest_revisions",
    "plan_revisions",
    "resolve_key",
    "signal_id",
    "signal_key",
    "superseded_keys",
]

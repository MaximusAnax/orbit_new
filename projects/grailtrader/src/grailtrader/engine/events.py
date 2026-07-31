"""FR-5: event identity, deduplication across feeds, and scope resolution.

An event's id is derived from ``event_type | brand_id | era_id | iso_week |
identity_attrs`` where ``identity_attrs`` are the *factual* discriminators only.
``source_ref`` and the judgement attributes (``reason``, ``acclaim``,
``severity``, ``polarity``) are deliberately excluded, so the same real-world
event reported by two feeds resolves to one row whose ``corroboration`` rises
instead of two rows whose impacts would combine log-additively (REVIEW D3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..ids import event_id
from ..models import (
    Category,
    EventIngestReport,
    EventSource,
    EventStatus,
    EventType,
    FashionEvent,
    TargetKind,
    identity_attrs_for,
)
from .strata import Gazetteer, brand_path, era_path, is_prefix, leaf_path

__all__ = [
    "EventTarget",
    "applies_to",
    "corroboration_of",
    "ingest_events",
    "make_event",
    "merge_event",
    "registrable_domain",
    "target_strata",
    "transition_status",
]

#: Multi-label public suffixes that need three labels for the registrable domain.
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "me.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.jp",
        "or.jp",
        "ne.jp",
        "ac.jp",
        "co.nz",
        "com.br",
        "com.cn",
        "com.mx",
        "com.tr",
        "co.za",
        "co.kr",
        "com.hk",
    }
)


@dataclass(frozen=True)
class EventTarget:
    """One stratum an event scopes to, with the prior-selector that produced it."""

    kind: TargetKind
    stratum: str


def registrable_domain(source_ref: str) -> str:
    """The registrable domain of a source reference; ``manual:<slug>`` counts as ``manual``.

    Non-URL references of the form ``<scheme>:<rest>`` (where the scheme carries
    no dot) collapse to their scheme, so every manual entry counts as one source
    regardless of slug. Anything else is used verbatim, lower-cased.
    """
    ref = source_ref.strip()
    if not ref:
        raise ValueError("empty source_ref")
    parts = urlsplit(ref)
    if parts.scheme in {"http", "https"} and parts.netloc:
        host = parts.netloc.split("@")[-1].split(":")[0].lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        labels = host.split(".")
        if len(labels) <= 2:
            return host
        if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
            return ".".join(labels[-3:])
        return ".".join(labels[-2:])
    if ":" in ref:
        scheme = ref.split(":", 1)[0].strip().lower()
        if scheme and "." not in scheme:
            return scheme
    return ref.lower()


def corroboration_of(source_refs: Sequence[str]) -> int:
    """Number of distinct registrable domains among ``source_refs`` (>= 1)."""
    domains = {registrable_domain(ref) for ref in source_refs}
    return max(1, len(domains))


def make_event(
    *,
    event_type: EventType,
    brand_id: str,
    occurred_on: str,
    source: EventSource,
    source_refs: Sequence[str],
    status: EventStatus,
    era_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    notes: str = "",
) -> FashionEvent:
    """Construct a ``FashionEvent`` with its content-derived id and corroboration."""
    attrs = dict(attributes or {})
    refs = tuple(dict.fromkeys(source_refs))
    if not refs:
        raise ValueError("an event needs at least one source_ref")
    identifier = event_id(
        event_type.value, brand_id, era_id, occurred_on, identity_attrs_for(event_type, attrs)
    )
    return FashionEvent(
        id=identifier,
        event_type=event_type,
        brand_id=brand_id,
        era_id=era_id,
        attributes=attrs,
        occurred_on=occurred_on,
        source=source,
        source_refs=refs,
        status=status,
        corroboration=corroboration_of(refs),
        notes=notes,
    )


def merge_event(existing: FashionEvent, incoming: FashionEvent) -> FashionEvent:
    """Merge a second sighting into an existing event row (FR-5).

    First-seen wins on ``occurred_on``, the judgement attributes, ``source`` and
    ``notes``; ``source_refs`` gains the new references and ``corroboration`` is
    recounted. A ``rejected`` event stays rejected; otherwise a confirmed
    sighting promotes a pending row.
    """
    if existing.id != incoming.id:
        raise ValueError(f"cannot merge events with different ids: {existing.id} vs {incoming.id}")
    refs = tuple(dict.fromkeys((*existing.source_refs, *incoming.source_refs)))
    if existing.status is EventStatus.REJECTED:
        status = EventStatus.REJECTED
    elif EventStatus.CONFIRMED in {existing.status, incoming.status}:
        status = EventStatus.CONFIRMED
    else:
        status = existing.status
    return existing.model_copy(
        update={
            "source_refs": refs,
            "corroboration": corroboration_of(refs),
            "status": status,
            "notes": existing.notes or incoming.notes,
        }
    )


def transition_status(event: FashionEvent, status: EventStatus) -> FashionEvent:
    """Apply the only permitted status mutation: ``pending -> confirmed|rejected``."""
    if event.status is status:
        return event
    if event.status is not EventStatus.PENDING:
        raise ValueError(
            f"event {event.id} is {event.status}; only pending events can be confirmed or rejected"
        )
    if status is EventStatus.PENDING:
        raise ValueError("cannot transition an event back to pending")
    return event.model_copy(update={"status": status})


def ingest_events(
    existing: Iterable[FashionEvent], incoming: Iterable[FashionEvent]
) -> tuple[dict[str, FashionEvent], EventIngestReport]:
    """Fold ``incoming`` events into ``existing`` ones, deduplicating on identity (FR-5).

    Re-ingesting the same feed is a no-op; the same real-world event from a
    second feed bumps ``corroboration`` without creating a second row.
    """
    store: dict[str, FashionEvent] = {event.id: event for event in existing}
    created = corroborated = unchanged = 0
    for event in incoming:
        current = store.get(event.id)
        if current is None:
            store[event.id] = event
            created += 1
            continue
        merged = merge_event(current, event)
        if merged == current:
            unchanged += 1
        else:
            store[event.id] = merged
            if merged.corroboration > current.corroboration:
                corroborated += 1
            else:
                unchanged += 1
    by_status: dict[EventStatus, int] = {}
    for event in store.values():
        by_status[event.status] = by_status.get(event.status, 0) + 1
    return store, EventIngestReport(
        created=created, corroborated=corroborated, unchanged=unchanged, by_status=by_status
    )


def target_strata(event: FashionEvent, gazetteer: Gazetteer) -> tuple[EventTarget, ...]:
    """Resolve an event to the stratum prefixes it scopes to (FR-5 typology table)."""
    brand = brand_path(event.brand_id)
    if event.event_type is EventType.DESIGNER_DEPARTURE:
        assert event.era_id is not None
        return (EventTarget(TargetKind.ERA, era_path(event.brand_id, event.era_id)),)

    if event.event_type is EventType.DESIGNER_APPOINTMENT:
        targets = [EventTarget(TargetKind.BRAND, brand)]
        predecessor = gazetteer.predecessor_era(
            event.brand_id, event.attributes.get("designer"), event.occurred_on
        )
        if predecessor is not None:
            targets.append(
                EventTarget(TargetKind.PREDECESSOR_ERA, era_path(event.brand_id, predecessor.id))
            )
        return tuple(targets)

    if event.event_type is EventType.COLLAB_ANNOUNCEMENT:
        targets = [EventTarget(TargetKind.BRAND, brand)]
        counterparty = _counterparty_brand(event, gazetteer)
        if counterparty is not None and counterparty != event.brand_id:
            targets.append(EventTarget(TargetKind.BRAND, brand_path(counterparty)))
        return tuple(targets)

    if event.event_type is EventType.CELEBRITY_COSIGN:
        category = event.attributes.get("category")
        if event.era_id is not None and category:
            stratum = leaf_path(event.brand_id, event.era_id, Category(category))
        elif event.era_id is not None:
            stratum = era_path(event.brand_id, event.era_id)
        else:
            stratum = brand
        return (EventTarget(TargetKind.GIVEN, stratum),)

    # runway_reception and brand_scandal both scope to the whole brand.
    return (EventTarget(TargetKind.BRAND, brand),)


def _counterparty_brand(event: FashionEvent, gazetteer: Gazetteer) -> str | None:
    explicit = event.attributes.get("counterparty_brand_id")
    if explicit:
        return explicit if gazetteer.has_brand(explicit) else None
    counterparty = event.attributes.get("counterparty")
    if not counterparty:
        return None
    try:
        return gazetteer.resolve_brand(counterparty)
    except ValueError:
        return None


def applies_to(event: FashionEvent, leaf: str, gazetteer: Gazetteer) -> bool:
    """True iff one of the event's target prefixes is a prefix of the garment's leaf path."""
    return any(is_prefix(target.stratum, leaf) for target in target_strata(event, gazetteer))

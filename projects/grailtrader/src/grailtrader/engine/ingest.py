"""FR-2: listing ingestion and normalisation.

Pure function from ``RawListing``s to validated ``Listing``s plus a report. The
rules the FR states, in order of application per row:

* non-USD rows are rejected and counted (``skipped_currency``; SCOPE non-goal 8);
* brand/era/category references that the gazetteer cannot resolve are skipped and
  counted (``skipped_unresolved``) — never guessed (FR-2, SCOPE D-19);
* platform condition labels outside the committed alias table raise
  :class:`UnmappedConditionLabelError` naming the label — the pipeline never
  invents a grade;
* rows violating a stated listing invariant (sold without a price, ``sold_at <
  listed_at``, non-positive prices) are skipped and counted;
* ids are content-derived from ``(source, external_id)``, so re-ingesting a feed
  changes nothing (``duplicates``).
"""

from __future__ import annotations

from collections.abc import Iterable

from ..ids import listing_id
from ..models import Category, IngestReport, Listing, ListingStatus, RawListing
from .conditions import ConditionMapper
from .strata import Gazetteer, UnknownReferenceError

__all__ = ["normalize_listings"]


def normalize_listings(
    raws: Iterable[RawListing],
    *,
    gazetteer: Gazetteer,
    mapper: ConditionMapper,
    known_ids: Iterable[str] = (),
) -> tuple[list[Listing], IngestReport]:
    """Normalise raw feed rows into ``Listing``s (FR-2). Deterministic and idempotent."""
    seen: set[str] = set(known_ids)
    listings: list[Listing] = []
    ingested = duplicates = skipped_unresolved = skipped_currency = skipped_invalid = 0
    unresolved: list[str] = []

    for raw in raws:
        if raw.currency.strip().upper() != "USD":
            skipped_currency += 1
            continue
        try:
            brand_id = gazetteer.resolve_brand(raw.brand_ref)
            era_id = gazetteer.resolve_era(brand_id, raw.era_ref)
            category = Category(raw.category.strip().casefold())
        except (UnknownReferenceError, ValueError):
            skipped_unresolved += 1
            unresolved.append(f"{raw.source.value}:{raw.external_id}")
            continue

        grade = mapper.grade_for(raw.platform_condition)

        try:
            status = ListingStatus(raw.status.strip().casefold())
        except ValueError:
            skipped_invalid += 1
            continue

        identifier = listing_id(raw.source.value, raw.external_id)
        if identifier in seen:
            duplicates += 1
            continue

        try:
            listing = Listing(
                id=identifier,
                source=raw.source,
                external_id=raw.external_id,
                brand_id=brand_id,
                era_id=era_id,
                category=category,
                condition=grade,
                platform_label=raw.platform_condition,
                size=raw.size,
                title=raw.title,
                status=status,
                listed_at=raw.listed_at,
                sold_at=raw.sold_at,
                ask_price=raw.ask_price,
                sold_price=raw.sold_price,
                currency="USD",
            )
        except ValueError:
            skipped_invalid += 1
            continue

        seen.add(identifier)
        listings.append(listing)
        ingested += 1

    report = IngestReport(
        ingested=ingested,
        duplicates=duplicates,
        skipped_unresolved=skipped_unresolved,
        skipped_currency=skipped_currency,
        skipped_invalid=skipped_invalid,
        unresolved_refs=tuple(unresolved),
    )
    return listings, report

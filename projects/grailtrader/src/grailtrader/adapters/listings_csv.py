"""Live ``ListingsFeed``: a user-exported CSV of sold and ask-only comps (US-7).

No network and no optional dependency — the live comp path for this pass is a
file the user exports from a marketplace (SCOPE non-goal 7, D-18 valve 1).

Documented column map (header names are matched case-insensitively; the aliases
in brackets are also accepted)::

    external_id   [id, listing_id]      required, unique per source
    brand         [brand_name]          required, resolved against the gazetteer
    era           [designer_era, era_id] required
    category                            required, one of the eight categories
    condition     [condition_label]     required, a platform label from conditions.json
    status                              required, "sold" or "active"
    listed_at     [listed, date_listed] required, ISO-8601
    sold_at       [sold, date_sold]     required iff status = sold
    sold_price    [price_sold]          required iff status = sold
    ask_price     [price_ask, asking]   required iff status = active
    currency                            optional, defaults to USD; non-USD is rejected
    size                                optional
    title                               optional

Ask-only rows are yielded (they feed the ask-over-sold spread diagnostic) but by
construction never enter an index: FR-4 reads sold listings only.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from ..models import ListingSource, RawListing

__all__ = ["COLUMN_ALIASES", "CsvListingsFeed"]

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("external_id", "id", "listing_id"),
    "brand": ("brand", "brand_name"),
    "era": ("era", "designer_era", "era_id"),
    "category": ("category",),
    "condition": ("condition", "condition_label", "platform_condition"),
    "status": ("status",),
    "listed_at": ("listed_at", "listed", "date_listed"),
    "sold_at": ("sold_at", "sold", "date_sold"),
    "sold_price": ("sold_price", "price_sold"),
    "ask_price": ("ask_price", "price_ask", "asking"),
    "currency": ("currency",),
    "size": ("size",),
    "title": ("title",),
}
_REQUIRED = ("external_id", "brand", "era", "category", "condition", "status", "listed_at")


class CsvListingsFeed:
    """Reads user-exported comps from a CSV file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def fetch(self) -> list[RawListing]:
        rows: list[RawListing] = []
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"{self.path}: the CSV has no header row")
            lookup = {name.strip().casefold(): name for name in reader.fieldnames}
            columns = {
                field: next((lookup[alias] for alias in aliases if alias in lookup), None)
                for field, aliases in COLUMN_ALIASES.items()
            }
            missing = [field for field in _REQUIRED if columns[field] is None]
            if missing:
                raise ValueError(
                    f"{self.path}: missing required column(s) {missing}; accepted names are "
                    + ", ".join(f"{f}={COLUMN_ALIASES[f]}" for f in missing)
                )
            for number, record in enumerate(reader, start=2):
                value = _reader(record, columns)
                try:
                    rows.append(
                        RawListing(
                            source=ListingSource.CSV,
                            external_id=value("external_id") or "",
                            brand_ref=value("brand") or "",
                            era_ref=value("era") or "",
                            category=value("category") or "",
                            platform_condition=value("condition") or "",
                            status=value("status") or "",
                            listed_at=value("listed_at") or "",
                            sold_at=value("sold_at"),
                            ask_price=_money(value("ask_price")),
                            sold_price=_money(value("sold_price")),
                            currency=value("currency") or "USD",
                            size=value("size"),
                            title=value("title"),
                        )
                    )
                except ValueError as exc:
                    raise ValueError(f"{self.path}:{number}: {exc}") from None
        rows.sort(key=lambda row: (row.sold_at or row.listed_at, row.external_id))
        return rows


def _reader(record: dict[str, str], columns: dict[str, str | None]):
    def value(field: str) -> str | None:
        column = columns[field]
        if column is None:
            return None
        raw = (record.get(column) or "").strip()
        return raw or None

    return value


def _money(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    return float(cleaned)

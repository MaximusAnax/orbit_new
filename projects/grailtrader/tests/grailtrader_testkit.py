"""Shared builders for the grailtrader engine tests.

Everything here is deterministic: prices are either exact (noiseless, so index
levels are checkable by hand) or drawn from a seeded generator.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

from grailtrader.engine.context import EngineContext
from grailtrader.engine.events import make_event
from grailtrader.engine.index import IndexView, build_index
from grailtrader.ids import garment_id, listing_id
from grailtrader.models import (
    Category,
    ConditionGrade,
    EventSource,
    EventStatus,
    EventType,
    FashionEvent,
    Garment,
    GarmentStatus,
    IndexPoint,
    Listing,
    ListingSource,
    ListingStatus,
)
from grailtrader.weeks import add_weeks

BRAND = "helmut-lang"
ERA = "helmut-lang:helmut"
ERA_SUFFIX = "helmut"
LEAF = f"{BRAND}/{ERA_SUFFIX}/outerwear"
ERA_STRATUM = f"{BRAND}/{ERA_SUFFIX}"
START = "2024-01-01"  # a Monday


def week(offset: int, start: str = START) -> str:
    return add_weeks(start, offset)


def sold_listing(
    *,
    external_id: str,
    price: float,
    week_key: str,
    brand_id: str = BRAND,
    era_id: str = ERA,
    category: Category = Category.OUTERWEAR,
    condition: ConditionGrade = ConditionGrade.EXCELLENT,
    platform_label: str = "Gently Used",
    title: str | None = None,
) -> Listing:
    return Listing(
        id=listing_id(ListingSource.FIXTURE.value, external_id),
        source=ListingSource.FIXTURE,
        external_id=external_id,
        brand_id=brand_id,
        era_id=era_id,
        category=category,
        condition=condition,
        platform_label=platform_label,
        title=title,
        status=ListingStatus.SOLD,
        listed_at=f"{week_key}T00:00:00Z",
        sold_at=f"{week_key}T12:00:00Z",
        sold_price=round(price, 2),
    )


def flat_market(
    *,
    weeks: int,
    per_week: int = 9,
    level: float | Callable[[int], float] = 1000.0,
    brand_id: str = BRAND,
    era_id: str = ERA,
    category: Category = Category.OUTERWEAR,
    condition: ConditionGrade = ConditionGrade.EXCELLENT,
    platform_label: str = "Gently Used",
    prefix: str = "L",
    start: str = START,
    noise: float = 0.0,
    seed: int = 11,
) -> list[Listing]:
    """A stratum with ``per_week`` identical (or seeded-noisy) sales every week."""
    level_of = level if callable(level) else (lambda _w: float(level))
    rng = random.Random(seed)
    out: list[Listing] = []
    for offset in range(weeks):
        week_key = add_weeks(start, offset)
        for i in range(per_week):
            price = level_of(offset)
            if noise:
                price *= math.exp(rng.gauss(0.0, noise))
            out.append(
                sold_listing(
                    external_id=f"{prefix}-{offset}-{i}",
                    price=price,
                    week_key=week_key,
                    brand_id=brand_id,
                    era_id=era_id,
                    category=category,
                    condition=condition,
                    platform_label=platform_label,
                )
            )
    return out


def build(listings: Sequence[Listing], ctx: EngineContext, *, as_of_week: str) -> IndexView:
    stamp = f"{as_of_week}T23:59:00Z"
    return build_index(
        listings, mapper=ctx.mapper, config=ctx.index_config, as_of=stamp, built_as_of=stamp
    )


def garment(
    *,
    label: str = "HL astro moto jacket",
    brand_id: str = BRAND,
    era_id: str = ERA,
    category: Category = Category.OUTERWEAR,
    condition: ConditionGrade = ConditionGrade.EXCELLENT,
    anchor_condition: ConditionGrade | None = None,
    status: GarmentStatus = GarmentStatus.OWNED,
    price: float = 1000.0,
    anchor_date: str = START,
    added_at: str = "2026-01-01T00:00:00Z",
    notes: str = "",
) -> Garment:
    suffix = era_id.split(":", 1)[1]
    stratum = f"{brand_id}/{suffix}/{Category(category).value}"
    fields: dict[str, object] = {
        "id": garment_id(stratum, anchor_date, price, added_at),
        "label": label,
        "brand_id": brand_id,
        "era_id": era_id,
        "category": category,
        "condition": condition,
        "anchor_condition": anchor_condition or condition,
        "status": status,
        "added_at": added_at,
        "notes": notes,
    }
    if status is GarmentStatus.WATCHING:
        fields["reference_price"] = price
        fields["reference_date"] = anchor_date
    else:
        fields["acquisition_price"] = price
        fields["acquired_on"] = anchor_date
        if status is GarmentStatus.SOLD_ARCHIVED:
            fields["disposed_price"] = price
            fields["disposed_on"] = anchor_date
    return Garment(**fields)


def departure(
    *,
    occurred_on: str,
    reason: str = "resignation",
    brand_id: str = BRAND,
    era_id: str = ERA,
    source: EventSource = EventSource.MANUAL,
    refs: Sequence[str] = ("manual:departure",),
    status: EventStatus = EventStatus.CONFIRMED,
    notes: str = "",
) -> FashionEvent:
    return make_event(
        event_type=EventType.DESIGNER_DEPARTURE,
        brand_id=brand_id,
        occurred_on=occurred_on,
        source=source,
        source_refs=refs,
        status=status,
        era_id=era_id,
        attributes={"reason": reason},
        notes=notes,
    )


def cosign(
    *,
    occurred_on: str,
    celebrity: str,
    tier: str = "a_list",
    brand_id: str = BRAND,
    era_id: str | None = None,
    category: str | None = None,
    source: EventSource = EventSource.MANUAL,
    refs: Sequence[str] | None = None,
) -> FashionEvent:
    attributes: dict[str, object] = {"celebrity": celebrity, "tier": tier}
    if category is not None:
        attributes["category"] = category
    return make_event(
        event_type=EventType.CELEBRITY_COSIGN,
        brand_id=brand_id,
        occurred_on=occurred_on,
        source=source,
        source_refs=refs or (f"manual:{celebrity}",),
        status=EventStatus.CONFIRMED,
        era_id=era_id,
        attributes=attributes,
    )


def scandal(
    *,
    occurred_on: str,
    severity: str = "severe",
    brand_id: str = BRAND,
    source: EventSource = EventSource.NEWS,
    refs: Sequence[str] = ("https://www.example-news.com/a",),
) -> FashionEvent:
    return make_event(
        event_type=EventType.BRAND_SCANDAL,
        brand_id=brand_id,
        occurred_on=occurred_on,
        source=source,
        source_refs=refs,
        status=EventStatus.CONFIRMED,
        attributes={"severity": severity},
    )


def index_view(
    series: dict[str, Sequence[tuple[str, float]]],
    *,
    weights: dict[str, dict[str, int]] | None = None,
    base_level: float = 1000.0,
    built_as_of: str = "2026-01-01T00:00:00Z",
    as_of: str | None = None,
) -> IndexView:
    """Build an ``IndexView`` directly from ``stratum -> [(week, index_value)]``.

    Advisor tests need exact control over the baseline, the observed level and the
    volatility, so they plant the index instead of recovering it from listings.
    Leaf levels are derived as ``base_level * index / 100``.
    """
    points: dict[str, list[IndexPoint]] = {}
    for stratum, rows in series.items():
        is_leaf = len(stratum.split("/")) == 3
        points[stratum] = [
            IndexPoint(
                stratum_id=stratum,
                week=week_key,
                level_usd=(base_level * value / 100.0) if is_leaf else None,
                index_value=value,
                n_sales=10,
                n_excluded=0,
                built_as_of=built_as_of,
            )
            for week_key, value in rows
        ]
    if weights is None:
        weights = {
            stratum: {week_key: 10 for week_key, _ in rows}
            for stratum, rows in series.items()
            if len(stratum.split("/")) == 3
        }
    last = max((row[0] for rows in series.values() for row in rows), default=START)
    return IndexView(points, weights, {}, built_as_of=built_as_of, as_of=as_of or last)


def alternating_series(
    stratum: str, weeks: int, *, step: float = 0.02, start: str = START, level: float = 100.0
) -> list[tuple[str, float]]:
    """Weekly points whose log-changes alternate +/- ``step`` (so sigma_w is exact)."""
    return [
        (add_weeks(start, offset), level * math.exp(step * (offset % 2))) for offset in range(weeks)
    ]

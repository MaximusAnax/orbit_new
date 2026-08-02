"""Factory helpers for the newsalpha test suite: articles, raw articles, bar series."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from newsalpha.datasets import Datasets
from newsalpha.engine import pipeline
from newsalpha.models import Article, BarSource, PriceBar, RawArticle

AS_OF = "2026-03-20T07:00:00Z"


def raw(
    external_id: str,
    title: str,
    body: str,
    *,
    domain: str = "reuters.example",
    published_at: str = "2026-03-10T08:00:00Z",
    estimated: bool = False,
) -> RawArticle:
    return RawArticle(
        external_id=external_id,
        url=f"https://{domain}/{external_id}",
        source_domain=domain,
        published_at=published_at,
        published_at_estimated=estimated,
        fetched_at=published_at,
        title=title,
        body=body,
    )


def article(
    datasets: Datasets,
    external_id: str,
    title: str,
    body: str,
    *,
    domain: str = "reuters.example",
    published_at: str = "2026-03-10T08:00:00Z",
    as_of: str = AS_OF,
    estimated: bool = False,
) -> Article:
    return pipeline.normalize_article(
        raw(
            external_id,
            title,
            body,
            domain=domain,
            published_at=published_at,
            estimated=estimated,
        ),
        datasets,
        as_of,
    )


def bars(
    asset_id: str,
    start: str,
    days: int,
    *,
    weekdays_only: bool = False,
    drift: float = 0.0,
    level: float = 100.0,
    skip: set[str] | None = None,
    jump: dict[str, float] | None = None,
    source: BarSource = BarSource.fixture,
) -> list[PriceBar]:
    """A deterministic bar series: constant `drift` per bar plus optional dated jumps."""
    out: list[PriceBar] = []
    day = date.fromisoformat(start)
    skip = skip or set()
    jump = jump or {}
    for _ in range(days):
        iso = day.isoformat()
        day += timedelta(days=1)
        if weekdays_only and date.fromisoformat(iso).weekday() >= 5:
            continue
        if iso in skip:
            continue
        open_level = level
        level = level * math.exp(drift + jump.get(iso, 0.0))
        out.append(
            PriceBar(
                asset_id=asset_id,
                date=iso,
                open=open_level,
                high=max(open_level, level),
                low=min(open_level, level),
                close=level,
                volume=1000.0,
                source=source,
            )
        )
    return out


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)

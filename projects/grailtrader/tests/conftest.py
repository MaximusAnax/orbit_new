"""Pytest fixtures for the grailtrader tests.

Shared builders live in ``grailtrader_testkit`` so the module name stays unique
across the workspace's twelve test suites.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import pytest
from grailtrader.datasets import build_context
from grailtrader.engine.context import EngineContext

#: A small, real-gazetteer world for the API/CLI walkthroughs: three leaf strata
#: under two brands, 60 weeks of sales, one departure at week 45 (so the advisor
#: has both a quiet stretch and an event to react to).
MINI_START = date(2025, 1, 6)  # a Monday
MINI_WEEKS = 60
MINI_EVENT_WEEK = 45
MINI_STRATA = (
    ("helmut-lang", "helmut", "outerwear", 1400.0),
    ("helmut-lang", "helmut", "tailoring", 900.0),
    ("celine", "philo", "outerwear", 2200.0),
)


@pytest.fixture(scope="session")
def ctx() -> EngineContext:
    """The real committed datasets, loaded and validated (FR-1)."""
    return build_context()


@pytest.fixture(scope="session")
def mini_feed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Write a deterministic listings feed and event feed; return their paths."""
    root = tmp_path_factory.mktemp("mini_feed")
    listings = root / "listings.jsonl"
    events = root / "events.jsonl"
    rng = random.Random(20260731)

    rows: list[dict[str, object]] = []
    for brand, era, category, level in MINI_STRATA:
        for offset in range(MINI_WEEKS):
            week = (MINI_START + timedelta(weeks=offset)).isoformat()
            for index in range(8):
                price = level * math.exp(rng.gauss(0.0, 0.08))
                rows.append(
                    {
                        "external_id": f"{brand}-{category}-{offset}-{index}",
                        "brand_ref": brand,
                        "era_ref": era,
                        "category": category,
                        "platform_condition": "Gently Used" if index % 3 else "New with tags",
                        "status": "sold",
                        "listed_at": (
                            MINI_START + timedelta(weeks=offset, days=-10)
                        ).isoformat(),
                        "sold_at": week,
                        "sold_price": round(
                            price * (1.25 if index % 3 == 0 else 1.0), 2
                        ),
                    }
                )
            rows.append(
                {
                    "external_id": f"{brand}-{category}-{offset}-ask",
                    "brand_ref": brand,
                    "era_ref": era,
                    "category": category,
                    "platform_condition": "Gently Used",
                    "status": "active",
                    "listed_at": week,
                    "ask_price": round(level * 1.35, 2),
                }
            )
    listings.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )

    event_week = (MINI_START + timedelta(weeks=MINI_EVENT_WEEK)).isoformat()
    events.write_text(
        json.dumps(
            {
                "event_type": "designer_departure",
                "brand_id": "helmut-lang",
                "era_id": "helmut-lang:helmut",
                "attributes": {"reason": "resignation"},
                "occurred_on": event_week,
                "source": "news",
                "source_refs": ["https://www.example-news.test/hl-departure"],
                "notes": "Announced at the end of the season.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return listings, events


@pytest.fixture(scope="session")
def mini_weeks() -> list[str]:
    return [(MINI_START + timedelta(weeks=offset)).isoformat() for offset in range(MINI_WEEKS)]

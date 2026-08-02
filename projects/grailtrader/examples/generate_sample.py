"""Generate the committed quickstart sample under ``examples/`` (seeded, deterministic).

    uv run python grailtrader/examples/generate_sample.py

The eval fixtures deliberately use *fictional* brands (SCOPE D-14: scandal and
death events must never be attached to real houses), so they cannot be loaded
against the shipped real-brand gazetteer. This sample is the opposite: five leaf
strata on three real archive brands and two mild, factual events, so the README's
quickstart runs end to end against ``data/brands.json`` with zero configuration.

The numbers are synthetic. They are a demo of the pipeline, not market data.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
START = date(2025, 1, 6)  # a Monday
WEEKS = 78
SEED = 20260731

#: (brand, era suffix, category, excellent-condition level in USD, sales per week)
STRATA = (
    ("helmut-lang", "helmut", "outerwear", 1400.0, 9),
    ("helmut-lang", "helmut", "tailoring", 820.0, 7),
    ("celine", "philo", "outerwear", 2300.0, 8),
    ("celine", "philo", "accessories", 900.0, 9),
    ("raf-simons", "raf", "knitwear", 780.0, 7),
)
#: A handful of platform labels per grade, so the FR-2 alias mapping is exercised.
LABELS = {
    "new": ("New with tags", "Deadstock"),
    "excellent": ("Gently Used", "Excellent condition"),
    "good": ("Used - Good", "Very good condition"),
    "fair": ("Very Worn", "Fair condition"),
}
MIX = (("new", 0.10), ("excellent", 0.50), ("good", 0.28), ("fair", 0.12))
MULTIPLIER = {"new": 1.25, "excellent": 1.0, "good": 0.80, "fair": 0.55}

#: Two mild, factual, non-defamatory events on closed archive eras.
EVENTS = (
    {
        "week": 40,
        "event_type": "designer_departure",
        "brand_id": "raf-simons",
        "era_id": "raf-simons:raf",
        "attributes": {"reason": "house_closure"},
        "source": "news",
        "source_refs": ["https://www.example-fashion-news.test/raf-simons-line-closed"],
        "notes": "The label's own line is wound down; the era is closed.",
        # planted truth: +18 % permanent, +12 % transient, half-life 10 weeks, 8-week diffusion
        "impact": (0.18, 0.12, 10.0, 8),
    },
    {
        "week": 52,
        "event_type": "celebrity_cosign",
        "brand_id": "celine",
        "era_id": "celine:philo",
        "attributes": {"celebrity": "A. Demo", "tier": "a_list"},
        "source": "social",
        "source_refs": ["social:demo-philo-coat"],
        "notes": "Archive Philo coat worn at a widely photographed event.",
        "impact": (0.0, 0.10, 3.0, 3),
    },
)


def impact_factor(week: int) -> dict[str, float]:
    """The planted multiplicative impact per stratum prefix at ``week``."""
    factors: dict[str, float] = {}
    for event in EVENTS:
        permanent, transient, half_life, diffusion = event["impact"]
        age = week - event["week"]
        if age < 0:
            continue
        ramp = min(1.0, age / diffusion)
        value = 1.0 + ramp * (permanent + transient * 0.5 ** (age / half_life))
        prefix = f"{event['brand_id']}/{event['era_id'].split(':', 1)[1]}"
        factors[prefix] = factors.get(prefix, 1.0) * value
    return factors


def main() -> None:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []
    for brand, era, category, level, per_week in STRATA:
        drift = 0.0
        for offset in range(WEEKS):
            drift += rng.gauss(0.0, 0.006)
            week = (START + timedelta(weeks=offset)).isoformat()
            factors = impact_factor(offset)
            latent = level * math.exp(drift) * factors.get(f"{brand}/{era}", 1.0)
            for index in range(per_week):
                draw = rng.random()
                total = 0.0
                grade = "excellent"
                for name, share in MIX:
                    total += share
                    if draw < total:
                        grade = name
                        break
                price = latent * MULTIPLIER[grade] * math.exp(rng.gauss(0.0, 0.18))
                if rng.random() < 0.02:  # a counterfeit dumped far below market
                    price *= rng.uniform(0.15, 0.30)
                rows.append(
                    {
                        "external_id": f"{brand}-{category}-{offset:03d}-{index}",
                        "brand_ref": brand,
                        "era_ref": era,
                        "category": category,
                        "platform_condition": rng.choice(LABELS[grade]),
                        "status": "sold",
                        "listed_at": (
                            START + timedelta(weeks=offset, days=-rng.randint(7, 40))
                        ).isoformat(),
                        "sold_at": week,
                        "sold_price": round(price, 2),
                    }
                )
            if offset % 3 == 0:  # a few ask-only rows (diagnostics only; never indexed)
                rows.append(
                    {
                        "external_id": f"{brand}-{category}-{offset:03d}-ask",
                        "brand_ref": brand,
                        "era_ref": era,
                        "category": category,
                        "platform_condition": "Gently Used",
                        "status": "active",
                        "listed_at": week,
                        "ask_price": round(latent * rng.uniform(1.25, 1.45), 2),
                    }
                )

    rows.sort(key=lambda row: (row.get("sold_at") or row["listed_at"], row["external_id"]))
    (HERE / "sample_listings.jsonl").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    (HERE / "sample_events.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "event_type": event["event_type"],
                    "brand_id": event["brand_id"],
                    "era_id": event["era_id"],
                    "attributes": event["attributes"],
                    "occurred_on": (START + timedelta(weeks=event["week"])).isoformat(),
                    "source": event["source"],
                    "source_refs": event["source_refs"],
                    "status": "confirmed",
                    "notes": event["notes"],
                },
                separators=(",", ":"),
            )
            for event in EVENTS
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} listings and {len(EVENTS)} events to {HERE}")


if __name__ == "__main__":
    main()

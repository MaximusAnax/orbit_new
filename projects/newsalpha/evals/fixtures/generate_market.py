#!/usr/bin/env python3
"""Seeded generator for the NewsAlpha market fixtures (EVALS.md "Fixture strategy").

Market ground truth is a **planted-effect table written from the event-study
literature, independently of `data/priors.json`** -- the two are correlated
through the literature, never through code, so a wrong prior cannot manufacture
its own passing grade.  Nothing here imports `newsalpha`.

Two effects are planted per truth event:

1. an **announcement jump** on the last bar dated on or before the earliest
   article's publication date -- the day the market actually reacted.  FR-10's
   entry is strictly after `observed_at >= event_date`, so a correct harness can
   never see it; a harness that enters one bar early captures a 5-20 % move and
   fails M4/M7 loudly.  It is the strongest leak canary in the suite.
2. a **post-entry drift**, applied from the first bar after the cluster's *latest*
   evidence publication (the same anchor rule FR-10 uses), spread over the
   horizon.  This is the only thing the product scores.

Prices are a market-adjusted GBM: benchmark daily log-return sigma 1.0 % (idx:US)
/ 3.0 % (idx:CX) with zero drift, plus idiosyncratic noise (0.6 % equity /
1.6 % crypto per bar), plus the planted effects.  Every bar's open equals the
previous close, so an abnormal return over [entry, exit] is exactly the sum of
the idiosyncratic and planted terms over that window.

Usage
-----
    python generate_market.py --seeds 1,2,3,4,5     # write market/seed_N/*.csv
    python generate_market.py --regen-check         # re-derive and diff
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TRUTH_PATH = HERE / "articles_truth.json"
MARKET_DIR = HERE / "market"
GAPPED_DIR = HERE / "gapped"
MARKET_TRUTH_PATH = HERE / "market_truth.json"

SEEDS = (1, 2, 3, 4, 5)
BENCHMARK_SIGMA = {"idx:US": 0.010, "idx:CX": 0.030}
IDIO_SIGMA = {"equity": 0.006, "crypto": 0.016}
BENCHMARK_OF = {"equity": "idx:US", "crypto": "idx:CX"}
EFFECT_DISPERSION = 0.25  # sigma of the realized draw, as a share of |mean|
T3_NO_EFFECT_PROBABILITY = 0.50
RUMOR_FIZZLE_PROBABILITY = 0.60
START_LEVEL = {"equity": 100.0, "crypto": 40.0}

#: (event_type, role, kind-or-"*", polarity-or-stage-or-"*") -> (post-entry mean, bars).
#: Literature-scaled; see EVALS.md "Planted effects: announcement vs post-entry".
PLANTED: dict[tuple[str, str, str, str], tuple[float, int]] = {
    ("earnings_surprise", "subject", "*", "beat"): (+0.020, 20),
    ("earnings_surprise", "subject", "*", "miss"): (-0.020, 20),
    ("guidance_change", "subject", "*", "raise"): (+0.015, 5),
    ("guidance_change", "subject", "*", "cut"): (-0.020, 5),
    ("guidance_change", "subject", "*", "withdraw"): (-0.025, 5),
    ("mna", "target", "*", "confirmed"): (+0.020, 20),
    ("mna", "target", "*", "rumored"): (+0.030, 5),
    ("mna", "target", "*", "denied"): (-0.030, 5),
    ("mna", "acquirer", "*", "confirmed"): (-0.006, 5),
    ("mna", "acquirer", "*", "rumored"): (-0.004, 5),
    ("regulatory_action", "subject", "equity", "adverse"): (-0.015, 5),
    ("regulatory_action", "subject", "equity", "favorable"): (+0.012, 5),
    ("regulatory_action", "subject", "crypto", "adverse"): (-0.030, 5),
    ("regulatory_action", "subject", "crypto", "favorable"): (+0.025, 5),
    ("listing", "subject", "crypto", "*"): (+0.045, 5),
    ("listing", "subject", "equity", "*"): (+0.005, 5),
    ("delisting", "subject", "crypto", "*"): (-0.050, 5),
    ("delisting", "subject", "equity", "*"): (-0.020, 5),
    ("hack_exploit", "subject", "crypto", "*"): (-0.045, 5),
    ("hack_exploit", "subject", "equity", "*"): (-0.0125, 5),
}

#: Announcement-window CARs from the literature -- planted on the reaction bar and
#: never scored.  Keyed the same way as PLANTED.
ANNOUNCEMENT: dict[tuple[str, str, str, str], float] = {
    ("earnings_surprise", "subject", "*", "beat"): +0.040,
    ("earnings_surprise", "subject", "*", "miss"): -0.040,
    ("guidance_change", "subject", "*", "raise"): +0.050,
    ("guidance_change", "subject", "*", "cut"): -0.060,
    ("guidance_change", "subject", "*", "withdraw"): -0.060,
    ("mna", "target", "*", "confirmed"): +0.220,
    ("mna", "target", "*", "rumored"): +0.140,
    ("mna", "target", "*", "denied"): -0.120,
    ("mna", "acquirer", "*", "confirmed"): -0.010,
    ("mna", "acquirer", "*", "rumored"): -0.010,
    ("regulatory_action", "subject", "equity", "adverse"): -0.050,
    ("regulatory_action", "subject", "equity", "favorable"): +0.050,
    ("regulatory_action", "subject", "crypto", "adverse"): -0.080,
    ("regulatory_action", "subject", "crypto", "favorable"): +0.080,
    ("listing", "subject", "crypto", "*"): +0.120,
    ("listing", "subject", "equity", "*"): +0.020,
    ("delisting", "subject", "crypto", "*"): -0.180,
    ("delisting", "subject", "equity", "*"): -0.080,
    ("hack_exploit", "subject", "crypto", "*"): -0.150,
    ("hack_exploit", "subject", "equity", "*"): -0.060,
}

SIGNAL_ROLES = ("subject", "acquirer", "target")


def stream(*parts: Any) -> random.Random:
    """A Random seeded from the hash of its key, so draws never depend on call order."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def calendar(day_zero: date, days: int, *, weekdays_only: bool) -> list[str]:
    out: list[str] = []
    for offset in range(days):
        current = day_zero + timedelta(days=offset)
        if weekdays_only and current.weekday() >= 5:
            continue
        out.append(current.isoformat())
    return out


def polarity_key(event: dict[str, Any]) -> str:
    if event["event_type"] == "mna":
        return event["stage"]
    return str(event["attributes"].get("polarity", "*"))


def lookup(
    table: dict[tuple[str, str, str, str], Any], event: dict[str, Any], role: str, kind: str
) -> Any:
    key = polarity_key(event)
    for kind_key in (kind, "*"):
        for polarity in (key, "*"):
            hit = table.get((event["event_type"], role, kind_key, polarity))
            if hit is not None:
                return hit
    return None


def build_effects(truth: dict[str, Any], seeds: tuple[int, ...]) -> list[dict[str, Any]]:
    """Every planted effect, per seed -- the market half of the ground truth."""
    day_zero = date.fromisoformat(truth["day_zero"])
    days = truth["series_days"]
    kinds = truth["asset_kinds"]
    calendars = {
        "equity": calendar(day_zero, days, weekdays_only=True),
        "crypto": calendar(day_zero, days, weekdays_only=False),
    }
    effects: list[dict[str, Any]] = []
    for event in truth["events"]:
        for link in event["links"]:
            role = link["role"]
            if role not in SIGNAL_ROLES:
                continue
            asset_id = link["asset_id"]
            kind = kinds[asset_id]
            planted = lookup(PLANTED, event, role, kind)
            if planted is None:
                continue  # e.g. a denied acquirer: no signal and no effect
            mean, bars = planted
            announcement = lookup(ANNOUNCEMENT, event, role, kind) or 0.0
            dates = calendars[kind]
            event_date = event["event_date"]
            observed_date = event["observed_at"][:10]
            reaction = [d for d in dates if d <= event_date]
            entry_index = next((i for i, d in enumerate(dates) if d > observed_date), None)
            if not reaction or entry_index is None or entry_index + bars > len(dates):
                continue
            for seed in seeds:
                draw = stream("effect", seed, event["key"], asset_id)
                fizzled = False
                if event["single_source_t3"] and draw.random() < T3_NO_EFFECT_PROBABILITY:
                    fizzled = True
                if (
                    event["event_type"] == "mna"
                    and event["stage"] == "rumored"
                    and draw.random() < RUMOR_FIZZLE_PROBABILITY
                ):
                    fizzled = True
                realized = 0.0 if fizzled else draw.gauss(mean, EFFECT_DISPERSION * abs(mean))
                effects.append(
                    {
                        "seed": seed,
                        "event_key": event["key"],
                        "asset_id": asset_id,
                        "role": role,
                        "kind": kind,
                        "event_type": event["event_type"],
                        "stage": event["stage"],
                        "announcement_date": reaction[-1],
                        "entry_date": dates[entry_index],
                        "exit_date": dates[entry_index + bars - 1],
                        "bars": bars,
                        "planted_mean": mean,
                        "announcement_mean": announcement,
                        "realized": round(realized, 8),
                        "fizzled": fizzled,
                    }
                )
    return effects


def weights(bars: int) -> list[float]:
    """PEAD-shaped decay over the horizon, normalized to sum to 1."""
    raw = [1.0 / (1.0 + 0.12 * i) for i in range(bars)]
    total = sum(raw)
    return [value / total for value in raw]


def build_series(
    truth: dict[str, Any], effects: list[dict[str, Any]], seed: int
) -> dict[str, list[dict[str, Any]]]:
    day_zero = date.fromisoformat(truth["day_zero"])
    days = truth["series_days"]
    kinds = dict(truth["asset_kinds"])
    calendars = {
        "equity": calendar(day_zero, days, weekdays_only=True),
        "crypto": calendar(day_zero, days, weekdays_only=False),
    }

    benchmark_returns: dict[str, dict[str, float]] = {}
    for index_id, sigma in BENCHMARK_SIGMA.items():
        dates = calendars["equity" if index_id == "idx:US" else "crypto"]
        rng = stream("benchmark", seed, index_id)
        benchmark_returns[index_id] = {d: rng.gauss(0.0, sigma) for d in dates}

    planted_by_asset: dict[str, dict[str, float]] = {}
    for effect in effects:
        if effect["seed"] != seed:
            continue
        bucket = planted_by_asset.setdefault(effect["asset_id"], {})
        if not effect["fizzled"]:
            bucket[effect["announcement_date"]] = (
                bucket.get(effect["announcement_date"], 0.0) + effect["announcement_mean"]
            )
            dates = calendars[effect["kind"]]
            start = dates.index(effect["entry_date"])
            for offset, weight in enumerate(weights(effect["bars"])):
                day = dates[start + offset]
                bucket[day] = bucket.get(day, 0.0) + effect["realized"] * weight

    series: dict[str, list[dict[str, Any]]] = {}
    for index_id in BENCHMARK_SIGMA:
        kind = "equity" if index_id == "idx:US" else "crypto"
        series[index_id] = _bars(
            index_id,
            calendars[kind],
            benchmark_returns[index_id],
            {},
            1000.0,
            stream("volume", seed, index_id),
        )
    for asset_id in market_assets(truth):
        kind = kinds[asset_id]
        dates = calendars[kind]
        rng = stream("idio", seed, asset_id)
        idio = {d: rng.gauss(0.0, IDIO_SIGMA[kind]) for d in dates}
        benchmark = benchmark_returns[BENCHMARK_OF[kind]]
        combined = {d: benchmark.get(d, 0.0) + idio[d] for d in dates}
        series[asset_id] = _bars(
            asset_id,
            dates,
            combined,
            planted_by_asset.get(asset_id, {}),
            START_LEVEL[kind],
            stream("volume", seed, asset_id),
        )
    return series


def market_assets(truth: dict[str, Any]) -> list[str]:
    """Assets that need a price series: every asset any truth event links.

    No-event articles and trap-only mentions never produce a signal, so committing
    a 252-bar series for them would be dead weight in the repository.
    """
    return sorted({link["asset_id"] for event in truth["events"] for link in event["links"]})


def _bars(
    asset_id: str,
    dates: list[str],
    returns: dict[str, float],
    planted: dict[str, float],
    level: float,
    volume_rng: random.Random,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in dates:
        open_level = level
        level = open_level * math.exp(returns.get(day, 0.0) + planted.get(day, 0.0))
        out.append(
            {
                "date": day,
                "open": round(open_level, 4),
                "high": round(max(open_level, level), 4),
                "low": round(min(open_level, level), 4),
                "close": round(level, 4),
                "volume": float(volume_rng.randrange(100, 50_000) * 100),
            }
        )
    return out


def csv_text(bars: list[dict[str, Any]]) -> str:
    lines = ["date,open,high,low,close,volume"]
    for bar in bars:
        lines.append(
            f"{bar['date']},{bar['open']:.4f},{bar['high']:.4f},{bar['low']:.4f},"
            f"{bar['close']:.4f},{bar['volume']:.0f}"
        )
    return "\n".join(lines) + "\n"


def filename_for(asset_id: str) -> str:
    return asset_id.replace(":", "_") + ".csv"


def build_gapped(truth: dict[str, Any]) -> dict[str, str]:
    """A tiny corpus with deliberate holes, for the gap-handling tests (EVALS "Files").

    `eq:AAPL` is missing three bars and `idx:US` is missing the bar on 2026-02-10,
    which is an entry date for the equity leg -- so FR-10 must exclude that signal
    with `benchmark_gap` rather than silently comparing different weeks.
    """
    day_zero = date.fromisoformat(truth["day_zero"])
    dates = calendar(day_zero, 60, weekdays_only=True)
    missing_asset = {"2026-02-03", "2026-02-04", "2026-02-05"}
    missing_benchmark = {"2026-02-10"}
    out: dict[str, str] = {}
    for asset_id, drop in (("eq:AAPL", missing_asset), ("idx:US", missing_benchmark)):
        rng = stream("gapped", asset_id)
        level = 100.0
        bars: list[dict[str, Any]] = []
        for day in dates:
            open_level = level
            level = open_level * math.exp(rng.gauss(0.0, 0.008))
            if day in drop:
                continue
            bars.append(
                {
                    "date": day,
                    "open": round(open_level, 4),
                    "high": round(max(open_level, level), 4),
                    "low": round(min(open_level, level), 4),
                    "close": round(level, 4),
                    "volume": 1_000_000.0,
                }
            )
        out[filename_for(asset_id)] = csv_text(bars)
    return out


def render(truth: dict[str, Any], seeds: tuple[int, ...]) -> tuple[dict[Path, str], str]:
    effects = build_effects(truth, seeds)
    files: dict[Path, str] = {}
    for seed in seeds:
        series = build_series(truth, effects, seed)
        for asset_id, bars in series.items():
            files[MARKET_DIR / f"seed_{seed}" / filename_for(asset_id)] = csv_text(bars)
    for name, text in build_gapped(truth).items():
        files[GAPPED_DIR / name] = text

    market_truth = {
        "seeds": list(seeds),
        "day_zero": truth["day_zero"],
        "series_days": truth["series_days"],
        "benchmark_sigma": BENCHMARK_SIGMA,
        "idiosyncratic_sigma": IDIO_SIGMA,
        "effect_dispersion": EFFECT_DISPERSION,
        "t3_no_effect_probability": T3_NO_EFFECT_PROBABILITY,
        "rumor_fizzle_probability": RUMOR_FIZZLE_PROBABILITY,
        "planted_table": {
            "|".join(key): {"post_entry_mean": value[0], "bars": value[1]}
            for key, value in sorted(PLANTED.items())
        },
        "announcement_table": {
            "|".join(key): value for key, value in sorted(ANNOUNCEMENT.items())
        },
        "gapped": {
            "asset_id": "eq:AAPL",
            "missing_asset_bars": ["2026-02-03", "2026-02-04", "2026-02-05"],
            "benchmark_id": "idx:US",
            "missing_benchmark_bars": ["2026-02-10"],
        },
        "effects": effects,
    }
    return files, json.dumps(market_truth, indent=1, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    parser.add_argument("--regen-check", action="store_true")
    args = parser.parse_args(argv)
    seeds = tuple(int(part) for part in args.seeds.split(","))
    if seeds != SEEDS:
        raise SystemExit(f"only the committed seeds {SEEDS} reproduce the fixtures")

    truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    files, market_truth = render(truth, seeds)

    if args.regen_check:
        if not MARKET_TRUTH_PATH.exists():
            print(f"MISSING {MARKET_TRUTH_PATH}", file=sys.stderr)
            return 1
        if MARKET_TRUTH_PATH.read_text(encoding="utf-8") != market_truth:
            print(f"DIFF {MARKET_TRUTH_PATH}", file=sys.stderr)
            return 1
        for path, text in files.items():
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"DIFF {path}", file=sys.stderr)
                return 1
        print("market fixtures reproduce byte-identically")
        return 0

    if MARKET_DIR.exists():
        shutil.rmtree(MARKET_DIR)
    if GAPPED_DIR.exists():
        shutil.rmtree(GAPPED_DIR)
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    MARKET_TRUTH_PATH.write_text(market_truth, encoding="utf-8")
    print(f"wrote {len(files)} series files across seeds {seeds}")
    print(f"planted effects: {sum(1 for _ in json.loads(market_truth)['effects'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

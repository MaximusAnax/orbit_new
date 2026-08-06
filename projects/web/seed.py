#!/usr/bin/env python3
"""Put demo data into every project so no screen opens empty.

    uv run python web/seed.py              # seed everything
    uv run python web/seed.py ethos almanac
    uv run python web/seed.py --reset      # wipe the demo home first

Each project is seeded through its own CLI — the same commands the READMEs
document — so this exercises the real paths rather than writing to the databases
behind their backs. A project that fails to seed is reported, not hidden; the
gateway still serves it and its screens show their empty state.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECTS_DIR = Path(__file__).parent.parent
DEMO_HOME = Path(os.environ.get("PROJECTS_DEMO_HOME", Path.home()))
SCRATCH = PROJECTS_DIR / "web" / ".demo-data"

SLUGS = [
    "ethos", "almanac", "flowlist", "chessmentor", "dresscast", "pointsmax",
    "newsalpha", "tickerpress", "grailtrader", "datasweep", "formcoach", "voicekin",
]


_BOX = str.maketrans("", "", "│╭╮╯╰─")


def _reason(output: str) -> str:
    """Pull the meaningful error out of Typer's boxed output."""
    lines = [ln.translate(_BOX).strip() for ln in output.splitlines()]
    lines = [ln for ln in lines if ln and ln not in {"{", "}"}]
    for line in lines:
        if line.lower().startswith("error") or "Error:" in line:
            return line[:150]
    return lines[-1][:150] if lines else "no output"


def run(args: list[str], timeout: int = 900) -> tuple[bool, str]:
    """Run one project CLI command inside the workspace."""
    env = {**os.environ, "HOME": str(DEMO_HOME)}
    try:
        proc = subprocess.run(
            ["uv", "run", *args], cwd=PROJECTS_DIR, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return True, ""
        return False, _reason(proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as exc:
        return False, str(exc)


# ---------------------------------------------------------------- demo inputs

def demo_playlist() -> Path:
    """A messy playlist: keys and tempos all over the place, so reordering visibly helps."""
    path = SCRATCH / "demo_playlist.csv"
    rows = [
        ("Golden Hour", "Vela Sun", 124.0, 8, 1, 0.71, -7.2, 214000),
        ("Night Drive", "Marta Quiet", 126.5, 9, 1, 0.76, -6.4, 233000),
        ("Slow Burn", "Cyan Drift", 86.0, 9, 0, 0.42, -11.0, 258000),
        ("Breakline", "Ostro", 174.0, 7, 1, 0.88, -5.1, 191000),
        ("Paper Lanterns", "Hana Iwai", 122.0, 8, 0, 0.55, -8.9, 246000),
        ("Copper Wire", "The Levellers", 128.0, 10, 1, 0.81, -6.0, 205000),
        ("Undertow", "Vela Sun", 120.0, 3, 1, 0.49, -9.8, 269000),
        ("Second Wind", "Ostro", 130.0, 10, 0, 0.85, -5.6, 198000),
        ("Blue Hour", "Hana Iwai", 92.0, 2, 1, 0.38, -12.1, 281000),
        ("Afterglow", "Marta Quiet", 125.0, 8, 1, 0.73, -7.0, 222000),
    ]
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Track Name", "Artist Name", "Tempo", "Key", "Mode",
                    "Energy", "Loudness", "Duration (ms)"])
        w.writerows(rows)
    return path


def demo_forecasts(days: int = 5) -> int:
    """Write hourly forecasts for today onward into dresscast's fixture directory.

    The offline weather adapter reads a file per date, and the committed fixtures
    are dated for the eval scenarios — so a demo opened on any other day has no
    forecast. These days deliberately swing (cold morning, warm afternoon, rain on
    day two) because a flat day makes the layering engine look like it does nothing.
    """
    import datetime as dt
    import math

    out_dir = DEMO_HOME / ".dresscast" / "weather"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Each day: (dawn low °C, afternoon high °C, wind km/h, rain probability, mm/h)
    shapes = [
        (8.0, 19.0, 11.0, 0.05, 0.0),
        (11.0, 16.0, 26.0, 0.80, 1.8),
        (3.0, 9.0, 18.0, 0.15, 0.0),
        (14.0, 27.0, 8.0, 0.00, 0.0),
        (6.0, 21.0, 13.0, 0.10, 0.0),
    ]
    today = dt.date.today()
    for offset in range(days):
        low, high, wind, prob, mmh = shapes[offset % len(shapes)]
        date = today + dt.timedelta(days=offset)
        hours = []
        for hour in range(24):
            # Coldest around 05:00, warmest around 15:00.
            phase = math.cos((hour - 15) / 24 * 2 * math.pi)
            temp = low + (high - low) * (phase + 1) / 2
            wet = prob if 12 <= hour <= 18 else prob * 0.25
            hours.append({
                "seq": hour, "hour": hour,
                "temp_c": round(temp, 1),
                "wind_kmh": round(wind + (3.0 if 12 <= hour <= 18 else 0.0), 1),
                "humidity_pct": round(78.0 - (temp - low) * 1.5, 1),
                "precip_prob": round(wet, 2),
                "precip_mmh": round(mmh if 12 <= hour <= 18 else 0.0, 2),
                "uv_index": round(max(0.0, 6.0 * phase), 1) if 7 <= hour <= 19 else 0.0,
            })
        (out_dir / f"{date.isoformat()}.json").write_text(json.dumps({
            "date": date.isoformat(), "location_name": "home",
            "lat": 40.71, "lon": -74.01, "timezone": "America/New_York",
            "hours": hours,
        }, indent=1), encoding="utf-8")
    return days


def demo_messy_csv() -> Path:
    """Every defect class datasweep detects, in one small file."""
    path = SCRATCH / "demo_sales.csv"
    path.write_text(
        "order_id, customer ,order_date,amount,region,notes\n"
        "1001,Acme Corp ,2024-01-15,1250.00,North,\n"
        "1002, Beta LLC,15/01/2024,980.50,north,rush\n"
        "1003,Acme Corp,2024-01-16,N/A,North,\n"
        "1004,Gamma Inc,2024-01-17,1.100,00,South,euro format\n"
        "1005, Beta LLC ,2024-01-18,-999,NORTH,sentinel?\n"
        "1005, Beta LLC ,2024-01-18,-999,NORTH,sentinel?\n"
        "1006,Delta Co,2024-01-19,875.25,Sud,\n"
        "1007,Acme Corp.,2024-01-20,99999999,North,outlier\n"
        "1008,Gamma Inc,,1420.00,South,missing date\n",
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------ seeders

def seed_ethos() -> list[list[str]]:
    return [["ethos", "init"],
            ["ethos", "ask", "what do I owe my aging parents?"],
            ["ethos", "ask", "is it wrong to walk away from a promise?"]]


def seed_almanac() -> list[list[str]]:
    return [["almanac", "init", "--seed", "7"],
            ["almanac", "import", "--starter"],
            ["almanac", "today"]]


def seed_flowlist() -> list[list[str]]:
    csv_path = demo_playlist()
    return [["flowlist", "import", str(csv_path), "--name", "Road Trip"],
            ["flowlist", "analyze", "Road Trip"],
            ["flowlist", "reorder", "Road Trip", "--seed", "7"]]


def seed_chessmentor() -> list[list[str]]:
    return [["chessmentor", "init", "--name", "Demo", "--challenge", "balanced"]]


def seed_dresscast() -> list[list[str]]:
    """A wardrobe with real layering range, so a cold morning into a warm afternoon
    has something to work with. Categories are D1 presets (engine/models.py)."""
    # Occasions must come from DEFAULT_OCCASIONS; non-neutral colours need an
    # explicit hue (`name:degrees`) so the compatibility rules can reason about them.
    wardrobe = [
        ("merino-tee", "tshirt", "charcoal", "casual"),
        ("oxford-shirt", "shirt_long_sleeve", "white", "work,formal"),
        ("flannel-shirt", "flannel_shirt", "forest:140", "casual,outdoor"),
        ("merino-crew", "sweater_thin", "oatmeal:40", "work,casual"),
        ("cable-knit", "sweater_thick", "cream:45", "casual,outdoor"),
        ("navy-blazer", "blazer", "navy:220", "work,formal"),
        ("field-jacket", "light_jacket", "olive:90", "casual,outdoor"),
        ("rain-shell", "rain_shell", "slate:210", "casual,work,outdoor"),
        ("wool-overcoat", "wool_coat", "charcoal", "work,formal"),
        ("chinos", "trousers_thin", "stone:35", "work,casual"),
        ("dark-jeans", "jeans", "indigo:230", "casual"),
        ("wool-trousers", "trousers_thick", "charcoal", "work,formal"),
        ("leather-boots", "boots", "brown:30", "casual,work,outdoor"),
        ("white-sneakers", "sneakers", "white", "casual,sport"),
        ("wool-scarf", "scarf", "rust:20", "casual,work"),
        ("compact-umbrella", "umbrella", "black", "work,casual"),
    ]
    demo_forecasts()
    cmds = [["dresscast", "add", "--name", n, "--category", c,
             "--colors", col, "--occasions", occ]
            for n, c, col, occ in wardrobe]
    cmds.append(["dresscast", "brief"])
    return cmds


def seed_pointsmax() -> list[list[str]]:
    return [["pointsmax", "init"],
            ["pointsmax", "wallet", "add-card", "chase_sapphire_reserve"],
            ["pointsmax", "wallet", "add-card", "amex_platinum"],
            ["pointsmax", "wallet", "set", "chase_ur", "210000"],
            ["pointsmax", "wallet", "set", "amex_mr", "130000"]]


def seed_newsalpha() -> list[list[str]]:
    return [["newsalpha", "init"], ["newsalpha", "ingest"]]


def seed_tickerpress() -> list[list[str]]:
    feeds = sorted((PROJECTS_DIR / "tickerpress/evals/fixtures/feeds").glob("*.xml"))
    cmds: list[list[str]] = [["tickerpress", "init"]]
    for ticker, name, ctx, anti in [
        ("AAPL", "Apple Inc.", "iphone", "orchard"),
        ("MSFT", "Microsoft Corp.", "azure", None),
        ("NVDA", "NVIDIA Corp.", "gpu", None),
    ]:
        cmds.append(["tickerpress", "company", "add", ticker, "--name", name])
        term = ["tickerpress", "term", "add", ticker, "--context", ctx]
        if anti:
            term += ["--anti", anti]
        cmds.append(term)
    for i, feed in enumerate(feeds[:3], 1):
        cmds.append(["tickerpress", "feed", "add", f"file://{feed}", "--name", f"Wire {i}"])
    cmds.append(["tickerpress", "ingest"])
    return cmds


def seed_grailtrader() -> list[list[str]]:
    ex = PROJECTS_DIR / "grailtrader/examples"
    return [["grailtrader", "init", "--reset"],
            ["grailtrader", "listings", "load", "--path", str(ex / "sample_listings.jsonl")],
            ["grailtrader", "events", "ingest", "--path", str(ex / "sample_events.jsonl")],
            ["grailtrader", "index", "build"],
            ["grailtrader", "advise"]]


def seed_datasweep() -> list[list[str]]:
    return [["datasweep", "clean", str(demo_messy_csv())]]


def seed_formcoach() -> list[list[str]]:
    return [["formcoach", "init"],
            ["formcoach", "profile", "set", "--goal", "hypertrophy",
             "--experience", "intermediate", "--days", "4",
             "-e", "barbell", "-e", "dumbbell", "-e", "machine",
             "-m", "chest", "-m", "lats"],
            ["formcoach", "profile", "ack-disclaimer"],
            ["formcoach", "program", "new", "--seed", "20260731"]]


def seed_voicekin() -> list[list[str]]:
    return [["voicekin", "init", "--operator", "Demo"]]


SEEDERS = {
    "ethos": seed_ethos, "almanac": seed_almanac, "flowlist": seed_flowlist,
    "chessmentor": seed_chessmentor, "dresscast": seed_dresscast, "pointsmax": seed_pointsmax,
    "newsalpha": seed_newsalpha, "tickerpress": seed_tickerpress, "grailtrader": seed_grailtrader,
    "datasweep": seed_datasweep, "formcoach": seed_formcoach, "voicekin": seed_voicekin,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slugs", nargs="*", default=None)
    parser.add_argument("--reset", action="store_true", help="delete each project's demo database first")
    args = parser.parse_args()

    slugs = args.slugs or SLUGS
    unknown = [s for s in slugs if s not in SEEDERS]
    if unknown:
        print(f"unknown project(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    SCRATCH.mkdir(parents=True, exist_ok=True)
    if args.reset:
        for slug in slugs:
            shutil.rmtree(DEMO_HOME / f".{slug}", ignore_errors=True)
        print(f"reset {len(slugs)} project database(s)\n")

    results: list[tuple[str, bool, str]] = []
    for slug in slugs:
        print(f"seeding {slug} ...", flush=True)
        failures: list[str] = []
        for cmd in SEEDERS[slug]():
            ok, out = run(cmd)
            if not ok:
                failures.append(f"{' '.join(cmd[:3])}: {out.strip().splitlines()[-1][:120] if out.strip() else 'failed'}")
        results.append((slug, not failures, "; ".join(failures[:2])))

    width = max(len(s) for s in slugs)
    print("\n" + "=" * (width + 46))
    seeded = 0
    for slug, ok, detail in results:
        mark = "seeded" if ok else "PARTIAL"
        seeded += ok
        print(f"{slug:<{width}}  {mark}")
        if detail:
            print(f"{'':<{width}}    {detail}")
    print("=" * (width + 46))
    print(f"\n{seeded}/{len(results)} projects seeded   (data under {DEMO_HOME})")
    print("\nNext:  uv run python web/server.py")
    return 0 if seeded == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

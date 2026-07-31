"""Derive ``labels/expected_digest.json`` from labels + watchlist (EVALS §3, M4).

This module imports **nothing from ``src/tickerpress``**. It reads only
``labels/mentions.json``, ``labels/story_groups.json`` and ``watchlist.json``,
and reimplements the FR-8 relevance arithmetic from the hand-counted placement
facts, so the expected step-1 digest selection is a property of the fixture and
never of the system under test.

    label_relevance(base, ticker)       = round_half_up(100·(0.50·title_hit
                                          + 0.25·lede_hit + 0.25·min(1, n/4)))
    label_story_relevance(group,ticker) = max over the group's member articles
    expected = {(ticker, group) : mode ∈ {digest, both}
                                ∧ label_story_relevance ≥ min_relevance
                                ∧ (ticker, group) not already alerted}

Step 1 of the M4 scenario ingests with ``alert_channel=file`` and then runs the
file-channel digest, so a (ticker, group) that fired an alert is *already
delivered on that channel* and must not appear in the digest.

It also asserts the §4 relevance band rule: no labelled story may land within
±6 points of a floor the selection depends on, which is what makes the expected
set stable under a ±1-mention detection difference.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
BAND = 6


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def label_relevance(row: dict) -> int:
    return round_half_up(
        100.0
        * (
            0.50 * float(bool(row["title_hit"]))
            + 0.25 * float(bool(row["lede_hit"]))
            + 0.25 * min(1.0, int(row["mention_count"]) / 4.0)
        )
    )


def load(root: Path = HERE) -> tuple[dict, list[dict], dict]:
    watchlist = json.loads((root / "watchlist.json").read_text(encoding="utf-8"))
    labels = json.loads((root / "labels" / "mentions.json").read_text(encoding="utf-8"))["labels"]
    groups = json.loads((root / "labels" / "story_groups.json").read_text(encoding="utf-8"))
    return watchlist, labels, groups


def story_relevances(labels: list[dict], groups: dict) -> dict[tuple[str, int], int]:
    """Label-derived relevance per (ticker, syndication group)."""

    base_of = {int(k): v for k, v in groups["base_of"].items()}
    by_base: dict[int, list[dict]] = {}
    for row in labels:
        if row.get("present"):
            by_base.setdefault(row["base_id"], []).append(row)

    relevance: dict[tuple[str, int], int] = {}
    for article_id, base_id in sorted(base_of.items()):
        group = groups["groups"][str(article_id)]
        for row in by_base.get(base_id, []):
            key = (row["ticker"], group)
            relevance[key] = max(relevance.get(key, 0), label_relevance(row))
    return relevance


def derive(root: Path = HERE) -> dict:
    watchlist, labels, groups = load(root)
    companies = {company["ticker"]: company for company in watchlist["companies"]}
    relevance = story_relevances(labels, groups)

    violations: list[str] = []
    for (ticker, group), value in sorted(relevance.items()):
        company = companies[ticker]
        floors = [("min_relevance", company["min_relevance"])]
        if company["mode"] in ("alert", "both"):
            floors.append(("alert_min_relevance", company["alert_min_relevance"]))
        for name, floor in floors:
            if abs(value - floor) <= BAND:
                violations.append(
                    f"{ticker}/group {group}: relevance {value} sits within ±{BAND} of "
                    f"{name}={floor} (EVALS §4 band rule)"
                )
    if violations:
        raise SystemExit("relevance band rule violated:\n  " + "\n  ".join(violations))

    alerted = sorted(
        {
            (ticker, group)
            for (ticker, group), value in relevance.items()
            if companies[ticker]["mode"] in ("alert", "both")
            and value >= companies[ticker]["alert_min_relevance"]
        }
    )
    expected = sorted(
        {
            (ticker, group)
            for (ticker, group), value in relevance.items()
            if companies[ticker]["mode"] in ("digest", "both")
            and value >= companies[ticker]["min_relevance"]
            and (ticker, group) not in set(alerted)
        }
    )
    return {
        "_comment": [
            "Derived by derive_expected.py from labels + watchlist; hand-reviewed and",
            "committed. M4 step 1 asserts the engine's file-channel digest item set,",
            "mapped from story ids to group ids, equals `expected`.",
            "`alerted` is the step-1 alert set on the same channel (TSLA, mode=both).",
        ],
        "alerted": [{"ticker": t, "group_id": g} for t, g in alerted],
        "expected": [{"ticker": t, "group_id": g} for t, g in expected],
        "story_relevance": [
            {"ticker": t, "group_id": g, "relevance": relevance[(t, g)]}
            for t, g in sorted(relevance)
        ],
    }


def main() -> None:
    payload = derive()
    out = HERE / "labels" / "expected_digest.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(payload['expected'])} expected items, {len(payload['alerted'])} alerted")


if __name__ == "__main__":
    main()

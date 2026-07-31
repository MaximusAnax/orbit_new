"""Generate ``advice_scenarios.json`` — the 13 M8 prioritisation scenarios.

Run: ``uv run python chessmentor/evals/fixtures/generate_advice_scenarios.py``

Each scenario is a hand-authored set of games, analyses and flagged moves.  The
expected ranking is computed here by ``expected_ranking`` — an independent
implementation of FR-12's formula (``score(c) = sum of delta_w``, tie-broken by
count, then by recency of the worst instance, then by category name) and of
DATA_MODEL's advice-resolution rule (exact ``(category, dominant phase)`` beats
``(category, null)``) — and then **asserted against the hand-stated intent**
recorded on every scenario.  A scenario whose computed ranking disagrees with
its stated intent is a generator error and aborts the run, so the committed
truth is hand-computed, not implementation-derived.

The set deliberately covers what a wrong implementation gets wrong:

* scenarios 2-4: high-frequency/low-``delta_w`` against low-frequency/high
  ``delta_w`` (frequency-only ordering fails all three);
* scenarios 5-7: exact ties resolved by count, then recency, then name;
* scenarios 8-9: phase-specific advice resolution;
* scenario 10: imported games excluded unless asked for;
* scenario 11: a game with no report-basis analysis lands in ``skipped``;
* scenario 12: the window is capped at ``last_games``;
* scenario 13: a game carrying both ``internal @ JUDGE_BUDGET`` and
  ``stockfish @ DEEP_BUDGET`` — the report must be computed from the
  report-basis analysis only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.fixtures import write_fixture

SCENARIO_SEED = 20260731
CURRENT = "CURRENT"  # replaced with the running engine version by the metric
JUDGE_BUDGET = 6_000
DEEP_BUDGET = 20_000
ADVICE_PATH = Path(__file__).resolve().parents[2] / "data" / "advice.json"

PHASES = ("opening", "middlegame", "endgame")


def _move(ply: int, san: str, category: str, phase: str, dw: float) -> dict[str, object]:
    return {"ply": ply, "san": san, "category": category, "phase": phase, "delta_w": dw}


def _game(
    game_id: int,
    moves: list[dict[str, object]],
    *,
    source: str = "played",
    analyses: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if analyses is None:
        analyses = [
            {
                "analyst": "internal",
                "analyst_version": CURRENT,
                "node_budget": JUDGE_BUDGET,
                "moves": moves,
            }
        ]
    return {"game_id": game_id, "source": source, "analyses": analyses}


def scenarios() -> list[dict[str, object]]:
    data: list[dict[str, object]] = []

    # 1 — one dominant category.
    data.append(
        {
            "id": "as-01",
            "description": "a single category carries all the lost win probability",
            "games": [
                _game(
                    1,
                    [
                        _move(11, "Nd5", "hung_piece", "middlegame", 0.30),
                        _move(19, "Bg4", "hung_piece", "middlegame", 0.22),
                        _move(27, "Rc8", "positional_drift", "middlegame", 0.11),
                    ],
                )
            ],
            "intent": ["hung_piece", "positional_drift"],
        }
    )

    # 2-4 — frequency against magnitude.
    data.append(
        {
            "id": "as-02",
            "description": "six small hung pieces (0.72) lose to two huge missed tactics (0.90)",
            "games": [
                _game(
                    1,
                    [_move(10 + 2 * i, "Ne4", "hung_piece", "middlegame", 0.12) for i in range(6)]
                    + [
                        _move(31, "Qd2", "missed_tactic", "middlegame", 0.45),
                        _move(35, "Re1", "missed_tactic", "middlegame", 0.45),
                    ],
                )
            ],
            "intent": ["missed_tactic", "hung_piece"],
        }
    )
    data.append(
        {
            "id": "as-03",
            "description": "frequency-only ordering would put bad_trade first; delta_w says otherwise",
            "games": [
                _game(
                    1,
                    [_move(9 + 2 * i, "Bxc6", "bad_trade", "middlegame", 0.11) for i in range(5)]
                    + [
                        _move(29, "Kg1", "allowed_mate", "middlegame", 0.62),
                        _move(33, "h3", "positional_drift", "middlegame", 0.13),
                        _move(37, "a4", "positional_drift", "middlegame", 0.13),
                        _move(41, "b3", "positional_drift", "middlegame", 0.13),
                    ],
                )
            ],
            "intent": ["allowed_mate", "bad_trade", "positional_drift"],
        }
    )
    data.append(
        {
            "id": "as-04",
            "description": (
                "three categories where frequency-only ordering (3 > 2 > 1) is exactly "
                "backwards against the delta_w sums (0.41 > 0.40 > 0.39)"
            ),
            "games": [
                _game(
                    1,
                    [
                        _move(11, "Nxe5", "bad_trade", "middlegame", 0.40),
                        _move(15, "Nf3", "missed_tactic", "middlegame", 0.21),
                        _move(19, "Bd2", "missed_tactic", "middlegame", 0.20),
                        _move(23, "Rb1", "allowed_tactic", "middlegame", 0.15),
                        _move(27, "a3", "allowed_tactic", "middlegame", 0.14),
                        _move(31, "h4", "allowed_tactic", "middlegame", 0.10),
                    ],
                )
            ],
            "intent": ["missed_tactic", "bad_trade", "allowed_tactic"],
        }
    )

    # 5 — exact sum tie broken by count.
    data.append(
        {
            "id": "as-05",
            "description": "equal delta_w sums (0.60); the category with more instances ranks first",
            "games": [
                _game(
                    1,
                    [
                        _move(11, "Nd5", "hung_piece", "middlegame", 0.20),
                        _move(13, "Be3", "hung_piece", "middlegame", 0.20),
                        _move(15, "Rc1", "hung_piece", "middlegame", 0.20),
                        _move(17, "Qd3", "bad_trade", "middlegame", 0.30),
                        _move(19, "Rf1", "bad_trade", "middlegame", 0.30),
                    ],
                )
            ],
            "intent": ["hung_piece", "bad_trade"],
        }
    )

    # 6 — sum and count tie broken by recency of the worst instance.
    data.append(
        {
            "id": "as-06",
            "description": "equal sums and counts; the more recent worst instance ranks first",
            "games": [
                _game(
                    1,
                    [
                        _move(11, "Nd5", "hung_piece", "middlegame", 0.25),
                        _move(41, "Bg5", "missed_tactic", "middlegame", 0.25),
                    ],
                )
            ],
            "intent": ["missed_tactic", "hung_piece"],
        }
    )

    # 7 — sum, count and recency tie broken by category name.
    data.append(
        {
            "id": "as-07",
            "description": "a total tie falls through to alphabetical category order",
            "games": [
                _game(
                    1,
                    [
                        _move(21, "Nd5", "hung_piece", "middlegame", 0.25),
                        _move(21, "Bg5", "bad_trade", "middlegame", 0.25),
                    ],
                )
            ],
            "intent": ["bad_trade", "hung_piece"],
        }
    )

    # 8-9 — phase-specific advice resolution.
    data.append(
        {
            "id": "as-08",
            "description": "positional_drift concentrated in the endgame picks the endgame advice",
            "games": [
                _game(
                    1,
                    [
                        _move(61, "Kf1", "positional_drift", "endgame", 0.30),
                        _move(65, "Rb2", "positional_drift", "endgame", 0.28),
                        _move(21, "h3", "positional_drift", "middlegame", 0.11),
                    ],
                )
            ],
            "intent": ["positional_drift"],
        }
    )
    data.append(
        {
            "id": "as-09",
            "description": "the same category concentrated in the opening picks the opening advice",
            "games": [
                _game(
                    1,
                    [
                        _move(7, "Qh5", "positional_drift", "opening", 0.24),
                        _move(9, "Qf3", "positional_drift", "opening", 0.24),
                        _move(45, "Rd4", "positional_drift", "endgame", 0.14),
                    ],
                )
            ],
            "intent": ["positional_drift"],
        }
    )

    # 10 — imported games are excluded by default.
    data.append(
        {
            "id": "as-10",
            "description": "an imported game is out of the window unless include_imported is set",
            "include_imported": False,
            "games": [
                _game(1, [_move(11, "Nd5", "hung_piece", "middlegame", 0.20)]),
                _game(
                    2,
                    [_move(13, "Bg5", "missed_tactic", "middlegame", 0.90)],
                    source="imported",
                ),
            ],
            "intent": ["hung_piece"],
            "expected_skipped": [],
        }
    )

    # 11 — a game with no report-basis analysis is skipped and reported.
    data.append(
        {
            "id": "as-11",
            "description": "a stockfish-only analysis is not a report basis; the game is skipped",
            "games": [
                _game(1, [_move(11, "Nd5", "hung_piece", "middlegame", 0.20)]),
                _game(
                    2,
                    [],
                    analyses=[
                        {
                            "analyst": "stockfish",
                            "analyst_version": "Stockfish 16",
                            "node_budget": DEEP_BUDGET,
                            "moves": [
                                _move(13, "Bg5", "missed_tactic", "middlegame", 0.90),
                            ],
                        }
                    ],
                ),
            ],
            "intent": ["hung_piece"],
            "expected_skipped": [2],
        }
    )

    # 12 — the window is capped at last_games.
    data.append(
        {
            "id": "as-12",
            "description": "only the most recent two games enter the window",
            "last_games": 2,
            "games": [
                _game(1, [_move(11, "Nd5", "allowed_mate", "middlegame", 0.95)]),
                _game(2, [_move(13, "Bg5", "hung_piece", "middlegame", 0.30)]),
                _game(3, [_move(15, "Rc1", "missed_tactic", "middlegame", 0.20)]),
            ],
            "intent": ["hung_piece", "missed_tactic"],
        }
    )

    # 13 — the analysis-selection case.
    data.append(
        {
            "id": "as-13",
            "description": (
                "one game, two analyses: the report must use internal @ JUDGE_BUDGET, not the "
                "deeper stockfish re-analysis"
            ),
            "games": [
                _game(
                    1,
                    [],
                    analyses=[
                        {
                            "analyst": "internal",
                            "analyst_version": CURRENT,
                            "node_budget": JUDGE_BUDGET,
                            "moves": [
                                _move(11, "Nd5", "hung_piece", "middlegame", 0.30),
                                _move(15, "Bg5", "hung_piece", "middlegame", 0.25),
                                _move(19, "Rc1", "bad_trade", "middlegame", 0.20),
                            ],
                        },
                        {
                            "analyst": "stockfish",
                            "analyst_version": "Stockfish 16",
                            "node_budget": DEEP_BUDGET,
                            "moves": [
                                _move(11, "Nd5", "missed_tactic", "middlegame", 0.80),
                                _move(15, "Bg5", "missed_tactic", "middlegame", 0.70),
                                _move(19, "Rc1", "allowed_tactic", "middlegame", 0.60),
                            ],
                        },
                    ],
                )
            ],
            "intent": ["hung_piece", "bad_trade"],
        }
    )
    return data


# --------------------------------------------------------------------------- #
# Independent FR-12 aggregation
# --------------------------------------------------------------------------- #


def load_advice() -> list[dict[str, object]]:
    return json.loads(ADVICE_PATH.read_text(encoding="utf-8"))


def resolve_advice(catalog: list[dict[str, object]], category: str, phase: str | None) -> str:
    fallback: str | None = None
    for entry in catalog:
        if entry["category"] != category:
            continue
        if phase is not None and entry.get("phase") == phase:
            return str(entry["id"])
        if entry.get("phase") is None:
            fallback = str(entry["id"])
    if fallback is None:
        raise SystemExit(f"advice catalog has no phase-null entry for {category}")
    return fallback


def expected_ranking(scenario: dict[str, object]) -> tuple[list[dict[str, object]], list[int]]:
    """FR-12's window selection, priority formula and advice resolution."""
    include_imported = bool(scenario.get("include_imported", False))
    last_games = int(scenario.get("last_games", 10))
    catalog = load_advice()

    candidates = [
        game
        for game in scenario["games"]  # type: ignore[index]
        if include_imported or game["source"] == "played"
    ]
    candidates.sort(key=lambda g: g["game_id"], reverse=True)

    window: list[dict[str, object]] = []
    skipped: list[int] = []
    for game in candidates:
        if len(window) >= last_games:
            break
        eligible = [
            analysis
            for analysis in game["analyses"]  # type: ignore[index]
            if analysis["analyst"] == "internal"
            and analysis["node_budget"] == JUDGE_BUDGET
            and analysis["analyst_version"] == CURRENT
        ]
        if not eligible:
            skipped.append(int(game["game_id"]))
            continue
        window.append({"game": game, "analysis": eligible[0]})
    window.reverse()
    skipped.reverse()

    instances: list[tuple[int, int, int, str, float]] = []
    for order, entry in enumerate(window):
        for move in sorted(entry["analysis"]["moves"], key=lambda m: m["ply"]):  # type: ignore[index]
            instances.append(
                (
                    int(entry["game"]["game_id"]),  # type: ignore[index]
                    order,
                    int(move["ply"]),
                    str(move["category"]),
                    float(move["delta_w"]),
                )
            )

    grouped: dict[str, list[tuple[int, int, int, str, float]]] = {}
    phases: dict[str, dict[str, float]] = {}
    for entry in window:
        for move in entry["analysis"]["moves"]:  # type: ignore[index]
            phases.setdefault(str(move["category"]), {})
            phases[str(move["category"])][str(move["phase"])] = phases[str(move["category"])].get(
                str(move["phase"]), 0.0
            ) + float(move["delta_w"])
    for instance in instances:
        grouped.setdefault(instance[3], []).append(instance)

    scores = []
    for category, group in grouped.items():
        worst = max(group, key=lambda i: (i[4], i[1], i[2]))
        scores.append(
            {
                "category": category,
                "dw_sum": sum(i[4] for i in group),
                "count": len(group),
                "worst_order": worst[1],
                "worst_ply": worst[2],
            }
        )
    scores.sort(
        key=lambda s: (
            -s["dw_sum"],
            -s["count"],
            -s["worst_order"],
            -s["worst_ply"],
            s["category"],
        )
    )

    ranked: list[dict[str, object]] = []
    for rank, score in enumerate(scores[:3], start=1):
        totals = phases.get(str(score["category"]), {})
        dominant = (
            min(totals, key=lambda p: (-totals[p], PHASES.index(p))) if totals else None
        )
        ranked.append(
            {
                "rank": rank,
                "category": score["category"],
                "priority_score": round(float(score["dw_sum"]), 6),
                "dominant_phase": dominant,
                "advice_id": resolve_advice(catalog, str(score["category"]), dominant),
            }
        )
    return ranked, skipped


def build(seed: int) -> dict[str, object]:
    out: list[dict[str, object]] = []
    for scenario in scenarios():
        ranked, skipped = expected_ranking(scenario)
        intent = list(scenario.pop("intent"))  # type: ignore[arg-type]
        computed = [str(item["category"]) for item in ranked]
        if computed != intent[: len(computed)]:
            raise SystemExit(
                f"{scenario['id']}: hand-stated intent {intent} != computed {computed}"
            )
        expected_skipped = scenario.pop("expected_skipped", None)
        if expected_skipped is not None and list(expected_skipped) != skipped:  # type: ignore[arg-type]
            raise SystemExit(
                f"{scenario['id']}: expected skipped {expected_skipped} != computed {skipped}"
            )
        scenario["expected"] = ranked
        scenario["expected_skipped"] = skipped
        out.append(scenario)
    return {"seed": seed, "current_version_placeholder": CURRENT, "scenarios": out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SCENARIO_SEED)
    args = parser.parse_args()
    payload = build(args.seed)
    path = write_fixture("advice_scenarios.json", payload)
    print(f"wrote {path}: {len(payload['scenarios'])} scenarios")


if __name__ == "__main__":
    main()

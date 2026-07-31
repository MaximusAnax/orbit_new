"""Generate ``sim_players.json`` — the 62 simulated players M2/M3 run on.

Run: ``uv run python chessmentor/evals/fixtures/generate_sim_players.py``

Cohorts (EVALS.md M2):

| Cohort | n  | Games | ``R~`` (ACPL channel)                | Results channel |
|--------|----|-------|--------------------------------------|-----------------|
| base   | 40 | 20    | ``R*``                               | ``R*``          |
| jump   | 12 | 20    | ``R*``, +300 after game 10           | same ``R*``     |
| biased | 10 | 20    | ``R* - 250`` (systematic model bias) | ``R*``          |

``R*`` is drawn uniform on ``[elo_L1 + 80, elo_L10 - 80]`` **computed from
levels.json at generation time**, so the ladder's actual calibrated ends define
the population and a perfect controller's M3 ceiling is genuinely 1.0.  Jump
players' pre-jump ``R*`` is drawn from ``[elo_L1 + 80, elo_L10 - 380]`` so the
post-jump rating still lies inside the covered range.

Because the bounds are relative to the ladder, this fixture must be regenerated
whenever ``data/levels.json``'s calibrated ``elo_internal`` values change — the
file records both the expression and the materialised numbers so the dependency
is auditable.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chessmentor.datasets import load_datasets
from evals.fixtures import hash64, write_fixture

SIM_SEED = 20260731
GAMES_PER_PLAYER = 20
COHORTS = {"base": 40, "jump": 12, "biased": 10}
JUMP_AT_GAME = 10
JUMP_DELTA = 300.0
BIAS_DELTA = -250.0
MARGIN = 80.0
DRAW_PROBABILITY = 0.08


def build(seed: int) -> dict[str, object]:
    levels = load_datasets().levels
    lo = min(level.elo_internal for level in levels) + MARGIN
    hi = max(level.elo_internal for level in levels) - MARGIN
    jump_hi = max(level.elo_internal for level in levels) - (MARGIN + JUMP_DELTA)
    if jump_hi <= lo:
        raise SystemExit("ladder is too short for the jump cohort's pre-jump range")

    rng = random.Random(seed)
    players: list[dict[str, object]] = []
    index = 0
    for cohort, count in COHORTS.items():
        upper = jump_hi if cohort == "jump" else hi
        for _ in range(count):
            index += 1
            true_rating = rng.uniform(lo, upper)
            players.append(
                {
                    "id": f"sp-{index:03d}",
                    "cohort": cohort,
                    "true_rating": round(true_rating, 2),
                    "seed": hash64(seed, index, 0, 0),
                    "games": GAMES_PER_PLAYER,
                    "jump_after_game": JUMP_AT_GAME if cohort == "jump" else None,
                    "jump_delta": JUMP_DELTA if cohort == "jump" else 0.0,
                    "acpl_channel_bias": BIAS_DELTA if cohort == "biased" else 0.0,
                }
            )
    return {
        "seed": seed,
        "draw_probability": DRAW_PROBABILITY,
        "true_rating_bounds": {
            "expression": "[elo_L1 + 80, elo_L10 - 80] (jump cohort: elo_L10 - 380)",
            "lo": round(lo, 2),
            "hi": round(hi, 2),
            "jump_hi": round(jump_hi, 2),
        },
        "ladder_elo": {str(level.id): level.elo_internal for level in levels},
        "players": players,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SIM_SEED)
    args = parser.parse_args()
    payload = build(args.seed)
    path = write_fixture("sim_players.json", payload)
    print(f"wrote {path}: {len(payload['players'])} players")


if __name__ == "__main__":
    main()

"""Naive baselines for every metric, computed live — never hardcoded.

EVALS.md names one naive model per metric and requires the gate to sit
meaningfully above it.  Most of those models cost nothing extra: they are
alternative *readings* of data a metric already produced (the results-only
Glicko estimate is a column of the same rating event, the raw-centipawn tier is
the same analyst evaluation scored differently), so they are computed inside
``metrics.py`` and travel on ``MetricResult.baseline``.

The one baseline that needs its own games is M1a's **collapsed ladder** — "all
levels share one config", so no pair is separated.  ``collapsed_ladder`` plays
it for real: the same nine adjacent pairings from the same committed openings
and seeds, but with *both* sides configured as the lower level.  Its expectation
is 0.5 by symmetry, and measuring it live proves the harness and the metric are
not what produce M1a's separation.
"""

from __future__ import annotations

import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from chessmentor.adapters import InternalAnalyst
from chessmentor.datasets import load_datasets

from evals.fixtures import load_fixture
from evals.harness import play_ladder_game

__all__ = ["BaselineResult", "collapsed_ladder"]


@dataclass(frozen=True)
class BaselineResult:
    metric: str
    label: str
    value: float
    detail: str


def _collapsed_job(args: tuple[int, int, int, str, tuple[str, ...]]) -> tuple[int, float]:
    """One pair game with *both* sides configured as the lower level."""
    low, index, seed, opening_id, opening_uci = args
    data = load_datasets()
    level = data.level_by_id(low)
    game = play_ladder_game(
        level,
        level,
        opening_uci=list(opening_uci),
        opening_id=opening_id,
        seed=seed,
        book=data.book,
        referee=InternalAnalyst(),
    )
    # "The stronger level" of a collapsed pair is whichever colour stands in for
    # level i+1; index parity decides that exactly as it does in M1a.
    return low, (1.0 - game.white_score) if index % 2 == 0 else game.white_score


def collapsed_ladder(*, games_per_pair: int = 1, workers: int = 3) -> BaselineResult:
    """M1a under a ladder whose ten rungs all share one config."""
    fixture = load_fixture("ladder_openings.json")
    openings = fixture["openings"]
    levels = load_datasets().levels
    jobs: list[tuple[int, int, int, str, tuple[str, ...]]] = []
    for low in range(1, len(levels)):
        seeds = fixture["m1a_seeds"][f"{low}-{low + 1}"][:games_per_pair]
        for index, seed in enumerate(seeds):
            opening = openings[index % len(openings)]
            jobs.append((low, index, seed, opening["id"], tuple(opening["uci"])))

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(_collapsed_job, jobs, chunksize=1))
    else:
        outcomes = [_collapsed_job(job) for job in jobs]

    per_pair: dict[int, list[float]] = {}
    for low, score in outcomes:
        per_pair.setdefault(low, []).append(score)
    means = [statistics.fmean(scores) for scores in per_pair.values()]
    value = statistics.fmean(means)
    return BaselineResult(
        metric="M1a",
        label="collapsed ladder (all levels share one config)",
        value=value,
        detail=f"{len(jobs)} games, {len(means)} pairs",
    )

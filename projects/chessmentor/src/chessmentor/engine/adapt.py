"""FR-8 — the adaptive level controller (hard part B2).

Per challenge mode the target expected score is ``comfort`` 0.60, ``balanced``
0.50, ``stretch`` 0.42.  The **ideal opponent rating** is

    elo* = R_hat + 400 * log10((1 - target) / target)

(balanced → ``R_hat``; comfort → ``R_hat - 70.4``; stretch → ``R_hat + 56.0``).

Cold start, hysteresis, the placement-phase step limit and the ladder clamp are
all invariants here, not implementer choices.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ..constants import (
    LEVEL_HYSTERESIS,
    MODE_TARGETS,
    NORMAL_MAX_STEP,
    PLACEMENT_GAMES,
    PLACEMENT_MAX_STEP,
    R_INIT,
)
from ..models import ChallengeMode, Level

__all__ = [
    "cold_start_level_id",
    "expected_score",
    "ideal_opponent_elo",
    "max_step_for",
    "nearest_level_id",
    "recommend_level_id",
    "target_score",
]


def target_score(mode: ChallengeMode) -> float:
    """The mode's target expected score (FR-8)."""
    return MODE_TARGETS[str(mode)]


def ideal_opponent_elo(r_hat: float, mode: ChallengeMode) -> float:
    """``elo* = R_hat + 400 * log10((1 - target) / target)``."""
    t = target_score(mode)
    return r_hat + 400.0 * math.log10((1.0 - t) / t)


def expected_score(r_hat: float, level_elo: float) -> float:
    """Elo expectation of the player against an opponent rated ``level_elo``."""
    return 1.0 / (1.0 + 10.0 ** ((level_elo - r_hat) / 400.0))


def nearest_level_id(levels: Sequence[Level], elo_star: float) -> int:
    """Level minimising ``|elo_internal - elo_star|``; ties → lower id."""
    if not levels:
        raise ValueError("the ladder is empty")
    return min(levels, key=lambda lv: (abs(lv.elo_internal - elo_star), lv.id)).id


def cold_start_level_id(levels: Sequence[Level], mode: ChallengeMode) -> int:
    """FR-8 cold-start invariant: the recommendation for ``R_hat = R_INIT``."""
    return nearest_level_id(levels, ideal_opponent_elo(R_INIT, mode))


def max_step_for(rated_games: int) -> int:
    """2 level steps during the 4-game placement phase, 1 afterwards."""
    return PLACEMENT_MAX_STEP if rated_games < PLACEMENT_GAMES else NORMAL_MAX_STEP


def recommend_level_id(
    levels: Sequence[Level],
    *,
    current_level_id: int,
    r_hat: float,
    mode: ChallengeMode,
    rated_games: int,
) -> int:
    """The level to play next.

    Keeps the current level while ``|E - target| <= 0.05`` (hysteresis);
    otherwise walks at most ``max_step_for(rated_games)`` rungs toward the level
    nearest ``elo*``, clamped to the ladder ends.  Never called mid-game.
    """
    by_id = {lv.id: lv for lv in levels}
    if current_level_id not in by_id:
        raise KeyError(f"no such level: {current_level_id}")
    lowest = min(by_id)
    highest = max(by_id)

    current_e = expected_score(r_hat, by_id[current_level_id].elo_internal)
    if abs(current_e - target_score(mode)) <= LEVEL_HYSTERESIS:
        return current_level_id

    desired = nearest_level_id(levels, ideal_opponent_elo(r_hat, mode))
    step = max(
        -max_step_for(rated_games), min(max_step_for(rated_games), desired - current_level_id)
    )
    return max(lowest, min(highest, current_level_id + step))

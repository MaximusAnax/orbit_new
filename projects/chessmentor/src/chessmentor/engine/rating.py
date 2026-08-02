"""FR-7 — rating estimation (hard part B1).

After each rated game finishes, in this exact order:

(a) **Results channel — Glicko-1** (Glickman 1999) against the CPU's
    ``game.level_elo`` snapshot with opponent RD = ``OPP_RD``.  Before the
    update the plain Elo expectation ``E_i`` is pushed onto a rolling
    ``surprise_window``; when that window is full, ``|sum| >= SURPRISE_THRESHOLD``
    and no inflation has fired within the last ``SURPRISE_WINDOW`` rated games,
    RD is re-inflated to ``RD_INFLATE_TO`` and the window cleared.  This is
    Glickman's RD-inflation step with *games* as the clock, driven by the
    results channel alone.

(b) **Move-quality channel.** The judge pass's ACPL is interpolated through the
    calibrated ``(acpl_mean_L, elo_L)`` anchors, clamped to ``PERF_CLAMP``, and
    folded into an EWMA at ``EWMA_ALPHA``.

(c) **Precision-weighted blend.** ``lambda = sigma_p^2 / (sigma_p^2 + rd_after^2)``
    and ``R_hat = lambda * glicko_rating + (1 - lambda) * perf_ewma``.  Lambda is
    *not* a function of game count: a regime change inflates RD, which
    automatically hands weight back to the fast channel.

(d) **Divergence warning.** Persistent channel disagreement sets
    ``calibration_warning``; it is a diagnostic and changes no math.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..constants import (
    DIVERGENCE_ELO,
    DIVERGENCE_STREAK,
    EWMA_ALPHA,
    OPP_RD,
    PERF_CLAMP,
    PERF_SIGMA,
    PERF_SIGMA_1,
    R_INIT,
    RD_FLOOR,
    RD_INFLATE_TO,
    RD_INIT,
    SURPRISE_THRESHOLD,
    SURPRISE_WINDOW,
)
from ..models import ChallengeMode, Level, RatingEvent, RatingState
from .adapt import recommend_level_id

__all__ = [
    "GLICKO_Q",
    "apply_rated_game",
    "blend_lambda",
    "blended_rating",
    "expected_score",
    "glicko_g",
    "glicko_update",
    "initial_rating_state",
    "perf_rating_from_acpl",
]

#: Glicko-1's ``q = ln(10) / 400``.
GLICKO_Q = math.log(10.0) / 400.0

_EPSILON = 1e-9


def expected_score(rating: float, opponent_elo: float) -> float:
    """Plain Elo expectation ``E_i = 1/(1+10^((opponent - rating)/400))`` (FR-7a)."""
    return 1.0 / (1.0 + 10.0 ** ((opponent_elo - rating) / 400.0))


def glicko_g(rd: float) -> float:
    """Glicko-1's ``g(RD)`` attenuation of an uncertain opponent."""
    return 1.0 / math.sqrt(1.0 + 3.0 * GLICKO_Q**2 * rd**2 / math.pi**2)


def glicko_update(
    rating: float,
    rd: float,
    *,
    opponent_elo: float,
    opponent_rd: float,
    score: float,
) -> tuple[float, float]:
    """One Glicko-1 single-game update, RD clamped to ``[RD_FLOOR, RD_INIT]``."""
    g = glicko_g(opponent_rd)
    e = 1.0 / (1.0 + 10.0 ** (-g * (rating - opponent_elo) / 400.0))
    e = min(1.0 - _EPSILON, max(_EPSILON, e))
    d_squared = 1.0 / (GLICKO_Q**2 * g**2 * e * (1.0 - e))
    denominator = 1.0 / (rd**2) + 1.0 / d_squared
    new_rating = rating + (GLICKO_Q / denominator) * g * (score - e)
    new_rd = math.sqrt(1.0 / denominator)
    return new_rating, min(RD_INIT, max(RD_FLOOR, new_rd))


def perf_rating_from_acpl(acpl: float, levels: Sequence[Level]) -> float:
    """Interpolate ACPL through the calibrated ``(acpl_mean, elo_internal)`` anchors.

    ``acpl_mean`` is strictly decreasing in level id (a ladder invariant), so the
    anchors sorted by ACPL are strictly decreasing in Elo and the interpolation is
    single-valued.  Outside the anchor range the nearest segment's slope is
    extrapolated; the result is clamped to ``PERF_CLAMP``.
    """
    if not levels:
        raise ValueError("the ladder is empty")
    anchors = sorted(((lv.acpl_mean, lv.elo_internal) for lv in levels), key=lambda a: a[0])
    lo, hi = PERF_CLAMP

    if len(anchors) == 1:
        return min(hi, max(lo, anchors[0][1]))

    if acpl <= anchors[0][0]:
        (x0, y0), (x1, y1) = anchors[0], anchors[1]
    elif acpl >= anchors[-1][0]:
        (x0, y0), (x1, y1) = anchors[-2], anchors[-1]
    else:
        index = 0
        for i in range(len(anchors) - 1):
            if anchors[i][0] <= acpl <= anchors[i + 1][0]:
                index = i
                break
        (x0, y0), (x1, y1) = anchors[index], anchors[index + 1]

    slope = (y1 - y0) / (x1 - x0)
    value = y0 + slope * (acpl - x0)
    return min(hi, max(lo, value))


def blend_lambda(rd: float, judged_games: int) -> float:
    """``lambda = sigma_p^2 / (sigma_p^2 + rd^2)`` (FR-7c)."""
    sigma_p = PERF_SIGMA_1 if judged_games == 1 else PERF_SIGMA
    return sigma_p**2 / (sigma_p**2 + rd**2)


def blended_rating(state: RatingState) -> float:
    """``R_hat``, derived on read; falls back to Glicko while ``perf_ewma`` is null."""
    if state.perf_ewma is None:
        return state.glicko_rating
    lam = blend_lambda(state.glicko_rd, state.judged_games)
    return lam * state.glicko_rating + (1.0 - lam) * state.perf_ewma


def initial_rating_state(*, current_level_id: int, updated_at: str) -> RatingState:
    """The pre-first-game state: ``R_hat = glicko_rating = R_INIT``."""
    return RatingState(
        glicko_rating=R_INIT,
        glicko_rd=RD_INIT,
        perf_ewma=None,
        judged_games=0,
        rated_games=0,
        surprise_window=[],
        # Inflation is allowed as soon as a full window is available.
        games_since_rd_inflation=SURPRISE_WINDOW,
        divergence_streak=0,
        calibration_warning=False,
        current_level_id=current_level_id,
        last_game_id=None,
        updated_at=updated_at,
    )


@dataclass(frozen=True)
class RatingOutcome:
    """The new state plus the append-only event that produced it."""

    state: RatingState
    event: RatingEvent


def apply_rated_game(
    state: RatingState,
    *,
    game_id: int,
    result_score: float,
    opponent_elo: float,
    level_played: int,
    perf_game: float | None,
    levels: Sequence[Level],
    mode: ChallengeMode,
    now: str,
) -> RatingOutcome:
    """Apply one rated game's evidence, in FR-7's exact order.

    ``perf_game`` is the performance rating derived from the rating-basis
    analysis (``perf_rating_from_acpl`` of its ACPL).  It is ``None`` only in the
    degenerate case where the judge pass had *no* non-book player moves to
    measure (every player move was inside the opening book); the move-quality
    channel then contributes nothing — the EWMA and ``judged_games`` are left
    untouched and the event records the unchanged EWMA — while the results
    channel still updates.
    """
    if result_score not in (0.0, 0.5, 1.0):
        raise ValueError("result_score must be 1, 0.5 or 0")
    if state.last_game_id is not None and game_id <= state.last_game_id:
        raise ValueError(
            f"rating events must be applied in game-termination order: game {game_id} "
            f"follows {state.last_game_id}"
        )

    r_before = state.glicko_rating
    rd_before = state.glicko_rd

    # --- (a) results channel ------------------------------------------------
    expected = expected_score(r_before, opponent_elo)
    window = [*state.surprise_window, result_score - expected][-SURPRISE_WINDOW:]
    surprise_after = math.fsum(window)

    rd_for_update = rd_before
    rd_inflated = (
        len(window) == SURPRISE_WINDOW
        and abs(surprise_after) >= SURPRISE_THRESHOLD
        and state.games_since_rd_inflation >= SURPRISE_WINDOW
    )
    if rd_inflated:
        rd_for_update = min(RD_INIT, max(rd_before, RD_INFLATE_TO))
        window = []
        games_since_inflation = 0
    else:
        games_since_inflation = state.games_since_rd_inflation + 1

    r_after, rd_after = glicko_update(
        r_before,
        rd_for_update,
        opponent_elo=opponent_elo,
        opponent_rd=OPP_RD,
        score=result_score,
    )

    # --- (b) move-quality channel ------------------------------------------
    if perf_game is None:
        judged_games = state.judged_games
        perf_ewma_after = state.perf_ewma if state.perf_ewma is not None else R_INIT
        perf_game_recorded = perf_ewma_after
    else:
        judged_games = state.judged_games + 1
        if state.perf_ewma is None:
            perf_ewma_after = perf_game
        else:
            perf_ewma_after = EWMA_ALPHA * perf_game + (1.0 - EWMA_ALPHA) * state.perf_ewma
        perf_game_recorded = perf_game

    # --- (c) precision-weighted blend --------------------------------------
    if judged_games == 0:
        # No move-quality evidence exists yet; the results channel is all there is.
        lam = 1.0
        r_hat_after = r_after
    else:
        lam = blend_lambda(rd_after, judged_games)
        r_hat_after = lam * r_after + (1.0 - lam) * perf_ewma_after

    # --- (d) divergence warning --------------------------------------------
    if abs(perf_ewma_after - r_after) > DIVERGENCE_ELO:
        divergence_streak = state.divergence_streak + 1
    else:
        divergence_streak = 0
    calibration_warning = divergence_streak >= DIVERGENCE_STREAK

    rated_games = state.rated_games + 1
    level_next = recommend_level_id(
        levels,
        current_level_id=state.current_level_id,
        r_hat=r_hat_after,
        mode=mode,
        rated_games=rated_games,
    )

    new_state = RatingState(
        glicko_rating=r_after,
        glicko_rd=rd_after,
        perf_ewma=perf_ewma_after if judged_games > 0 else None,
        judged_games=judged_games,
        rated_games=rated_games,
        surprise_window=window,
        games_since_rd_inflation=games_since_inflation,
        divergence_streak=divergence_streak,
        calibration_warning=calibration_warning,
        current_level_id=level_next,
        last_game_id=game_id,
        updated_at=now,
    )
    event = RatingEvent(
        game_id=game_id,
        created_at=now,
        result_score=result_score,
        opponent_elo=opponent_elo,
        expected_score=expected,
        surprise_after=surprise_after,
        rd_inflated=rd_inflated,
        glicko_r_before=r_before,
        glicko_rd_before=rd_before,
        glicko_r_after=r_after,
        glicko_rd_after=rd_after,
        perf_game=perf_game_recorded,
        perf_ewma_after=perf_ewma_after,
        lambda_used=lam,
        r_hat_after=r_hat_after,
        level_played=level_played,
        level_next=level_next,
    )
    return RatingOutcome(state=new_state, event=event)

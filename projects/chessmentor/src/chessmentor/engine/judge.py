"""FR-9 — post-game judgment.

For each player move the ``Analyst`` evaluates the pre-move position (best move,
score, PV) and the post-move position, from the player's perspective:

    cp_loss = min(CP_LOSS_CAP, max(0, cp_best - cp_played))
    w(cp)   = 1 / (1 + e^(-WIN_K * cp))            on [0, 1]
    delta_w = max(0, w_before - w_after)

severity: blunder ``>= SEV_BLUNDER``, mistake ``>= SEV_MISTAKE``, inaccuracy
``>= SEV_INACCURACY``, else ok.

**Book-ply exclusion rule (invariant, applied identically here and in FR-5):**
moves at ``ply <= book_depth`` are still analysed and stored (with
``in_acpl = false``) but are excluded from ACPL, accuracy, per-phase aggregates
and key moments.  Without this, a player who knows theory gets an ACPL deflated
relative to the calibration anchors, which are measured the same way.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

import chess

from ..constants import (
    CP_LOSS_CAP,
    JUDGE_BUDGET,
    KEY_MOMENTS,
    WIN_K,
)
from ..models import (
    FLAGGED_SEVERITIES,
    AnalystKind,
    Color,
    GameAnalysis,
    KeyMoment,
    Level,
    Motif,
    MoveAnalysis,
    Phase,
    PhaseStats,
    Severity,
    severity_for,
)
from .phase import PhaseBoundaries, compute_boundaries
from .rating import perf_rating_from_acpl
from .taxonomy import MoveContext, classify

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the engine import-pure
    from ..adapters.analyst import Analyst

__all__ = [
    "included_move_count",
    "judge_game",
    "move_accuracy",
    "win_probability",
]

#: Lichess accuracy curve, defined on win-*percentage* points.
_ACC_A = 103.1668
_ACC_B = 0.04354
_ACC_C = 3.1669

#: Guard against overflow in ``exp`` for mate-magnitude scores.
_EXP_LIMIT = 60.0

_PV_MAX_PLIES = 6


def win_probability(cp: int) -> float:
    """``w(cp) = 1/(1 + e^(-WIN_K*cp))`` on [0, 1]; mate scores map to ~0/1."""
    exponent = -WIN_K * cp
    if exponent >= _EXP_LIMIT:
        return 0.0
    if exponent <= -_EXP_LIMIT:
        return 1.0
    return 1.0 / (1.0 + math.exp(exponent))


def move_accuracy(delta_w: float) -> float:
    """Lichess per-move accuracy from ``delta_w`` (converted to win-% points)."""
    delta_wp = 100.0 * delta_w
    value = _ACC_A * math.exp(-_ACC_B * delta_wp) - _ACC_C
    return min(100.0, max(0.0, value))


def included_move_count(analysis: GameAnalysis) -> int:
    """Player moves that entered the aggregates (``in_acpl``)."""
    return sum(1 for move in analysis.moves if move.in_acpl)


def _san_line(board: chess.Board, pv: Sequence[str], limit: int = _PV_MAX_PLIES) -> list[str]:
    """Render a UCI principal variation as SAN from ``board``."""
    probe = board.copy(stack=False)
    line: list[str] = []
    for uci in list(pv)[:limit]:
        move = chess.Move.from_uci(uci)
        if move not in probe.legal_moves:
            break
        line.append(probe.san(move))
        probe.push(move)
    return line


def judge_game(
    *,
    uci_moves: Sequence[str],
    player_color: Color,
    book_depth: int,
    analyst: Analyst,
    levels: Sequence[Level],
    created_at: str,
    node_budget: int = JUDGE_BUDGET,
    is_rating_basis: bool = False,
    game_id: int | None = None,
    boundaries: PhaseBoundaries | None = None,
) -> GameAnalysis:
    """Run the full FR-9 judge pass over one game and build its ``GameAnalysis``."""
    if book_depth < 0:
        raise ValueError("book_depth must be >= 0")
    player_is_white = player_color is Color.WHITE
    bounds = boundaries or compute_boundaries(uci_moves, book_depth)

    board = chess.Board()
    move_analyses: list[MoveAnalysis] = []
    prior_moves: list[str] = []

    for ply, uci in enumerate(uci_moves, start=1):
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {uci} at ply {ply}")
        mover_is_white = board.turn == chess.WHITE
        if mover_is_white is not player_is_white:
            board.push(move)
            prior_moves.append(uci)
            continue

        board_before = board.copy(stack=False)
        before = analyst.analyse(board_before, node_budget=node_budget)
        cp_best = before.score_cp
        best_uci = before.best_move or move.uci()

        board.push(move)
        board_after = board.copy(stack=False)
        after = analyst.analyse(board_after, node_budget=node_budget)
        # ``after`` is scored from the opponent's perspective; flip it.
        cp_played = -after.score_cp

        cp_loss = min(CP_LOSS_CAP, max(0, cp_best - cp_played))
        w_before = win_probability(cp_best)
        w_after = win_probability(cp_played)
        delta_w = max(0.0, w_before - w_after)
        severity = severity_for(delta_w)
        in_acpl = ply > book_depth
        move_phase = bounds.phase_at(ply)

        category = None
        motif_value = Motif.NONE
        evidence = None
        if in_acpl and severity in FLAGGED_SEVERITIES:
            classification = classify(
                MoveContext(
                    board_before=board_before,
                    move=move,
                    player_color=chess.WHITE if player_is_white else chess.BLACK,
                    phase=move_phase,
                    cp_best=cp_best,
                    cp_played=cp_played,
                    w_before=w_before,
                    w_after=w_after,
                    best_uci=before.best_move,
                    best_pv=tuple(before.pv),
                    reply_pv=tuple(after.pv),
                    prior_moves=tuple(prior_moves),
                )
            )
            category = classification.category
            motif_value = classification.motif
            evidence = classification.evidence

        move_analyses.append(
            MoveAnalysis(
                ply=ply,
                in_acpl=in_acpl,
                cp_best=cp_best,
                cp_played=cp_played,
                cp_loss=cp_loss,
                w_before=w_before,
                w_after=w_after,
                delta_w=delta_w,
                severity=severity,
                best_uci=best_uci,
                best_line_san=_san_line(board_before, before.pv),
                phase=move_phase,
                category=category,
                motif=motif_value,
                evidence=evidence,
            )
        )
        prior_moves.append(uci)

    included = [m for m in move_analyses if m.in_acpl]
    # Degenerate case: every player move was inside the opening book, so there is
    # nothing to measure.  ACPL/accuracy/perf follow the formulas on an empty set
    # rather than inventing a special number; consumers detect it with
    # ``included_move_count(analysis) == 0`` and the rating layer then skips the
    # move-quality channel entirely (``apply_rated_game(perf_game=None)``).
    acpl = _mean([m.cp_loss for m in included], default=0.0)
    accuracy = _mean([move_accuracy(m.delta_w) for m in included], default=100.0)
    perf = perf_rating_from_acpl(acpl, levels)

    key_moments = [
        KeyMoment(ply=m.ply, dw=m.delta_w, severity=m.severity)
        for m in sorted(included, key=lambda m: (-m.delta_w, m.ply))[:KEY_MOMENTS]
    ]

    return GameAnalysis(
        game_id=game_id,
        analyst=AnalystKind(analyst.kind),
        analyst_version=analyst.version,
        node_budget=node_budget,
        created_at=created_at,
        book_depth=book_depth,
        is_rating_basis=is_rating_basis,
        acpl=acpl,
        accuracy=accuracy,
        perf_rating=perf,
        n_blunders=sum(1 for m in included if m.severity is Severity.BLUNDER),
        n_mistakes=sum(1 for m in included if m.severity is Severity.MISTAKE),
        n_inaccuracies=sum(1 for m in included if m.severity is Severity.INACCURACY),
        mg_start_ply=bounds.mg_start_ply,
        eg_start_ply=bounds.eg_start_ply,
        per_phase=_per_phase(included),
        key_moments=key_moments,
        moves=move_analyses,
    )


def _mean(values: Sequence[float], *, default: float) -> float:
    if not values:
        return default
    return math.fsum(values) / len(values)


def _per_phase(included: Sequence[MoveAnalysis]) -> dict[Phase, PhaseStats]:
    stats: dict[Phase, PhaseStats] = {}
    for phase in Phase:
        moves = [m for m in included if m.phase is phase]
        if not moves:
            continue
        stats[phase] = PhaseStats(
            acpl=_mean([m.cp_loss for m in moves], default=0.0),
            accuracy=_mean([move_accuracy(m.delta_w) for m in moves], default=100.0),
            n_blunders=sum(1 for m in moves if m.severity is Severity.BLUNDER),
            n_mistakes=sum(1 for m in moves if m.severity is Severity.MISTAKE),
            n_inaccuracies=sum(1 for m in moves if m.severity is Severity.INACCURACY),
            dw_sum=math.fsum(m.delta_w for m in moves),
            n_moves=len(moves),
        )
    return stats

"""FR-4 — the difficulty throttle (hard part A).

Exactly one code path chooses every CPU move:

0. If exactly one legal move exists, play it (``depth = 0``, ``nodes = 0``).
1. **Book.** If ``ply <= book_plies`` and the book returns >= 1 entry, play a
   seeded weighted choice among them; no search runs.
2. **Root scoring.** One iterative-deepening pass under the single budget
   ``node_budget``, full window at the root, producing one comparable score
   vector ``s(.)`` from the last fully completed iteration.
3. **Blunder injection.** ``u ~ U(0,1)``; if ``u < blunder_prob`` form
   ``C = {m : best - margin_hi <= s(m) <= best - margin_lo}`` and, when it is
   non-empty, play ``argmin_{m in C} s(m)`` (ties → lowest UCI), skipping step 4.
4. **Noise.** Otherwise draw ``eps_m ~ N(0, noise_sigma_cp)`` for each root move
   in ascending-UCI order and play ``argmax_m (s(m) + eps_m)`` (ties → lowest
   UCI).  With ``noise_sigma_cp == 0`` no draws are consumed.

Every comparison in steps 3-4 uses the step-2 vector ``s(.)`` — one depth, one
budget, no mixed-depth comparisons and no second search.

All randomness comes from a fresh ``random.Random(seed_ply)`` per ply with
``seed_ply = (game_seed XOR (ply * 0x9E3779B97F4A7C15)) mod 2^64``, consumed in
exactly the order above, so replays are exact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import chess

from ..constants import PLY_SEED_MULTIPLIER, UINT64_MASK
from ..models import CpuMeta, Level
from .search import SearchConfig, search

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the engine import-pure
    from ..adapters.book import OpeningBook

__all__ = ["CpuChoice", "ply_seed", "choose_cpu_move"]


@dataclass(frozen=True)
class CpuChoice:
    """The CPU's move for one ply plus its throttle metadata."""

    move: chess.Move
    is_book: bool
    meta: CpuMeta


def ply_seed(game_seed: int, ply: int) -> int:
    """Derive the per-ply RNG substream seed (FR-4)."""
    if ply < 1:
        raise ValueError("ply is 1-based")
    return (game_seed ^ (ply * PLY_SEED_MULTIPLIER)) & UINT64_MASK


def _weighted_choice(uci_weights: list[tuple[str, int]], rng: random.Random) -> str:
    """Seeded weighted choice consuming exactly one draw, in ascending-UCI order."""
    ordered = sorted(uci_weights)
    total = sum(weight for _, weight in ordered)
    threshold = rng.random() * total
    cumulative = 0.0
    for uci, weight in ordered:
        cumulative += weight
        if threshold < cumulative:
            return uci
    return ordered[-1][0]


def _argmax_uci(scores: dict[str, int]) -> str:
    """argmax over the score vector, ties broken by lowest UCI."""
    return min(scores, key=lambda uci: (-scores[uci], uci))


def choose_cpu_move(
    board: chess.Board,
    level: Level,
    *,
    game_seed: int,
    ply: int,
    book: OpeningBook | None = None,
) -> CpuChoice:
    """Choose the CPU's move for ``board`` at ``ply`` under ``level``'s throttle."""
    legal = list(board.legal_moves)
    if not legal:
        raise ValueError("no legal move available")

    rng = random.Random(ply_seed(game_seed, ply))

    # --- step 0: forced move ------------------------------------------------
    if len(legal) == 1:
        return CpuChoice(
            move=legal[0],
            is_book=False,
            meta=CpuMeta(depth=0, nodes=0, root_moves=1),
        )

    # --- step 1: book -------------------------------------------------------
    if book is not None and ply <= level.book_plies:
        entries = book.probe(board)
        if entries:
            legal_uci = {m.uci() for m in legal}
            candidates = [(e.uci, e.weight) for e in entries if e.uci in legal_uci]
            if candidates:
                chosen_uci = _weighted_choice(candidates, rng)
                return CpuChoice(
                    move=chess.Move.from_uci(chosen_uci),
                    is_book=True,
                    meta=CpuMeta(depth=0, nodes=0, root_moves=len(legal)),
                )

    # --- step 2: one root pass, one budget ----------------------------------
    result = search(board, SearchConfig(max_depth=level.max_depth), level.node_budget)
    scores = result.root_scores
    best_uci = _argmax_uci(scores)
    best_score = scores[best_uci]

    # --- step 3: blunder injection -----------------------------------------
    roll = rng.random()
    blunder_rolled = roll < level.blunder_prob
    if blunder_rolled and level.blunder_margin_lo_cp is not None:
        lo = best_score - level.blunder_margin_hi_cp  # type: ignore[operator]
        hi = best_score - level.blunder_margin_lo_cp
        window = {uci: s for uci, s in scores.items() if lo <= s <= hi}
        if window:
            chosen_uci = min(window, key=lambda uci: (window[uci], uci))
            return CpuChoice(
                move=chess.Move.from_uci(chosen_uci),
                is_book=False,
                meta=CpuMeta(
                    depth=result.depth,
                    nodes=result.nodes,
                    root_moves=len(scores),
                    score_cp=scores[chosen_uci],
                    best_score_cp=best_score,
                    blunder_rolled=True,
                    blunder_injected=True,
                    noise_changed_pick=False,
                ),
            )

    # --- step 4: noise ------------------------------------------------------
    if level.noise_sigma_cp > 0:
        noisy: dict[str, float] = {}
        for uci in sorted(scores):
            noisy[uci] = scores[uci] + rng.gauss(0.0, level.noise_sigma_cp)
        chosen_uci = min(noisy, key=lambda uci: (-noisy[uci], uci))
    else:
        chosen_uci = best_uci

    return CpuChoice(
        move=chess.Move.from_uci(chosen_uci),
        is_book=False,
        meta=CpuMeta(
            depth=result.depth,
            nodes=result.nodes,
            root_moves=len(scores),
            score_cp=scores[chosen_uci],
            best_score_cp=best_score,
            blunder_rolled=blunder_rolled,
            blunder_injected=False,
            noise_changed_pick=chosen_uci != best_uci,
        ),
    )

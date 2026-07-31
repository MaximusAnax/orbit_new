"""Generate ``judgment_cases.json`` — the 120 constructed M4 severity cases.

Run: ``uv run python chessmentor/evals/fixtures/generate_judgment.py --seed 20260731``

Construction, exactly as EVALS.md specifies it:

1. A **quiet base position** is composed from a committed skeleton plus a
   material-offset recipe, and ``truth.quiet_by_see`` asserts EVALS.md's
   quietness condition on it: no capture with ``|SEE| > 0`` exists for either
   side (exactly even captures are permitted).
2. Every legal move is scored by ``truth.forced_value``: a full-width negamax
   over python-chess move generation with a material-only leaf score, i.e. the
   **forced material delta within 4 plies**.  ``cp_best`` is the best move's
   value and ``cp_played`` the played move's — both by exhaustive enumeration,
   never by the engine under evaluation.
3. The truth tier is ``SEV_*`` applied to ``Δw`` of those two numbers.
4. **Robustness is verified numerically per case:** the tier must survive a
   ±40 cp common-mode shift jointly with a ±20 cp differential error in all
   four sign combinations (``truth.survives_perturbation``).  Cases that fail
   are refused, and among the survivors the generator prefers the largest
   differential margin, which is free headroom for M4.

Composition: 40 ok / 20 inaccuracy / 25 mistake / 35 blunder, of which at least
20 are **decided-position traps**: ``|cp_best| >= 700`` with a truth tier of ok
or inaccuracy while the naive raw-centipawn thresholds (50/100/300) would
assign a strictly higher tier.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.fixtures import write_fixture
from evals.fixtures.truth import (
    PIECE_VALUE,
    delta_w,
    forced_value,
    perturbation_margin,
    quiet_by_see,
    severity_of,
    shallow_value,
    survives_perturbation,
)

JUDGMENT_SEED = 20260731

#: Contact-free skeletons.  Each is verified quiet at generation time.
SKELETONS: list[tuple[str, str]] = [
    ("closed-classical", "r1bqkb1r/pp3ppp/2nppn2/8/8/2NPPN2/PP3PPP/R1BQKB1R w KQkq - 0 1"),
    ("closed-bishops", "r2qk2r/pppbbppp/2np1n2/8/8/2NP1N2/PPPBBPPP/R2QK2R w KQkq - 0 1"),
    ("double-fianchetto", "r1bq1rk1/pp2ppbp/2np1np1/8/8/2NP1NP1/PP2PPBP/R1BQ1RK1 w - - 0 1"),
    ("castled-classical", "r1bq1rk1/pp3ppp/2nppn2/8/8/2NPPN2/PP3PPP/R1BQ1RK1 w - - 0 1"),
    ("stonewall", "r1bqk2r/pp1n1ppp/4pn2/3p4/3P4/4PN2/PP1N1PPP/R1BQK2R w KQkq - 0 1"),
    ("open-c-rooks", "2rq1rk1/pp2ppbp/3p1np1/8/8/3P1NP1/PP2PPBP/2RQ1RK1 w - - 0 1"),
    ("open-d-queens", "r1b2rk1/pp2ppbp/2n2np1/8/8/2N2NP1/PP2PPBP/R1B2RK1 w - - 0 1"),
    ("queenless", "r1b2rk1/pp2ppbp/2np1np1/8/8/2NP1NP1/PP2PPBP/R1B2RK1 w - - 0 1"),
    ("heavy-back-rank", "r2qkb1r/pppb1ppp/2n1pn2/8/8/2N1PN2/PPPB1PPP/R2QKB1R w KQkq - 0 1"),
    ("open-c-file", "r1bqk2r/pp2bppp/2nppn2/8/8/2NPPN2/PP2BPPP/R1BQK2R w KQkq - 0 1"),
    # Lighter skeletons: at JUDGE_BUDGET the analyst completes far deeper
    # iterations here, so the shallow-resolvability filter keeps a rich pool
    # (the heavy skeletons alone cannot fill every tier once unresolvable
    # quiet-refutation cases are excluded — build-stage finding B4).
    ("rook-minor-mid", "2r1r1k1/pp3ppp/2n5/8/8/2N5/PP3PPP/2R1R1K1 w - - 0 1"),
    ("queenless-light", "r4rk1/pp2ppbp/2n3p1/8/8/2N3P1/PP2PPBP/R4RK1 w - - 0 1"),
    ("two-rook-bishop", "2r3k1/pp2bppp/8/8/8/8/PP2BPPP/2R3K1 w - - 0 1"),
    ("knight-ending", "6k1/pp3ppp/2n5/8/8/2N5/PP3PPP/6K1 w - - 0 1"),
    ("bishop-ending", "6k1/pp3ppp/4b3/8/8/4B3/PP3PPP/6K1 w - - 0 1"),
    ("rook-ending", "3r2k1/pp3ppp/8/8/8/8/PP3PPP/2R3K1 w - - 0 1"),
    ("queen-ending", "3q2k1/pp3ppp/8/8/8/8/PP3PPP/2Q3K1 w - - 0 1"),
]

#: Removal recipes: symbolic piece removals that create a material offset.
#: ``w``/``b`` prefixes name the side losing the material.
RECIPES: list[tuple[str, tuple[str, ...]]] = [
    ("level", ()),
    ("w-p1", ("wP",)),
    ("b-p1", ("bP",)),
    ("w-p2", ("wP", "wP")),
    ("b-p2", ("bP", "bP")),
    ("w-p3", ("wP", "wP", "wP")),
    ("b-p3", ("bP", "bP", "bP")),
    ("w-p4", ("wP", "wP", "wP", "wP")),
    ("b-p4", ("bP", "bP", "bP", "bP")),
    ("w-n", ("wN",)),
    ("b-n", ("bN",)),
    ("w-r", ("wR",)),
    ("b-r", ("bR",)),
    ("w-n-p1", ("wN", "wP")),
    ("b-n-p1", ("bN", "bP")),
    ("w-r-p2", ("wR", "wP", "wP")),
    ("b-r-p2", ("bR", "bP", "bP")),
    ("w-q", ("wQ",)),
    ("b-q", ("bQ",)),
    ("w-nn", ("wN", "wN")),
    ("b-nn", ("bN", "bN")),
    ("w-rn", ("wR", "wN")),
    ("b-rn", ("bR", "bN")),
    ("w-rr", ("wR", "wR")),
    ("b-rr", ("bR", "bR")),
    ("w-qp2", ("wQ", "wP", "wP")),
    ("b-qp2", ("bQ", "bP", "bP")),
    ("w-b", ("wB",)),
    ("b-b", ("bB",)),
    ("w-bp1", ("wB", "wP")),
    ("b-bp1", ("bB", "bP")),
]

_SYMBOL = {
    "P": chess.PAWN,
    "N": chess.KNIGHT,
    "B": chess.BISHOP,
    "R": chess.ROOK,
    "Q": chess.QUEEN,
}

TIER_TARGETS = {"ok": 40, "inaccuracy": 20, "mistake": 25, "blunder": 35}
TRAP_TARGET = 20
DECIDED_CP = 700
#: The naive raw-centipawn baseline EVALS.md names, used to certify the traps.
RAW_TIERS = ((300, "blunder"), (100, "mistake"), (50, "inaccuracy"))


def raw_cp_tier(cp_loss: int) -> str:
    for threshold, tier in RAW_TIERS:
        if cp_loss >= threshold:
            return tier
    return "ok"


_TIER_RANK = {"ok": 0, "inaccuracy": 1, "mistake": 2, "blunder": 3}


def apply_recipe(board: chess.Board, recipe: tuple[str, ...]) -> chess.Board | None:
    """Remove the named pieces, farthest-from-the-king first, deterministically."""
    work = board.copy(stack=False)
    for token in recipe:
        colour = chess.WHITE if token[0] == "w" else chess.BLACK
        piece_type = _SYMBOL[token[1]]
        squares = sorted(work.pieces(piece_type, colour))
        if not squares:
            return None
        # Deterministic pick: the piece nearest the a-file on the outermost rank.
        target = squares[0] if colour == chess.WHITE else squares[-1]
        work.remove_piece_at(target)
    work.set_castling_fen(_surviving_castling(work))
    return work


def _surviving_castling(board: chess.Board) -> str:
    rights = ""
    if board.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE):
        if board.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights += "K"
        if board.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights += "Q"
    if board.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK):
        if board.piece_at(chess.H8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights += "k"
        if board.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights += "q"
    return rights or "-"


@dataclass(frozen=True)
class Candidate:
    skeleton: str
    recipe: str
    fen: str
    played_uci: str
    played_san: str
    best_uci: str
    cp_best: int
    cp_played: int
    cp_loss: int
    delta_w: float
    tier: str
    margin: int
    trap: bool


def scan_position(
    skeleton: str, recipe_name: str, board: chess.Board, *, depth: int
) -> list[Candidate]:
    """Score every legal move by exhaustive material minimax and build candidates."""
    if board.is_check() or not board.is_valid() or not quiet_by_see(board):
        return []
    values: dict[chess.Move, int] = {}
    for move in board.legal_moves:
        board.push(move)
        values[move] = -forced_value(board, depth - 1).value
        board.pop()
    if not values:
        return []
    best_move = min(values, key=lambda m: (-values[m], m.uci()))
    cp_best = values[best_move]
    if abs(cp_best) > 5_000:  # a mate is in view: not a quiet material fixture
        return []

    # Resolvability by construction (EVALS.md D14: cases must be solvable
    # inside the analyst's horizon).  The 4-ply full-width truth is only fair
    # if a *depth-2 + capture-quiescence* material search — the weakest view
    # the analyst can take of a heavy position at JUDGE_BUDGET — reaches the
    # same tier.  Cases whose refutations need quiet moves at ply 3-4 of a
    # 28-piece position are refused here rather than blamed on the analyst.
    shallow_best = shallow_value(board)

    candidates: list[Candidate] = []
    for move, value in values.items():
        loss = cp_best - value
        if loss < 0 or abs(value) > 5_000:
            continue
        drop = delta_w(cp_best, value)
        tier = severity_of(drop)
        if not survives_perturbation(cp_best, value):
            continue
        board.push(move)
        shallow_played = -shallow_value(board)
        board.pop()
        if severity_of(delta_w(shallow_best, shallow_played)) != tier:
            continue
        if not survives_perturbation(shallow_best, shallow_played):
            continue
        capped_loss = min(1_000, loss)
        candidates.append(
            Candidate(
                skeleton=skeleton,
                recipe=recipe_name,
                fen=board.fen(),
                played_uci=move.uci(),
                played_san=board.san(move),
                best_uci=best_move.uci(),
                cp_best=cp_best,
                cp_played=value,
                cp_loss=capped_loss,
                delta_w=drop,
                tier=tier,
                margin=perturbation_margin(cp_best, value),
                trap=(
                    abs(cp_best) >= DECIDED_CP
                    and tier in {"ok", "inaccuracy"}
                    and _TIER_RANK[raw_cp_tier(capped_loss)] > _TIER_RANK[tier]
                ),
            )
        )
    return candidates


def collect(depth: int) -> list[Candidate]:
    found: list[Candidate] = []
    for skeleton_name, fen in SKELETONS:
        base = chess.Board(fen)
        if not quiet_by_see(base):
            raise AssertionError(f"skeleton {skeleton_name} is not quiet")
        for recipe_name, recipe in RECIPES:
            positioned = apply_recipe(base, recipe)
            if positioned is None:
                continue
            for turn in (chess.WHITE, chess.BLACK):
                work = positioned.copy(stack=False)
                work.turn = turn
                found.extend(scan_position(skeleton_name, recipe_name, work, depth=depth))
    return found


def _rationale(case: Candidate) -> str:
    return (
        f"{case.skeleton}/{case.recipe}: forced material {case.cp_best} cp before, "
        f"{case.cp_played} cp after {case.played_san} (loss {case.cp_loss} cp, "
        f"dW {case.delta_w:.3f}); tier survives +/-40 cp common-mode with "
        f"+/-{case.margin} cp differential"
    )


def select(candidates: list[Candidate], rng: random.Random) -> list[Candidate]:
    """Fill the committed composition, preferring robust and varied cases."""
    by_tier: dict[str, list[Candidate]] = {tier: [] for tier in TIER_TARGETS}
    for case in candidates:
        by_tier[case.tier].append(case)

    chosen: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    used_positions: dict[str, int] = {}

    def take(pool: list[Candidate], count: int, *, per_position: int) -> int:
        taken = 0
        for case in pool:
            if taken >= count:
                break
            key = (case.fen, case.played_uci)
            if key in seen:
                continue
            if used_positions.get(case.fen, 0) >= per_position:
                continue
            seen.add(key)
            used_positions[case.fen] = used_positions.get(case.fen, 0) + 1
            chosen.append(case)
            taken += 1
        return taken

    # Traps first: they are the scarce, deliberately adversarial cases.
    traps = sorted(
        (c for c in candidates if c.trap), key=lambda c: (-c.margin, c.fen, c.played_uci)
    )
    rng.shuffle(traps)
    traps.sort(key=lambda c: -c.margin)
    trap_ok = [c for c in traps if c.tier == "ok"]
    trap_inacc = [c for c in traps if c.tier == "inaccuracy"]
    take(trap_inacc, min(TIER_TARGETS["inaccuracy"], TRAP_TARGET // 2), per_position=2)
    take(trap_ok, TRAP_TARGET - len([c for c in chosen if c.trap]), per_position=2)

    for tier, target in TIER_TARGETS.items():
        pool = sorted(by_tier[tier], key=lambda c: (-c.margin, c.fen, c.played_uci))
        rng.shuffle(pool)
        pool.sort(key=lambda c: -c.margin)
        have = sum(1 for c in chosen if c.tier == tier)
        for per_position in (1, 2, 3, 6):
            if have >= target:
                break
            have += take(pool, target - have, per_position=per_position)
    return chosen


def build(seed: int, depth: int) -> dict[str, object]:
    rng = random.Random(seed)
    candidates = collect(depth)
    chosen = select(candidates, rng)
    counts: dict[str, int] = {}
    for case in chosen:
        counts[case.tier] = counts.get(case.tier, 0) + 1
    traps = sum(1 for c in chosen if c.trap)
    missing = {t: TIER_TARGETS[t] - counts.get(t, 0) for t in TIER_TARGETS}
    if any(value > 0 for value in missing.values()):
        raise SystemExit(f"composition not met: {missing} (found {len(candidates)} candidates)")
    if traps < TRAP_TARGET:
        raise SystemExit(f"only {traps} decided-position traps, need {TRAP_TARGET}")

    chosen.sort(key=lambda c: (c.tier, c.fen, c.played_uci))
    return {
        "seed": seed,
        "forced_depth_plies": depth,
        "composition": counts,
        "traps": traps,
        "cases": [
            {
                "id": f"jc-{index:03d}",
                "fen": case.fen,
                "played_uci": case.played_uci,
                "played_san": case.played_san,
                "best_uci": case.best_uci,
                "truth_cp_best": case.cp_best,
                "truth_cp_played": case.cp_played,
                "truth_cp_loss": case.cp_loss,
                "truth_delta_w": round(case.delta_w, 6),
                "truth_tier": case.tier,
                "raw_cp_tier": raw_cp_tier(case.cp_loss),
                "decided_trap": case.trap,
                "differential_margin_cp": case.margin,
                "rationale": _rationale(case),
            }
            for index, case in enumerate(chosen, start=1)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=JUDGMENT_SEED)
    parser.add_argument("--depth", type=int, default=4)
    args = parser.parse_args()
    payload = build(args.seed, args.depth)
    path = write_fixture("judgment_cases.json", payload)
    print(f"wrote {path}: {payload['composition']} ({payload['traps']} traps)")


if __name__ == "__main__":
    main()

"""Generate ``phase_games.json`` — 20 constructed games with truth phase boundaries.

Run: ``uv run python chessmentor/evals/fixtures/generate_phase_games.py``

Each game is *constructed*, not played by the engine under evaluation:

1. a **book prefix** taken verbatim from the committed ``data/openings.json``,
   so the out-of-book ply is known before a single move is generated;
2. a **development block** driven by a fixture policy that only ever plays a
   minor piece off its home square to a safe square — this is what makes the
   ``middlegame_start`` ply a property of the script;
3. a **trade block** driven by a fixture policy that plays the highest-value
   non-losing capture of a non-pawn piece, or manoeuvres a piece into position
   to offer such a trade — this is what walks the non-pawn/non-king piece count
   down across the ``<= 6`` threshold at a scripted point;
4. a short quiet tail so the endgame boundary is not the last ply.

Both policies live here and use nothing but python-chess.  The truth boundaries
are then computed by ``reference_boundaries`` below — an independent
re-implementation of the FR-10 rule written from SCOPE.md's text, which never
imports ``chessmentor.engine.phase``.  Exactly 18 of the 20 games are required
to reach an endgame (38 labelled boundaries in total, as M6 states).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import chess

if __package__ in (None, ""):  # script entry: make ``evals`` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chessmentor.datasets import load_datasets
from evals.fixtures import hash64, write_fixture
from evals.fixtures.truth import PIECE_VALUE, see

PHASE_SEED = 20260731
N_GAMES = 20
N_WITH_ENDGAME = 18
MAX_PLIES = 150
QUIET_TAIL = 6

# --- the FR-10 rule, re-implemented from SCOPE.md (no engine import) --------- #

MINOR_HOME = {
    chess.WHITE: frozenset({chess.B1, chess.C1, chess.F1, chess.G1}),
    chess.BLACK: frozenset({chess.B8, chess.C8, chess.F8, chess.G8}),
}
MIN_DEVELOPED = 2
MIDDLEGAME_FULLMOVE = 10
ENDGAME_MAX_PIECES = 6


def developed(board: chess.Board, colour: chess.Color) -> int:
    return sum(
        1
        for piece_type in (chess.KNIGHT, chess.BISHOP)
        for square in board.pieces(piece_type, colour)
        if square not in MINOR_HOME[colour]
    )


def non_pawn_pieces(board: chess.Board) -> int:
    return sum(
        len(board.pieces(piece_type, colour))
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for colour in (chess.WHITE, chess.BLACK)
    )


def reference_boundaries(uci_moves: list[str], book_depth: int) -> tuple[int | None, int | None]:
    """FR-10 boundaries, computed independently of ``engine/phase.py``."""
    board = chess.Board()
    mg: int | None = None
    eg: int | None = None
    for ply, uci in enumerate(uci_moves, start=1):
        board.push(chess.Move.from_uci(uci))
        out_of_book = ply > book_depth
        both_developed = (
            developed(board, chess.WHITE) >= MIN_DEVELOPED
            and developed(board, chess.BLACK) >= MIN_DEVELOPED
        )
        if mg is None and out_of_book and (both_developed or board.fullmove_number >= MIDDLEGAME_FULLMOVE):
            mg = ply
        if mg is not None and eg is None and ply >= mg and non_pawn_pieces(board) <= ENDGAME_MAX_PIECES:
            eg = ply
    return mg, eg


# --- fixture move policies --------------------------------------------------- #


def _safe_after(board: chess.Board, move: chess.Move) -> bool:
    """True when the moved piece is not simply lost on its destination square."""
    probe = board.copy(stack=False)
    probe.push(move)
    for reply in probe.legal_moves:
        if reply.to_square == move.to_square and probe.is_capture(reply) and see(probe, reply) > 0:
            return False
    return True


def developing_move(board: chess.Board) -> chess.Move | None:
    """Lowest-UCI safe move of a minor piece off its home square."""
    options = [
        move
        for move in board.legal_moves
        if move.from_square in MINOR_HOME[board.turn]
        and board.piece_type_at(move.from_square) in (chess.KNIGHT, chess.BISHOP)
        and not board.is_capture(move)
        and not board.gives_check(move)
        and _safe_after(board, move)
    ]
    options.sort(key=lambda m: m.uci())
    return options[0] if options else None


def _victim_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return PIECE_VALUE[chess.PAWN]
    piece = board.piece_type_at(move.to_square)
    return PIECE_VALUE[piece] if piece else 0


def trading_capture(board: chess.Board) -> chess.Move | None:
    """Highest-value non-losing capture of a non-pawn piece."""
    options = [
        move
        for move in board.legal_moves
        if board.is_capture(move)
        and not board.is_en_passant(move)
        and board.piece_type_at(move.to_square) != chess.PAWN
        and see(board, move) >= 0
    ]
    options.sort(key=lambda m: (-_victim_value(board, m), m.uci()))
    return options[0] if options else None


def trade_offer(board: chess.Board) -> chess.Move | None:
    """Move a piece so it attacks an enemy piece of at least equal value."""
    best: tuple[int, str, chess.Move] | None = None
    for move in board.legal_moves:
        mover = board.piece_type_at(move.from_square)
        if mover in (None, chess.PAWN, chess.KING):
            continue
        if board.is_capture(move) or board.gives_check(move):
            continue
        probe = board.copy(stack=False)
        probe.push(move)
        targets = probe.attacks(move.to_square) & probe.occupied_co[not probe.turn ^ True]
        gain = 0
        for square in probe.attacks(move.to_square):
            piece = probe.piece_at(square)
            if piece is None or piece.color == board.turn or piece.piece_type == chess.PAWN:
                continue
            if PIECE_VALUE[piece.piece_type] >= PIECE_VALUE[mover]:
                gain = max(gain, PIECE_VALUE[piece.piece_type])
        del targets
        if gain and _safe_after(board, move):
            key = (-gain, move.uci(), move)
            if best is None or key[:2] < best[:2]:
                best = key
    return best[2] if best else None


def quiet_move(board: chess.Board, rng: random.Random) -> chess.Move | None:
    options = [
        move
        for move in board.legal_moves
        if not board.gives_check(move) and _safe_after(board, move)
    ]
    if not options:
        options = list(board.legal_moves)
    if not options:
        return None
    options.sort(key=lambda m: m.uci())
    return options[rng.randrange(len(options))]


def construct_game(
    opening_uci: list[str], seed: int, *, want_endgame: bool
) -> list[str] | None:
    """Build one constructed game; ``None`` when the script cannot be completed."""
    rng = random.Random(seed)
    board = chess.Board()
    moves: list[str] = []
    for uci in opening_uci:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return None
        board.push(move)
        moves.append(uci)

    stage = "develop"
    tail = 0
    while len(moves) < MAX_PLIES:
        if board.outcome(claim_draw=True) is not None:
            break
        move: chess.Move | None = None
        if stage == "develop":
            if (
                developed(board, chess.WHITE) >= MIN_DEVELOPED
                and developed(board, chess.BLACK) >= MIN_DEVELOPED
            ):
                stage = "trade" if want_endgame else "quiet"
            else:
                move = developing_move(board)
                if move is None:
                    stage = "trade" if want_endgame else "quiet"
        if move is None and stage == "trade":
            if non_pawn_pieces(board) <= ENDGAME_MAX_PIECES:
                stage = "tail"
            else:
                move = trading_capture(board) or trade_offer(board)
        if move is None and stage == "tail":
            tail += 1
            if tail > QUIET_TAIL:
                break
            move = quiet_move(board, rng)
        if move is None and stage == "quiet":
            tail += 1
            if tail > QUIET_TAIL * 3:
                break
            move = quiet_move(board, rng)
        if move is None:
            move = quiet_move(board, rng)
        if move is None:
            break
        board.push(move)
        moves.append(move.uci())

    if board.outcome(claim_draw=True) is not None and len(moves) < 20:
        return None
    return moves


def build(seed: int) -> dict[str, object]:
    data = load_datasets()
    book = data.book
    lines = sorted(book.lines, key=lambda line: (line.eco, line.name, "".join(line.uci)))

    games: list[dict[str, object]] = []
    attempt = 0
    endgames = 0
    while len(games) < N_GAMES and attempt < 4_000:
        index = attempt
        attempt += 1
        line = lines[index % len(lines)]
        want_endgame = endgames < N_WITH_ENDGAME
        moves = construct_game(list(line.uci), hash64(seed, index, 0, 1), want_endgame=want_endgame)
        if moves is None or len(moves) < 24:
            continue
        opening = book.identify(moves)
        book_depth = opening.depth if opening else 0
        mg, eg = reference_boundaries(moves, book_depth)
        if mg is None:
            continue
        if want_endgame and eg is None:
            continue
        if not want_endgame and eg is not None:
            continue
        if eg is not None:
            endgames += 1
        games.append(
            {
                "id": f"pg-{len(games) + 1:02d}",
                "opening_eco": line.eco,
                "opening_name": line.name,
                "book_prefix_plies": len(line.uci),
                "book_depth": book_depth,
                "uci_moves": moves,
                "mg_start_ply": mg,
                "eg_start_ply": eg,
                "rationale": (
                    f"book prefix {len(line.uci)} plies (identified depth {book_depth}); "
                    f"development block ends at ply {mg}; "
                    + (
                        f"trade block crosses <= {ENDGAME_MAX_PIECES} non-pawn pieces at ply {eg}"
                        if eg
                        else "no trade block: the endgame threshold is never crossed"
                    )
                ),
            }
        )
    if len(games) < N_GAMES:
        raise SystemExit(f"only constructed {len(games)} games")
    labelled = sum(1 for g in games if g["mg_start_ply"]) + sum(
        1 for g in games if g["eg_start_ply"]
    )
    if endgames != N_WITH_ENDGAME:
        raise SystemExit(f"{endgames} games reach an endgame, need {N_WITH_ENDGAME}")
    return {"seed": seed, "labelled_boundaries": labelled, "games": games}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=PHASE_SEED)
    args = parser.parse_args()
    payload = build(args.seed)
    path = write_fixture("phase_games.json", payload)
    print(f"wrote {path}: {payload['labelled_boundaries']} labelled boundaries")


if __name__ == "__main__":
    main()

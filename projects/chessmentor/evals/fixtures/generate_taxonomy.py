"""Generate ``taxonomy_cases.json`` — the 63 constructed M5 cases (>= 7 per class).

Run: ``uv run python chessmentor/evals/fixtures/generate_taxonomy.py``

Ground truth is produced by ``reference_classify`` below: an **independent**
implementation of the FR-11 precedence table written from SCOPE.md's rule text,
which uses only python-chess plus ``truth.py`` (exhaustive material minimax,
static exchange evaluation, attack maps).  It never imports
``chessmentor.engine.taxonomy`` and never consults the analyst, so a case's
label is a property of the position, not of the classifier under evaluation.

Two construction paths, because the nine categories split cleanly in two:

* **Tactical classes (rules 1-6).**  Positions are composed by seeded random
  placement; every legal move is scored by exhaustive 4-ply material minimax,
  the best line and the refutation line are read off that same search, and the
  precedence table is applied.  Whatever the table says is the truth — including
  the deliberate precedence collisions (a losing capture that also hangs the
  capturing piece must come out ``bad_trade``, because rule 3 precedes rule 4).

* **Phase classes (rules 7-9).**  These fire exactly when *no* tactical rule
  does, so their cases are built as **inert** positions: the played move is not
  a capture, no mate exists for either side within the horizon, no opponent
  reply is a capture with ``SEE >= +100``, and no opponent reply creates fork /
  pin / skewer geometry.  Inertness is verified exhaustively over *every* legal
  reply, so rules 1-6 cannot fire whatever line the analyst picks — which makes
  the declared ``phase`` (plus, for ``opening_principle``, a scripted violation
  of (a), (b) or (c)) the sole determinant of the label.
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
from evals.fixtures.truth import MATE_VALUE, PIECE_VALUE, forced_value, see

TAXONOMY_SEED = 20260731
PER_CATEGORY = 7
MATE_THRESHOLD = MATE_VALUE - 1_000
BAD_TRADE_SEE_CP = -100
HUNG_PIECE_SEE_CP = 100
MISSED_TACTIC_CP = 200
MISSED_TACTIC_MATERIAL_CP = 200
FORK_MIN_PIECE_VALUE = 300
SEARCH_DEPTH = 4

CATEGORIES = [
    "allowed_mate",
    "missed_mate",
    "bad_trade",
    "hung_piece",
    "missed_tactic",
    "allowed_tactic",
    "endgame_technique",
    "opening_principle",
    "positional_drift",
]
TACTICAL = CATEGORIES[:6]


# --------------------------------------------------------------------------- #
# Independent search with a principal variation
# --------------------------------------------------------------------------- #


def _pv_negamax(
    board: chess.Board, depth: int, alpha: int, beta: int, ply: int
) -> tuple[int, list[chess.Move]]:
    if board.is_checkmate():
        return -(MATE_VALUE - ply), []
    if board.is_stalemate() or board.is_insufficient_material():
        return 0, []
    if depth == 0:
        return _material(board), []
    best = -MATE_VALUE
    best_line: list[chess.Move] = []
    moves = sorted(
        board.legal_moves,
        key=lambda m: (
            -(PIECE_VALUE.get(t, 0) if (t := board.piece_type_at(m.to_square)) else 0),
            m.uci(),
        ),
    )
    for move in moves:
        board.push(move)
        score, line = _pv_negamax(board, depth - 1, -beta, -alpha, ply + 1)
        board.pop()
        score = -score
        if score > best or (score == best and not best_line):
            best = score
            best_line = [move, *line]
        alpha = max(alpha, best)
        if alpha >= beta:
            break
    return best, best_line


def _material(board: chess.Board) -> int:
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        if piece_type is chess.KING:
            continue
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total -= value * len(board.pieces(piece_type, chess.BLACK))
    return total if board.turn == chess.WHITE else -total


def search_pv(board: chess.Board, depth: int = SEARCH_DEPTH) -> tuple[int, list[str]]:
    value, line = _pv_negamax(board.copy(stack=False), depth, -MATE_VALUE, MATE_VALUE, 0)
    return value, [move.uci() for move in line]


def material_for(board: chess.Board, colour: chess.Color) -> int:
    total = 0
    for piece_type, value in PIECE_VALUE.items():
        if piece_type is chess.KING:
            continue
        total += value * len(board.pieces(piece_type, colour))
        total -= value * len(board.pieces(piece_type, not colour))
    return total


def pv_material_delta(board: chess.Board, pv: list[str], colour: chess.Color, plies: int) -> int:
    probe = board.copy(stack=False)
    before = material_for(probe, colour)
    for uci in pv[:plies]:
        move = chess.Move.from_uci(uci)
        if move not in probe.legal_moves:
            break
        probe.push(move)
    return material_for(probe, colour) - before


# --------------------------------------------------------------------------- #
# Independent geometry detectors (FR-11 motif definitions)
# --------------------------------------------------------------------------- #

SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)


def detect_fork(board_after: chess.Board, square: int, mover: chess.Color) -> dict | None:
    """Moved piece attacks >= 2 enemy pieces worth >= 300, or any piece + king."""
    targets: list[tuple[str, int]] = []
    king_attacked = False
    for attacked in board_after.attacks(square):
        piece = board_after.piece_at(attacked)
        if piece is None or piece.color == mover:
            continue
        if piece.piece_type == chess.KING:
            king_attacked = True
            continue
        targets.append((chess.square_name(attacked), PIECE_VALUE[piece.piece_type]))
    heavy = [t for t in targets if t[1] >= FORK_MIN_PIECE_VALUE]
    if len(heavy) >= 2 or (king_attacked and targets):
        return {
            "motif": "fork",
            "fork_square": chess.square_name(square),
            "attacked": sorted(name for name, _ in targets),
            "attacks_king": king_attacked,
        }
    return None


def detect_pin_or_skewer(
    board_before: chess.Board, board_after: chess.Board, move: chess.Move, mover: chess.Color
) -> dict | None:
    """A slider newly aligned through two enemy pieces on one ray."""
    piece = board_after.piece_at(move.to_square)
    if piece is None or piece.piece_type not in SLIDERS:
        return None
    for direction in _ray_directions(piece.piece_type):
        line = _scan_ray(board_after, move.to_square, direction)
        if len(line) < 2:
            continue
        front_square, back_square = line[0], line[1]
        front = board_after.piece_at(front_square)
        back = board_after.piece_at(back_square)
        if front is None or back is None:
            continue
        if front.color == mover or back.color == mover:
            continue
        if _already_aligned(board_before, move.from_square, front_square, back_square, direction):
            continue
        front_value = PIECE_VALUE[front.piece_type]
        back_value = PIECE_VALUE[back.piece_type]
        motif = "pin" if front_value < back_value else "skewer"
        return {
            "motif": motif,
            "front": chess.square_name(front_square),
            "back": chess.square_name(back_square),
            "front_value": front_value,
            "back_value": back_value,
        }
    return None


def _ray_directions(piece_type: chess.PieceType) -> tuple[int, ...]:
    diagonal = (9, 7, -7, -9)
    straight = (8, 1, -1, -8)
    if piece_type == chess.BISHOP:
        return diagonal
    if piece_type == chess.ROOK:
        return straight
    return diagonal + straight


def _scan_ray(board: chess.Board, origin: int, direction: int) -> list[int]:
    found: list[int] = []
    square = origin
    while True:
        nxt = square + direction
        if not 0 <= nxt < 64:
            break
        if abs(chess.square_file(nxt) - chess.square_file(square)) > 1:
            break
        square = nxt
        if board.piece_at(square) is not None:
            found.append(square)
            if len(found) == 2:
                break
    return found


def _already_aligned(
    board_before: chess.Board, from_square: int, front: int, back: int, direction: int
) -> bool:
    """True when the same two pieces were already on this ray before the move."""
    line = _scan_ray(board_before, from_square, direction)
    return len(line) >= 2 and line[0] == front and line[1] == back


# --------------------------------------------------------------------------- #
# The independent FR-11 precedence table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Label:
    category: str
    motif: str
    rationale: str


def reference_classify(
    board_before: chess.Board,
    move: chess.Move,
    *,
    phase: str,
    prior_moves: list[str],
    depth: int = SEARCH_DEPTH,
) -> Label | None:
    """FR-11's rules, applied in order, using exhaustive material analysis only."""
    mover = board_before.turn
    board_after = board_before.copy(stack=False)
    board_after.push(move)

    cp_best, best_pv = search_pv(board_before, depth)
    reply_value, reply_pv = search_pv(board_after, depth - 1)
    cp_played = -reply_value

    # 1 / 2 — mate rules.
    if cp_played <= -MATE_THRESHOLD and cp_best > -MATE_THRESHOLD:
        return Label("allowed_mate", "none", f"forced mate against the mover after {move.uci()}")
    if cp_best >= MATE_THRESHOLD and cp_played < MATE_THRESHOLD:
        return Label("missed_mate", "none", f"forced mate available with {best_pv[0]}")

    # 3 — bad trade.
    if board_before.is_capture(move):
        value = see(board_before, move)
        if value <= BAD_TRADE_SEE_CP:
            return Label("bad_trade", "none", f"capture {move.uci()} has SEE {value} cp")

    # 4 — hung piece.
    if reply_pv:
        reply = chess.Move.from_uci(reply_pv[0])
        if reply in board_after.legal_moves and board_after.is_capture(reply):
            see_after = see(board_after, reply)
            if see_after >= HUNG_PIECE_SEE_CP:
                probe = board_before.copy(stack=False)
                probe.turn = not probe.turn
                probe.ep_square = None
                see_before = see(probe, reply) if reply in probe.legal_moves else None
                if see_before is None or see_before < HUNG_PIECE_SEE_CP:
                    return Label(
                        "hung_piece",
                        "none",
                        f"reply {reply.uci()} wins material (SEE {see_after} cp, "
                        f"before {see_before})",
                    )

    # 5 — missed tactic.
    if cp_best - cp_played >= MISSED_TACTIC_CP and best_pv:
        delta = pv_material_delta(board_before, best_pv, mover, 4)
        if delta >= MISSED_TACTIC_MATERIAL_CP:
            best_move = chess.Move.from_uci(best_pv[0])
            probe = board_before.copy(stack=False)
            probe.push(best_move)
            motif = "other"
            fork = detect_fork(probe, best_move.to_square, mover)
            if fork is not None:
                motif = "fork"
            else:
                geometry = detect_pin_or_skewer(board_before, probe, best_move, mover)
                if geometry is not None:
                    motif = geometry["motif"]
                elif board_before.is_capture(best_move) and see(board_before, best_move) >= HUNG_PIECE_SEE_CP:
                    motif = "hanging_capture"
            return Label(
                "missed_tactic",
                motif,
                f"best {best_pv[0]} wins {delta} cp of material in 4 plies",
            )

    # 6 — allowed tactic.
    if reply_pv:
        reply = chess.Move.from_uci(reply_pv[0])
        if reply in board_after.legal_moves:
            probe = board_after.copy(stack=False)
            probe.push(reply)
            fork = detect_fork(probe, reply.to_square, not mover)
            motif = None
            if fork is not None:
                motif = "fork"
            else:
                geometry = detect_pin_or_skewer(board_after, probe, reply, not mover)
                if geometry is not None:
                    motif = geometry["motif"]
            if motif is not None:
                return Label("allowed_tactic", motif, f"reply {reply.uci()} creates a {motif}")

    # 7 / 8 / 9 — phase rules.
    if phase == "endgame":
        return Label("endgame_technique", "none", "endgame phase, no tactical rule matched")
    if phase == "opening":
        violation = opening_violation(board_before, board_after, move, prior_moves, mover)
        if violation is not None:
            return Label("opening_principle", "none", violation)
    return Label("positional_drift", "none", "evaluation loss with no tactical pattern")


MINOR_HOME = {
    chess.WHITE: frozenset({chess.B1, chess.C1, chess.F1, chess.G1}),
    chess.BLACK: frozenset({chess.B8, chess.C8, chess.F8, chess.G8}),
}


def undeveloped_minors(board: chess.Board, colour: chess.Color) -> int:
    return sum(
        1
        for piece_type in (chess.KNIGHT, chess.BISHOP)
        for square in board.pieces(piece_type, colour)
        if square in MINOR_HOME[colour]
    )


def piece_move_counts(uci_moves: list[str]) -> tuple[dict[int, int], dict[int, int]]:
    board = chess.Board()
    instance_of = {square: square for square in board.piece_map()}
    counts: dict[int, int] = dict.fromkeys(instance_of, 0)
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        captured: int | None = None
        if board.is_en_passant(move):
            captured = move.to_square + (-8 if board.turn == chess.WHITE else 8)
        elif board.piece_at(move.to_square) is not None:
            captured = move.to_square
        if captured is not None:
            instance_of.pop(captured, None)
        if board.is_castling(move):
            rook_from = chess.H1 if move.to_square == chess.G1 else chess.A1
            rook_to = chess.F1 if move.to_square == chess.G1 else chess.D1
            if board.turn == chess.BLACK:
                rook_from = chess.H8 if move.to_square == chess.G8 else chess.A8
                rook_to = chess.F8 if move.to_square == chess.G8 else chess.D8
            instance = instance_of.pop(rook_from, None)
            if instance is not None:
                instance_of[rook_to] = instance
                counts[instance] = counts.get(instance, 0) + 1
        instance = instance_of.pop(move.from_square, None)
        if instance is not None:
            instance_of[move.to_square] = instance
            counts[instance] = counts.get(instance, 0) + 1
        board.push(move)
    return instance_of, counts


def opening_violation(
    board_before: chess.Board,
    board_after: chess.Board,
    move: chess.Move,
    prior_moves: list[str],
    colour: chess.Color,
) -> str | None:
    at_home = undeveloped_minors(board_before, colour)
    instance_of, counts = piece_move_counts(prior_moves)
    instance = instance_of.get(move.from_square)
    repeats = counts.get(instance, 0) if instance is not None else 0
    if repeats >= 2 and at_home >= 2:
        return f"repeated_piece_move: {repeats} prior moves, {at_home} minors at home"
    for square in board_after.pieces(chess.QUEEN, colour):
        relative = chess.square_rank(square) if colour == chess.WHITE else 7 - chess.square_rank(square)
        if relative >= 3 and at_home >= 3:
            return f"early_queen on {chess.square_name(square)}, {at_home} minors at home"
    if board_after.fullmove_number > 10 and board_after.has_castling_rights(colour):
        home = chess.E1 if colour == chess.WHITE else chess.E8
        if board_after.king(colour) == home:
            return f"uncastled_king at fullmove {board_after.fullmove_number}"
    return None


# --------------------------------------------------------------------------- #
# Inertness: what makes a phase-class case unambiguous
# --------------------------------------------------------------------------- #


def is_inert(board_before: chess.Board, move: chess.Move, depth: int = SEARCH_DEPTH) -> bool:
    """No tactical rule can fire, whatever line the analyst chooses.

    The cheap universally-quantified reply scan runs *first*: most candidates
    fail it, and the exhaustive searches are two orders of magnitude slower.
    """
    if board_before.is_capture(move) or board_before.gives_check(move):
        return False
    board_after = board_before.copy(stack=False)
    board_after.push(move)
    if board_after.is_game_over(claim_draw=True):
        return False
    # No opponent reply may win material or create geometry.
    for reply in board_after.legal_moves:
        if board_after.is_capture(reply) and see(board_after, reply) >= HUNG_PIECE_SEE_CP:
            return False
        probe = board_after.copy(stack=False)
        probe.push(reply)
        if detect_fork(probe, reply.to_square, board_after.turn) is not None:
            return False
        if detect_pin_or_skewer(board_after, probe, reply, board_after.turn) is not None:
            return False
    cp_best, best_pv = search_pv(board_before, depth)
    if abs(cp_best) > 5_000:
        return False
    reply_value, _ = search_pv(board_after, depth - 1)
    if abs(reply_value) > 5_000:
        return False
    # And the mover may not have a material tactic available either, which is
    # what would otherwise trip rule 5.
    if pv_material_delta(board_before, best_pv, board_before.turn, 4) >= MISSED_TACTIC_MATERIAL_CP:
        return False
    return True


def non_pawn_pieces(board: chess.Board) -> int:
    return sum(
        len(board.pieces(piece_type, colour))
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for colour in (chess.WHITE, chess.BLACK)
    )


# --------------------------------------------------------------------------- #
# Position corpora
# --------------------------------------------------------------------------- #

TACTICAL_POOLS: list[tuple[chess.PieceType, ...]] = [
    (chess.QUEEN, chess.ROOK, chess.KNIGHT, chess.PAWN),
    (chess.ROOK, chess.ROOK, chess.BISHOP, chess.PAWN),
    (chess.QUEEN, chess.BISHOP, chess.KNIGHT),
    (chess.ROOK, chess.KNIGHT, chess.KNIGHT, chess.PAWN),
    (chess.QUEEN, chess.KNIGHT, chess.PAWN, chess.PAWN),
    (chess.ROOK, chess.BISHOP, chess.PAWN, chess.PAWN),
    (chess.QUEEN, chess.ROOK, chess.BISHOP),
    (chess.ROOK, chess.QUEEN, chess.PAWN),
]


def compose_tactical(rng: random.Random) -> chess.Board | None:
    board = chess.Board(None)
    squares = list(chess.SQUARES)
    rng.shuffle(squares)
    cursor = 0

    def take() -> int:
        nonlocal cursor
        square = squares[cursor]
        cursor += 1
        return square

    board.set_piece_at(take(), chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(take(), chess.Piece(chess.KING, chess.BLACK))
    for colour in (chess.WHITE, chess.BLACK):
        for piece_type in rng.choice(TACTICAL_POOLS):
            square = take()
            if piece_type is chess.PAWN and chess.square_rank(square) in (0, 7):
                return None
            board.set_piece_at(square, chess.Piece(piece_type, colour))
    board.turn = chess.WHITE if rng.random() < 0.5 else chess.BLACK
    board.castling_rights = chess.BB_EMPTY
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        return None
    return board


INERT_POOLS: list[tuple[chess.PieceType, ...]] = [
    (chess.ROOK, chess.PAWN, chess.PAWN),
    (chess.KNIGHT, chess.PAWN, chess.PAWN),
    (chess.BISHOP, chess.PAWN, chess.PAWN),
    (chess.ROOK, chess.KNIGHT, chess.PAWN),
    (chess.PAWN, chess.PAWN, chess.PAWN),
    (chess.QUEEN, chess.PAWN),
]

#: Pools with four non-pawn pieces per side (> 6 on the board), so the FR-10
#: piece-count rule calls the position *middlegame* and an inert move there is a
#: ``positional_drift`` truth case.  The original pools topped out at 4 non-pawn
#: pieces total, which made ``positional_drift`` unreachable by construction.
INERT_POOLS_MIDDLEGAME: list[tuple[chess.PieceType, ...]] = [
    (chess.ROOK, chess.ROOK, chess.BISHOP, chess.KNIGHT),
    (chess.ROOK, chess.BISHOP, chess.BISHOP, chess.KNIGHT),
    (chess.ROOK, chess.ROOK, chess.KNIGHT, chess.KNIGHT, chess.PAWN),
    (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT),
    (chess.ROOK, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN),
]


def compose_inert(rng: random.Random, *, middlegame: bool = False) -> chess.Board | None:
    board = chess.Board(None)
    squares = [sq for sq in chess.SQUARES]
    rng.shuffle(squares)
    cursor = 0

    def take() -> int:
        nonlocal cursor
        square = squares[cursor]
        cursor += 1
        return square

    board.set_piece_at(take(), chess.Piece(chess.KING, chess.WHITE))
    board.set_piece_at(take(), chess.Piece(chess.KING, chess.BLACK))
    pool = rng.choice(INERT_POOLS_MIDDLEGAME if middlegame else INERT_POOLS)
    for colour in (chess.WHITE, chess.BLACK):
        for piece_type in pool:
            square = take()
            if piece_type is chess.PAWN and chess.square_rank(square) in (0, 7):
                return None
            board.set_piece_at(square, chess.Piece(piece_type, colour))
    board.turn = chess.WHITE if rng.random() < 0.5 else chess.BLACK
    board.castling_rights = chess.BB_EMPTY
    if not board.is_valid() or board.is_game_over(claim_draw=False):
        return None
    return board


#: Scripted opening games that violate (a), (b) or (c) of FR-11 rule 8.
#:
#: Every script keeps the *opponent's* bishops and queen boxed behind their own
#: pawns (b7/d7/e7/g7 stay home) and leaves no reply capture with SEE >= +100
#: and no one-move fork/pin/skewer reply, so ``is_inert``'s universal reply scan
#: holds and rules 1-6 cannot fire whatever line the analyst picks.  The first
#: draft's scripts all failed that scan (e.g. after 1.Nc3 Nc6 2.Nb5 Nb4 3.Na3
#: the reply ...Nc2+ forks king and rook).
OPENING_SCRIPTS: list[tuple[str, list[str]]] = [
    # (a) third move of the same knight, shuffling inside its own camp.
    ("repeated_piece_move", ["b1a3", "a7a6", "a3b1", "h7h6", "b1a3"]),
    ("repeated_piece_move", ["g1h3", "h7h6", "h3g1", "a7a6", "g1h3"]),
    ("repeated_piece_move", ["b1c3", "a7a6", "c3b1", "h7h6", "b1c3"]),
    ("repeated_piece_move", ["g1f3", "h7h6", "f3g1", "a7a6", "g1f3"]),
    # (b) queen beyond its third rank while >= 3 own minors sit at home.
    ("early_queen", ["c2c4", "a7a6", "d1a4"]),
    ("early_queen", ["e2e4", "h7h6", "d1h5"]),
    ("early_queen", ["c2c4", "h7h6", "d1a4"]),
    ("early_queen", ["e2e4", "a7a6", "d1h5"]),
    # (c) king still on e1 with castling rights after fullmove 10; both sides
    # only shuffled pawns/knights/rooks on the wings, every advanced pawn stays
    # defended so no reply capture reaches SEE >= +100.
    (
        "uncastled_king",
        [
            "a2a3", "h7h6", "b2b3", "g7g6", "c2c3", "f7f6", "d2d3", "a7a6",
            "g2g3", "h6h5", "h2h3", "g6g5", "a3a4", "a6a5", "d3d4", "b8c6",
            "g3g4", "h8h6", "b3b4", "c6b8", "d1c2",
        ],
    ),
    (
        "uncastled_king",
        [
            "a2a3", "h7h6", "b2b3", "g7g6", "d2d3", "f7f6", "g2g3", "a7a6",
            "h2h3", "h6h5", "c2c4", "g6g5", "a3a4", "a6a5", "d3d4", "b8c6",
            "g3g4", "h8h6", "e2e3", "c6b8", "b1c3",
        ],
    ),
]


def build(seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    buckets: dict[str, list[dict[str, object]]] = {name: [] for name in CATEGORIES}
    seen: set[tuple[str, str]] = set()

    # --- tactical classes ------------------------------------------------- #
    attempts = 0
    while attempts < 200_000 and any(len(buckets[name]) < PER_CATEGORY for name in TACTICAL):
        attempts += 1
        board = compose_tactical(rng)
        if board is None:
            continue
        phase = "endgame" if non_pawn_pieces(board) <= 6 else "middlegame"
        moves = sorted(board.legal_moves, key=lambda m: m.uci())
        rng.shuffle(moves)
        for move in moves[:6]:
            key = (board.fen(), move.uci())
            if key in seen:
                continue
            label = reference_classify(board, move, phase=phase, prior_moves=[])
            if label is None or label.category not in TACTICAL:
                continue
            if len(buckets[label.category]) >= PER_CATEGORY:
                continue
            seen.add(key)
            buckets[label.category].append(
                {
                    "fen": board.fen(),
                    "played_uci": move.uci(),
                    "played_san": board.san(move),
                    "phase": phase,
                    "prior_moves": [],
                    "truth_category": label.category,
                    "truth_motif": label.motif,
                    "rationale": label.rationale,
                }
            )
            break

    # --- endgame_technique / positional_drift ------------------------------ #
    attempts = 0
    while attempts < 200_000 and (
        len(buckets["endgame_technique"]) < PER_CATEGORY
        or len(buckets["positional_drift"]) < PER_CATEGORY
    ):
        attempts += 1
        # positional_drift needs > 6 non-pawn pieces (FR-10 middlegame), which
        # only the heavier pools can produce.
        want_middlegame = len(buckets["positional_drift"]) < PER_CATEGORY
        board = compose_inert(rng, middlegame=want_middlegame)
        if board is None:
            continue
        moves = sorted(board.legal_moves, key=lambda m: m.uci())
        rng.shuffle(moves)
        for move in moves[:4]:
            key = (board.fen(), move.uci())
            if key in seen or not is_inert(board, move):
                continue
            heavy = non_pawn_pieces(board)
            phase = "endgame" if heavy <= 6 else "middlegame"
            category = "endgame_technique" if phase == "endgame" else "positional_drift"
            if len(buckets[category]) >= PER_CATEGORY:
                continue
            label = reference_classify(board, move, phase=phase, prior_moves=[])
            if label is None or label.category != category:
                continue
            seen.add(key)
            buckets[category].append(
                {
                    "fen": board.fen(),
                    "played_uci": move.uci(),
                    "played_san": board.san(move),
                    "phase": phase,
                    "prior_moves": [],
                    "truth_category": category,
                    "truth_motif": "none",
                    "rationale": (
                        f"{label.rationale}; inert position ({heavy} non-pawn pieces): no "
                        "capture, mate, material tactic or geometry is available to either side"
                    ),
                }
            )
            break

    # --- opening_principle -------------------------------------------------- #
    for violation, script in OPENING_SCRIPTS:
        if len(buckets["opening_principle"]) >= PER_CATEGORY:
            break
        board = chess.Board()
        prior: list[str] = []
        ok = True
        for uci in script[:-1]:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                ok = False
                break
            board.push(move)
            prior.append(uci)
        if not ok:
            continue
        played = chess.Move.from_uci(script[-1])
        if played not in board.legal_moves:
            continue
        if not is_inert(board, played):
            continue
        label = reference_classify(board, played, phase="opening", prior_moves=prior)
        if label is None or label.category != "opening_principle":
            continue
        buckets["opening_principle"].append(
            {
                "fen": board.fen(),
                "played_uci": played.uci(),
                "played_san": board.san(played),
                "phase": "opening",
                "prior_moves": prior,
                "truth_category": "opening_principle",
                "truth_motif": "none",
                "rationale": f"{violation}: {label.rationale}; position is inert",
            }
        )

    missing = {name: PER_CATEGORY - len(bucket) for name, bucket in buckets.items() if len(bucket) < PER_CATEGORY}
    if missing:
        raise SystemExit(f"could not fill every category: {missing}")

    cases: list[dict[str, object]] = []
    for name in CATEGORIES:
        for case in buckets[name][:PER_CATEGORY]:
            cases.append(case)
    for index, case in enumerate(cases, start=1):
        case["id"] = f"tx-{index:03d}"
    return {
        "seed": seed,
        "per_category": PER_CATEGORY,
        "search_depth_plies": SEARCH_DEPTH,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=TAXONOMY_SEED)
    args = parser.parse_args()
    payload = build(args.seed)
    path = write_fixture("taxonomy_cases.json", payload)
    print(f"wrote {path}: {len(payload['cases'])} cases")


if __name__ == "__main__":
    main()

"""FR-2 — negamax alpha-beta search.

Knuth & Moore (1975) negamax with alpha-beta, iterative deepening, quiescence
search over captures and promotions with stand-pat, a transposition table keyed
by ``chess.polyglot.zobrist_hash`` (Zobrist 1970; fixed 2^16 entries,
depth-preferred replacement), and move ordering = TT move, then MVV-LVA
captures, then two killer moves, then the history heuristic (Schaeffer 1989).

**Table lifetime (invariant).** The TT, killer table and history table are
created empty at every top-level :func:`search` call and discarded when it
returns.  Nothing carries across moves or across analyst calls.

**Root TT rule.** Interior nodes may take TT cutoffs; the root never does,
because FR-4 needs a score for *every* root move.

**Determinism contract.** ``search(fen, config, node_budget)`` returns the same
best move, the same root score vector and the same node count for the same
arguments, independent of anything searched before it — the contract is per
*position*, not per game.  The board's move history is therefore deliberately
discarded: the search always starts from a bare FEN.

Termination is by exact node budget (never wall clock); mate scores are encoded
as ``+/-(MATE_SCORE - ply)`` so shorter mates win.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess
import chess.polyglot

from ..constants import MATE_SCORE, MATE_THRESHOLD, MAX_PLY, TT_SIZE
from .evaluate import PIECE_VALUE, evaluate

__all__ = [
    "SearchConfig",
    "SearchResult",
    "INFINITY",
    "search",
    "is_mate_score",
    "mate_distance_plies",
]

INFINITY = 1 << 20

#: Transposition-table entry flags.
_EXACT = 0
_LOWER = 1
_UPPER = 2

#: Maximum quiescence extension in plies past the horizon.
_MAX_QUIESCENCE_PLY = 8

_MOVE_ORDER_TT = 1_000_000
_MOVE_ORDER_CAPTURE = 100_000
_MOVE_ORDER_KILLER_1 = 90_000
_MOVE_ORDER_KILLER_2 = 89_000
#: History scores are clamped below the killer band so ordering classes never mix.
_MOVE_ORDER_HISTORY_CAP = 80_000


def is_mate_score(score: int) -> bool:
    """True when ``score`` encodes a forced mate."""
    return abs(score) >= MATE_THRESHOLD


def mate_distance_plies(score: int) -> int | None:
    """Plies to mate encoded in ``score``, or ``None`` when it is not a mate score."""
    if not is_mate_score(score):
        return None
    return MATE_SCORE - abs(score)


@dataclass(frozen=True)
class SearchConfig:
    """Everything the search itself needs to know (FR-2)."""

    max_depth: int = 4

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValueError("max_depth must be >= 1")


@dataclass(frozen=True)
class SearchResult:
    """Outcome of one top-level :func:`search` call."""

    best_move: chess.Move | None
    best_score_cp: int
    depth: int
    nodes: int
    #: UCI → score of the *last fully completed* iteration, one entry per legal move.
    root_scores: dict[str, int] = field(default_factory=dict)
    pv: list[chess.Move] = field(default_factory=list)

    @property
    def pv_uci(self) -> list[str]:
        return [m.uci() for m in self.pv]


class _BudgetExceeded(Exception):
    """Raised the moment the node budget is spent; unwinds to the ID loop."""


class _TTEntry:
    __slots__ = ("key", "depth", "score", "flag", "move")

    def __init__(self, key: int, depth: int, score: int, flag: int, move: chess.Move | None):
        self.key = key
        self.depth = depth
        self.score = score
        self.flag = flag
        self.move = move


class _Searcher:
    """One top-level search.  All tables live and die with this object."""

    def __init__(self, board: chess.Board, config: SearchConfig, node_budget: int) -> None:
        self.board = board
        self.max_depth = config.max_depth
        self.budget = node_budget
        self.nodes = 0
        self.tt: list[_TTEntry | None] = [None] * TT_SIZE
        self.killers: list[list[chess.Move | None]] = [[None, None] for _ in range(MAX_PLY + 1)]
        self.history: dict[tuple[bool, int, int], int] = {}
        self.pv_table: list[list[chess.Move]] = [[] for _ in range(MAX_PLY + 2)]

    # -- bookkeeping -------------------------------------------------------- #

    def _spend_node(self) -> None:
        if self.nodes >= self.budget:
            raise _BudgetExceeded
        self.nodes += 1

    # -- transposition table ------------------------------------------------ #

    def _tt_probe(self, key: int) -> _TTEntry | None:
        entry = self.tt[key & (TT_SIZE - 1)]
        if entry is not None and entry.key == key:
            return entry
        return None

    def _tt_store(
        self, key: int, depth: int, score: int, flag: int, move: chess.Move | None, ply: int
    ) -> None:
        index = key & (TT_SIZE - 1)
        existing = self.tt[index]
        # Depth-preferred replacement: keep the deeper entry for a different key.
        if existing is not None and existing.key != key and existing.depth > depth:
            return
        self.tt[index] = _TTEntry(key, depth, _score_to_tt(score, ply), flag, move)

    # -- move ordering ------------------------------------------------------ #

    def _capture_score(self, move: chess.Move) -> int:
        board = self.board
        if board.is_en_passant(move):
            victim = PIECE_VALUE[chess.PAWN]
        else:
            captured = board.piece_type_at(move.to_square)
            victim = PIECE_VALUE[captured] if captured is not None else 0
        attacker_type = board.piece_type_at(move.from_square)
        attacker = PIECE_VALUE[attacker_type] if attacker_type is not None else 0
        score = _MOVE_ORDER_CAPTURE + victim * 10 - attacker
        if move.promotion:
            score += PIECE_VALUE[move.promotion]
        return score

    def _order_moves(
        self, moves: list[chess.Move], tt_move: chess.Move | None, ply: int
    ) -> list[chess.Move]:
        board = self.board
        killers = self.killers[ply] if ply <= MAX_PLY else [None, None]
        history = self.history
        turn = board.turn

        def key(move: chess.Move) -> tuple[int, str]:
            if tt_move is not None and move == tt_move:
                score = _MOVE_ORDER_TT
            elif board.is_capture(move) or move.promotion:
                score = self._capture_score(move)
            elif killers[0] is not None and move == killers[0]:
                score = _MOVE_ORDER_KILLER_1
            elif killers[1] is not None and move == killers[1]:
                score = _MOVE_ORDER_KILLER_2
            else:
                score = min(
                    history.get((turn, move.from_square, move.to_square), 0),
                    _MOVE_ORDER_HISTORY_CAP,
                )
            # Ties break on UCI so ordering — and therefore node counts — are exact.
            return (-score, move.uci())

        return sorted(moves, key=key)

    # -- search ------------------------------------------------------------- #

    def _is_drawn(self) -> bool:
        board = self.board
        if board.halfmove_clock >= 100:
            return True
        return board.occupied.bit_count() <= 4 and board.is_insufficient_material()

    def _quiescence(self, alpha: int, beta: int, ply: int, qply: int) -> int:
        self._spend_node()
        board = self.board
        self.pv_table[ply] = []
        if self._is_drawn():
            return 0

        legal = list(board.legal_moves)
        in_check = board.is_check()
        if not legal:
            return -(MATE_SCORE - ply) if in_check else 0

        if in_check and qply < _MAX_QUIESCENCE_PLY:
            # In check there are no quiet alternatives: search every evasion.
            moves = legal
            best = -INFINITY
        else:
            stand_pat = evaluate(board)
            if stand_pat >= beta:
                return stand_pat
            if stand_pat > alpha:
                alpha = stand_pat
            best = stand_pat
            if qply >= _MAX_QUIESCENCE_PLY:
                return best
            moves = [m for m in legal if board.is_capture(m) or m.promotion]
            if not moves:
                return best

        for move in self._order_moves(moves, None, ply):
            board.push(move)
            try:
                score = -self._quiescence(-beta, -alpha, ply + 1, qply + 1)
            finally:
                board.pop()
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    self.pv_table[ply] = [move, *self.pv_table[ply + 1]]
                    if alpha >= beta:
                        break
        return best

    def _negamax(self, depth: int, alpha: int, beta: int, ply: int) -> int:
        if depth <= 0:
            return self._quiescence(alpha, beta, ply, 0)

        self._spend_node()
        board = self.board
        self.pv_table[ply] = []
        if self._is_drawn():
            return 0

        alpha_orig = alpha
        key = chess.polyglot.zobrist_hash(board)
        entry = self._tt_probe(key)
        tt_move = entry.move if entry is not None else None
        if entry is not None and entry.depth >= depth:
            score = _score_from_tt(entry.score, ply)
            if entry.flag == _EXACT:
                return score
            if entry.flag == _LOWER and score >= beta:
                return score
            if entry.flag == _UPPER and score <= alpha:
                return score

        moves = list(board.legal_moves)
        if not moves:
            return -(MATE_SCORE - ply) if board.is_check() else 0

        best_score = -INFINITY
        best_move: chess.Move | None = None
        for move in self._order_moves(moves, tt_move, ply):
            board.push(move)
            try:
                score = -self._negamax(depth - 1, -beta, -alpha, ply + 1)
            finally:
                board.pop()
            if score > best_score:
                best_score = score
                best_move = move
                if score > alpha:
                    alpha = score
                    self.pv_table[ply] = [move, *self.pv_table[ply + 1]]
            if alpha >= beta:
                if not board.is_capture(move) and not move.promotion:
                    self._remember_quiet(move, depth, ply)
                break

        flag = _EXACT
        if best_score <= alpha_orig:
            flag = _UPPER
        elif best_score >= beta:
            flag = _LOWER
        self._tt_store(key, depth, best_score, flag, best_move, ply)
        return best_score

    def _remember_quiet(self, move: chess.Move, depth: int, ply: int) -> None:
        if ply <= MAX_PLY:
            killers = self.killers[ply]
            if killers[0] != move:
                killers[1] = killers[0]
                killers[0] = move
        key = (self.board.turn, move.from_square, move.to_square)
        self.history[key] = self.history.get(key, 0) + depth * depth

    # -- root --------------------------------------------------------------- #

    def run(self) -> SearchResult:
        board = self.board
        root_moves = list(board.legal_moves)
        if not root_moves:
            score = -MATE_SCORE if board.is_check() else 0
            return SearchResult(None, score, 0, 0, {}, [])

        completed_scores: dict[str, int] = {}
        completed_depth = 0
        completed_best: chess.Move | None = None
        completed_pv: list[chess.Move] = []
        previous_best: chess.Move | None = None

        for depth in range(1, self.max_depth + 1):
            scores: dict[str, int] = {}
            best_move: chess.Move | None = None
            best_score = -INFINITY
            best_pv: list[chess.Move] = []
            try:
                # Full window at every root move: the root never narrows alpha
                # across siblings, so all root scores stay comparable (FR-4).
                for move in self._order_moves(root_moves, previous_best, 0):
                    board.push(move)
                    try:
                        score = -self._negamax(depth - 1, -INFINITY, INFINITY, 1)
                    finally:
                        board.pop()
                    scores[move.uci()] = score
                    if score > best_score or (
                        score == best_score
                        and best_move is not None
                        and move.uci() < best_move.uci()
                    ):
                        best_score = score
                        best_move = move
                        best_pv = [move, *self.pv_table[1]]
            except _BudgetExceeded:
                # A partial iteration is discarded, never merged (FR-4 step 2).
                break

            completed_scores = scores
            completed_depth = depth
            completed_best = best_move
            completed_pv = best_pv
            previous_best = best_move

        if completed_best is None:
            # The budget did not even cover depth 1.  Fall back to a static score
            # vector so every root move still has a comparable value (depth 0).
            completed_scores = {}
            for move in root_moves:
                board.push(move)
                try:
                    completed_scores[move.uci()] = -evaluate(board)
                finally:
                    board.pop()
            best_uci = min(
                completed_scores, key=lambda u: (-completed_scores[u], u)
            )
            completed_best = chess.Move.from_uci(best_uci)
            completed_pv = [completed_best]
            completed_depth = 0

        return SearchResult(
            best_move=completed_best,
            best_score_cp=completed_scores[completed_best.uci()],
            depth=completed_depth,
            nodes=self.nodes,
            root_scores=completed_scores,
            pv=completed_pv,
        )


def _score_to_tt(score: int, ply: int) -> int:
    """Store mate scores relative to the node, not to the root."""
    if score >= MATE_THRESHOLD:
        return score + ply
    if score <= -MATE_THRESHOLD:
        return score - ply
    return score


def _score_from_tt(score: int, ply: int) -> int:
    if score >= MATE_THRESHOLD:
        return score - ply
    if score <= -MATE_THRESHOLD:
        return score + ply
    return score


def search(
    position: chess.Board | str,
    config: SearchConfig,
    node_budget: int,
) -> SearchResult:
    """Search ``position`` under an exact ``node_budget``.

    ``position`` may be a FEN or a :class:`chess.Board`; only the FEN matters —
    move history is discarded so the determinism contract holds per position.
    """
    if node_budget < 1:
        raise ValueError("node_budget must be >= 1")
    fen = position if isinstance(position, str) else position.fen()
    board = chess.Board(fen)
    return _Searcher(board, config, node_budget).run()

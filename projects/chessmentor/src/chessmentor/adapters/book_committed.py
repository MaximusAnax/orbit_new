"""Offline ``OpeningBook``: a trie over the committed ``data/openings.json``.

This is the default implementation — the one tests and evals exercise.  It is
deterministic, needs no network and is the source of ECO naming for coaching.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import chess

from ..constants import MAX_BOOK_DEPTH
from ..models import BookMove, Opening, OpeningLine

__all__ = ["CommittedBook", "BookValidationError"]


class BookValidationError(ValueError):
    """Raised when a committed opening line is illegal or duplicated."""


class _Node:
    __slots__ = ("children", "weight", "eco", "name", "depth")

    def __init__(self, depth: int) -> None:
        self.children: dict[str, _Node] = {}
        self.weight: int = 0
        self.eco: str | None = None
        self.name: str | None = None
        self.depth = depth


class CommittedBook:
    """Trie over committed ECO-tagged lines.

    Every line is validated for legality from the starting position at
    construction time (the ``init`` dataset check of DATA_MODEL.md).
    """

    def __init__(self, lines: Iterable[OpeningLine]) -> None:
        self._root = _Node(0)
        self._lines: list[OpeningLine] = []
        seen: set[tuple[str, ...]] = set()
        for line in lines:
            key = tuple(line.uci)
            if key in seen:
                raise BookValidationError(f"duplicate opening line: {' '.join(line.uci)}")
            seen.add(key)
            self._validate_legal(line)
            self._insert(line)
            self._lines.append(line)

    # -- construction ------------------------------------------------------- #

    @staticmethod
    def _validate_legal(line: OpeningLine) -> None:
        board = chess.Board()
        for uci in line.uci:
            try:
                move = chess.Move.from_uci(uci)
            except ValueError as exc:  # pragma: no cover - pydantic guards the shape
                raise BookValidationError(f"{line.eco} {line.name}: bad UCI {uci!r}") from exc
            if move not in board.legal_moves:
                raise BookValidationError(
                    f"{line.eco} {line.name}: illegal move {uci} after "
                    f"{' '.join(m.uci() for m in board.move_stack)}"
                )
            board.push(move)

    def _insert(self, line: OpeningLine) -> None:
        node = self._root
        node.weight += line.weight
        for depth, uci in enumerate(line.uci, start=1):
            child = node.children.get(uci)
            if child is None:
                child = _Node(depth)
                node.children[uci] = child
            child.weight += line.weight
            node = child
        # The deepest line terminating here names the node.
        node.eco = line.eco
        node.name = line.name

    # -- OpeningBook -------------------------------------------------------- #

    @property
    def name(self) -> str:
        return "committed"

    @property
    def lines(self) -> list[OpeningLine]:
        return list(self._lines)

    def _walk(self, ucis: Sequence[str]) -> list[_Node]:
        """Nodes visited following ``ucis``; stops at the first miss."""
        node = self._root
        path: list[_Node] = []
        for uci in ucis:
            child = node.children.get(uci)
            if child is None:
                break
            path.append(child)
            node = child
        return path

    def probe(self, board: chess.Board) -> list[BookMove]:
        if board.root().fen() != chess.STARTING_FEN:
            return []
        ucis = [m.uci() for m in board.move_stack]
        if len(ucis) > MAX_BOOK_DEPTH:
            return []
        path = self._walk(ucis)
        if len(path) != len(ucis):
            return []
        node = path[-1] if path else self._root
        return [
            BookMove(uci=uci, weight=child.weight)
            for uci, child in sorted(node.children.items())
        ]

    def identify(self, moves: Sequence[str]) -> Opening | None:
        path = self._walk(list(moves)[:MAX_BOOK_DEPTH])
        best: _Node | None = None
        for node in path:
            if node.name is not None:
                best = node
        if best is None or best.eco is None or best.name is None:
            return None
        return Opening(eco=best.eco, name=best.name, depth=best.depth)

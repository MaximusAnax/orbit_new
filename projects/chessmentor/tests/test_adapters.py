"""Adapters — the provider contracts, offline determinism and live gating (FR-16)."""

from __future__ import annotations

import chess
import pytest
from chessmentor.adapters import (
    Analyst,
    AnalystUnavailableError,
    CommittedBook,
    InternalAnalyst,
    OpeningBook,
    live_analyst,
    live_book,
)
from chessmentor.adapters.analyst_stockfish import STOCKFISH_PATH_ENV
from chessmentor.adapters.book_lichess import LICHESS_LIVE_ENV, BookUnavailableError
from chessmentor.constants import JUDGE_BUDGET
from chessmentor.models import AnalystKind

from chessmentor import __version__

MIDDLEGAME = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 1"


# --- Analyst ------------------------------------------------------------------- #


def test_internal_analyst_satisfies_the_protocol() -> None:
    analyst = InternalAnalyst()
    assert isinstance(analyst, Analyst)
    assert analyst.kind is AnalystKind.INTERNAL
    assert analyst.version == __version__
    assert analyst.max_depth == 6


def test_internal_analyst_is_deterministic() -> None:
    analyst = InternalAnalyst(max_depth=3)
    board = chess.Board(MIDDLEGAME)
    first = analyst.analyse(board, node_budget=800)
    second = analyst.analyse(board, node_budget=800)
    assert first == second


def test_internal_analyst_respects_the_node_budget() -> None:
    analyst = InternalAnalyst(max_depth=5)
    board = chess.Board(MIDDLEGAME)
    for budget in (200, 800, 3_000):
        result = analyst.analyse(board, node_budget=budget)
        # The PV extension adds a small fixed budget only when the PV is 1 ply.
        assert result.nodes <= budget + 256


def test_internal_analyst_returns_a_legal_pv() -> None:
    analyst = InternalAnalyst(max_depth=3)
    board = chess.Board(MIDDLEGAME)
    result = analyst.analyse(board, node_budget=1_500)
    assert result.best_move is not None
    probe = board.copy(stack=False)
    for uci in result.pv:
        move = chess.Move.from_uci(uci)
        assert move in probe.legal_moves
        probe.push(move)
    assert result.pv[0] == result.best_move


def test_internal_analyst_gives_a_two_ply_line_where_one_exists() -> None:
    analyst = InternalAnalyst(max_depth=1)
    result = analyst.analyse(chess.Board(MIDDLEGAME), node_budget=60)
    assert len(result.pv) >= 2


def test_internal_analyst_handles_a_terminal_position() -> None:
    analyst = InternalAnalyst(max_depth=3)
    result = analyst.analyse(chess.Board("7k/5Q1K/8/8/8/8/8/8 b - - 0 1"), node_budget=500)
    assert result.best_move is None
    assert result.pv == []


def test_internal_analyst_defaults_to_the_judge_budget() -> None:
    import inspect

    signature = inspect.signature(InternalAnalyst.analyse)
    assert signature.parameters["node_budget"].default == JUDGE_BUDGET


def test_internal_analyst_has_no_throttle() -> None:
    """The analyst must never apply noise or blunder injection."""
    analyst = InternalAnalyst(max_depth=3)
    board = chess.Board(MIDDLEGAME)
    results = {analyst.analyse(board, node_budget=1_200).best_move for _ in range(5)}
    assert len(results) == 1


# --- OpeningBook ---------------------------------------------------------------- #


def test_committed_book_satisfies_the_protocol(datasets) -> None:
    assert isinstance(datasets.book, OpeningBook)
    assert isinstance(datasets.book, CommittedBook)
    assert datasets.book.name == "committed"


def test_committed_book_probe_is_pure(datasets) -> None:
    board = chess.Board()
    fen = board.fen()
    datasets.book.probe(board)
    assert board.fen() == fen


# --- live adapters are gated ------------------------------------------------------ #


def test_live_analyst_requires_a_configured_binary(monkeypatch) -> None:
    monkeypatch.delenv(STOCKFISH_PATH_ENV, raising=False)
    with pytest.raises(AnalystUnavailableError, match=STOCKFISH_PATH_ENV):
        live_analyst()


def test_live_analyst_rejects_a_non_executable_path(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "not-stockfish"
    fake.write_text("")
    monkeypatch.setenv(STOCKFISH_PATH_ENV, str(fake))
    with pytest.raises(AnalystUnavailableError, match="not an executable"):
        live_analyst()


def test_live_book_requires_its_flag(monkeypatch, datasets) -> None:
    monkeypatch.delenv(LICHESS_LIVE_ENV, raising=False)
    with pytest.raises(BookUnavailableError, match=LICHESS_LIVE_ENV):
        live_book(datasets.book)


def test_live_book_delegates_identify_to_the_committed_book(monkeypatch, datasets) -> None:
    """ECO naming and book depth must stay reproducible (FR-16)."""
    monkeypatch.setenv(LICHESS_LIVE_ENV, "1")
    live = live_book(datasets.book)
    moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"]
    assert live.identify(moves) == datasets.book.identify(moves)
    assert live.name == "lichess-explorer"


def test_offline_path_never_imports_the_live_modules() -> None:
    """The offline default must not pull in ``chess.engine`` or urllib clients."""
    import subprocess
    import sys

    script = (
        "import sys;"
        "import chessmentor.adapters as a;"
        "import chessmentor.engine.judge, chessmentor.engine.throttle;"
        "loaded = set(sys.modules);"
        "assert 'chessmentor.adapters.analyst_stockfish' not in loaded, 'stockfish imported';"
        "assert 'chessmentor.adapters.book_lichess' not in loaded, 'lichess imported';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout

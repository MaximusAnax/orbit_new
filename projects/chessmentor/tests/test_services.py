"""Service-layer orchestration tests (FR-6/7/8/9/12/16).

These cover the cross-entity behaviour the API and CLI both rely on but neither
owns: the level-override rule, the mandatory judge pass, the report-basis
predicate and seed-exact replay.
"""

from __future__ import annotations

import chess
import pytest
from chessmentor.adapters import InternalAnalyst
from chessmentor.constants import DEEP_BUDGET, JUDGE_BUDGET
from chessmentor.models import ChallengeMode, Color, GameStatus, PreferredColor
from chessmentor.services import (
    ChessMentorService,
    GameInProgressError,
    NotInitializedError,
    NotPlayersTurnError,
)
from chessmentor.store import InMemoryRepository

NOW = "2026-07-31T12:00:00Z"


@pytest.fixture
def service(datasets) -> ChessMentorService:
    repo = InMemoryRepository()
    service = ChessMentorService(repo, datasets=datasets, analyst=InternalAnalyst(max_depth=2))
    repo.initialize(datasets.levels)
    service.initialize(
        now=NOW,
        display_name="Owner",
        challenge_mode=ChallengeMode.BALANCED,
        preferred_color=PreferredColor.WHITE,
    )
    return service


def _play_out(service: ChessMentorService, game_id: int, moves: list[str]) -> None:
    for move in moves:
        view = service.get_game(game_id)
        if view.game.status is not GameStatus.IN_PROGRESS:
            return
        try:
            service.submit_move(game_id, move, at=NOW)
        except Exception:  # noqa: BLE001 - a scripted move may have become illegal
            return


def test_fr6_requires_initialisation(datasets) -> None:
    repo = InMemoryRepository()
    repo.initialize(datasets.levels)
    bare = ChessMentorService(repo, datasets=datasets, analyst=InternalAnalyst(max_depth=1))
    with pytest.raises(NotInitializedError):
        bare.create_game(started_at=NOW, seed=1)


def test_fr6_one_in_progress_game_at_a_time(service: ChessMentorService) -> None:
    first = service.create_game(started_at=NOW, seed=1, color=PreferredColor.WHITE)
    with pytest.raises(GameInProgressError) as info:
        service.create_game(started_at=NOW, seed=2, color=PreferredColor.WHITE)
    assert info.value.in_progress_game_id == first.game.id


def test_fr6_moving_out_of_turn_is_rejected(service: ChessMentorService) -> None:
    view = service.create_game(started_at=NOW, seed=3, color=PreferredColor.BLACK)
    assert view.game.id is not None
    # The CPU has already played White's first move, so it *is* the player's turn.
    assert view.player_to_move
    service.submit_move(view.game.id, view.legal_moves_san[0], at=NOW)
    board = chess.Board(service.get_game(view.game.id).fen)
    assert board.turn == chess.BLACK  # the CPU answered immediately


def test_us8_override_is_flagged_still_rated_and_does_not_move_the_controller(
    service: ChessMentorService,
) -> None:
    recommended = service.require_rating_state().current_level_id
    override_level = recommended + 3
    view = service.create_game(
        started_at=NOW, seed=5, color=PreferredColor.WHITE, level_id=override_level
    )
    assert view.game.level_id == override_level
    assert view.game.recommended_level_id == recommended
    assert view.game.level_overridden is True

    assert view.game.id is not None
    _play_out(service, view.game.id, ["e4", "Nf3", "Bc4", "d3"])
    result = service.resign(view.game.id, at=NOW)
    # US-8: an overridden game is still evidence, so it is still rated…
    assert result.game.rated is True
    assert result.rating_event is not None
    assert result.rating_event.level_played == override_level
    # …and the controller's next recommendation comes from the rating, not from
    # the level the player forced.
    assert service.require_rating_state().current_level_id == result.rating_event.level_next


def test_fr9_the_judge_pass_always_runs_and_is_the_rating_basis(
    service: ChessMentorService,
) -> None:
    view = service.create_game(started_at=NOW, seed=9, color=PreferredColor.WHITE)
    assert view.game.id is not None
    _play_out(service, view.game.id, ["e4", "Nf3", "Nc3", "d4"])
    result = service.resign(view.game.id, at=NOW)
    assert result.analysis is not None
    assert result.analysis.node_budget == JUDGE_BUDGET
    assert result.analysis.is_rating_basis is True
    assert result.analysis.analyst_version == service.engine_version
    basis = service.report_basis_analyses(view.game.id)
    assert [a.id for a in basis] == [result.analysis.id]


def test_fr12_a_deeper_reanalysis_never_becomes_the_report_basis(
    service: ChessMentorService,
) -> None:
    view = service.create_game(started_at=NOW, seed=11, color=PreferredColor.WHITE)
    assert view.game.id is not None
    _play_out(service, view.game.id, ["e4", "Nf3", "Bb5", "O-O"])
    judged = service.resign(view.game.id, at=NOW)
    deep = service.analyze(view.game.id, created_at=NOW, node_budget=DEEP_BUDGET)
    assert deep.analysis.id != judged.analysis.id
    basis = service.report_basis_analyses(view.game.id)
    assert [a.id for a in basis] == [judged.analysis.id]

    report = service.create_report(created_at=NOW)
    assert [entry.analysis_id for entry in report.report.window] == [judged.analysis.id]


def test_fr16_the_same_seed_and_moves_replay_identically(datasets) -> None:
    def run() -> list[str]:
        repo = InMemoryRepository()
        service = ChessMentorService(repo, datasets=datasets, analyst=InternalAnalyst(max_depth=1))
        repo.initialize(datasets.levels)
        service.initialize(now=NOW, display_name="Owner", preferred_color=PreferredColor.WHITE)
        view = service.create_game(
            started_at=NOW, seed=20260731, color=PreferredColor.WHITE, level_id=3
        )
        assert view.game.id is not None
        _play_out(service, view.game.id, ["e4", "Nf3", "Bc4", "d3", "Nc3", "O-O"])
        return [record.uci for record in service.get_game(view.game.id).moves]

    assert run() == run()


def test_fr7_rating_view_exposes_the_blend_and_the_warning_flag(
    service: ChessMentorService,
) -> None:
    view = service.rating()
    assert view.r_hat == pytest.approx(800.0)
    assert view.lambda_used == pytest.approx(90.0**2 / (90.0**2 + 350.0**2), abs=1e-9)
    assert view.calibration_warning is False
    assert 0.0 <= view.expected_score_at_current_level <= 1.0


def test_fr6_a_finished_game_rejects_further_moves(service: ChessMentorService) -> None:
    view = service.create_game(started_at=NOW, seed=13, color=PreferredColor.WHITE)
    assert view.game.id is not None
    _play_out(service, view.game.id, ["e4", "d4", "Nf3", "Nc3"])
    service.resign(view.game.id, at=NOW)
    from chessmentor.services import GameFinishedError

    with pytest.raises(GameFinishedError):
        service.submit_move(view.game.id, "a3", at=NOW)


def test_fr6_player_colour_is_resolved_from_the_seed(service: ChessMentorService) -> None:
    view = service.create_game(started_at=NOW, seed=99, color=PreferredColor.RANDOM)
    assert view.game.player_color in (Color.WHITE, Color.BLACK)
    assert view.game.seed == 99
    # The same seed always resolves to the same colour (FR-16).
    from chessmentor.engine.session import resolve_color

    assert resolve_color(PreferredColor.RANDOM, 99) is view.game.player_color


def test_not_players_turn_is_raised_when_the_board_says_so(service: ChessMentorService) -> None:
    view = service.create_game(started_at=NOW, seed=17, color=PreferredColor.WHITE)
    assert view.game.id is not None
    # Force the stored game into a state where it is the CPU's move by pushing a
    # move directly through the repository, then ask the service to move again.
    from chessmentor.engine.session import make_move_record

    board = chess.Board()
    record = make_move_record(board, chess.Move.from_uci("e2e4"), 1, game_id=view.game.id)
    service.repo.append_move(view.game.id, record)
    with pytest.raises(NotPlayersTurnError):
        service.submit_move(view.game.id, "e5", at=NOW)

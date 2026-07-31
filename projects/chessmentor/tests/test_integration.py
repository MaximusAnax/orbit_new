"""End-to-end wiring across the engine modules (a small-scale rehearsal of M10).

Plays a seeded game through the pure session functions, runs the judge pass,
feeds its ACPL into the rating estimator and the controller, and builds a
coaching report — all with offline adapters and an in-memory store.
"""

from __future__ import annotations

import chess
import pytest
from chessmentor.constants import DEEP_BUDGET, JUDGE_BUDGET, R_INIT
from chessmentor.engine.adapt import cold_start_level_id
from chessmentor.engine.coach import build_report, select_window
from chessmentor.engine.judge import included_move_count, judge_game
from chessmentor.engine.rating import (
    apply_rated_game,
    blended_rating,
    initial_rating_state,
    perf_rating_from_acpl,
)
from chessmentor.engine.session import (
    apply_cpu_move,
    derive_rated,
    to_pgn,
)
from chessmentor.models import (
    ChallengeMode,
    Color,
    Game,
    GameSource,
    GameStatus,
    PlayerProfile,
    PreferredColor,
)
from chessmentor.store import InMemoryRepository

NOW = "2026-07-31T12:00:00Z"
#: The judge pass must run at JUDGE_BUDGET for its analysis to be the rating and
#: report basis (FR-12), so this test pays for it on a deliberately short game.
SHORT_GAME_PLIES = 14
#: Cheap budget for the determinism replays, which do not feed a report.
REPLAY_BUDGET = 500


def _play_seeded_game(levels, book, *, seed: int, level_id: int, max_plies: int = 30):
    """CPU vs CPU at one level — the deterministic stand-in for a scripted player."""
    level = next(lv for lv in levels if lv.id == level_id)
    board = chess.Board()
    records = []
    outcome = None
    while len(board.move_stack) < max_plies:
        result = apply_cpu_move(board, level, game_seed=seed, player_color=Color.WHITE, book=book)
        records.append(result.record)
        outcome = result.outcome
        if outcome is not None:
            break
    return board, records, outcome


def test_end_to_end_game_judge_rating_controller_report(levels, book, analyst) -> None:
    repo = InMemoryRepository()
    repo.initialize(levels)

    profile = PlayerProfile(
        display_name="Owner",
        challenge_mode=ChallengeMode.BALANCED,
        preferred_color=PreferredColor.WHITE,
        created_at=NOW,
        updated_at=NOW,
    )
    repo.save_profile(profile)

    cold_level = cold_start_level_id(levels, profile.challenge_mode)
    state = initial_rating_state(current_level_id=cold_level, updated_at=NOW)
    repo.save_rating_state(state)
    assert blended_rating(state) == R_INIT

    level = repo.get_level(cold_level)
    board, records, outcome = _play_seeded_game(
        levels, book, seed=20260731, level_id=cold_level, max_plies=SHORT_GAME_PLIES
    )
    uci_moves = [record.uci for record in records]

    game = repo.create_game(
        Game(
            source=GameSource.PLAYED,
            created_at=NOW,
            seed=20260731,
            player_color=Color.WHITE,
            level_id=level.id,
            level_elo=level.elo_internal,
            recommended_level_id=level.id,
            status=GameStatus.IN_PROGRESS,
            ply_count=0,
        )
    )
    for record in records:
        repo.append_move(game.id, record)

    # Terminate the game: either it ended naturally or we resign it out.
    if outcome is None:
        from chessmentor.engine.session import resignation_outcome

        outcome = resignation_outcome(Color.BLACK, Color.WHITE)
    status = outcome.status
    finished = repo.update_game(
        game.model_copy(
            update={
                "status": status,
                "termination": outcome.termination,
                "result_score": outcome.result_score,
                "ply_count": len(uci_moves),
                "final_fen": board.fen(),
                "rated": derive_rated(GameSource.PLAYED, status, len(uci_moves)),
            }
        )
    )
    assert finished.rated

    opening = book.identify(uci_moves)
    book_depth = opening.depth if opening else 0

    analysis = judge_game(
        uci_moves=uci_moves,
        player_color=Color.WHITE,
        book_depth=book_depth,
        analyst=analyst,
        levels=levels,
        created_at=NOW,
        node_budget=JUDGE_BUDGET,
        is_rating_basis=True,
        game_id=finished.id,
    )
    stored_analysis = repo.add_analysis(analysis)
    assert stored_analysis.id is not None
    assert included_move_count(stored_analysis) > 0
    assert stored_analysis.perf_rating == pytest.approx(
        perf_rating_from_acpl(stored_analysis.acpl, levels)
    )

    outcome_rating = apply_rated_game(
        state,
        game_id=finished.id,
        result_score=finished.result_score,
        opponent_elo=finished.level_elo,
        level_played=finished.level_id,
        perf_game=stored_analysis.perf_rating,
        levels=levels,
        mode=profile.challenge_mode,
        now=NOW,
    )
    repo.save_rating_state(outcome_rating.state)
    repo.append_rating_event(outcome_rating.event)

    assert outcome_rating.state.rated_games == 1
    assert outcome_rating.state.judged_games == 1
    assert outcome_rating.state.last_game_id == finished.id
    assert outcome_rating.event.r_hat_after == pytest.approx(blended_rating(outcome_rating.state))
    # The controller may only move one rung per game outside placement, two inside.
    assert abs(outcome_rating.state.current_level_id - cold_level) <= 2

    # A deeper re-analysis is stored alongside but never becomes the report basis.
    deep = repo.add_analysis(
        stored_analysis.model_copy(
            update={"id": None, "node_budget": DEEP_BUDGET, "is_rating_basis": False}
        )
    )
    assert deep.id != stored_analysis.id

    selection = select_window(
        repo.list_games(),
        {finished.id: repo.list_analyses(finished.id)},
        engine_version=analyst.version,
    )
    assert [entry.analysis.id for entry in selection.entries] == [stored_analysis.id]

    from chessmentor.datasets import load_advice

    report = repo.add_report(
        build_report(
            selection=selection,
            san_by_game={finished.id: [record.san for record in records]},
            advice_catalog=load_advice(),
            created_at=NOW,
        )
    )
    assert report.id is not None
    assert report.window[0].analysis_id == stored_analysis.id
    for suggestion in report.suggestions:
        assert suggestion.evidence
        assert suggestion.advice_body

    pgn = to_pgn(
        uci_moves,
        player_color=Color.WHITE,
        status=status,
        headers={"White": "Owner", "Black": level.name},
    )
    assert '[White "Owner"]' in pgn


def test_fr16_the_same_seed_replays_a_byte_identical_game(levels, book, analyst) -> None:
    """D0 in miniature: identical inputs ⇒ identical games and analyses."""

    def run() -> tuple[list[str], str]:
        _, records, _ = _play_seeded_game(levels, book, seed=99, level_id=3, max_plies=20)
        uci_moves = [record.uci for record in records]
        analysis = judge_game(
            uci_moves=uci_moves,
            player_color=Color.WHITE,
            book_depth=book.identify(uci_moves).depth if book.identify(uci_moves) else 0,
            analyst=analyst,
            levels=levels,
            created_at=NOW,
            node_budget=REPLAY_BUDGET,
        )
        return uci_moves, analysis.model_dump_json()

    first_moves, first_json = run()
    second_moves, second_json = run()
    assert first_moves == second_moves
    assert first_json == second_json


def test_fr16_cpu_metadata_never_exceeds_its_level_budget(levels, book) -> None:
    """US-1's hermetic acceptance criterion, across the whole ladder."""
    for level in levels[:5]:
        _, records, _ = _play_seeded_game(levels, book, seed=7, level_id=level.id, max_plies=8)
        for record in records:
            assert record.cpu_meta is not None
            assert record.cpu_meta.nodes <= level.node_budget


def test_fr6_rating_events_replay_to_the_stored_state(levels) -> None:
    """DATA_MODEL: replaying the event log reproduces rating_state exactly."""
    state = initial_rating_state(current_level_id=4, updated_at=NOW)
    events = []
    for game_id in range(1, 9):
        result = apply_rated_game(
            state,
            game_id=game_id,
            result_score=[1.0, 0.0, 0.5][game_id % 3],
            opponent_elo=850.0,
            level_played=4,
            perf_game=880.0 + 10 * game_id,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        )
        events.append(result.event)
        state = result.state

    replayed = initial_rating_state(current_level_id=4, updated_at=NOW)
    for event in events:
        replayed = apply_rated_game(
            replayed,
            game_id=event.game_id,
            result_score=event.result_score,
            opponent_elo=event.opponent_elo,
            level_played=event.level_played,
            perf_game=event.perf_game,
            levels=levels,
            mode=ChallengeMode.BALANCED,
            now=NOW,
        ).state
    assert replayed == state

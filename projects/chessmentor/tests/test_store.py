"""Store — the repository contract, run identically against both backends."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from chessmentor.constants import JUDGE_BUDGET
from chessmentor.engine.rating import initial_rating_state
from chessmentor.models import (
    AnalystKind,
    ChallengeMode,
    CoachingReport,
    Color,
    CpuMeta,
    EvidenceItem,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    MistakeCategory,
    MoveAnalysis,
    MoveRecord,
    Phase,
    PlayerProfile,
    PreferredColor,
    RatingEvent,
    Severity,
    Suggestion,
    Termination,
    WindowEntry,
)
from chessmentor.store import ConflictError, InMemoryRepository, NotFoundError, SQLiteRepository

NOW = "2026-07-31T12:00:00Z"


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, levels, tmp_path) -> Iterator[object]:
    if request.param == "memory":
        backend = InMemoryRepository()
    else:
        backend = SQLiteRepository(tmp_path / "chessmentor.db")
    backend.initialize(levels)
    yield backend
    backend.close()


def _game(**overrides) -> Game:
    base = {
        "source": GameSource.PLAYED,
        "created_at": NOW,
        "seed": 12345,
        "player_color": Color.WHITE,
        "level_id": 4,
        "level_elo": 850.0,
        "recommended_level_id": 4,
        "status": GameStatus.IN_PROGRESS,
        "ply_count": 0,
    }
    base.update(overrides)
    return Game.model_validate(base)


def _finished(game: Game, **overrides) -> Game:
    data = game.model_dump()
    data.update(
        {
            "status": GameStatus.PLAYER_WIN,
            "termination": Termination.CHECKMATE,
            "result_score": 1.0,
            "ply_count": 30,
            "rated": True,
        }
    )
    data.update(overrides)
    return Game.model_validate(data)


# --- levels --------------------------------------------------------------------- #


def test_store_materialises_the_committed_ladder(repo, levels) -> None:
    assert [lv.id for lv in repo.list_levels()] == [lv.id for lv in levels]
    assert repo.get_level(4).elo_internal == 850.0
    with pytest.raises(NotFoundError):
        repo.get_level(99)


def test_store_initialize_is_idempotent(repo, levels) -> None:
    repo.initialize(levels)
    assert len(repo.list_levels()) == len(levels)


# --- profile / rating state -------------------------------------------------------- #


def test_store_profile_is_a_singleton(repo) -> None:
    assert repo.get_profile() is None
    profile = PlayerProfile(
        display_name="Owner",
        challenge_mode=ChallengeMode.STRETCH,
        preferred_color=PreferredColor.WHITE,
        created_at=NOW,
        updated_at=NOW,
    )
    saved = repo.save_profile(profile)
    assert saved.id == 1
    updated = repo.save_profile(profile.model_copy(update={"display_name": "New"}))
    assert repo.get_profile().display_name == "New"
    assert updated.id == 1


def test_store_rating_state_round_trips(repo) -> None:
    state = initial_rating_state(current_level_id=4, updated_at=NOW)
    state = state.model_copy(
        update={"surprise_window": [0.1, -0.2], "perf_ewma": 900.0, "judged_games": 2}
    )
    repo.save_rating_state(state)
    loaded = repo.get_rating_state()
    assert loaded.surprise_window == [0.1, -0.2]
    assert loaded.perf_ewma == 900.0
    assert loaded.judged_games == 2


# --- games ------------------------------------------------------------------------ #


def test_store_allows_only_one_in_progress_game(repo) -> None:
    first = repo.create_game(_game())
    assert first.id is not None
    with pytest.raises(ConflictError, match="already in progress"):
        repo.create_game(_game())
    repo.update_game(_finished(first))
    second = repo.create_game(_game())
    assert second.id != first.id


def test_store_finished_games_are_immutable(repo) -> None:
    game = repo.create_game(_game())
    repo.update_game(_finished(game))
    with pytest.raises(ConflictError, match="immutable"):
        repo.update_game(_finished(game, result_score=0.0, status=GameStatus.OPPONENT_WIN))


def test_store_lists_games_by_status_and_source(repo) -> None:
    first = repo.create_game(_game())
    repo.update_game(_finished(first))
    repo.create_game(_game())
    assert len(repo.list_games()) == 2
    assert len(repo.list_games(status=GameStatus.IN_PROGRESS)) == 1
    assert len(repo.list_games(source=GameSource.IMPORTED)) == 0
    assert len(repo.list_games(limit=1)) == 1


def test_store_reports_the_in_progress_game(repo) -> None:
    assert repo.get_in_progress_game() is None
    game = repo.create_game(_game())
    assert repo.get_in_progress_game().id == game.id
    repo.update_game(_finished(game))
    assert repo.get_in_progress_game() is None


def test_store_missing_game_raises(repo) -> None:
    with pytest.raises(NotFoundError):
        repo.get_game(404)


# --- moves ------------------------------------------------------------------------- #


def test_store_moves_are_append_only_and_contiguous(repo) -> None:
    game = repo.create_game(_game())
    repo.append_move(
        game.id,
        MoveRecord(ply=1, color=Color.WHITE, san="e4", uci="e2e4", fen_after="fen1"),
    )
    repo.append_move(
        game.id,
        MoveRecord(
            ply=2,
            color=Color.BLACK,
            san="e5",
            uci="e7e5",
            fen_after="fen2",
            cpu_meta=CpuMeta(
                depth=2,
                nodes=1_000,
                root_moves=20,
                score_cp=-10,
                best_score_cp=5,
                noise_changed_pick=True,
            ),
        ),
    )
    stored = repo.list_moves(game.id)
    assert [m.ply for m in stored] == [1, 2]
    assert stored[1].cpu_meta.noise_changed_pick is True
    assert stored[0].cpu_meta is None
    with pytest.raises(ConflictError, match="append-only"):
        repo.append_move(
            game.id,
            MoveRecord(ply=2, color=Color.BLACK, san="c5", uci="c7c5", fen_after="x"),
        )


# --- analyses ---------------------------------------------------------------------- #


def _analysis(game_id: int, **overrides) -> GameAnalysis:
    base = {
        "game_id": game_id,
        "analyst": AnalystKind.INTERNAL,
        "analyst_version": "0.1.0",
        "node_budget": JUDGE_BUDGET,
        "created_at": NOW,
        "book_depth": 4,
        "is_rating_basis": True,
        "acpl": 42.5,
        "accuracy": 88.0,
        "perf_rating": 940.0,
        "mg_start_ply": 7,
        "eg_start_ply": 41,
        "moves": [
            MoveAnalysis(
                ply=5,
                in_acpl=True,
                cp_best=40,
                cp_played=-310,
                cp_loss=350,
                w_before=0.537,
                w_after=0.243,
                delta_w=0.294,
                severity=Severity.BLUNDER,
                best_uci="c1e3",
                best_line_san=["Be3", "Ng4"],
                phase=Phase.MIDDLEGAME,
                category=MistakeCategory.HUNG_PIECE,
                evidence={"rule": "hung_piece", "see_cp": 320},
            )
        ],
    }
    base.update(overrides)
    return GameAnalysis.model_validate(base)


def test_store_analysis_round_trips_with_its_moves(repo) -> None:
    game = repo.create_game(_game())
    stored = repo.add_analysis(_analysis(game.id))
    assert stored.id is not None
    loaded = repo.get_analysis(stored.id)
    assert loaded.acpl == 42.5
    assert loaded.eg_start_ply == 41
    assert len(loaded.moves) == 1
    assert loaded.moves[0].category is MistakeCategory.HUNG_PIECE
    assert loaded.moves[0].evidence["see_cp"] == 320
    assert loaded.moves[0].best_line_san == ["Be3", "Ng4"]


def test_store_dedups_on_the_full_config_tuple(repo) -> None:
    game = repo.create_game(_game())
    first = repo.add_analysis(_analysis(game.id))
    again = repo.add_analysis(_analysis(game.id))
    assert again.id == first.id
    # A version bump produces a *new* row rather than stale numbers (D8).
    bumped = repo.add_analysis(_analysis(game.id, analyst_version="0.2.0", acpl=1.0))
    assert bumped.id != first.id
    assert len(repo.list_analyses(game.id)) == 2
    assert repo.latest_analysis(game.id).id == bumped.id


def test_store_analysis_needs_a_game(repo) -> None:
    with pytest.raises(ConflictError, match="reference a game"):
        repo.add_analysis(_analysis(1).model_copy(update={"game_id": None}))


# --- rating events -------------------------------------------------------------------- #


def _event(game_id: int, **overrides) -> RatingEvent:
    base = {
        "game_id": game_id,
        "created_at": NOW,
        "result_score": 1.0,
        "opponent_elo": 850.0,
        "expected_score": 0.43,
        "surprise_after": 0.57,
        "rd_inflated": False,
        "glicko_r_before": 800.0,
        "glicko_rd_before": 350.0,
        "glicko_r_after": 900.0,
        "glicko_rd_after": 247.0,
        "perf_game": 950.0,
        "perf_ewma_after": 950.0,
        "lambda_used": 0.22,
        "r_hat_after": 939.0,
        "level_played": 4,
        "level_next": 5,
    }
    base.update(overrides)
    return RatingEvent.model_validate(base)


def test_store_rating_events_are_append_only_and_unique(repo) -> None:
    first = repo.create_game(_game())
    repo.update_game(_finished(first))
    stored = repo.append_rating_event(_event(first.id))
    assert stored.id is not None
    with pytest.raises(ConflictError, match="already has a rating event"):
        repo.append_rating_event(_event(first.id))
    assert repo.list_rating_events()[0].r_hat_after == 939.0


def test_store_rating_events_follow_termination_order(repo) -> None:
    first = repo.create_game(_game())
    repo.update_game(_finished(first))
    second = repo.create_game(_game())
    repo.update_game(_finished(second))
    repo.save_rating_state(
        initial_rating_state(current_level_id=4, updated_at=NOW).model_copy(
            update={"last_game_id": second.id}
        )
    )
    with pytest.raises(ConflictError, match="termination order"):
        repo.append_rating_event(_event(first.id))


# --- reports ------------------------------------------------------------------------- #


def test_store_report_round_trips_with_snapshotted_advice(repo) -> None:
    report = CoachingReport(
        created_at=NOW,
        window=[WindowEntry(game_id=1, analysis_id=10)],
        skipped_game_ids=[2, 3],
        include_imported=False,
        totals_by_category={MistakeCategory.HUNG_PIECE: {"count": 2, "dw_sum": 0.9}},
        totals_by_phase={Phase.MIDDLEGAME: 0.9},
        suggestions=[
            Suggestion(
                rank=1,
                category=MistakeCategory.HUNG_PIECE,
                priority_score=0.9,
                advice_id="hung-piece-board-vision",
                advice_title="T",
                advice_body="B",
                advice_drill="D",
                evidence=[EvidenceItem(game_id=1, ply=5, san="Nd5", dw=0.5)],
            )
        ],
    )
    stored = repo.add_report(report)
    loaded = repo.get_report(stored.id)
    assert loaded.window[0].analysis_id == 10
    assert loaded.skipped_game_ids == [2, 3]
    assert loaded.totals_by_category[MistakeCategory.HUNG_PIECE].count == 2
    assert loaded.totals_by_phase[Phase.MIDDLEGAME] == 0.9
    assert loaded.suggestions[0].advice_title == "T"
    assert loaded.suggestions[0].evidence[0].san == "Nd5"
    assert repo.list_reports(limit=1)[0].id == stored.id


def test_store_missing_report_raises(repo) -> None:
    with pytest.raises(NotFoundError):
        repo.get_report(404)


# --- backend parity --------------------------------------------------------------------- #


def test_store_both_backends_satisfy_the_protocol(levels) -> None:
    from chessmentor.store.repository import Repository

    assert isinstance(InMemoryRepository(), Repository)
    assert isinstance(SQLiteRepository(), Repository)

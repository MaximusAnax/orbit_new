"""DATA_MODEL.md — entity invariants enforced by the Pydantic models."""

from __future__ import annotations

import pytest
from chessmentor.constants import CP_LOSS_CAP, RD_FLOOR, RD_INIT
from chessmentor.models import (
    AnalystKind,
    Color,
    CpuMeta,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    Level,
    MistakeCategory,
    Motif,
    MoveAnalysis,
    MoveRecord,
    Phase,
    RatingState,
    Severity,
    Suggestion,
    Termination,
)
from pydantic import ValidationError

NOW = "2026-07-31T12:00:00Z"


def _level(**overrides: object) -> dict:
    base = {
        "id": 1,
        "name": "L1",
        "max_depth": 2,
        "node_budget": 1_000,
        "noise_sigma_cp": 100.0,
        "blunder_prob": 0.1,
        "blunder_margin_lo_cp": 80,
        "blunder_margin_hi_cp": 600,
        "book_plies": 4,
        "elo_internal": 800.0,
        "acpl_mean": 120.0,
        "acpl_std": 30.0,
        "calibration_seed": 1,
        "calibrated_at": NOW,
        "engine_version": "0.1.0",
    }
    base.update(overrides)
    return base


def _game(**overrides: object) -> dict:
    base = {
        "source": GameSource.PLAYED,
        "created_at": NOW,
        "seed": 7,
        "player_color": Color.WHITE,
        "level_id": 4,
        "level_elo": 850.0,
        "status": GameStatus.PLAYER_WIN,
        "termination": Termination.CHECKMATE,
        "result_score": 1.0,
        "ply_count": 30,
        "rated": True,
    }
    base.update(overrides)
    return base


# --- Level ------------------------------------------------------------------------ #


def test_level_margins_must_be_null_exactly_when_blunder_prob_is_zero() -> None:
    Level.model_validate(
        _level(blunder_prob=0.0, blunder_margin_lo_cp=None, blunder_margin_hi_cp=None)
    )
    with pytest.raises(ValidationError, match="must be null"):
        Level.model_validate(_level(blunder_prob=0.0))
    with pytest.raises(ValidationError, match="required"):
        Level.model_validate(_level(blunder_margin_lo_cp=None, blunder_margin_hi_cp=None))


def test_level_margins_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="lo < hi"):
        Level.model_validate(_level(blunder_margin_lo_cp=600, blunder_margin_hi_cp=80))


def test_level_blunder_prob_is_capped_at_035() -> None:
    with pytest.raises(ValidationError):
        Level.model_validate(_level(blunder_prob=0.5))


def test_level_is_frozen() -> None:
    level = Level.model_validate(_level())
    with pytest.raises(ValidationError):
        level.node_budget = 5  # type: ignore[misc]


# --- RatingState -------------------------------------------------------------------- #


def test_rating_state_rd_stays_inside_its_bounds() -> None:
    for rd in (RD_FLOOR, RD_INIT):
        RatingState(glicko_rating=800, glicko_rd=rd, current_level_id=1, updated_at=NOW)
    with pytest.raises(ValidationError):
        RatingState(glicko_rating=800, glicko_rd=10, current_level_id=1, updated_at=NOW)
    with pytest.raises(ValidationError):
        RatingState(glicko_rating=800, glicko_rd=400, current_level_id=1, updated_at=NOW)


def test_rating_state_judged_games_and_perf_ewma_agree() -> None:
    with pytest.raises(ValidationError, match="judged_games must be 0"):
        RatingState(
            glicko_rating=800,
            glicko_rd=200,
            perf_ewma=None,
            judged_games=3,
            current_level_id=1,
            updated_at=NOW,
        )
    with pytest.raises(ValidationError, match="requires judged_games"):
        RatingState(
            glicko_rating=800,
            glicko_rd=200,
            perf_ewma=900.0,
            judged_games=0,
            current_level_id=1,
            updated_at=NOW,
        )


# --- Game --------------------------------------------------------------------------- #


def test_game_played_requires_seed_and_level() -> None:
    Game.model_validate(_game())
    with pytest.raises(ValidationError, match="require a seed"):
        Game.model_validate(_game(seed=None))
    with pytest.raises(ValidationError, match="require a level_id"):
        Game.model_validate(_game(level_id=None))


def test_game_imported_forbids_seed_and_level() -> None:
    imported = _game(
        source=GameSource.IMPORTED,
        seed=None,
        level_id=None,
        level_elo=None,
        termination=Termination.IMPORTED_RESULT,
        rated=False,
    )
    Game.model_validate(imported)
    with pytest.raises(ValidationError, match="must not carry a seed"):
        Game.model_validate({**imported, "seed": 3})


def test_game_result_score_matches_the_status() -> None:
    with pytest.raises(ValidationError, match="inconsistent"):
        Game.model_validate(_game(status=GameStatus.OPPONENT_WIN, result_score=1.0))
    with pytest.raises(ValidationError, match="must be null"):
        Game.model_validate(
            _game(
                status=GameStatus.ABORTED,
                termination=Termination.ABORTED,
                result_score=0.0,
                rated=False,
            )
        )


def test_game_rated_is_derived() -> None:
    with pytest.raises(ValidationError, match="rated is derived"):
        Game.model_validate(_game(ply_count=4, rated=True))
    with pytest.raises(ValidationError, match="rated is derived"):
        Game.model_validate(_game(rated=False))


def test_game_in_progress_has_no_termination() -> None:
    with pytest.raises(ValidationError, match="no termination"):
        Game.model_validate(_game(status=GameStatus.IN_PROGRESS, result_score=None, rated=False))
    Game.model_validate(
        _game(status=GameStatus.IN_PROGRESS, termination=None, result_score=None, rated=False)
    )


def test_game_override_requires_a_different_level() -> None:
    with pytest.raises(ValidationError, match="level_overridden"):
        Game.model_validate(_game(recommended_level_id=4, level_overridden=True))
    Game.model_validate(_game(recommended_level_id=3, level_overridden=True))


# --- MoveRecord / CpuMeta -------------------------------------------------------------- #


def test_move_record_colour_alternates_from_white() -> None:
    MoveRecord(ply=1, color=Color.WHITE, san="e4", uci="e2e4", fen_after="x")
    MoveRecord(ply=2, color=Color.BLACK, san="e5", uci="e7e5", fen_after="x")
    with pytest.raises(ValidationError, match="must be played by"):
        MoveRecord(ply=2, color=Color.WHITE, san="e5", uci="e7e5", fen_after="x")


def test_cpu_meta_injection_implies_a_roll() -> None:
    with pytest.raises(ValidationError, match="requires blunder_rolled"):
        CpuMeta(depth=2, nodes=10, root_moves=20, blunder_injected=True)
    with pytest.raises(ValidationError, match="noise is skipped"):
        CpuMeta(
            depth=2,
            nodes=10,
            root_moves=20,
            blunder_rolled=True,
            blunder_injected=True,
            noise_changed_pick=True,
        )


# --- MoveAnalysis ------------------------------------------------------------------------ #


def _move_analysis(**overrides: object) -> dict:
    base = {
        "ply": 5,
        "in_acpl": True,
        "cp_best": 40,
        "cp_played": -310,
        "cp_loss": 350,
        "w_before": 0.537,
        "w_after": 0.243,
        "delta_w": 0.294,
        "severity": Severity.BLUNDER,
        "best_uci": "c1e3",
        "best_line_san": ["Be3", "Ng4"],
        "phase": Phase.MIDDLEGAME,
        "category": MistakeCategory.HUNG_PIECE,
    }
    base.update(overrides)
    return base


def test_move_analysis_cp_loss_is_derived_and_capped() -> None:
    MoveAnalysis.model_validate(_move_analysis())
    with pytest.raises(ValidationError, match="cp_loss"):
        MoveAnalysis.model_validate(_move_analysis(cp_loss=10))
    capped = MoveAnalysis.model_validate(
        _move_analysis(cp_best=0, cp_played=-5_000, cp_loss=CP_LOSS_CAP)
    )
    assert capped.cp_loss == CP_LOSS_CAP


def test_move_analysis_delta_w_and_severity_are_consistent() -> None:
    with pytest.raises(ValidationError, match="delta_w"):
        MoveAnalysis.model_validate(_move_analysis(delta_w=0.9))
    with pytest.raises(ValidationError, match="severity"):
        MoveAnalysis.model_validate(_move_analysis(severity=Severity.OK))


def test_move_analysis_category_only_on_flagged_included_moves() -> None:
    with pytest.raises(ValidationError, match="must carry a category"):
        MoveAnalysis.model_validate(_move_analysis(category=None))
    with pytest.raises(ValidationError, match="only flagged"):
        MoveAnalysis.model_validate(_move_analysis(in_acpl=False))
    MoveAnalysis.model_validate(_move_analysis(in_acpl=False, category=None))


def test_move_analysis_motifs_only_annotate_tactics() -> None:
    MoveAnalysis.model_validate(
        _move_analysis(category=MistakeCategory.MISSED_TACTIC, motif=Motif.FORK)
    )
    with pytest.raises(ValidationError, match="motifs only annotate"):
        MoveAnalysis.model_validate(_move_analysis(motif=Motif.FORK))


def test_move_analysis_w_values_stay_in_the_unit_interval() -> None:
    with pytest.raises(ValidationError):
        MoveAnalysis.model_validate(_move_analysis(w_before=1.4))


# --- GameAnalysis ---------------------------------------------------------------------- #


def test_game_analysis_boundaries_are_ordered() -> None:
    base = {
        "game_id": 1,
        "analyst": AnalystKind.INTERNAL,
        "analyst_version": "0.1.0",
        "node_budget": 6_000,
        "created_at": NOW,
        "book_depth": 0,
        "acpl": 40.0,
        "accuracy": 90.0,
        "perf_rating": 900.0,
    }
    GameAnalysis.model_validate({**base, "mg_start_ply": 7, "eg_start_ply": 40})
    with pytest.raises(ValidationError, match="eg_start_ply"):
        GameAnalysis.model_validate({**base, "mg_start_ply": 40, "eg_start_ply": 7})
    with pytest.raises(ValidationError, match="requires mg_start_ply"):
        GameAnalysis.model_validate({**base, "eg_start_ply": 7})


def test_game_analysis_plies_are_unique() -> None:
    move = MoveAnalysis.model_validate(_move_analysis())
    with pytest.raises(ValidationError, match="unique"):
        GameAnalysis(
            game_id=1,
            analyst=AnalystKind.INTERNAL,
            analyst_version="0.1.0",
            node_budget=6_000,
            created_at=NOW,
            book_depth=0,
            acpl=1.0,
            accuracy=90.0,
            perf_rating=900.0,
            moves=[move, move],
        )


# --- Suggestion ------------------------------------------------------------------------- #


def test_suggestion_needs_at_least_one_piece_of_evidence() -> None:
    with pytest.raises(ValidationError):
        Suggestion(
            rank=1,
            category=MistakeCategory.HUNG_PIECE,
            priority_score=1.0,
            advice_id="a",
            advice_title="t",
            advice_body="b",
            advice_drill="d",
            evidence=[],
        )


def test_suggestion_rank_is_bounded_to_three() -> None:
    with pytest.raises(ValidationError):
        Suggestion(
            rank=4,
            category=MistakeCategory.HUNG_PIECE,
            priority_score=1.0,
            advice_id="a",
            advice_title="t",
            advice_body="b",
            advice_drill="d",
            evidence=[{"game_id": 1, "ply": 3, "san": "Nd5", "dw": 0.3}],
        )

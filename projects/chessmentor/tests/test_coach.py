"""FR-12 — coaching reports: window selection, priority formula, advice snapshot."""

from __future__ import annotations

import pytest
from chessmentor.constants import DEEP_BUDGET, JUDGE_BUDGET
from chessmentor.engine.coach import (
    build_report,
    collect_flagged,
    dominant_phase,
    is_report_basis,
    rank_categories,
    report_deltas,
    select_window,
)
from chessmentor.models import (
    AnalystKind,
    Color,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    MistakeCategory,
    MoveAnalysis,
    Phase,
    Severity,
    Termination,
)

VERSION = "0.1.0"
NOW = "2026-07-31T12:00:00Z"


def _move(
    ply: int,
    delta_w: float,
    category: MistakeCategory | None,
    phase: Phase = Phase.MIDDLEGAME,
) -> MoveAnalysis:
    """A synthetic move row whose delta_w is exactly the value under test."""
    w_before = 0.5 + delta_w / 2
    w_after = w_before - delta_w
    severity = (
        Severity.BLUNDER
        if delta_w >= 0.15
        else (Severity.MISTAKE if delta_w >= 0.10 else Severity.OK)
    )
    return MoveAnalysis(
        ply=ply,
        in_acpl=True,
        cp_best=0,
        cp_played=0,
        cp_loss=0,
        w_before=w_before,
        w_after=w_after,
        delta_w=delta_w,
        severity=severity,
        best_uci="e2e4",
        best_line_san=["e4", "e5"],
        phase=phase,
        category=category,
    )


def _analysis(
    analysis_id: int,
    game_id: int,
    moves: list[MoveAnalysis],
    *,
    analyst: AnalystKind = AnalystKind.INTERNAL,
    node_budget: int = JUDGE_BUDGET,
    version: str = VERSION,
) -> GameAnalysis:
    return GameAnalysis(
        id=analysis_id,
        game_id=game_id,
        analyst=analyst,
        analyst_version=version,
        node_budget=node_budget,
        created_at=NOW,
        book_depth=0,
        acpl=40.0,
        accuracy=90.0,
        perf_rating=1_000.0,
        moves=moves,
    )


def _game(game_id: int, source: GameSource = GameSource.PLAYED) -> Game:
    played = source is GameSource.PLAYED
    return Game(
        id=game_id,
        source=source,
        created_at=NOW,
        seed=1 if played else None,
        player_color=Color.WHITE,
        level_id=4 if played else None,
        level_elo=850.0 if played else None,
        status=GameStatus.PLAYER_WIN,
        termination=Termination.CHECKMATE if played else Termination.IMPORTED_RESULT,
        result_score=1.0,
        ply_count=40,
        rated=played,
    )


# --- report-basis predicate ---------------------------------------------------- #


def test_fr12_report_basis_requires_internal_judge_budget_and_version() -> None:
    assert is_report_basis(_analysis(1, 1, []), VERSION)
    assert not is_report_basis(_analysis(1, 1, [], node_budget=DEEP_BUDGET), VERSION)
    assert not is_report_basis(_analysis(1, 1, [], analyst=AnalystKind.STOCKFISH), VERSION)
    assert not is_report_basis(_analysis(1, 1, [], version="0.0.9"), VERSION)


def test_fr12_window_prefers_the_report_basis_analysis() -> None:
    """M8 scenario 13: a game with both a judge pass and a deeper re-analysis."""
    games = [_game(1)]
    analyses = {
        1: [
            _analysis(10, 1, [_move(5, 0.40, MistakeCategory.HUNG_PIECE)]),
            _analysis(
                11,
                1,
                [_move(5, 0.18, MistakeCategory.POSITIONAL_DRIFT)],
                analyst=AnalystKind.STOCKFISH,
                node_budget=DEEP_BUDGET,
                version="Stockfish 16",
            ),
        ]
    }
    selection = select_window(games, analyses, engine_version=VERSION)
    assert [entry.analysis.id for entry in selection.entries] == [10]
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    assert report.suggestions[0].category is MistakeCategory.HUNG_PIECE


def test_fr12_games_without_a_qualifying_analysis_are_skipped() -> None:
    games = [_game(1), _game(2)]
    analyses = {
        1: [_analysis(10, 1, [_move(5, 0.30, MistakeCategory.HUNG_PIECE)])],
        2: [_analysis(11, 2, [], node_budget=DEEP_BUDGET)],
    }
    selection = select_window(games, analyses, engine_version=VERSION)
    assert [entry.game.id for entry in selection.entries] == [1]
    assert selection.skipped_game_ids == (2,)


def test_fr12_window_is_capped_at_last_games() -> None:
    games = [_game(i) for i in range(1, 8)]
    analyses = {
        i: [_analysis(100 + i, i, [_move(3, 0.2, MistakeCategory.BAD_TRADE)])] for i in range(1, 8)
    }
    selection = select_window(games, analyses, engine_version=VERSION, last_games=3)
    assert [entry.game.id for entry in selection.entries] == [5, 6, 7]


def test_fr12_imported_games_are_excluded_by_default() -> None:
    games = [_game(1), _game(2, GameSource.IMPORTED)]
    analyses = {
        i: [_analysis(100 + i, i, [_move(3, 0.2, MistakeCategory.BAD_TRADE)])] for i in (1, 2)
    }
    default = select_window(games, analyses, engine_version=VERSION)
    assert [entry.game.id for entry in default.entries] == [1]
    included = select_window(games, analyses, engine_version=VERSION, include_imported=True)
    assert [entry.game.id for entry in included.entries] == [1, 2]


def test_fr12_last_games_must_be_positive() -> None:
    with pytest.raises(ValueError, match="last_games"):
        select_window([], {}, engine_version=VERSION, last_games=0)


# --- priority formula ------------------------------------------------------------ #


def test_fr12_priority_is_delta_w_mass_not_frequency() -> None:
    """The conflict scenario frequency-only ordering gets wrong."""
    moves = [
        _move(1, 0.11, MistakeCategory.OPENING_PRINCIPLE, Phase.OPENING),
        _move(3, 0.11, MistakeCategory.OPENING_PRINCIPLE, Phase.OPENING),
        _move(5, 0.11, MistakeCategory.OPENING_PRINCIPLE, Phase.OPENING),
        _move(7, 0.60, MistakeCategory.HUNG_PIECE),
    ]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    scores = rank_categories(collect_flagged(selection.entries, {}))
    assert scores[0].category is MistakeCategory.HUNG_PIECE
    assert scores[0].dw_sum == pytest.approx(0.60)
    assert scores[1].count == 3


def test_fr12_ties_break_on_count_then_recency_then_name() -> None:
    moves = [
        _move(1, 0.20, MistakeCategory.BAD_TRADE),
        _move(3, 0.10, MistakeCategory.HUNG_PIECE),
        _move(5, 0.10, MistakeCategory.HUNG_PIECE),
    ]
    scores = rank_categories(
        collect_flagged(
            select_window(
                [_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION
            ).entries,
            {},
        )
    )
    # Equal dw_sum (0.20) → the higher count wins.
    assert scores[0].dw_sum == pytest.approx(scores[1].dw_sum)
    assert scores[0].category is MistakeCategory.HUNG_PIECE


def test_fr12_name_breaks_a_total_tie() -> None:
    moves = [
        _move(1, 0.20, MistakeCategory.HUNG_PIECE),
        _move(3, 0.20, MistakeCategory.BAD_TRADE),
    ]
    scores = rank_categories(
        collect_flagged(
            select_window(
                [_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION
            ).entries,
            {},
        )
    )
    # Same mass and count; the later instance (ply 3, bad_trade) is more recent.
    assert scores[0].category is MistakeCategory.BAD_TRADE


def test_fr12_only_categorised_moves_count() -> None:
    # An ok-severity move carries no category and never enters a report.
    moves = [_move(1, 0.02, None), _move(3, 0.20, MistakeCategory.BAD_TRADE)]
    flagged = collect_flagged(
        select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION).entries,
        {},
    )
    assert [f.category for f in flagged] == [MistakeCategory.BAD_TRADE]


# --- advice resolution and snapshotting --------------------------------------------- #


def _catalog():
    from chessmentor.datasets import load_advice

    return load_advice()


def test_fr12_advice_prefers_the_phase_specific_entry() -> None:
    moves = [
        _move(1, 0.30, MistakeCategory.HUNG_PIECE, Phase.ENDGAME),
        _move(3, 0.05 + 0.05, MistakeCategory.HUNG_PIECE, Phase.MIDDLEGAME),
    ]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    scores = rank_categories(collect_flagged(selection.entries, {}))
    assert dominant_phase(scores[0]) is Phase.ENDGAME
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    assert report.suggestions[0].advice_id == "hung-piece-endgame-loose-pieces"


def test_fr12_advice_falls_back_to_the_phase_null_entry() -> None:
    moves = [_move(1, 0.30, MistakeCategory.MISSED_MATE, Phase.MIDDLEGAME)]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    entry = next(a for a in _catalog() if a.id == report.suggestions[0].advice_id)
    assert entry.phase is None
    assert entry.category is MistakeCategory.MISSED_MATE


def test_fr12_advice_text_is_snapshotted_into_the_suggestion() -> None:
    catalog = _catalog()
    moves = [_move(1, 0.30, MistakeCategory.HUNG_PIECE)]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=catalog,
        created_at=NOW,
    )
    suggestion = report.suggestions[0]
    source = next(a for a in catalog if a.id == suggestion.advice_id)
    assert suggestion.advice_title == source.title
    assert suggestion.advice_body == source.body
    assert suggestion.advice_drill == source.drill

    # Editing the catalog afterwards cannot change the stored report.
    edited = [
        a.model_copy(update={"title": "REWRITTEN"}) if a.id == suggestion.advice_id else a
        for a in catalog
    ]
    later = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=edited,
        created_at=NOW,
    )
    assert later.suggestions[0].advice_title == "REWRITTEN"
    assert report.suggestions[0].advice_title != "REWRITTEN"


# --- report shape ------------------------------------------------------------------- #


def test_fr12_report_has_at_most_three_ranked_suggestions_with_evidence() -> None:
    moves = [
        _move(1, 0.50, MistakeCategory.HUNG_PIECE),
        _move(3, 0.40, MistakeCategory.BAD_TRADE),
        _move(5, 0.30, MistakeCategory.MISSED_TACTIC),
        _move(7, 0.20, MistakeCategory.POSITIONAL_DRIFT),
    ]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    report = build_report(
        selection=selection,
        san_by_game={1: [f"m{i}" for i in range(40)]},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    assert [s.rank for s in report.suggestions] == [1, 2, 3]
    for suggestion in report.suggestions:
        assert 1 <= len(suggestion.evidence) <= 3
        for item in suggestion.evidence:
            assert item.game_id == 1
            assert item.san
            assert item.dw > 0


def test_fr12_totals_cover_every_flagged_move() -> None:
    moves = [
        _move(1, 0.50, MistakeCategory.HUNG_PIECE, Phase.MIDDLEGAME),
        _move(3, 0.40, MistakeCategory.HUNG_PIECE, Phase.ENDGAME),
    ]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    total = report.totals_by_category[MistakeCategory.HUNG_PIECE]
    assert total.count == 2
    assert total.dw_sum == pytest.approx(0.90)
    assert report.totals_by_phase[Phase.MIDDLEGAME] == pytest.approx(0.50)
    assert report.totals_by_phase[Phase.ENDGAME] == pytest.approx(0.40)


def test_fr12_window_records_the_exact_analysis_ids() -> None:
    games = [_game(1), _game(2)]
    analyses = {
        1: [_analysis(10, 1, [_move(3, 0.2, MistakeCategory.BAD_TRADE)])],
        2: [_analysis(11, 2, [_move(3, 0.2, MistakeCategory.BAD_TRADE)])],
    }
    selection = select_window(games, analyses, engine_version=VERSION)
    report = build_report(
        selection=selection,
        san_by_game={1: ["e4"] * 40, 2: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    assert [(w.game_id, w.analysis_id) for w in report.window] == [(1, 10), (2, 11)]


def test_fr12_report_is_deterministic() -> None:
    moves = [
        _move(1, 0.50, MistakeCategory.HUNG_PIECE),
        _move(3, 0.40, MistakeCategory.BAD_TRADE),
    ]
    selection = select_window([_game(1)], {1: [_analysis(10, 1, moves)]}, engine_version=VERSION)

    def run():
        return build_report(
            selection=selection,
            san_by_game={1: ["e4"] * 40},
            advice_catalog=_catalog(),
            created_at=NOW,
        )

    assert run().model_dump_json() == run().model_dump_json()


def test_fr12_trend_deltas_are_computed_on_read() -> None:
    first = build_report(
        selection=select_window(
            [_game(1)],
            {1: [_analysis(10, 1, [_move(1, 0.50, MistakeCategory.HUNG_PIECE)])]},
            engine_version=VERSION,
        ),
        san_by_game={1: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
    )
    second = build_report(
        selection=select_window(
            [_game(2)],
            {2: [_analysis(11, 2, [_move(1, 0.20, MistakeCategory.HUNG_PIECE)])]},
            engine_version=VERSION,
        ),
        san_by_game={2: ["e4"] * 40},
        advice_catalog=_catalog(),
        created_at=NOW,
        prev_report_id=1,
    )
    deltas = report_deltas(second, first)
    assert deltas[MistakeCategory.HUNG_PIECE] == pytest.approx(-0.30)

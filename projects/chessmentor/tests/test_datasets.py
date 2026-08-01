"""Committed datasets — the invariants `chessmentor init` validates."""

from __future__ import annotations

import json
from itertools import pairwise

import chess
import pytest
from chessmentor.adapters.book_committed import BookValidationError, CommittedBook
from chessmentor.datasets import (
    LADDER_GAP_MAX,
    LADDER_GAP_MIN,
    DatasetError,
    data_dir,
    default_datasets,
    load_advice,
    load_datasets,
    load_levels,
    load_openings,
    sha256_of,
    validate_ladder,
)
from chessmentor.models import MistakeCategory, OpeningLine

from chessmentor import __version__

# --- levels.json --------------------------------------------------------------- #


def test_fr5_ladder_elo_is_strictly_increasing(levels) -> None:
    for prev, nxt in pairwise(levels):
        assert nxt.elo_internal > prev.elo_internal


def test_fr5_ladder_gaps_sit_inside_the_documented_window(levels) -> None:
    """M1b (ii): every adjacent gap in [100, 170] Elo."""
    for prev, nxt in pairwise(levels):
        gap = nxt.elo_internal - prev.elo_internal
        assert LADDER_GAP_MIN <= gap <= LADDER_GAP_MAX


def test_fr7b_acpl_mean_is_strictly_decreasing(levels) -> None:
    """Required for the FR-7b interpolation to be single-valued."""
    for prev, nxt in pairwise(levels):
        assert nxt.acpl_mean < prev.acpl_mean


def test_fr4_ladder_knobs_are_monotone_in_difficulty(levels) -> None:
    """M9's premise: the throttle knobs really are wired and ordered.

    Every rung keeps ``noise_sigma_cp > 0`` and ``blunder_prob > 0``: the FR-5
    calibration landed the whole [100, 170]-gap ladder inside the throttled
    region of the knob space (a clean sigma=0/p=0 top rung would add a final
    gap far above 170 — docs/REVIEW.md B5), so US-3's human-plausible-error
    machinery is active at every level and M9's checks (a) and (c) apply to
    all ten rungs.
    """
    for prev, nxt in pairwise(levels):
        assert nxt.node_budget > prev.node_budget
        assert nxt.max_depth >= prev.max_depth
        assert nxt.noise_sigma_cp <= prev.noise_sigma_cp
        assert nxt.blunder_prob <= prev.blunder_prob
        assert nxt.book_plies >= prev.book_plies
    for level in levels:
        assert level.noise_sigma_cp > 0
        assert level.blunder_prob > 0


def test_ladder_ids_are_contiguous(levels) -> None:
    assert [lv.id for lv in levels] == list(range(1, len(levels) + 1))


def test_ladder_engine_version_must_match(levels) -> None:
    assert all(lv.engine_version == __version__ for lv in levels)
    stale = [levels[0].model_copy(update={"engine_version": "0.0.1"})]
    with pytest.raises(DatasetError, match="re-run FR-5 calibration"):
        validate_ladder(stale)


def test_validate_ladder_rejects_a_collapsed_gap(levels) -> None:
    collapsed = [levels[0], levels[1].model_copy(update={"elo_internal": levels[0].elo_internal})]
    with pytest.raises(DatasetError, match="strictly increase"):
        validate_ladder(collapsed)


def test_validate_ladder_rejects_an_oversized_gap(levels) -> None:
    stretched = [
        levels[0],
        levels[1].model_copy(update={"elo_internal": levels[0].elo_internal + 300}),
    ]
    with pytest.raises(DatasetError, match="outside"):
        validate_ladder(stretched)


def test_validate_ladder_rejects_non_decreasing_acpl(levels) -> None:
    broken = [
        levels[0],
        levels[1].model_copy(update={"acpl_mean": levels[0].acpl_mean + 1}),
    ]
    with pytest.raises(DatasetError, match="acpl_mean"):
        validate_ladder(broken)


# --- openings.json ---------------------------------------------------------------- #


def test_book_lines_are_legal_and_unique(datasets) -> None:
    lines = datasets.book.lines
    assert len(lines) >= 100
    sequences = {tuple(line.uci) for line in lines}
    assert len(sequences) == len(lines)
    for line in lines:
        board = chess.Board()
        for uci in line.uci:
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves, f"{line.eco} {line.name}: {uci}"
            board.push(move)


def test_book_covers_all_five_eco_families(datasets) -> None:
    families = {line.eco[0] for line in datasets.book.lines}
    assert families == {"A", "B", "C", "D", "E"}


def test_book_probe_returns_weighted_continuations(datasets) -> None:
    entries = datasets.book.probe(chess.Board())
    assert entries
    assert all(entry.weight >= 1 for entry in entries)
    ucis = [entry.uci for entry in entries]
    assert ucis == sorted(ucis)
    assert "e2e4" in ucis


def test_book_probe_stops_out_of_book(datasets) -> None:
    board = chess.Board()
    board.push_san("a3")
    assert datasets.book.probe(board) == []


def test_book_probe_ignores_a_non_start_position(datasets) -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    assert datasets.book.probe(board) == []


def test_book_identify_returns_the_longest_prefix(datasets) -> None:
    opening = datasets.book.identify(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "d2d3"])
    assert opening is not None
    assert opening.eco == "C50"
    assert opening.depth == 6
    assert datasets.book.identify(["a2a3"]) is None


def test_book_identify_is_capped_at_twelve_plies(datasets) -> None:
    opening = datasets.book.identify(
        [
            "d2d4",
            "g8f6",
            "c2c4",
            "g7g6",
            "b1c3",
            "f8g7",
            "e2e4",
            "d7d6",
            "g1f3",
            "e8g8",
            "f1e2",
            "e7e5",
            "e1g1",
        ]
    )
    assert opening is not None
    assert opening.depth <= 12


def test_book_rejects_an_illegal_committed_line() -> None:
    bad = OpeningLine(eco="A00", name="Impossible", uci=["e2e4", "e7e5", "e2e4", "e7e5"], weight=1)
    with pytest.raises(BookValidationError, match="illegal move"):
        CommittedBook([bad])


def test_book_rejects_duplicate_lines() -> None:
    line = OpeningLine(eco="C50", name="Italian", uci=["e2e4", "e7e5", "g1f3", "b8c6"], weight=1)
    with pytest.raises(BookValidationError, match="duplicate"):
        CommittedBook([line, line])


# --- advice.json --------------------------------------------------------------------- #


def test_advice_covers_every_category_with_a_generic_entry(datasets) -> None:
    generic = {entry.category for entry in datasets.advice if entry.phase is None}
    assert generic == set(MistakeCategory)


def test_advice_entries_carry_grounding(datasets) -> None:
    for entry in datasets.advice:
        assert entry.source_note.strip()
        assert entry.drill.strip()
        assert len(entry.body) > 40


def test_advice_resolution_prefers_the_phase_variant(datasets) -> None:
    from chessmentor.models import Phase

    endgame = datasets.advice_for(MistakeCategory.HUNG_PIECE, Phase.ENDGAME)
    assert endgame.phase is Phase.ENDGAME
    generic = datasets.advice_for(MistakeCategory.HUNG_PIECE, Phase.MIDDLEGAME)
    assert generic.phase is None


def test_advice_rejects_a_missing_category(tmp_path) -> None:
    (tmp_path / "advice.json").write_text(json.dumps([]))
    with pytest.raises(DatasetError, match="missing"):
        load_advice(tmp_path)


def test_advice_rejects_duplicate_ids(tmp_path, datasets) -> None:
    entries = [entry.model_dump(mode="json") for entry in datasets.advice]
    entries.append(entries[0])
    (tmp_path / "advice.json").write_text(json.dumps(entries))
    with pytest.raises(DatasetError, match="duplicate advice ids"):
        load_advice(tmp_path)


# --- loader behaviour ------------------------------------------------------------------ #


def test_loader_reports_a_missing_dataset(tmp_path) -> None:
    with pytest.raises(DatasetError, match="missing committed dataset"):
        load_levels(tmp_path)


def test_loader_reports_malformed_json(tmp_path) -> None:
    (tmp_path / "openings.json").write_text("{not json")
    with pytest.raises(DatasetError, match="not valid JSON"):
        load_openings(tmp_path)


def test_loader_requires_a_list(tmp_path) -> None:
    (tmp_path / "levels.json").write_text(json.dumps({"levels": []}))
    with pytest.raises(DatasetError, match="list of levels"):
        load_levels(tmp_path)


def test_datasets_expose_integrity_hashes() -> None:
    bundle = load_datasets()
    assert bundle.levels_sha256 == sha256_of(data_dir() / "levels.json")
    assert len(bundle.levels_sha256) == 64
    assert bundle.openings_sha256 != bundle.advice_sha256


def test_default_datasets_are_cached() -> None:
    assert default_datasets() is default_datasets()

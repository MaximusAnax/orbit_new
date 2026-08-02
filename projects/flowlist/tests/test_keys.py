"""FR-5: Camelot conversion and key-relation classification."""

from __future__ import annotations

import pytest
from flowlist.engine.keys import (
    KEY_RELATION_SCORES,
    MAJOR,
    MINOR,
    all_camelot_codes,
    camelot,
    camelot_number,
    camelot_to_pitch_class,
    key_name,
    key_relation,
    key_score,
    parse_key,
    relation_label,
)
from flowlist.engine.models import KeyRelation


def test_fr5_camelot_reference_codes() -> None:
    """The anchor codes any DJ can check against the published wheel."""
    assert camelot(0, MAJOR) == "8B"  # C major
    assert camelot(9, MINOR) == "8A"  # A minor
    assert camelot(7, MAJOR) == "9B"  # G major
    assert camelot(4, MINOR) == "9A"  # E minor
    assert camelot(5, MAJOR) == "7B"  # F major
    assert camelot(2, MINOR) == "7A"  # D minor
    assert camelot(6, MAJOR) == "2B"  # F#/Gb major
    assert camelot(3, MINOR) == "2A"  # D#/Eb minor


def test_fr5_formula_matches_spec() -> None:
    """n_major(pc) = ((7pc + 7) mod 12) + 1 and n_minor(pc) = n_major(pc+3)."""
    for pc in range(12):
        assert camelot_number(pc, MAJOR) == ((7 * pc + 7) % 12) + 1
        assert camelot_number(pc, MINOR) == camelot_number((pc + 3) % 12, MAJOR)


def test_fr5_round_trip_all_24_keys() -> None:
    for pc in range(12):
        for mode in (MINOR, MAJOR):
            code = camelot(pc, mode)
            assert camelot_to_pitch_class(code) == (pc, mode)
    codes = all_camelot_codes()
    assert len(codes) == 24
    assert len({camelot(*camelot_to_pitch_class(c)) for c in codes}) == 24


def test_fr5_wheel_is_a_bijection() -> None:
    """Every (pc, mode) maps to a distinct code and back."""
    produced = {camelot(pc, mode) for pc in range(12) for mode in (MINOR, MAJOR)}
    assert produced == set(all_camelot_codes())


@pytest.mark.parametrize(
    ("frm", "to", "expected"),
    [
        ("8A", "8A", KeyRelation.SAME_KEY),
        ("8B", "8B", KeyRelation.SAME_KEY),
        ("8A", "8B", KeyRelation.RELATIVE),
        ("8B", "8A", KeyRelation.RELATIVE),
        ("8A", "9A", KeyRelation.ADJACENT_FIFTH),
        ("8A", "7A", KeyRelation.ADJACENT_FIFTH),
        ("8A", "9B", KeyRelation.DIAGONAL),
        ("8A", "7B", KeyRelation.DIAGONAL),
        ("8A", "10A", KeyRelation.ENERGY_BOOST),
        ("8A", "6A", KeyRelation.ENERGY_DROP),
        ("8A", "11B", KeyRelation.PARALLEL),  # A minor -> A major
        ("11B", "8A", KeyRelation.PARALLEL),  # A major -> A minor
        ("8A", "3A", KeyRelation.SEMITONE_LIFT),  # +7 steps == +1 semitone
        ("8A", "2A", KeyRelation.CLASH),
        ("8A", "1B", KeyRelation.CLASH),
    ],
)
def test_fr5_named_relations(frm: str, to: str, expected: KeyRelation) -> None:
    a, b = camelot_to_pitch_class(frm), camelot_to_pitch_class(to)
    assert key_relation(*a, *b) is expected


@pytest.mark.parametrize(
    ("frm", "to", "expected"),
    [
        ("12A", "1A", KeyRelation.ADJACENT_FIFTH),
        ("1A", "12A", KeyRelation.ADJACENT_FIFTH),
        ("12B", "1B", KeyRelation.ADJACENT_FIFTH),
        ("1B", "12B", KeyRelation.ADJACENT_FIFTH),
        ("12A", "2A", KeyRelation.ENERGY_BOOST),
        ("1A", "11A", KeyRelation.ENERGY_DROP),
        ("12A", "1B", KeyRelation.DIAGONAL),
        ("12A", "12B", KeyRelation.RELATIVE),
    ],
)
def test_fr5_wraparound(frm: str, to: str, expected: KeyRelation) -> None:
    """The 12<->1 seam is where naive modulo implementations break."""
    a, b = camelot_to_pitch_class(frm), camelot_to_pitch_class(to)
    assert key_relation(*a, *b) is expected


def test_fr5_semitone_lift_is_one_semitone_up() -> None:
    """+7 wheel steps is exactly +1 semitone, in the same mode."""
    for pc in range(12):
        for mode in (MINOR, MAJOR):
            lifted = ((pc + 1) % 12, mode)
            assert key_relation(pc, mode, *lifted) is KeyRelation.SEMITONE_LIFT


def test_fr5_parallel_shares_a_tonic() -> None:
    """Parallel major/minor keeps the tonic and flips the mode."""
    for pc in range(12):
        assert key_relation(pc, MINOR, pc, MAJOR) is KeyRelation.PARALLEL
        assert key_relation(pc, MAJOR, pc, MINOR) is KeyRelation.PARALLEL


def test_fr5_relative_shares_all_seven_tones() -> None:
    """Relative major of a minor key is 3 semitones up (A minor -> C major)."""
    for pc in range(12):
        assert key_relation(pc, MINOR, (pc + 3) % 12, MAJOR) is KeyRelation.RELATIVE
        assert key_relation(pc, MAJOR, (pc - 3) % 12, MINOR) is KeyRelation.RELATIVE


def test_fr5_every_pair_is_classified_exactly_once() -> None:
    """FR-5: any ordered key pair lands in exactly one named relation."""
    seen: dict[KeyRelation, int] = {}
    for a_pc in range(12):
        for a_mode in (MINOR, MAJOR):
            for b_pc in range(12):
                for b_mode in (MINOR, MAJOR):
                    relation = key_relation(a_pc, a_mode, b_pc, b_mode)
                    assert relation is not KeyRelation.UNKNOWN
                    seen[relation] = seen.get(relation, 0) + 1
    assert sum(seen.values()) == 24 * 24
    # Every named relation from the D2 table is reachable.
    assert set(seen) == set(KEY_RELATION_SCORES)


def test_fr5_relation_scores_match_the_d2_table() -> None:
    assert KEY_RELATION_SCORES == {
        KeyRelation.SAME_KEY: 1.00,
        KeyRelation.RELATIVE: 0.95,
        KeyRelation.ADJACENT_FIFTH: 0.85,
        KeyRelation.DIAGONAL: 0.75,
        KeyRelation.PARALLEL: 0.65,
        KeyRelation.ENERGY_BOOST: 0.55,
        KeyRelation.ENERGY_DROP: 0.45,
        KeyRelation.SEMITONE_LIFT: 0.30,
        KeyRelation.CLASH: 0.10,
    }


def test_fr5_boost_outranks_drop() -> None:
    """D2's intentional asymmetry: +2 semitones beats -2."""
    assert (
        KEY_RELATION_SCORES[KeyRelation.ENERGY_BOOST]
        > (KEY_RELATION_SCORES[KeyRelation.ENERGY_DROP])
    )
    up = key_score(*camelot_to_pitch_class("8A"), *camelot_to_pitch_class("10A"))
    down = key_score(*camelot_to_pitch_class("8A"), *camelot_to_pitch_class("6A"))
    assert up.score is not None and down.score is not None
    assert up.score > down.score


def test_fr5_clash_floor_is_not_zero() -> None:
    """D2: the 0.10 floor keeps the optimizer's landscape informative."""
    score, relation = key_score(*camelot_to_pitch_class("8A"), *camelot_to_pitch_class("2A"))
    assert relation is KeyRelation.CLASH
    assert score == 0.10


def test_fr5_missing_key_is_unknown_not_a_guess() -> None:
    assert key_score(None, None, 9, 0) == (None, KeyRelation.UNKNOWN)
    assert key_score(9, 0, None, None) == (None, KeyRelation.UNKNOWN)
    assert key_score(None, None, None, None) == (None, KeyRelation.UNKNOWN)


def test_fr5_enharmonics_collapse() -> None:
    """F# and Gb are one pitch class (D2)."""
    assert parse_key("F#") == parse_key("Gb")
    assert camelot(*parse_key("F#")) == camelot(*parse_key("Gb"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8A", (9, MINOR)),
        ("8a", (9, MINOR)),
        (" 12B ", (4, MAJOR)),
        ("Am", (9, MINOR)),
        ("A minor", (9, MINOR)),
        ("C", (0, MAJOR)),
        ("CM", (0, MAJOR)),
        ("Cm", (0, MINOR)),
        ("F#m", (6, MINOR)),
        ("Bb", (10, MAJOR)),
    ],
)
def test_fr5_parse_key(text: str, expected: tuple[int, int]) -> None:
    assert parse_key(text) == expected


@pytest.mark.parametrize("text", ["", "13A", "8C", "H", "Am7b5", "zz"])
def test_fr5_parse_key_rejects_nonsense(text: str) -> None:
    with pytest.raises(ValueError):
        parse_key(text)


def test_fr5_key_name_round_trips() -> None:
    for pc in range(12):
        for mode in (MINOR, MAJOR):
            assert parse_key(key_name(pc, mode)) == (pc, mode)


@pytest.mark.parametrize(("pc", "mode"), [(-1, 0), (12, 1), (0, 2), (0, -1)])
def test_fr5_invalid_inputs_rejected(pc: int, mode: int) -> None:
    with pytest.raises(ValueError):
        camelot(pc, mode)


@pytest.mark.parametrize("code", ["0A", "13B", "8C", "A8", ""])
def test_fr5_invalid_camelot_codes_rejected(code: str) -> None:
    with pytest.raises(ValueError):
        camelot_to_pitch_class(code)


def test_fr5_every_relation_has_a_label() -> None:
    for relation in KeyRelation:
        assert relation_label(relation)

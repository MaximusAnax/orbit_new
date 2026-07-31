"""Camelot key math and DJ relation classification (FR-5, D2).

The Camelot wheel (Mark Davis / Camelot Sound "Easymix", popularised by Mixed
In Key) numbers the 24 keys 1-12 with a letter: ``A`` for minor, ``B`` for
major.  One step clockwise is a perfect fifth (+7 semitones), so

    n_major(pc) = ((7 * pc + 7) mod 12) + 1
    n_minor(pc) = n_major((pc + 3) mod 12)

Both conversions are total and exactly invertible: 7 is its own inverse
mod 12 (7 * 7 = 49 = 1 mod 12), which is what makes
:func:`camelot_to_pitch_class` a closed form rather than a lookup table.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from flowlist.engine.models import KeyRelation

#: Score per named relation (SCOPE.md D2 table).  The 0.10 clash floor is not
#: zero on purpose: it keeps the optimizer's landscape informative.
KEY_RELATION_SCORES: dict[KeyRelation, float] = {
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

#: Human labels used by ``explain`` and the CLI (US-4).
KEY_RELATION_LABELS: dict[KeyRelation, str] = {
    KeyRelation.SAME_KEY: "same key",
    KeyRelation.RELATIVE: "relative major/minor",
    KeyRelation.ADJACENT_FIFTH: "adjacent fifth/fourth",
    KeyRelation.DIAGONAL: "diagonal",
    KeyRelation.PARALLEL: "parallel major/minor",
    KeyRelation.ENERGY_BOOST: "energy boost (+2)",
    KeyRelation.ENERGY_DROP: "energy drop (-2)",
    KeyRelation.SEMITONE_LIFT: "semitone lift",
    KeyRelation.CLASH: "clash",
    KeyRelation.UNKNOWN: "unknown key",
}

MINOR = 0
MAJOR = 1

_CAMELOT_RE = re.compile(r"^\s*(\d{1,2})\s*([AaBb])\s*$")
_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)\s*(.*?)\s*$")

_NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PC_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

_MINOR_WORDS = {"m", "min", "minor", "-"}
_MAJOR_WORDS = {"", "maj", "major"}


class KeyScore(NamedTuple):
    """Result of :func:`key_score`.

    ``score`` is ``None`` exactly when ``relation`` is
    :attr:`~flowlist.engine.models.KeyRelation.UNKNOWN` (a key is missing on at
    least one side); the caller substitutes the D10 neutral.
    """

    score: float | None
    relation: KeyRelation


def camelot_number(pitch_class: int, mode: int) -> int:
    """Wheel number 1-12 for a (pitch class, mode) pair."""
    if not 0 <= pitch_class <= 11:
        raise ValueError(f"pitch class out of range: {pitch_class}")
    if mode not in (MINOR, MAJOR):
        raise ValueError(f"mode must be 0 (minor) or 1 (major), got {mode}")
    pc = pitch_class if mode == MAJOR else (pitch_class + 3) % 12
    return ((7 * pc + 7) % 12) + 1


def camelot(pitch_class: int, mode: int) -> str:
    """Camelot code, e.g. ``(9, 0) -> "8A"`` (A minor)."""
    return f"{camelot_number(pitch_class, mode)}{'B' if mode == MAJOR else 'A'}"


def camelot_to_pitch_class(code: str) -> tuple[int, int]:
    """Inverse of :func:`camelot`: ``"8A" -> (9, 0)``.

    Closed form: from ``n = ((7*pc + 7) mod 12) + 1`` we get
    ``pc = 7*(n - 8) mod 12`` because 7 is self-inverse mod 12.
    """
    match = _CAMELOT_RE.match(code)
    if match is None:
        raise ValueError(f"not a Camelot code: {code!r}")
    number = int(match.group(1))
    if not 1 <= number <= 12:
        raise ValueError(f"Camelot number out of range: {number}")
    mode = MAJOR if match.group(2).upper() == "B" else MINOR
    pc_major = (7 * (number - 8)) % 12
    pc = pc_major if mode == MAJOR else (pc_major - 3) % 12
    return pc, mode


def key_name(pitch_class: int, mode: int) -> str:
    """Musical spelling, e.g. ``(9, 0) -> "Am"``.  Enharmonics collapse (D2)."""
    if not 0 <= pitch_class <= 11:
        raise ValueError(f"pitch class out of range: {pitch_class}")
    if mode not in (MINOR, MAJOR):
        raise ValueError(f"mode must be 0 (minor) or 1 (major), got {mode}")
    return _PC_NAMES[pitch_class] + ("" if mode == MAJOR else "m")


def parse_key(text: str) -> tuple[int, int]:
    """Parse ``"8A"``, ``"Am"``, ``"F#m"``, ``"C major"`` -> (pitch class, mode).

    Used by the CLI's ``features set --key`` (SCOPE Architecture-CLI); pure so
    it lives with the rest of the key math.
    """
    if _CAMELOT_RE.match(text):
        return camelot_to_pitch_class(text)
    match = _NOTE_RE.match(text)
    if match is None:
        raise ValueError(f"unrecognised key: {text!r}")
    letter, accidental, qualifier = match.groups()
    pc = _NOTE_PC[letter.upper()]
    if accidental in ("#", "♯"):
        pc = (pc + 1) % 12
    elif accidental in ("b", "♭"):
        pc = (pc - 1) % 12
    qualifier = qualifier.strip()
    if qualifier == "M":  # case matters: "CM" is C major, "Cm" is C minor
        return pc, MAJOR
    lowered = qualifier.lower()
    if lowered in _MINOR_WORDS:
        return pc, MINOR
    if lowered in _MAJOR_WORDS:
        return pc, MAJOR
    raise ValueError(f"unrecognised key quality in {text!r}")


def key_relation(from_pc: int, from_mode: int, to_pc: int, to_mode: int) -> KeyRelation:
    """Classify an ordered key pair into exactly one named relation (D2).

    Everything is decided by ``delta = (n_b - n_a) mod 12`` plus whether the
    letter changed, which handles the 12<->1 wraparound without special cases.
    """
    n_a = camelot_number(from_pc, from_mode)
    n_b = camelot_number(to_pc, to_mode)
    delta = (n_b - n_a) % 12
    same_letter = from_mode == to_mode

    if same_letter:
        if delta == 0:
            return KeyRelation.SAME_KEY
        if delta in (1, 11):
            return KeyRelation.ADJACENT_FIFTH
        if delta == 2:
            return KeyRelation.ENERGY_BOOST
        if delta == 10:  # -2
            return KeyRelation.ENERGY_DROP
        if delta == 7:  # +7 wheel steps == +1 semitone
            return KeyRelation.SEMITONE_LIFT
        return KeyRelation.CLASH

    if delta == 0:
        return KeyRelation.RELATIVE
    if delta in (1, 11):
        return KeyRelation.DIAGONAL
    # Parallel major/minor shares a tonic: A(minor) -> B(major) is +3 steps,
    # B(major) -> A(minor) is -3 steps.
    if from_mode == MINOR and delta == 3:
        return KeyRelation.PARALLEL
    if from_mode == MAJOR and delta == 9:
        return KeyRelation.PARALLEL
    return KeyRelation.CLASH


def key_score(
    from_pc: int | None,
    from_mode: int | None,
    to_pc: int | None,
    to_mode: int | None,
) -> KeyScore:
    """Harmonic compatibility of an ordered pair.

    Returns ``(score, relation)`` where ``score`` is the D2 table value, or
    ``(None, UNKNOWN)`` when either side has no key — the D10 neutral is the
    caller's job so the key rulebook itself never invents data.
    """
    if from_pc is None or from_mode is None or to_pc is None or to_mode is None:
        return KeyScore(None, KeyRelation.UNKNOWN)
    relation = key_relation(from_pc, from_mode, to_pc, to_mode)
    return KeyScore(KEY_RELATION_SCORES[relation], relation)


def relation_label(relation: KeyRelation) -> str:
    return KEY_RELATION_LABELS[relation]


def all_camelot_codes() -> list[str]:
    """The 24 codes, ``1A``..``12A`` then ``1B``..``12B``."""
    return [f"{n}{letter}" for letter in ("A", "B") for n in range(1, 13)]


__all__ = [
    "KEY_RELATION_LABELS",
    "KEY_RELATION_SCORES",
    "MAJOR",
    "MINOR",
    "KeyScore",
    "all_camelot_codes",
    "camelot",
    "camelot_number",
    "camelot_to_pitch_class",
    "key_name",
    "key_relation",
    "key_score",
    "parse_key",
    "relation_label",
]

"""FR-1: normalization, the duplicate hash, tags, and the porter stemmer."""

from __future__ import annotations

import pytest
from almanac.engine.normalize import (
    normalize_author,
    normalize_tag,
    normalize_text,
    normalized_hash,
    phrase_occurrences,
    stemmed_tokens,
    tokenize,
)
from almanac.engine.stemmer import stem


def test_fr1_normalization_strips_case_punctuation_and_whitespace():
    assert normalize_text("  The Quick,  BROWN fox! ") == "the quick brown fox"


def test_fr1_normalization_folds_diacritics_and_smart_quotes():
    # FR-4 relies on this: a diacritic or curly-quote fix must not change the hash.
    assert normalize_text("Café — don’t") == normalize_text("Cafe - don't")  # noqa: RUF001
    assert normalized_hash("Café — don’t") == normalized_hash("cafe - dont")  # noqa: RUF001


def test_fr1_hash_is_stable_under_punctuation_only_edits():
    original = "You have power over your mind - not outside events."
    typo_fixed = "You have power over your mind — not outside events!"
    assert normalized_hash(original) == normalized_hash(typo_fixed)


def test_fr1_hash_changes_on_a_semantic_edit():
    assert normalized_hash("power over your mind") != normalized_hash("power over your money")


def test_fr1_hash_is_sha256_hex():
    digest = normalized_hash("anything")
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_fr1_tokenize_and_stem():
    assert tokenize("Running, quickly!") == ["running", "quickly"]
    # "quickly" -> "quickli" is Porter's documented behaviour, not a bug.
    assert stemmed_tokens("Running quickly") == ["run", "quickli"]
    assert stemmed_tokens("habits") == stemmed_tokens("habit")


def test_fr1_tag_normalization():
    assert normalize_tag("  Deep Work ") == "deep-work"
    assert normalize_tag("Stoicism") == "stoicism"
    assert normalize_tag("a  b   c") == "a-b-c"
    assert normalize_tag("!!!") == ""


def test_fr1_author_normalization():
    assert normalize_author("Albert  Einstein") == "albert einstein"
    assert normalize_author(None) == ""


def test_fr1_phrase_occurrences_counts_contiguous_runs():
    tokens = tokenize("time and time again time")
    assert phrase_occurrences(tokens, ["time"]) == 3
    assert phrase_occurrences(tokens, ["time", "again"]) == 1
    assert phrase_occurrences(tokens, ["again", "time", "and"]) == 0


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("caresses", "caress"),
        ("ponies", "poni"),
        ("cats", "cat"),
        ("feed", "feed"),
        ("agreed", "agre"),
        ("plastered", "plaster"),
        ("motoring", "motor"),
        ("sing", "sing"),
        ("hopping", "hop"),
        ("falling", "fall"),
        ("happy", "happi"),
        ("sky", "sky"),
        ("relational", "relat"),
        ("conditional", "condit"),
        ("digitizer", "digit"),
        ("predication", "predic"),
        ("hopefulness", "hope"),
        ("formaliti", "formal"),
        ("electricity", "electr"),
        ("adjustment", "adjust"),
        ("dependent", "depend"),
        ("adoption", "adopt"),
        ("effective", "effect"),
        ("controlling", "control"),
        ("roll", "roll"),
    ],
)
def test_fr1_porter_stemmer_matches_reference_vocabulary(word, expected):
    assert stem(word) == expected


def test_fr1_stemmer_leaves_short_and_non_alpha_tokens_alone():
    assert stem("is") == "is"
    assert stem("2026") == "2026"

"""FR-1: normalization, hashing, idempotent ingestion, the active-window flag."""

from __future__ import annotations

import pytest
from newsalpha.engine.normalize import (
    canonical_json,
    content_hash,
    hex16,
    normalize_text,
    split_sentences,
)
from newsalpha.engine.pipeline import active_window, normalize_article, prepare_articles
from newsalpha_testkit import AS_OF, article, raw


def test_fr1_html_strip_entity_decode_and_whitespace_collapse():
    text = normalize_text(
        "<p>Apple  &amp; Co.</p>\n<script>var x = '<b>';</script><b>beat</b> estimates</p>"
    )
    assert text == "Apple & Co. beat estimates"


def test_fr1_normalization_is_nfkc():
    # U+FF21 FULLWIDTH LATIN CAPITAL A folds to "A" under NFKC.
    assert normalize_text("\uff21\uff2150") == "AA50"  # fullwidth A A


def test_fr1_content_hash_is_over_title_newline_body():
    assert content_hash("t", "b") == hex16_full("t\nb")


def hex16_full(payload: str) -> str:
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_fr1_ids_are_content_derived_and_stable(datasets):
    first = article(datasets, "x1", "Title", "Body")
    second = article(datasets, "x1", "Title", "Body")
    assert first.id == second.id
    assert len(first.id) == 16
    assert hex16("a") != hex16("b")


def test_fr1_reingesting_the_same_external_id_is_a_no_op(datasets):
    stored = [article(datasets, "x1", "Title", "Body")]
    again = prepare_articles([raw("x1", "Title", "Body")], stored, datasets, AS_OF)
    assert again == []


def test_fr1_reingesting_the_same_content_hash_is_a_no_op(datasets):
    stored = [article(datasets, "x1", "Title", "Body")]
    duplicate = raw("different-id", "Title", "Body")
    assert prepare_articles([duplicate], stored, datasets, AS_OF) == []


def test_fr1_tier_resolves_from_source_tiers(datasets):
    assert article(datasets, "a", "t", "b", domain="reuters.example").tier.value == "t2_wire"
    assert article(datasets, "b", "t", "b", domain="sec.example").tier.value == "t1_official"
    assert article(datasets, "c", "t", "b", domain="unknown.example").tier.value == "t3_other"


def test_fr1_old_articles_are_archived_and_excluded_from_analysis(datasets):
    old = article(datasets, "old", "t", "b", published_at="2026-01-01T00:00:00Z", as_of=AS_OF)
    fresh = article(datasets, "new", "t2", "b2", published_at="2026-03-19T00:00:00Z")
    assert old.excluded_from_analysis is True
    assert fresh.excluded_from_analysis is False
    assert active_window([old, fresh], AS_OF) == [fresh]


def test_fr1_exclusion_flag_is_set_once_at_insert(datasets):
    """The flag is a function of the *inserting* as_of, and the model is frozen."""
    old = article(datasets, "old", "t", "b", published_at="2026-01-01T00:00:00Z", as_of=AS_OF)
    with pytest.raises(Exception, match=r"frozen|immutable"):
        old.excluded_from_analysis = False


def test_fr1_sentence_split_honours_abbreviations(datasets):
    text = "Apple Inc. beat estimates. Shares rose 4%."
    sentences = split_sentences(text, datasets.lexicons.abbreviations)
    assert [s.text for s in sentences] == ["Apple Inc. beat estimates.", "Shares rose 4%."]


def test_fr1_sentence_offsets_index_the_analysis_text(datasets):
    row = article(datasets, "s", "Headline here", "First sentence. Second sentence.")
    text = row.analysis_text
    for sentence in split_sentences(text, datasets.lexicons.abbreviations):
        assert text[sentence.start : sentence.end] == sentence.text
    assert text.startswith("Headline here\n")


def test_fr1_headline_is_its_own_sentence(datasets):
    row = article(datasets, "s", "APPLE NEAR DEAL", "Apple is in talks.")
    sentences = split_sentences(row.analysis_text, datasets.lexicons.abbreviations)
    assert sentences[0].text == "APPLE NEAR DEAL"


def test_fr1_canonical_json_is_sorted_and_compact():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_fr1_published_at_is_normalized_to_utc_z(datasets):
    row = normalize_article(
        raw("tz", "t", "b", published_at="2026-03-10T10:00:00+02:00"), datasets, AS_OF
    )
    assert row.published_at == "2026-03-10T08:00:00Z"

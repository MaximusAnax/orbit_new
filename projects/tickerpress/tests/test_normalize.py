"""FR-3 — normalization, tokenization and sentence splitting."""

from __future__ import annotations

import pytest
from tickerpress.engine.models import TextField
from tickerpress.engine.normalize import (
    analyze_article_text,
    analyze_field,
    normalize_text,
    split_sentences,
    strip_html,
    tokenize,
)
from tickerpress_testkit import make_lexicons


@pytest.fixture
def lex():
    return make_lexicons()


def test_fr3_html_is_stripped_and_entities_decoded() -> None:
    raw = "<p>Apple &amp; Co. <b>beats</b> estimates</p><script>evil()</script>"
    assert normalize_text(raw) == "Apple & Co. beats estimates"


def test_fr3_tags_act_as_token_separators() -> None:
    assert strip_html("Apple<b>Inc</b>").strip() == "Apple Inc"


def test_fr3_curly_quotes_and_dashes_are_folded() -> None:
    raw = "\u201cApple\u2019s\u201d March\u2014quarter \u2013 results\u2026"
    assert normalize_text(raw) == '"Apple\'s" March-quarter - results...'


def test_fr3_unicode_is_nfc_normalized() -> None:
    decomposed = "Cafe\u0301 Corp"
    composed = "Caf\u00e9 Corp"
    assert decomposed != composed
    assert normalize_text(decomposed) == composed


def test_fr3_whitespace_is_collapsed() -> None:
    assert normalize_text("  Apple\n\tbeats   estimates \u00a0 ") == "Apple beats estimates"


def test_fr3_invisible_characters_are_dropped() -> None:
    assert normalize_text("App\u200ble \u00adInc.") == "Apple Inc."


def test_fr3_hyphen_slash_and_dot_are_token_separators(lex) -> None:
    tokens = [token.text for token in tokenize("Meta-analysis and/or U.S.-based apple.com", lex)]
    assert tokens == ["Meta", "analysis", "and", "or", "U.S.", "based", "apple", "com"]


def test_fr3_meta_is_matchable_inside_a_hyphen_compound(lex) -> None:
    tokens = tokenize("Meta-analysis finds", lex)
    assert tokens[0].text == "Meta"
    assert (tokens[0].start, tokens[0].end) == (0, 4)


def test_fr3_abbreviations_keep_their_dot(lex) -> None:
    tokens = [token.text for token in tokenize("Apple Inc. said Dr. Chen agreed", lex)]
    assert tokens == ["Apple", "Inc.", "said", "Dr.", "Chen", "agreed"]


def test_fr3_token_offsets_index_back_into_the_text(lex) -> None:
    text = "Apple Inc. (NASDAQ: AAPL) rose 3% today"
    for token in tokenize(text, lex):
        assert text[token.start : token.end] == token.text


def test_fr3_sentences_split_on_terminator_space_uppercase(lex) -> None:
    text = "Apple rose. Shares fell later! Was that expected? Yes."
    spans = [text[s.start : s.end] for s in split_sentences(text, lex)]
    assert spans == ["Apple rose.", "Shares fell later!", "Was that expected?", "Yes."]


def test_fr3_sentence_split_respects_committed_abbreviations(lex) -> None:
    text = "Apple Inc. said Tuesday it would appeal. Regulators disagreed."
    spans = [text[s.start : s.end] for s in split_sentences(text, lex)]
    assert spans == ["Apple Inc. said Tuesday it would appeal.", "Regulators disagreed."]


def test_fr3_decimal_numbers_do_not_split_sentences(lex) -> None:
    text = "Revenue was 96.4 billion dollars. Shares rose."
    assert len(split_sentences(text, lex)) == 2


def test_fr3_title_is_treated_as_one_sentence(lex) -> None:
    title = "Apple rose. Shares fell later."
    field = analyze_field(TextField.TITLE, title, lex, single_sentence=True)
    assert len(field.sentences) == 1
    assert field.is_sentence_initial(0)
    assert not field.is_sentence_initial(1)


def test_fr3_summary_splits_but_title_does_not(lex) -> None:
    article = analyze_article_text(
        "Apple rose. Shares fell.", "Apple rose. Shares fell.", None, lex
    )
    assert len(article.title.sentences) == 1
    assert len(article.summary.sentences) == 2


def test_fr3_allcaps_run_requires_three_consecutive_caps_tokens(lex) -> None:
    shouty = analyze_field(TextField.TITLE, "FED SAYS ALL OPTIONS REMAIN OPEN", lex)
    assert shouty.in_allcaps_run(0, 0)
    quiet = analyze_field(TextField.TITLE, "Apple Inc. (NASDAQ: AAPL) rose", lex)
    assert not any(quiet.in_allcaps_run(i, i) for i in range(quiet.token_count))


def test_fr3_two_caps_tokens_are_not_a_run(lex) -> None:
    field = analyze_field(TextField.SUMMARY, "The SEC and FTC disagreed about it", lex)
    assert field.allcaps_token_indices == frozenset()


def test_fr3_token_span_covers_a_multi_word_surface(lex) -> None:
    field = analyze_field(TextField.SUMMARY, "Apple Inc. said today", lex)
    assert field.token_span(0, 10) == (0, 1)
    assert field.token_span(0, 5) == (0, 0)


def test_fr3_article_text_token_counts(lex) -> None:
    article = analyze_article_text("One two", "three four five", "six seven", lex)
    assert article.token_count == 7
    assert article.content_token_count == 2
    article_without_content = analyze_article_text("One two", "three", None, lex)
    assert article_without_content.content_token_count == 0
    assert article_without_content.content is None

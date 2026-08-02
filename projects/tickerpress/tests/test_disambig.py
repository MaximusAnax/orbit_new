"""FR-6 — disambiguation scoring, including the four SCOPE worked examples.

Feature vectors are pinned exactly. The lexicons here are synthetic on purpose:
SCOPE's architecture passes :class:`Lexicons` into engine functions precisely so
a test can fix the cue inventory and assert a feature value rather than a mood.
"""

from __future__ import annotations

import pytest
from tickerpress.engine.detect import scan
from tickerpress.engine.disambig import THETA, MentionFeatures, score_article, score_features
from tickerpress.engine.models import AliasKind, Strength
from tickerpress.engine.watchlist import compile_matcher
from tickerpress_testkit import article_of, build_watchlist, make_lexicons, score_in

CUES = ["beats", "estimates", "quarterly", "revenue", "analyst"]
ANTIS = ["growers", "frost", "harvest", "orchard", "statin"]


@pytest.fixture
def lex():
    return make_lexicons(corporate_cues=CUES, anti_cues=ANTIS, common_words=["meta", "all", "cat"])


def apple(**kwargs):
    entry = {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "aliases": [
            ("AAPL", AliasKind.TICKER_SYMBOL),
            ("$AAPL", AliasKind.CASHTAG),
            ("Apple Inc.", AliasKind.LEGAL_NAME),
            ("Apple", AliasKind.SHORT_NAME),
        ],
    }
    entry.update(kwargs)
    return entry


def meta(**kwargs):
    entry = {
        "ticker": "META",
        "name": "Meta Platforms, Inc.",
        "aliases": [("Meta", AliasKind.SHORT_NAME)],
    }
    entry.update(kwargs)
    return entry


def weak_one(scored):
    weak = [item for item in scored if item.candidate.strength is Strength.WEAK]
    assert len(weak) == 1, f"expected one weak candidate, got {[w.candidate.surface for w in weak]}"
    return weak[0]


# ---------------------------------------------------------------------------
# the four SCOPE FR-6 worked examples
# ---------------------------------------------------------------------------


def test_fr6_worked_example_mumbai_accepts_on_a_context_term(lex) -> None:
    article = article_of(
        "Apple opens flagship store in Mumbai",
        "The Cupertino store will open on Friday.",
        lexicons=lex,
    )
    scored = weak_one(
        score_in(article, [apple(context_terms=["cupertino"], anti_terms=["orchard"])], lex)
    )
    assert scored.features == MentionFeatures(
        prior=0.25,
        coref_strong=0,
        case_signal=0,
        window_cues=0,
        window_antis=0,
        doc_cues=0,
        doc_antis=0,
        ctx_terms=1,
        anti_terms=0,
        hyphen_compound=0,
        allcaps_run=0,
    )
    assert scored.score == 0.40
    assert scored.accepted


def test_fr6_worked_example_apple_growers_is_rejected(lex) -> None:
    article = article_of(
        "Apple growers brace for frost",
        "Orchard owners expect a difficult harvest this year.",
        lexicons=lex,
    )
    scored = weak_one(
        score_in(article, [apple(anti_terms=["orchard", "fruit", "cider", "harvest"])], lex)
    )
    assert scored.features == MentionFeatures(
        prior=0.25,
        coref_strong=0,
        case_signal=0,
        window_cues=0,
        window_antis=2,
        doc_cues=0,
        doc_antis=2,
        ctx_terms=0,
        anti_terms=2,
        hyphen_compound=0,
        allcaps_run=0,
    )
    assert scored.score == 0.0
    assert not scored.accepted


def test_fr6_worked_example_meta_analysis_is_rejected(lex) -> None:
    article = article_of(
        "Meta-analysis finds statin benefit overstated",
        "Researchers pooled 42 clinical trials involving 12,000 patients.",
        lexicons=lex,
    )
    scored = weak_one(score_in(article, [meta(anti_terms=["clinical", "patients"])], lex))
    assert scored.features == MentionFeatures(
        prior=0.25,
        coref_strong=0,
        case_signal=0,
        window_cues=0,
        window_antis=1,
        doc_cues=0,
        doc_antis=0,
        ctx_terms=0,
        anti_terms=2,
        hyphen_compound=1,
        allcaps_run=0,
    )
    assert scored.score == 0.0
    assert not scored.accepted


def test_fr6_worked_example_coreference_clamps_to_one(lex) -> None:
    article = article_of(
        "Apple beats March-quarter estimates",
        "Apple Inc. (NASDAQ: AAPL) posted quarterly revenue ahead of analyst "
        "forecasts as iPhone sales held up.",
        lexicons=lex,
    )
    scored = weak_one(score_in(article, [apple(context_terms=["cupertino", "iphone"])], lex))
    assert scored.features == MentionFeatures(
        prior=0.25,
        coref_strong=1,
        case_signal=0,
        window_cues=2,
        window_antis=0,
        doc_cues=3,
        doc_antis=0,
        ctx_terms=1,
        anti_terms=0,
        hyphen_compound=0,
        allcaps_run=0,
    )
    assert scored.score == 1.0  # raw 1.25, clamped
    assert scored.accepted


# ---------------------------------------------------------------------------
# the five FR-6 scoring rules
# ---------------------------------------------------------------------------


def test_fr6_rule1_title_is_a_sentence_so_its_first_token_has_no_case_signal(lex) -> None:
    article = article_of("Apple opens a store", lexicons=lex)
    assert weak_one(score_in(article, [apple()], lex)).features.case_signal == 0


def test_fr6_rule1_mid_sentence_surface_earns_the_case_signal(lex) -> None:
    article = article_of("Retailers gather as Apple opens a store", lexicons=lex)
    assert weak_one(score_in(article, [apple()], lex)).features.case_signal == 1


def test_fr6_rule1_summary_sentence_initial_surface_has_no_case_signal(lex) -> None:
    article = article_of("Retail roundup", "Shops opened. Apple opened one too.", lexicons=lex)
    assert weak_one(score_in(article, [apple()], lex)).features.case_signal == 0


def test_fr6_rule2_window_never_crosses_a_field_boundary(lex) -> None:
    article = article_of("Apple opens a store", "Revenue and analyst estimates rose.", lexicons=lex)
    features = weak_one(score_in(article, [apple()], lex)).features
    assert features.window_cues == 0
    assert features.doc_cues == 3


def test_fr6_rule2_window_is_clipped_to_twelve_tokens(lex) -> None:
    far = " ".join(["filler"] * 15)
    article = article_of(f"Apple {far} revenue", lexicons=lex)
    features = weak_one(score_in(article, [apple()], lex)).features
    assert features.window_cues == 0
    assert features.doc_cues == 1


def test_fr6_rule2_window_and_doc_counts_are_disjoint_by_position(lex) -> None:
    """One lemma inside the window and again outside it counts on both sides."""

    filler = " ".join(["filler"] * 20)
    article = article_of(f"Apple revenue rose {filler} revenue again", lexicons=lex)
    features = weak_one(score_in(article, [apple()], lex)).features
    assert features.window_cues == 1
    assert features.doc_cues == 1


def test_fr6_rule3_global_cues_and_company_terms_score_independently(lex) -> None:
    """`revenue` is both a global cue and a company context term here."""

    article = article_of("Apple revenue rose", lexicons=lex)
    features = weak_one(score_in(article, [apple(context_terms=["revenue"])], lex)).features
    assert features.window_cues == 1
    assert features.ctx_terms == 1
    assert features.prior + 0.10 + 0.15 == pytest.approx(features.prior + 0.25)


def test_fr6_rule3_multi_word_terms_match_as_token_sequences(lex) -> None:
    article = article_of("Apple opens a shop", "The App Store rules changed.", lexicons=lex)
    features = weak_one(score_in(article, [apple(context_terms=["app store"])], lex)).features
    assert features.ctx_terms == 1


def test_fr6_rule4_coref_is_resolved_regardless_of_candidate_order(lex) -> None:
    article = article_of("Apple opens a store", "Apple Inc. confirmed the plan.", lexicons=lex)
    companies, aliases = build_watchlist([apple()], lex)
    matcher = compile_matcher(companies, aliases, lex)
    candidates = scan(article.fields, matcher)
    by_ticker = {company.ticker: company for company in companies}

    forward = score_article(article, candidates, by_ticker, lex)
    backward = score_article(article, tuple(reversed(candidates)), by_ticker, lex)
    forward_weak = weak_one(forward)
    backward_weak = weak_one(backward)
    assert forward_weak.features == backward_weak.features
    assert forward_weak.features.coref_strong == 1
    assert forward_weak.accepted


def test_fr6_rule5_every_candidate_keeps_its_feature_vector(lex) -> None:
    article = article_of(
        "Apple growers brace for frost", "Orchard owners expect a harvest.", lexicons=lex
    )
    scored = score_in(article, [apple(anti_terms=["orchard"])], lex)
    assert all(item.features is not None for item in scored)
    assert all(not item.accepted for item in scored)


def test_fr6_strong_candidates_are_accepted_unscored(lex) -> None:
    article = article_of("Apple Inc. beats estimates", lexicons=lex)
    (scored,) = score_in(article, [apple()], lex)
    assert scored.features is None
    assert scored.score == 1.0
    assert scored.accepted


# ---------------------------------------------------------------------------
# feature-by-feature behaviour
# ---------------------------------------------------------------------------


def test_fr6_hyphen_compound_penalty_applies_only_to_lowercase_continuations(lex) -> None:
    lower = article_of("Meta-analysis finds a result", lexicons=lex)
    assert weak_one(score_in(lower, [meta()], lex)).features.hyphen_compound == 1
    upper = article_of("Meta-Analysis Group renamed itself", lexicons=lex)
    assert weak_one(score_in(upper, [meta()], lex)).features.hyphen_compound == 0
    spaced = article_of("Meta - analysis of the filing", lexicons=lex)
    assert weak_one(score_in(spaced, [meta()], lex)).features.hyphen_compound == 0


def test_fr6_allcaps_penalty_hits_word_collision_tickers(lex) -> None:
    allstate = {
        "ticker": "ALL",
        "name": "Allstate Corporation",
        "aliases": [("ALL", AliasKind.TICKER_SYMBOL), ("Allstate", AliasKind.SHORT_NAME)],
    }
    article = article_of("FED SAYS ALL OPTIONS REMAIN OPEN", lexicons=lex)
    scored = weak_one(score_in(article, [allstate], lex))
    assert scored.features.allcaps_run == 1
    assert scored.features.case_signal == 0
    assert scored.score == 0.0
    assert not scored.accepted


def test_fr6_allcaps_penalty_does_not_hit_name_aliases(lex) -> None:
    article = article_of("APPLE UNVEILS NEW IPHONE TODAY", lexicons=lex)
    scored = weak_one(score_in(article, [apple()], lex))
    assert scored.features.allcaps_run == 0
    assert scored.features.case_signal == 0


def test_fr6_anti_terms_are_capped_at_two(lex) -> None:
    article = article_of(
        "Apple pie season", "Orchard, cider, fruit and pie all featured.", lexicons=lex
    )
    features = weak_one(
        score_in(article, [apple(anti_terms=["orchard", "cider", "fruit", "pie"])], lex)
    ).features
    assert features.anti_terms == 4
    assert score_features(features) == score_features(features.model_copy(update={"anti_terms": 2}))


def test_fr6_context_terms_are_capped_at_two(lex) -> None:
    features = MentionFeatures(prior=0.0, ctx_terms=5)
    assert score_features(features) == 0.30


# ---------------------------------------------------------------------------
# threshold behaviour (FR-6 / non-goal 10)
# ---------------------------------------------------------------------------


def test_fr6_theta_is_a_single_committed_constant() -> None:
    assert THETA == 0.35


def test_fr6_acceptance_is_inclusive_at_theta(lex) -> None:
    """prior 0.25 + case_signal 0.10 lands exactly on theta."""

    article = article_of("Retailers gather as Apple opens a store", lexicons=lex)
    scored = weak_one(score_in(article, [apple()], lex))
    assert scored.score == pytest.approx(0.35)
    assert scored.accepted


def test_fr6_just_below_theta_is_rejected(lex) -> None:
    article = article_of("Apple opens a store", lexicons=lex)
    scored = weak_one(score_in(article, [apple()], lex))
    assert scored.score == pytest.approx(0.25)
    assert not scored.accepted


def test_fr6_every_candidate_records_the_same_threshold(lex) -> None:
    article = article_of(
        "Apple and Meta rose", "Apple Inc. and Meta Platforms, Inc. both gained.", lexicons=lex
    )
    scored = score_in(article, [apple(), meta()], lex)
    assert {item.threshold for item in scored} == {THETA}


def test_fr6_scores_are_clamped_to_the_unit_interval() -> None:
    high = MentionFeatures(prior=0.3, coref_strong=1, window_cues=9, doc_cues=9, ctx_terms=9)
    assert score_features(high) == 1.0
    low = MentionFeatures(prior=0.0, window_antis=9, doc_antis=9, anti_terms=9, hyphen_compound=1)
    assert score_features(low) == 0.0


def test_fr6_score_is_free_of_float_noise() -> None:
    assert score_features(MentionFeatures(prior=0.25, case_signal=1)) == 0.35
    assert score_features(MentionFeatures(prior=0.05, window_cues=3)) == 0.35
    assert score_features(MentionFeatures(prior=0.1, coref_strong=1)) == 0.6


def test_fr6_weights_match_the_scope_formula() -> None:
    base = MentionFeatures(prior=0.0)
    assert score_features(base) == 0.0
    assert score_features(MentionFeatures(coref_strong=1)) == 0.50
    assert score_features(MentionFeatures(prior=0.3, case_signal=1)) == 0.40
    assert score_features(MentionFeatures(prior=0.3, window_cues=1)) == 0.40
    assert score_features(MentionFeatures(prior=0.3, doc_cues=1)) == 0.35
    assert score_features(MentionFeatures(prior=0.3, window_antis=1)) == pytest.approx(0.15)
    assert score_features(MentionFeatures(prior=0.3, doc_antis=1)) == pytest.approx(0.20)
    assert score_features(MentionFeatures(prior=0.3, ctx_terms=1)) == pytest.approx(0.45)
    assert score_features(MentionFeatures(prior=0.3, anti_terms=1)) == pytest.approx(0.10)
    assert score_features(MentionFeatures(prior=0.3, hyphen_compound=1)) == pytest.approx(0.10)
    assert score_features(MentionFeatures(prior=0.3, allcaps_run=1)) == pytest.approx(0.10)


def test_fr6_no_candidates_scores_nothing(lex) -> None:
    article = article_of("Nothing to see here", lexicons=lex)
    assert score_in(article, [apple()], lex) == ()

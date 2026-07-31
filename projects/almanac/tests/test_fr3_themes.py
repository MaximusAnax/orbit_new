"""FR-3: the deterministic keyword theme suggester."""

from __future__ import annotations

from almanac.engine.themes import (
    MAX_THEMES_PER_ENTRY,
    score_theme,
    score_themes,
    suggest_themes,
    suggestion_document,
)
from almanac.models import LexiconTerm, Theme

ALPHA = Theme(
    id="alpha_theme",
    name="Alpha",
    description="d",
    lexicon=[LexiconTerm(term=f"alpha{i}", weight=1.0) for i in range(7)]
    + [LexiconTerm(term="shared word", weight=2.0)],
)
BETA = Theme(
    id="beta_theme",
    name="Beta",
    description="d",
    lexicon=[LexiconTerm(term=f"beta{i}", weight=1.0) for i in range(7)]
    + [LexiconTerm(term="shared word", weight=2.0)],
)


def test_fr3_scores_are_weighted_hit_counts():
    tokens = suggestion_document("alpha0 alpha0 alpha1")
    assert score_theme(tokens, ALPHA) == 3.0


def test_fr3_phrases_match_contiguously_only():
    assert score_theme(suggestion_document("a shared word here"), ALPHA) == 2.0
    assert score_theme(suggestion_document("shared and word"), ALPHA) == 0.0


def test_fr3_matching_is_stemmed(datasets):
    scores = score_themes(datasets.themes, "Habits are what remain when motivation leaves")
    assert scores["discipline_and_habit"] > 0


def test_fr3_tags_and_note_contribute():
    with_note = score_themes([ALPHA], "nothing here", note="alpha0")
    with_tag = score_themes([ALPHA], "nothing here", tags=["alpha1"])
    assert with_note["alpha_theme"] == 1.0
    assert with_tag["alpha_theme"] == 1.0


def test_fr3_ties_break_lexicographically_by_theme_id():
    suggestions = suggest_themes([BETA, ALPHA], "shared word")
    assert [s.theme_id for s in suggestions] == ["alpha_theme", "beta_theme"]


def test_fr3_zero_score_themes_are_not_proposed():
    assert suggest_themes([ALPHA, BETA], "completely unrelated text") == []


def test_fr3_zero_scores_can_be_requested_explicitly():
    padded = suggest_themes([ALPHA, BETA], "unrelated", include_zero_scores=True)
    assert len(padded) == 2 and all(s.score == 0.0 for s in padded)


def test_fr3_returns_at_most_three(datasets):
    text = "courage and habit and death and gratitude and honesty and attention"
    suggestions = suggest_themes(datasets.themes, text)
    assert len(suggestions) <= MAX_THEMES_PER_ENTRY


def test_fr3_is_deterministic(datasets):
    text = "The obstacle in the path becomes the path; never forget there is a purpose"
    first = suggest_themes(datasets.themes, text)
    second = suggest_themes(datasets.themes, text)
    assert first == second


def test_fr3_committed_lexicons_meet_their_floor(datasets):
    assert len(datasets.themes) == 16
    for theme in datasets.themes:
        assert len(theme.lexicon) >= 8
        assert len({t.term for t in theme.lexicon}) == len(theme.lexicon)


def test_fr3_recognizable_quotes_route_to_the_expected_theme(datasets):
    cases = {
        "It is not that we have a short time to live, but that we waste a lot of it": "mortality_and_time",
        "We are what we repeatedly do; excellence is a habit": "discipline_and_habit",
        "A soft answer turneth away wrath, but grievous words stir up anger": "equanimity_and_anger",
        "He that can have patience can have what he will": "equanimity_and_anger",
        "An investment in knowledge pays the best interest": "learning_and_growth",
    }
    for text, expected in cases.items():
        top = [s.theme_id for s in suggest_themes(datasets.themes, text)]
        assert expected in top, (text, top)


def test_fr3_service_never_assigns_a_theme_silently(service):
    result = service.capture("Courage is the first of human qualities")
    assert result.suggestions and result.suggestions[0].theme_id == "courage"
    assert result.themes == []


def test_fr3_service_accepts_the_top_suggestion_on_request(service):
    result = service.capture("Courage is the first of human qualities", accept_suggestions=True)
    assert result.themes == ["courage"]

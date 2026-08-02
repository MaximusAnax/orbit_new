"""FR-10: the personalizer output validator — checks (a) through (e)."""

from __future__ import annotations

import pytest
from almanac.engine.validate import MAX_CHARS, MAX_WORDS, validate_prompt
from almanac.models import PromptKind

CLEAN = {
    PromptKind.REFLECT: "Tonight, what did you avoid because it was uncomfortable?",
    PromptKind.CONNECT: "Where else in your work is this already true?",
    PromptKind.ACT: "When the first hard email arrives today, I will answer it before any other.",
    PromptKind.REFRAME: "The delay is the assignment, not the interruption to it.",
}


@pytest.mark.parametrize("kind", list(PromptKind))
def test_fr10_clean_outputs_pass(kind):
    assert validate_prompt(CLEAN[kind], kind).ok


def test_fr10a_rejects_empty_output():
    verdict = validate_prompt("   ", PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "a"


def test_fr10a_rejects_output_over_the_character_limit():
    verdict = validate_prompt("a " * (MAX_CHARS // 2 + 10) + "?", PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "a"


def test_fr10a_rejects_output_over_the_word_limit():
    text = " ".join(["word"] * (MAX_WORDS + 1)) + "?"
    verdict = validate_prompt(text, PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "a"


def test_fr10a_rejects_multi_line_output():
    verdict = validate_prompt("What now?\nAnd then?", PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "a"


def test_fr10b_rejects_unfilled_slots():
    verdict = validate_prompt("What would {author} do here?", PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "b"


def test_fr10c_requires_the_excerpt_verbatim_when_the_template_used_it():
    excerpt = "we waste a lot of it"
    ok = validate_prompt(
        "Given that we waste a lot of it, what changes?", PromptKind.REFLECT, excerpt
    )
    bad = validate_prompt("Given that we squander it, what changes?", PromptKind.REFLECT, excerpt)
    assert ok.ok
    assert not bad.ok and bad.check == "c"


def test_fr10c_does_not_apply_when_the_template_had_no_excerpt():
    assert validate_prompt("Any question at all?", PromptKind.REFLECT, None).ok


@pytest.mark.parametrize(
    "text",
    [
        "Read more at https://example.com and reflect?",
        "See www.example.org for context?",
        "Check example.com later?",
    ],
)
def test_fr10d_rejects_urls(text):
    verdict = validate_prompt(text, PromptKind.REFLECT)
    assert not verdict.ok and verdict.check == "d"


def test_fr10e_reflect_and_connect_must_end_with_a_question_mark():
    for kind in (PromptKind.REFLECT, PromptKind.CONNECT):
        verdict = validate_prompt("Consider this carefully.", kind)
        assert not verdict.ok and verdict.check == "e"


def test_fr10e_act_must_keep_the_if_then_scaffold():
    missing_when = validate_prompt("I will do the hard thing first today.", PromptKind.ACT)
    missing_will = validate_prompt("When the day starts, do the hard thing.", PromptKind.ACT)
    assert not missing_when.ok and missing_when.check == "e"
    assert not missing_will.ok and missing_will.check == "e"


def test_fr10e_reframe_allows_only_a_trailing_question_mark():
    trailing = validate_prompt("Consider the opposite for a moment. What then?", PromptKind.REFRAME)
    interior = validate_prompt("What then? Consider the opposite.", PromptKind.REFRAME)
    assert trailing.ok
    assert not interior.ok and interior.check == "e"


def test_fr10_validator_bounds_form_not_meaning():
    """Documented boundary: a fluent but unrelated rewrite passes (non-goal 13)."""
    off_spec = "Tonight, what is your favourite colour and why?"
    assert validate_prompt(off_spec, PromptKind.REFLECT).ok


def test_fr10_every_committed_template_rendering_would_validate(datasets):
    from almanac.engine.prompts import render_template

    for template in datasets.templates:
        text, excerpt = render_template(
            template, "A quoted line of some length here", "A", "S", "T"
        )
        verdict = validate_prompt(text, template.kind, excerpt)
        assert verdict.ok, f"{template.id}: {verdict.reason}"

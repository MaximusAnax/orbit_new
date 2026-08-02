"""FR-9: prompt kind rotation, candidate filtering, seeded pick, slot rendering."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.engine.prompts import (
    PromptSelectionError,
    base_kind,
    build_prompt,
    choose_kind,
    render_template,
    select_template,
    text_short,
    theme_pool,
)
from almanac.models import GENERAL_THEME_ID, Grade, PromptKind, PromptTemplate

DAY = dt.date(2026, 5, 1)


@pytest.fixture
def templates(datasets):
    return datasets.templates


def test_fr9_base_kind_rotates_through_all_four(datasets):
    assert [base_kind(n) for n in range(5)] == [
        PromptKind.REFLECT,
        PromptKind.ACT,
        PromptKind.REFRAME,
        PromptKind.CONNECT,
        PromptKind.REFLECT,
    ]


def test_fr9_resonated_overrides_to_act(datasets):
    assert choose_kind(0, Grade.RESONATED) is PromptKind.ACT
    assert choose_kind(2, Grade.RESONATED) is PromptKind.ACT


def test_fr9_applied_overrides_to_connect(datasets):
    assert choose_kind(0, Grade.APPLIED) is PromptKind.CONNECT


def test_fr9_flat_and_no_reflection_keep_the_rotation(datasets):
    assert choose_kind(1, Grade.FLAT) is PromptKind.ACT
    assert choose_kind(2, None) is PromptKind.REFRAME


def test_fr9_pool_is_theme_scoped(templates):
    pool = theme_pool(templates, ["courage"])
    assert pool and all(t.theme_id == "courage" for t in pool)


def test_fr9_general_pool_only_when_the_entry_has_no_themes(templates):
    general = theme_pool(templates, [])
    assert general and all(t.theme_id == GENERAL_THEME_ID for t in general)
    themed = theme_pool(templates, ["courage"])
    assert not any(t.theme_id == GENERAL_THEME_ID for t in themed)


def test_fr9_pool_unions_multiple_themes(templates):
    pool = theme_pool(templates, ["courage", "humility"])
    assert {t.theme_id for t in pool} == {"courage", "humility"}


def test_fr9_selection_honours_the_requested_kind(templates):
    selection = select_template(templates, ["courage"], PromptKind.ACT, [], 7, DAY, "e1")
    assert selection.template.kind is PromptKind.ACT
    assert selection.recency_relaxed is False


def test_fr9_selection_excludes_recently_used_templates(templates):
    first = select_template(templates, ["courage"], PromptKind.ACT, [], 7, DAY, "e1")
    second = select_template(
        templates, ["courage"], PromptKind.ACT, [first.template.id], 7, DAY, "e1"
    )
    assert second.template.id != first.template.id


def test_fr9_falls_through_kinds_in_rotation_order_when_exhausted(templates):
    act_ids = [t.id for t in templates if t.theme_id == "courage" and t.kind is PromptKind.ACT]
    selection = select_template(templates, ["courage"], PromptKind.ACT, act_ids, 7, DAY, "e1")
    assert selection.template.kind is PromptKind.REFRAME  # next kind after act
    assert selection.recency_relaxed is False


def test_fr9_exhausted_pool_drops_the_recency_exclusion_and_flags_it(templates):
    all_courage = [t.id for t in templates if t.theme_id == "courage"]
    selection = select_template(templates, ["courage"], PromptKind.ACT, all_courage, 7, DAY, "e1")
    assert selection.recency_relaxed is True
    assert selection.template.id in all_courage


def test_fr9_pick_is_deterministic_for_a_seed(templates):
    a = select_template(templates, ["courage"], PromptKind.REFLECT, [], 7, DAY, "e1")
    b = select_template(templates, ["courage"], PromptKind.REFLECT, [], 7, DAY, "e1")
    assert a.template.id == b.template.id


def test_fr9_pick_varies_with_seed_date_and_entry(templates):
    picks = {
        select_template(templates, ["courage"], PromptKind.REFLECT, [], seed, DAY, "e1").template.id
        for seed in range(12)
    }
    assert len(picks) > 1


def test_fr9_empty_pool_is_an_error(templates):
    with pytest.raises(PromptSelectionError):
        select_template(templates, ["not_a_theme"], PromptKind.ACT, [], 7, DAY, "e1")


def test_fr9_text_short_truncates_at_twelve_words():
    long_text = " ".join(f"w{i}" for i in range(20))
    assert text_short(long_text) == " ".join(f"w{i}" for i in range(12)) + "…"


def test_fr9_text_short_leaves_short_text_alone():
    assert text_short("three words only") == "three words only"


def test_fr9_render_fills_every_slot():
    template = PromptTemplate(
        id="t1",
        theme_id="courage",
        kind=PromptKind.REFLECT,
        template="{author} on {theme_name} in {source}: {text_short}?",
    )
    rendered, excerpt = render_template(
        template, "Fear is the mind killer", "Herbert", "Dune", "Courage"
    )
    assert rendered == "Herbert on Courage in Dune: Fear is the mind killer?"
    assert excerpt == "Fear is the mind killer"
    assert "{" not in rendered


def test_fr9_render_uses_documented_fallbacks():
    template = PromptTemplate(
        id="t1",
        theme_id="courage",
        kind=PromptKind.REFLECT,
        template="{author} in {source} about {theme_name}?",
    )
    rendered, excerpt = render_template(template, "text")
    assert rendered == "the author in this about this idea?"
    assert excerpt is None


def test_fr9_build_prompt_end_to_end(templates, datasets):
    rendered = build_prompt(
        templates,
        ["mortality_and_time"],
        ["Mortality & Time"],
        prior_exposures=0,
        last_grade=Grade.RESONATED,
        recent_template_ids=[],
        seed=7,
        on_date=DAY,
        entry_id="e1",
        entry_text="It is not that we have a short time to live, but that we waste a lot of it.",
        author="Seneca",
        source="On the Shortness of Life",
    )
    assert rendered.template.kind is PromptKind.ACT
    assert rendered.template.theme_id == "mortality_and_time"
    assert "{" not in rendered.text
    assert "When " in rendered.text and " I will " in rendered.text


def test_fr9_general_templates_serve_untagged_entries(templates):
    rendered = build_prompt(
        templates,
        [],
        [],
        prior_exposures=0,
        last_grade=None,
        recent_template_ids=[],
        seed=3,
        on_date=DAY,
        entry_id="e2",
        entry_text="An idea with no theme at all",
    )
    assert rendered.template.theme_id == GENERAL_THEME_ID
    assert rendered.text.endswith("?")

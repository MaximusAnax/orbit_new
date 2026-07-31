"""Committed-data floors and the model invariants from DATA_MODEL.md."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.datasets import THEME_COUNT
from almanac.models import (
    GENERAL_THEME_ID,
    Clamp,
    Entry,
    EntryKind,
    EntryTheme,
    LexiconTerm,
    PromptKind,
    PromptTemplate,
    Reflection,
    SchedulerParams,
    SchedulerState,
    SelectPool,
    Surfacing,
    SurfacingKind,
    Tag,
    Theme,
    ThemeSource,
)
from pydantic import ValidationError

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
HASH = "a" * 64


def surfacing(**overrides) -> Surfacing:
    base = dict(
        id="s1",
        entry_id="e1",
        on_date=dt.date(2026, 1, 1),
        slot=0,
        kind=SurfacingKind.DAILY,
        select_pool=SelectPool.NOVELTY,
        prompt_template_id="t1",
        prompt_kind=PromptKind.REFLECT,
        prompt_text="text",
        scheduler_version="sched-1",
        seed=7,
        created_at=NOW,
    )
    base.update(overrides)
    return Surfacing(**base)


# --- committed data floors -------------------------------------------------


def test_data_theme_taxonomy_is_fixed_at_sixteen(datasets):
    assert len(datasets.themes) == THEME_COUNT
    assert len({t.id for t in datasets.themes}) == THEME_COUNT
    assert GENERAL_THEME_ID not in {t.id for t in datasets.themes}


def test_data_every_theme_pool_meets_the_template_floor(datasets):
    for theme in datasets.themes:
        pool = [t for t in datasets.templates if t.theme_id == theme.id]
        assert len(pool) >= 6, theme.id
        assert {t.kind for t in pool} == set(PromptKind), theme.id


def test_data_general_pool_meets_its_floor(datasets):
    pool = [t for t in datasets.templates if t.theme_id == GENERAL_THEME_ID]
    assert len(pool) >= 8
    assert {t.kind for t in pool} == set(PromptKind)


def test_data_template_ids_are_unique_and_slots_are_fillable(datasets):
    assert len({t.id for t in datasets.templates}) == len(datasets.templates)
    assert len(datasets.templates) >= 100


def test_data_scheduler_parameter_constraints(datasets):
    params = datasets.params
    assert params.I0 == params.W
    assert params.clamp.lo >= params.W
    assert params.clamp.lo <= params.clamp.hi <= params.clamp.hi_flat
    assert params.archive_flat_streak >= params.demote_flat_streak
    assert 1 <= params.k <= 5


def test_data_starter_pack_is_usable_on_day_one(starter_quotes, datasets):
    theme_ids = {t.id for t in datasets.themes}
    assert len(starter_quotes) >= 50
    for quote in starter_quotes:
        assert quote.text.strip()
        assert len(quote.themes) <= 3
        assert set(quote.themes) <= theme_ids


def test_data_load_rejects_a_scheduler_whose_floor_is_below_the_cooldown():
    with pytest.raises(ValidationError):
        SchedulerParams(
            params_version="bad",
            W=10,
            I0=10,
            clamp=Clamp(lo=2, hi=60, hi_flat=240),
            multipliers={"resonated": 1.25, "applied": 1.5, "none": 1.9, "flat": 3.0},
            demote_flat_streak=2,
            archive_flat_streak=3,
            pinned_cap=21,
            P_rescue=32,
            rho=0.35,
            H=28,
            S=90,
            tau=7,
            k=1,
            jitter=0.05,
            prompt_reuse_window=6,
        )


def test_data_load_rejects_i0_that_differs_from_the_cooldown(datasets):
    payload = datasets.params.model_dump()
    payload["I0"] = 3
    with pytest.raises(ValidationError):
        SchedulerParams.model_validate(payload)


# --- model invariants ------------------------------------------------------


def test_model_entry_requires_a_valid_hash():
    with pytest.raises(ValidationError):
        Entry(
            id="e1",
            kind=EntryKind.QUOTE,
            text="x",
            normalized_hash="not-a-hash",
            captured_on=dt.date(2026, 1, 1),
            created_at=NOW,
            updated_at=NOW,
        )


def test_model_entry_text_must_not_be_blank():
    with pytest.raises(ValidationError):
        Entry(
            id="e1",
            kind=EntryKind.QUOTE,
            text="   ",
            normalized_hash=HASH,
            captured_on=dt.date(2026, 1, 1),
            created_at=NOW,
            updated_at=NOW,
        )


def test_model_tags_must_already_be_normalized():
    Tag(id="t1", name="deep-work")
    with pytest.raises(ValidationError):
        Tag(id="t1", name="Deep Work")


def test_model_general_is_not_an_assignable_theme():
    with pytest.raises(ValidationError):
        EntryTheme(entry_id="e1", theme_id=GENERAL_THEME_ID, source=ThemeSource.USER)
    with pytest.raises(ValidationError):
        Theme(
            id=GENERAL_THEME_ID,
            name="General",
            description="d",
            lexicon=[LexiconTerm(term=f"t{i}", weight=1.0) for i in range(8)],
        )


def test_model_theme_lexicon_floor():
    with pytest.raises(ValidationError):
        Theme(
            id="x",
            name="X",
            description="d",
            lexicon=[LexiconTerm(term=f"t{i}", weight=1.0) for i in range(4)],
        )


def test_model_template_kind_signatures_are_enforced():
    with pytest.raises(ValidationError):
        PromptTemplate(id="a", theme_id="courage", kind=PromptKind.REFLECT, template="No question")
    with pytest.raises(ValidationError):
        PromptTemplate(id="b", theme_id="courage", kind=PromptKind.ACT, template="Just do it.")
    with pytest.raises(ValidationError):
        PromptTemplate(
            id="c", theme_id="courage", kind=PromptKind.REFRAME, template="Why? Consider this."
        )
    with pytest.raises(ValidationError):
        PromptTemplate(
            id="d", theme_id="courage", kind=PromptKind.REFLECT, template="What about {nope}?"
        )


def test_model_surfacing_pool_and_kind_agree():
    surfacing()
    with pytest.raises(ValidationError):
        surfacing(kind=SurfacingKind.EXTRA)
    with pytest.raises(ValidationError):
        surfacing(select_pool=SelectPool.EXTRA)
    with pytest.raises(ValidationError):
        surfacing(kind=SurfacingKind.EXTRA, select_pool=SelectPool.EXTRA, slot=1)


def test_model_relaxed_cooldown_iff_relaxed_pool():
    surfacing(select_pool=SelectPool.RELAXED, relaxed_cooldown=True)
    with pytest.raises(ValidationError):
        surfacing(select_pool=SelectPool.RELAXED)
    with pytest.raises(ValidationError):
        surfacing(relaxed_cooldown=True)


def test_model_filters_are_for_draws_only():
    with pytest.raises(ValidationError):
        surfacing(filter_theme_id="courage")
    surfacing(kind=SurfacingKind.EXTRA, select_pool=SelectPool.EXTRA, filter_theme_id="courage")


def test_model_surfacings_and_reflections_are_frozen():
    row = surfacing()
    with pytest.raises(ValidationError):
        row.prompt_text = "changed"
    reflection = Reflection(
        id="r1", surfacing_id="s1", entry_id="e1", grade="applied", logged_at=NOW
    )
    with pytest.raises(ValidationError):
        reflection.grade = "flat"


def test_model_scheduler_state_last_seen_matches_exposure_count():
    SchedulerState(entry_id="e1", exposure_count=0, interval_days=10)
    with pytest.raises(ValidationError):
        SchedulerState(entry_id="e1", exposure_count=1, interval_days=10)
    with pytest.raises(ValidationError):
        SchedulerState(
            entry_id="e1", exposure_count=0, last_surfaced_on=dt.date(2026, 1, 1), interval_days=10
        )

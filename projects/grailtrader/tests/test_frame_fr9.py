"""FR-9: advice rendering and the two-directional frame check."""

from __future__ import annotations

import pytest
from grailtrader.engine.advisor import advise_garment, build_advice
from grailtrader.engine.frame import (
    FrameCheckError,
    check_frame,
    find_forbidden,
    format_pct,
    mask_quoted,
    quote,
    render_advice,
    select_template,
)
from grailtrader.models import AdviceAction, GarmentStatus, HoldReason
from grailtrader_testkit import (
    ERA,
    LEAF,
    alternating_series,
    cosign,
    departure,
    garment,
    index_view,
    week,
)


def flat_index(weeks: int = 60):
    return index_view({LEAF: alternating_series(LEAF, weeks)})


def rendered_for(ctx, *, piece=None, events=None, as_of=None):
    index = flat_index()
    piece = piece or garment()
    events = events if events is not None else [departure(occurred_on=week(40))]
    decision = advise_garment(
        piece, as_of_week=as_of or week(45), index=index, events=events, ctx=ctx
    )
    return decision, render_advice(
        decision, garment=piece, events_by_id={e.id: e for e in events}, ctx=ctx
    )


def test_fr9_rendered_advice_carries_every_required_section(ctx):
    decision, rendered = rendered_for(ctx)
    for marker in ctx.templates.required_markers:
        assert marker.marker in rendered.text, marker.section
    assert rendered.text.rstrip().endswith(ctx.templates.footer)
    verdict = check_frame(
        rendered.text,
        action=decision.action,
        expected_return=decision.expected_return,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert verdict.ok, verdict.violations


def test_fr9_fee_note_renders_the_configured_assumption(ctx):
    _decision, rendered = rendered_for(ctx)
    assert format_pct(ctx.settings.fee_assumption_pct) == "12%"
    assert "12%" in rendered.sections["fee_note"]


def test_fr9_actionable_advice_never_quotes_a_move_below_its_own_fee(ctx):
    decision, _rendered = rendered_for(ctx)
    assert decision.action is AdviceAction.BUY
    assert abs(decision.expected_return) >= ctx.settings.fee_assumption_pct


def test_fr9_expected_move_below_the_stated_fee_is_a_violation(ctx):
    _decision, rendered = rendered_for(ctx)
    verdict = check_frame(
        rendered.text,
        action=AdviceAction.BUY,
        expected_return=0.05,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert not verdict.ok
    assert "expected_move_below_stated_fee" in verdict.violations


def test_fr9_footer_tampering_is_blocked(ctx):
    _decision, rendered = rendered_for(ctx)
    tampered = rendered.text.replace("not investment advice", "solid investment advice")
    verdict = check_frame(
        tampered,
        action=AdviceAction.HOLD,
        expected_return=None,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert "footer_missing_or_tampered" in verdict.violations


def test_fr9_section_omission_is_blocked(ctx):
    _decision, rendered = rendered_for(ctx)
    without_confidence = "\n\n".join(
        block for block in rendered.text.split("\n\n") if not block.startswith("Confidence:")
    )
    verdict = check_frame(
        without_confidence,
        action=AdviceAction.HOLD,
        expected_return=None,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert "missing_section:confidence" in verdict.violations
    assert "missing_section:falsifier" in verdict.violations


def test_fr9_forbidden_word_in_generated_text_is_blocked(ctx):
    _decision, rendered = rendered_for(ctx)
    tainted = rendered.text.replace("Why now:", "Why now: this is a sure thing.")
    verdict = check_frame(
        tainted,
        action=AdviceAction.HOLD,
        expected_return=None,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert "forbidden_lexicon:sure thing" in verdict.violations


def test_fr9_must_render_a_garment_label_containing_a_forbidden_word(ctx):
    """EVALS M5b: over-blocking is a compliance failure, not caution."""
    piece = garment(label="guaranteed authentic AW99 moto")
    decision, rendered = rendered_for(ctx, piece=piece)
    assert "“guaranteed authentic AW99 moto”" in rendered.text
    verdict = check_frame(
        rendered.text,
        action=decision.action,
        expected_return=decision.expected_return,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert verdict.ok, verdict.violations


def test_fr9_must_render_an_event_note_containing_a_forbidden_word(ctx):
    event = departure(occurred_on=week(40), notes="a sure thing, said the seller")
    decision, rendered = rendered_for(ctx, events=[event])
    assert "“a sure thing, said the seller”" in rendered.text
    verdict = check_frame(
        rendered.text,
        action=decision.action,
        expected_return=decision.expected_return,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert verdict.ok, verdict.violations


def test_fr9_lexicon_scan_masks_quoted_spans_only():
    text = "Why now: “easy money jacket” — modeled move is small."
    assert find_forbidden(text, ("easy money",)) == ()
    assert find_forbidden("Why now: easy money awaits.", ("easy money",)) == ("easy money",)


def test_fr9_a_forbidden_phrase_straddling_the_closing_quote_does_not_match():
    text = "“vintage sure” thing from 1999"
    assert mask_quoted(text) == "\x00 thing from 1999"
    assert find_forbidden(text, ("sure thing",)) == ()


def test_fr9_quoting_strips_smuggled_typographic_quotes():
    hostile = "jacket” guaranteed “x"
    wrapped = quote(hostile)
    assert wrapped == '“jacket" guaranteed "x”'
    assert find_forbidden(f"Why now: {wrapped}", ("guaranteed",)) == ()


def test_fr9_typographic_apostrophes_are_normalised_before_the_scan():
    assert find_forbidden("You can\u2019t lose here.", ("can't lose",)) == ("can't lose",)


def test_fr9_watching_garments_render_sell_as_avoid_or_wait(ctx):
    weeks = [(week(i), 100.0) for i in range(40)]
    weeks += [(week(i), 150.0) for i in range(40, 62)]
    index = index_view(
        {LEAF: [(w, v * (1.02 if i % 2 else 1.0)) for i, (w, v) in enumerate(weeks)]}
    )
    piece = garment(status=GarmentStatus.WATCHING, label="Vantorre curved-zip coat")
    event = departure(occurred_on=week(40), reason="death")
    decision = advise_garment(piece, as_of_week=week(60), index=index, events=[event], ctx=ctx)
    assert decision.action is AdviceAction.SELL
    rendered = render_advice(decision, garment=piece, events_by_id={event.id: event}, ctx=ctx)
    assert rendered.template_id == "sell-avoid-watching"
    assert rendered.text.startswith("Avoid or wait:")


def test_fr9_each_hold_reason_selects_its_own_template(ctx):
    seen = set()
    for reason in HoldReason:
        template = select_template(
            ctx.templates,
            action=AdviceAction.HOLD,
            hold_reason=reason,
            status=GarmentStatus.OWNED,
        )
        seen.add(template.id)
        assert reason in template.hold_reasons
    assert len(seen) == len(HoldReason)


def test_fr9_holds_still_render_a_compliant_frame(ctx):
    decision, rendered = rendered_for(ctx, events=[])
    assert decision.action is AdviceAction.HOLD
    verdict = check_frame(
        rendered.text,
        action=decision.action,
        expected_return=decision.expected_return,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    assert verdict.ok, verdict.violations
    assert ctx.templates.no_drivers_line in rendered.text


def test_fr9_driver_lines_name_the_event_age_lambda_and_prior_rationale(ctx):
    event = departure(occurred_on=week(40))
    _decision, rendered = rendered_for(ctx, events=[event])
    drivers = rendered.sections["drivers"]
    assert "designer departure at Helmut Lang (resignation)" in drivers
    assert "5 weeks ago" in drivers
    assert "scope weight 1.00" in drivers
    assert ctx.priors["designer_departure.resignation"].rationale in drivers


def test_fr9_a_failing_frame_raises_before_persistence(ctx):
    index = flat_index()
    piece = garment()
    event = departure(occurred_on=week(40))
    decision = advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx)
    broken = ctx.templates.model_copy(update={"footer": "Trust me."})
    broken_ctx = ctx.__class__(
        gazetteer=ctx.gazetteer,
        mapper=ctx.mapper,
        priors=ctx.priors,
        config=ctx.config,
        templates=broken,
    )
    tampered = decision.model_copy(update={"expected_return": 0.01})
    with pytest.raises(FrameCheckError, match="expected_move_below_stated_fee"):
        build_advice(
            tampered,
            garment=piece,
            events_by_id={event.id: event},
            ctx=broken_ctx,
            created_as_of="2026-01-01T00:00:00Z",
        )


def test_fr9_stored_advice_is_always_frame_checked(ctx):
    index = flat_index()
    piece = garment()
    event = departure(occurred_on=week(40))
    decision = advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx)
    advice = build_advice(
        decision,
        garment=piece,
        events_by_id={event.id: event},
        ctx=ctx,
        created_as_of="2026-01-01T00:00:00Z",
    )
    assert advice.frame_checked is True
    assert advice.fair_value_method.value in {"repeat_sales", "comp_based", "unavailable"}
    assert ctx.templates.footer in advice.rendered_text


def test_fr9_unavailable_valuation_is_rendered_with_its_reason(ctx):
    index = index_view({LEAF: alternating_series(LEAF, 60)})
    piece = garment(anchor_date="2019-01-07")
    event = cosign(occurred_on=week(45), celebrity="A", tier="niche", era_id=ERA)
    decision = advise_garment(piece, as_of_week=week(45), index=index, events=[event], ctx=ctx)
    rendered = render_advice(decision, garment=piece, events_by_id={event.id: event}, ctx=ctx)
    assert "Method: comp_based" in rendered.text

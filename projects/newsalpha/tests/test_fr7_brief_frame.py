"""FR-7: brief rendering from the committed catalog and the framing safeguard."""

from __future__ import annotations

import pytest
from newsalpha.engine import frame, pipeline
from newsalpha.engine.brief import already_priced, fmt_money, fmt_pct, render_brief
from newsalpha.models import EventType
from newsalpha_testkit import raw

AS_OF = "2026-03-20T07:00:00Z"

LEXICON = ("buy", "sell", "you should", "guaranteed", "act now", "short it")


def run(datasets, rows):
    return pipeline.ingest(rows, datasets, as_of=AS_OF)


# --------------------------------------------------------------------------- #
# The frame check
# --------------------------------------------------------------------------- #


def test_fr7_bare_imperatives_are_violations():
    assert frame.check_text("You should buy it now.", LEXICON)
    assert frame.check_text("Act now before the window closes.", LEXICON)
    assert frame.check_text("This is guaranteed to work.", LEXICON)


def test_fr7_word_boundary_matching_spares_buyout_and_sell_off():
    for text in (
        "The buyout closed.",
        "A sell-off followed.",
        "The buyer walked.",
        "sell-side analysts",
        "buy-side desks",
    ):
        assert frame.check_text(text, LEXICON) == [], text


def test_fr7_quoted_and_attributed_evidence_is_exempt():
    text = 'Evidence: "analysts told clients to buy the dip" -- reuters.example.'
    assert frame.check_text(text, LEXICON) == []


def test_fr7_unattributed_forbidden_quote_is_a_violation():
    text = 'Evidence: "analysts told clients to buy the dip".'
    assert frame.check_text(text, LEXICON)


def test_fr7_check_brief_requires_all_four_sections_and_the_footer():
    footer = "This is information, not investment advice."
    ok = frame.check_brief(
        what_happened="A happened.",
        why_it_matters="B matters.",
        what_to_watch=["C"],
        uncertainty_note="D is uncertain.",
        rendered_text=f"A happened. B matters. C. D is uncertain.\n\n{footer}",
        footer=footer,
        forbidden_lexicon=LEXICON,
    )
    assert ok.ok

    missing_footer = frame.check_brief(
        what_happened="A happened.",
        why_it_matters="B matters.",
        what_to_watch=["C"],
        uncertainty_note="D is uncertain.",
        rendered_text="A happened. B matters. C. D is uncertain.",
        footer=footer,
        forbidden_lexicon=LEXICON,
    )
    assert not missing_footer.ok
    assert "footer" in missing_footer.violations[0]

    empty_section = frame.check_brief(
        what_happened="   ",
        why_it_matters="B matters.",
        what_to_watch=["C"],
        uncertainty_note="D is uncertain.",
        rendered_text=f"B matters. C. D is uncertain.\n\n{footer}",
        footer=footer,
        forbidden_lexicon=LEXICON,
    )
    assert not empty_section.ok


def test_fr7_enforce_raises_on_a_failing_check():
    result = frame.FrameCheckResult(ok=False, violations=("forbidden term 'buy'",))
    with pytest.raises(frame.FrameCheckError):
        frame.enforce(result)


def test_fr7_shipped_lexicon_covers_the_documented_imperatives(datasets):
    shipped = {term.casefold() for term in datasets.lexicons.forbidden_lexicon}
    for term in (
        "buy",
        "sell",
        "short it",
        "you should",
        "act now",
        "guaranteed",
        "can't lose",
        "sure thing",
    ):
        assert term in shipped


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_fr7_every_persisted_brief_has_four_sections_and_the_footer(datasets):
    _, result = run(
        datasets,
        [
            raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter."),
            raw(
                "b",
                "Exploit",
                "The Aave protocol was exploited in a bridge hack that drained $47 million.",
                domain="coindesk.example",
                published_at="2026-03-11T08:00:00Z",
            ),
        ],
    )
    assert result.briefs
    for brief in result.briefs:
        assert brief.what_happened.strip()
        assert brief.why_it_matters.strip()
        assert brief.what_to_watch
        assert brief.uncertainty_note.strip()
        assert datasets.templates.footer in brief.rendered_text
        assert brief.frame_checked is True


def test_fr7_brief_quotes_evidence_with_source_attribution(datasets):
    _, result = run(
        datasets,
        [raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter.")],
    )
    brief = result.briefs[0]
    assert '"' in brief.what_happened
    assert "reuters.example" in brief.what_happened


def test_fr7_why_it_matters_carries_the_prior_rationale_verbatim(datasets):
    _, result = run(
        datasets,
        [raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter.")],
    )
    prior = datasets.resolve_prior("earnings_surprise.subject.beat", "equity")
    assert prior.rationale in result.briefs[0].why_it_matters
    assert prior.source_note in result.briefs[0].why_it_matters


def test_fr7_already_priced_note_appears_when_the_announcement_band_dominates(datasets):
    mna = datasets.resolve_prior("mna.target.*", "equity")
    assert already_priced(mna, mna.mid_expected_ar) is True
    equity_listing = datasets.resolve_prior("listing.subject.*", "equity")
    assert already_priced(equity_listing, equity_listing.mid_expected_ar) is True

    _, result = run(
        datasets,
        [raw("a", "Deal", "Microsoft agreed to acquire Datadog for $18 billion.")],
    )
    target = next(s for s in result.new_signals if s.asset_id == "eq:DDOG")
    brief = next(b for b in result.briefs if b.signal_id == target.id)
    assert datasets.templates.already_priced_note.strip() in brief.uncertainty_note


def test_fr7_uncertainty_note_states_confidence_and_a_falsifier(datasets):
    _, result = run(
        datasets,
        [raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter.")],
    )
    signal = result.new_signals[0]
    brief = result.briefs[0]
    assert f"{signal.confidence:.2f}" in brief.uncertainty_note
    falsifier = datasets.templates.falsifiers[EventType.earnings_surprise.value]
    assert falsifier in brief.uncertainty_note


def test_fr7_a_brief_that_fails_the_frame_check_is_never_returned(datasets):
    """Tampering with the committed template must make the pipeline raise, not emit."""
    _, result = run(
        datasets,
        [raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter.")],
    )
    signal = result.new_signals[0]
    event = next(e for e in result.events if e.id == signal.event_id)
    articles = {}
    tampered = datasets.templates.templates[0].model_copy(
        update={"what_happened": "You should buy {asset_name} now."}
    )
    catalog = datasets.templates
    patched = type(catalog)(
        templates=(tampered, *catalog.templates[1:]),
        footer=catalog.footer,
        trigger_summaries=catalog.trigger_summaries,
        attribute_labels=catalog.attribute_labels,
        falsifiers=catalog.falsifiers,
        already_priced_note=catalog.already_priced_note,
    )
    broken = type(datasets)(
        assets=datasets.assets,
        patterns=datasets.patterns,
        lexicons=datasets.lexicons,
        priors=datasets.priors,
        templates=patched,
        source_tiers=datasets.source_tiers,
        default_tier=datasets.default_tier,
        tier_weights=datasets.tier_weights,
        benchmarks=datasets.benchmarks,
    )
    with pytest.raises(frame.FrameCheckError):
        render_brief(signal, event, articles, broken)


def test_fr7_footer_is_present_verbatim_in_every_rendering(datasets):
    _, result = run(
        datasets,
        [raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter.")],
    )
    assert result.briefs[0].rendered_text.endswith(datasets.templates.footer)


def test_fr7_no_imperative_survives_into_a_rendered_brief(datasets):
    _, result = run(
        datasets,
        [
            raw("a", "Nvidia beat estimates", "Nvidia beat estimates for the quarter."),
            raw(
                "b",
                "Delisting",
                "Kraken will remove support for the Harmony ONE token, the crypto exchange said.",
                domain="coindesk.example",
                published_at="2026-03-11T08:00:00Z",
            ),
        ],
    )
    for brief in result.briefs:
        assert frame.check_text(brief.rendered_text, datasets.lexicons.forbidden_lexicon) == []


def test_fr7_formatting_helpers():
    assert fmt_pct(0.045) == "+4.5%"
    assert fmt_pct(-0.02) == "-2.0%"
    assert fmt_money(18_000_000_000.0) == "$18.00 billion"
    assert fmt_money(47_000_000.0) == "$47.00 million"

"""FR-7: brief rendering from the committed template catalog.

Every sentence of a brief is traceable to either a committed template, a
committed prior rationale, or a quoted span of a source article -- never free
generation (SCOPE D-14).  The four sections are rendered, the not-advice footer
is appended verbatim, and `frame.check_brief` runs before anything is returned:
a brief that fails the frame check is never produced.
"""

from __future__ import annotations

from ..datasets import Datasets, TemplateCatalog
from ..models import Article, Brief, Event, EventPrior, Signal, Stage
from . import frame

MAX_EVIDENCE_QUOTES = 2
ALREADY_PRICED_RATIO = 3.0


def fmt_pct(value: float) -> str:
    """`0.045` -> `+4.5%`."""
    return f"{value * 100:+.1f}%"


def fmt_money(value: float) -> str:
    """`68700000000.0` -> `$68.70 billion`."""
    for scale, label in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if abs(value) >= scale:
            return f"${value / scale:,.2f} {label}"
    return f"${value:,.2f}"


def format_attribute(name: str, value: object) -> str:
    if name.endswith("_usd") and isinstance(value, int | float):
        return fmt_money(float(value))
    if name.endswith("_pct") and isinstance(value, int | float):
        return f"{float(value):g}%"
    return str(value)


def attributes_summary(event: Event, catalog: TemplateCatalog) -> str:
    """ " Details: polarity beat; surprise 4.2%." -- or an empty string."""
    parts: list[str] = []
    for name, value in sorted(event.attributes.items()):
        label = catalog.attribute_labels.get(name, name)
        parts.append(f"{label} {format_attribute(name, value)}")
    if not parts:
        return ""
    return " Details: " + "; ".join(parts) + "."


def evidence_block(event: Event, articles: dict[str, Article]) -> str:
    """Up to two quoted evidence spans, each with source attribution.

    A headline and its opening sentence often carry the same words; identical
    quotes are collapsed so the brief shows two *different* pieces of evidence.
    """
    quotes: list[str] = []
    seen: set[tuple[str, str]] = set()
    for span in event.evidence:
        article = articles.get(span.article_id)
        domain = article.source_domain if article is not None else "unknown source"
        quote = " ".join(span.quote.split())
        if (domain, quote) in seen:
            continue
        seen.add((domain, quote))
        quotes.append(f'"{quote}" -- {domain}')
        if len(quotes) == MAX_EVIDENCE_QUOTES:
            break
    if not quotes:  # pragma: no cover - an event always carries >= 1 evidence span
        return ""
    return " Evidence: " + "; ".join(quotes) + "."


def trigger_summary(prior_key: str, stage: Stage, catalog: TemplateCatalog) -> str:
    for candidate in (f"{prior_key}|{stage.value}", prior_key):
        text = catalog.trigger_summaries.get(candidate)
        if text is not None:
            return text
    raise KeyError(f"templates.json has no trigger summary for {prior_key} at stage {stage}")


def already_priced(prior: EventPrior, expected_mid: float) -> bool:
    """True when the announcement band dwarfs the post-entry band (SCOPE D-6)."""
    if expected_mid == 0:  # pragma: no cover - unclear never reaches a brief
        return False
    return abs(prior.mid_announcement_ar) > ALREADY_PRICED_RATIO * abs(expected_mid)


def announcement_context(prior: EventPrior, signal: Signal) -> str:
    return (
        f"The announcement-window move documented in the literature for this event type runs "
        f"{fmt_pct(prior.announcement_ar_lo)} to {fmt_pct(prior.announcement_ar_hi)}; the band "
        f"scored here is the post-entry part only, {fmt_pct(signal.expected_ar_lo)} to "
        f"{fmt_pct(signal.expected_ar_hi)} over {signal.horizon_bars} trading bars measured from "
        f"the first bar after the evidence was published."
    )


def render_brief(
    signal: Signal,
    event: Event,
    articles: dict[str, Article],
    datasets: Datasets,
) -> Brief:
    """Render and frame-check one brief. Raises `FrameCheckError` on a violation."""
    catalog = datasets.templates
    asset = datasets.assets[signal.asset_id]
    prior = datasets.resolve_prior(signal.prior_key, asset.kind.value)
    if prior is None:  # pragma: no cover - the signal was scored from this prior
        raise KeyError(f"no prior for {signal.prior_key}/{asset.kind.value}")
    template = catalog.resolve(signal.event_snapshot.event_type, signal.direction)

    snapshot = signal.event_snapshot
    expected_mid = (signal.expected_ar_lo + signal.expected_ar_hi) / 2
    slots = {
        "asset_name": asset.name,
        "asset_symbol": asset.symbol,
        "asset_id": asset.id,
        "asset_kind": asset.kind.value,
        "role": signal.role.value,
        "event_type": snapshot.event_type.value,
        "stage": snapshot.stage.value,
        "direction": signal.direction.value,
        "magnitude": signal.magnitude.value,
        "confidence": f"{signal.confidence:.2f}",
        "horizon_bars": str(signal.horizon_bars),
        "corroboration": str(snapshot.corroboration),
        "best_tier": snapshot.best_tier.value,
        "event_date": snapshot.event_date,
        "trigger_summary": trigger_summary(signal.prior_key, snapshot.stage, catalog),
        "attributes_summary": attributes_summary(event, catalog),
        "evidence_block": evidence_block(event, articles),
        "prior_rationale": prior.rationale,
        "announcement_context": announcement_context(prior, signal),
        "source_note": prior.source_note,
        "falsifier": catalog.falsifiers[snapshot.event_type.value],
        "already_priced_note": (
            catalog.already_priced_note if already_priced(prior, expected_mid) else ""
        ),
        "band_lo": fmt_pct(signal.expected_ar_lo),
        "band_hi": fmt_pct(signal.expected_ar_hi),
    }

    what_happened = _fill(template.what_happened, slots, template.id, "what_happened")
    why_it_matters = _fill(template.why_it_matters, slots, template.id, "why_it_matters")
    what_to_watch = tuple(
        _fill(item, slots, template.id, "what_to_watch") for item in template.what_to_watch
    )
    uncertainty_note = _fill(template.uncertainty_note, slots, template.id, "uncertainty_note")

    rendered_text = _render_text(
        asset_name=asset.name,
        asset_id=asset.id,
        signal=signal,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        what_to_watch=what_to_watch,
        uncertainty_note=uncertainty_note,
        footer=catalog.footer,
    )

    result = frame.check_brief(
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        what_to_watch=what_to_watch,
        uncertainty_note=uncertainty_note,
        rendered_text=rendered_text,
        footer=catalog.footer,
        forbidden_lexicon=datasets.lexicons.forbidden_lexicon,
    )
    frame.enforce(result)

    return Brief(
        signal_id=signal.id,
        template_id=template.id,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        what_to_watch=what_to_watch,
        uncertainty_note=uncertainty_note,
        rendered_text=rendered_text,
        frame_checked=True,
    )


def _fill(template_text: str, slots: dict[str, str], template_id: str, section: str) -> str:
    try:
        return " ".join(template_text.format_map(slots).split())
    except KeyError as exc:
        raise KeyError(
            f"template {template_id}.{section} references unknown slot {exc.args[0]!r}"
        ) from exc


def _render_text(
    *,
    asset_name: str,
    asset_id: str,
    signal: Signal,
    what_happened: str,
    why_it_matters: str,
    what_to_watch: tuple[str, ...],
    uncertainty_note: str,
    footer: str,
) -> str:
    watch_lines = "\n".join(f"- {item}" for item in what_to_watch)
    return (
        f"{asset_name} ({asset_id}) | {signal.event_snapshot.event_type.value} | "
        f"{signal.direction.value} | {signal.magnitude.value} | "
        f"confidence {signal.confidence:.2f} | {signal.horizon_bars} bars\n\n"
        f"What happened\n{what_happened}\n\n"
        f"Why it matters\n{why_it_matters}\n\n"
        f"What to watch\n{watch_lines}\n\n"
        f"Uncertainty\n{uncertainty_note}\n\n"
        f"{footer}"
    )


def one_line_summary(signal: Signal, datasets: Datasets) -> str:
    """The digest's one-line summary -- template catalog text only (FR-8)."""
    asset = datasets.assets[signal.asset_id]
    summary = trigger_summary(signal.prior_key, signal.event_snapshot.stage, datasets.templates)
    return f"{asset.name} ({asset.symbol}) {summary}."


__all__ = [
    "already_priced",
    "announcement_context",
    "attributes_summary",
    "evidence_block",
    "fmt_money",
    "fmt_pct",
    "one_line_summary",
    "render_brief",
    "trigger_summary",
]

"""FR-9: advice rendering and the two-directional frame check.

Advice text renders only from the committed template catalog plus event/garment
fields. The check then asserts, **in both directions**:

* zero forbidden-lexicon matches in *generated* text — user-supplied strings
  (garment label/notes, event notes, listing titles) are rendered only inside
  typographic quotes and are exempt inside those quotes, so a real listing that
  says "guaranteed authentic" still produces advice (over-blocking is a
  compliance failure, EVALS M5b);
* every required section marker present;
* the collectibles-not-advice footer present verbatim, as the final block;
* the fee note rendering ``fee_assumption_pct``, and — since
  ``theta == fee_assumption_pct`` — every actionable advice quoting a modeled
  move at least as large as the friction it also quotes.

A failure raises before persistence; the store's CHECK constraint then makes a
non-checked advice row unrepresentable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..models import (
    AdviceAction,
    AdviceDecision,
    AdviceTemplate,
    AdviceTemplateCatalog,
    Driver,
    FashionEvent,
    FrameCheckResult,
    Garment,
    GarmentStatus,
    HoldReason,
    RenderedAdvice,
    ValuationMethod,
)
from .context import EngineContext
from .strata import Gazetteer

__all__ = [
    "FrameCheckError",
    "check_frame",
    "find_forbidden",
    "format_money",
    "format_pct",
    "format_signed_pct",
    "mask_quoted",
    "quote",
    "render_advice",
    "select_template",
]

#: Placeholder that replaces a quoted (user-supplied) span before the lexicon scan.
_MASK = "\x00"
_QUOTED = re.compile("“[^”]*(?:”|$)")
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'"})


class FrameCheckError(ValueError):
    """Raised before persistence when a rendered advice fails the FR-9 frame check."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__("advice failed the FR-9 frame check: " + ", ".join(violations))


def quote(user_text: str) -> str:
    """Wrap user-supplied text in typographic quotes, stripping any it already contains.

    Stripping is what stops a crafted label from closing the quoted span early and
    smuggling unquoted text past the lexicon scan.
    """
    cleaned = user_text.replace("“", '"').replace("”", '"').replace(_MASK, " ")
    return f"“{cleaned}”"


def mask_quoted(text: str) -> str:
    """Replace every quoted span with a sentinel so the lexicon scan skips user text."""
    return _QUOTED.sub(_MASK, text)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(phrase.strip().casefold())}(?!\w)")


def find_forbidden(text: str, lexicon: tuple[str, ...]) -> tuple[str, ...]:
    """Forbidden phrases present in ``text`` outside quoted spans, in catalog order."""
    haystack = mask_quoted(text).translate(_APOSTROPHES).casefold()
    return tuple(
        phrase for phrase in lexicon if _phrase_pattern(phrase).search(haystack) is not None
    )


def format_pct(fraction: float) -> str:
    """Render a fraction as an unsigned percentage (``0.12`` -> ``12%``)."""
    value = fraction * 100.0
    if abs(value - round(value)) < 1e-9:
        return f"{round(value):d}%"
    return f"{value:.1f}%"


def format_signed_pct(fraction: float) -> str:
    """Render a fraction as a signed percentage (``0.1232`` -> ``+12.3%``)."""
    return f"{fraction * 100.0:+.1f}%"


def format_money(value: float) -> str:
    return f"${value:,.2f}"


def select_template(
    catalog: AdviceTemplateCatalog,
    *,
    action: AdviceAction,
    hold_reason: HoldReason | None,
    status: GarmentStatus,
) -> AdviceTemplate:
    """Pick the one template serving ``(action, hold_reason, garment status)``.

    Status-specific templates win over status-agnostic ones so a ``watching``
    garment's sell advice can render as "avoid or wait" (FR-11).
    """
    candidates = [t for t in catalog.templates if t.action is action]
    if action is AdviceAction.HOLD:
        if hold_reason is None:
            raise ValueError("a hold advice must carry a hold reason")
        candidates = [t for t in candidates if hold_reason in t.hold_reasons]
    specific = [t for t in candidates if status in t.garment_statuses]
    generic = [t for t in candidates if not t.garment_statuses]
    chosen = specific or generic
    if not chosen:
        raise ValueError(
            f"no advice template for action={action} hold_reason={hold_reason} status={status}"
        )
    return chosen[0]


def _event_label(driver: Driver, event: FashionEvent | None, gazetteer: Gazetteer) -> str:
    type_words = driver.event_type.value.replace("_", " ")
    brand_name = (
        gazetteer.brand(event.brand_id).name
        if event is not None and gazetteer.has_brand(event.brand_id)
        else driver.target_stratum.split("/")[0]
    )
    label = f"{type_words} at {brand_name}"
    if event is not None:
        qualifier = next(
            (
                str(event.attributes[key])
                for key in ("reason", "acclaim", "severity", "polarity", "tier")
                if key in event.attributes
            ),
            None,
        )
        if qualifier:
            label += f" ({qualifier.replace('_', ' ')})"
        if event.era_id is not None:
            label += f", {gazetteer.era(event.era_id).label}"
        if event.notes:
            label += f" — {quote(event.notes)}"
    label += f" [scope {driver.target_stratum}]"
    return label


def _driver_lines(
    decision: AdviceDecision,
    events_by_id: Mapping[str, FashionEvent],
    ctx: EngineContext,
) -> str:
    if not decision.drivers:
        return ctx.templates.no_drivers_line
    lines = []
    for driver in decision.drivers:
        event = events_by_id.get(driver.event_id)
        prior = ctx.priors.get(driver.prior_key)
        lines.append(
            ctx.templates.driver_line_format.format(
                event_label=_event_label(driver, event, ctx.gazetteer),
                age_weeks=driver.age_weeks,
                lam=f"{driver.lam:.2f}",
                prior_rationale=prior.rationale if prior is not None else driver.prior_key,
            )
        )
    return "\n".join(lines)


def render_advice(
    decision: AdviceDecision,
    *,
    garment: Garment,
    events_by_id: Mapping[str, FashionEvent],
    ctx: EngineContext,
) -> RenderedAdvice:
    """Render one decision into its final text and named sections (FR-9)."""
    template = select_template(
        ctx.templates,
        action=decision.action,
        hold_reason=decision.hold_reason,
        status=garment.status,
    )
    if decision.fair_value is None:
        reason = decision.fair_value_reason.value if decision.fair_value_reason else "unknown"
        fair_value = f"unavailable ({reason})"
    else:
        fair_value = format_money(decision.fair_value)
    fields = {
        "garment_label": quote(garment.label),
        "action": decision.action.value,
        "expected_move": (
            "not computable"
            if decision.expected_return is None
            else format_signed_pct(decision.expected_return)
        ),
        "horizon_weeks": decision.horizon_weeks,
        "driver_lines": _driver_lines(decision, events_by_id, ctx),
        "fair_value": fair_value,
        "valuation_method": decision.fair_value_method.value,
        "level_usd": (
            "unavailable" if decision.level_usd is None else format_money(decision.level_usd)
        ),
        "confidence": ("n/a" if decision.confidence is None else f"{decision.confidence:.2f}"),
        "falsifier": template.falsifier,
        "fee_assumption_pct": format_pct(ctx.settings.fee_assumption_pct),
    }
    sections = {
        "headline": template.headline.format(**fields),
        "drivers": template.drivers.format(**fields),
        "valuation_context": template.valuation_context.format(**fields),
        "uncertainty": template.uncertainty.format(**fields),
        "fee_note": template.fee_note.format(**fields),
        "footer": ctx.templates.footer,
    }
    text = "\n\n".join(
        sections[name]
        for name in (
            "headline",
            "drivers",
            "valuation_context",
            "uncertainty",
            "fee_note",
            "footer",
        )
    )
    return RenderedAdvice(template_id=template.id, text=text, sections=sections)


def check_frame(
    text: str,
    *,
    action: AdviceAction,
    expected_return: float | None,
    catalog: AdviceTemplateCatalog,
    fee_assumption_pct: float,
) -> FrameCheckResult:
    """The FR-9 frame check. Returns every violation found (empty tuple == compliant)."""
    violations: list[str] = []
    for phrase in find_forbidden(text, catalog.forbidden_lexicon):
        violations.append(f"forbidden_lexicon:{phrase}")
    for marker in catalog.required_markers:
        if marker.marker not in text:
            violations.append(f"missing_section:{marker.section}")
    if not text.rstrip().endswith(catalog.footer):
        violations.append("footer_missing_or_tampered")
    if format_pct(fee_assumption_pct) not in text:
        violations.append("fee_assumption_not_rendered")
    if action in (AdviceAction.BUY, AdviceAction.SELL) and (
        expected_return is None or abs(expected_return) < fee_assumption_pct
    ):
        violations.append("expected_move_below_stated_fee")
    return FrameCheckResult(ok=not violations, violations=tuple(violations))


def render_checked(
    decision: AdviceDecision,
    *,
    garment: Garment,
    events_by_id: Mapping[str, FashionEvent],
    ctx: EngineContext,
) -> RenderedAdvice:
    """Render and frame-check in one step; raises :class:`FrameCheckError` on failure."""
    rendered = render_advice(decision, garment=garment, events_by_id=events_by_id, ctx=ctx)
    result = check_frame(
        rendered.text,
        action=decision.action,
        expected_return=decision.expected_return,
        catalog=ctx.templates,
        fee_assumption_pct=ctx.settings.fee_assumption_pct,
    )
    if not result.ok:
        raise FrameCheckError(result.violations)
    return rendered


def valuation_label(method: ValuationMethod) -> str:
    """Human label for a valuation method, used by the API/CLI layer."""
    return {
        ValuationMethod.REPEAT_SALES: "repeat-sales",
        ValuationMethod.COMP_BASED: "comp-based, not repeat-sales",
        ValuationMethod.UNAVAILABLE: "unavailable",
    }[method]

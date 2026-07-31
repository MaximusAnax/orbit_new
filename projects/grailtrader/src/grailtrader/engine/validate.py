"""FR-1: validation of the five committed datasets.

Pure: it operates on already-parsed models and raises
:class:`DatasetValidationError` naming the offending record. ``grailtrader init``
calls it before materialising anything, so a bad dataset aborts the load rather
than poisoning the pipeline.
"""

from __future__ import annotations

import string
from collections.abc import Mapping, Sequence
from itertools import pairwise

from ..models import (
    AdviceAction,
    AdviceTemplateCatalog,
    AdvisorConfig,
    Brand,
    ConditionTable,
    GarmentStatus,
    HoldReason,
    ImpactPrior,
)
from .frame import find_forbidden, select_template
from .impact import reachable_prior_keys
from .strata import Gazetteer

__all__ = ["DatasetValidationError", "validate_datasets"]

#: Fields the renderer supplies to an advice template (FR-9).
TEMPLATE_FIELDS = frozenset(
    {
        "garment_label",
        "action",
        "expected_move",
        "horizon_weeks",
        "driver_lines",
        "fair_value",
        "valuation_method",
        "level_usd",
        "confidence",
        "falsifier",
        "fee_assumption_pct",
    }
)
#: Fields the renderer supplies to the per-driver line format.
DRIVER_LINE_FIELDS = frozenset({"event_label", "age_weeks", "lam", "prior_rationale"})

_REQUIRED_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "drivers": ("driver_lines",),
    "valuation_context": ("fair_value", "valuation_method", "level_usd"),
    "uncertainty": ("confidence", "falsifier"),
    "fee_note": ("fee_assumption_pct",),
}


class DatasetValidationError(ValueError):
    """A committed dataset violates an FR-1 invariant."""

    def __init__(self, record: str, problem: str) -> None:
        self.record = record
        self.problem = problem
        super().__init__(f"{record}: {problem}")


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def validate_datasets(
    *,
    brands: Sequence[Brand],
    priors: Sequence[ImpactPrior],
    conditions: ConditionTable,
    templates: AdviceTemplateCatalog,
    config: AdvisorConfig,
) -> None:
    """Run every FR-1 check. Raises on the first violation, naming the record."""
    _validate_gazetteer(brands)
    prior_map = _validate_priors(priors, templates)
    _validate_conditions(conditions)
    _validate_templates(templates)
    _validate_config(config)
    # Cross-check: every reachable prior key must have a usable prior row.
    for key, kind in reachable_prior_keys().items():
        prior = prior_map[key]
        if prior.target is not kind:
            raise DatasetValidationError(
                f"impact_priors.json[{key}]",
                f"target {prior.target} does not match the FR-5 scope {kind}",
            )


def _validate_gazetteer(brands: Sequence[Brand]) -> None:
    if not brands:
        raise DatasetValidationError("brands.json", "the gazetteer is empty")
    seen_brands: set[str] = set()
    seen_eras: set[str] = set()
    seen_aliases: dict[str, str] = {}
    for brand in brands:
        if brand.id in seen_brands:
            raise DatasetValidationError(f"brands.json[{brand.id}]", "duplicate brand id")
        seen_brands.add(brand.id)
        for alias in (brand.name, brand.id, *brand.aliases):
            folded = alias.strip().casefold()
            if folded in seen_aliases and seen_aliases[folded] != brand.id:
                raise DatasetValidationError(
                    f"brands.json[{brand.id}]",
                    f"alias {alias!r} is already used by brand {seen_aliases[folded]}",
                )
            seen_aliases[folded] = brand.id
        for era in brand.eras:
            if era.id in seen_eras:
                raise DatasetValidationError(f"brands.json[{era.id}]", "duplicate era id")
            seen_eras.add(era.id)
    Gazetteer(brands)  # re-checks membership and uniqueness structurally


def _validate_priors(
    priors: Sequence[ImpactPrior], templates: AdviceTemplateCatalog
) -> Mapping[str, ImpactPrior]:
    prior_map: dict[str, ImpactPrior] = {}
    for prior in priors:
        if prior.key in prior_map:
            raise DatasetValidationError(f"impact_priors.json[{prior.key}]", "duplicate prior key")
        if not prior.rationale.strip():
            raise DatasetValidationError(
                f"impact_priors.json[{prior.key}]", "rationale must be non-empty"
            )
        if not prior.source_note.strip():
            raise DatasetValidationError(
                f"impact_priors.json[{prior.key}]", "source_note must be non-empty"
            )
        # Rationales are rendered verbatim into advice, so they face FR-9's lexicon too.
        forbidden = find_forbidden(prior.rationale, templates.forbidden_lexicon)
        if forbidden:
            raise DatasetValidationError(
                f"impact_priors.json[{prior.key}]",
                f"rationale contains forbidden lexicon {list(forbidden)}",
            )
        prior_map[prior.key] = prior
    missing = sorted(set(reachable_prior_keys()) - set(prior_map))
    if missing:
        raise DatasetValidationError(
            "impact_priors.json", f"no prior row for reachable keys {missing}"
        )
    return prior_map


def _validate_conditions(conditions: ConditionTable) -> None:
    # ConditionTable's own validators enforce the ordering and alias uniqueness;
    # this re-states the FR-1 requirement with a dataset-scoped error.
    multipliers = [row.multiplier for row in conditions.grades]
    for row, (before, after) in zip(conditions.grades[1:], pairwise(multipliers), strict=True):
        if after >= before:
            raise DatasetValidationError(
                f"conditions.json[{row.grade}]",
                f"multipliers must strictly decrease ({before} -> {after})",
            )


def _validate_templates(templates: AdviceTemplateCatalog) -> None:
    if not templates.footer.strip():
        raise DatasetValidationError("advice_templates.json", "footer must be non-empty")
    for phrase in templates.forbidden_lexicon:
        if not phrase.strip():
            raise DatasetValidationError(
                "advice_templates.json", "forbidden_lexicon carries an empty phrase"
            )
    unknown = _placeholders(templates.driver_line_format) - DRIVER_LINE_FIELDS
    if unknown:
        raise DatasetValidationError(
            "advice_templates.json[driver_line_format]",
            f"unknown placeholders {sorted(unknown)}",
        )

    for template in templates.templates:
        record = f"advice_templates.json[{template.id}]"
        for name in (
            "headline",
            "drivers",
            "valuation_context",
            "uncertainty",
            "fee_note",
            "falsifier",
        ):
            text = getattr(template, name)
            if not text.strip():
                raise DatasetValidationError(record, f"section {name} must be non-empty")
            unknown = _placeholders(text) - TEMPLATE_FIELDS
            if unknown:
                raise DatasetValidationError(
                    record, f"section {name} uses unknown placeholders {sorted(unknown)}"
                )
        for name, required in _REQUIRED_PLACEHOLDERS.items():
            present = _placeholders(getattr(template, name))
            missing = [field for field in required if field not in present]
            if missing:
                raise DatasetValidationError(record, f"section {name} must interpolate {missing}")
        body = "\n".join((*template.sections, template.falsifier, templates.footer))
        forbidden = find_forbidden(body, templates.forbidden_lexicon)
        if forbidden:
            raise DatasetValidationError(
                record, f"template text contains forbidden lexicon {list(forbidden)}"
            )
        for marker in templates.required_markers:
            if marker.marker not in body:
                raise DatasetValidationError(
                    record, f"template never renders required marker {marker.marker!r}"
                )

    # Every (action x hold-reason family x garment status) must resolve to exactly one template.
    for status in GarmentStatus:
        for action in (AdviceAction.BUY, AdviceAction.SELL):
            _require_template(templates, action=action, hold_reason=None, status=status)
        for reason in HoldReason:
            _require_template(
                templates, action=AdviceAction.HOLD, hold_reason=reason, status=status
            )


def _require_template(
    templates: AdviceTemplateCatalog,
    *,
    action: AdviceAction,
    hold_reason: HoldReason | None,
    status: GarmentStatus,
) -> None:
    try:
        select_template(templates, action=action, hold_reason=hold_reason, status=status)
    except ValueError as exc:
        raise DatasetValidationError("advice_templates.json", str(exc)) from None
    candidates = [t for t in templates.templates if t.action is action]
    if action is AdviceAction.HOLD:
        candidates = [t for t in candidates if hold_reason in t.hold_reasons]
    specific = [t for t in candidates if status in t.garment_statuses]
    generic = [t for t in candidates if not t.garment_statuses]
    resolved = specific or generic
    if len(resolved) != 1:
        raise DatasetValidationError(
            "advice_templates.json",
            f"{len(resolved)} templates resolve for action={action} "
            f"hold_reason={hold_reason} status={status}; exactly one is required",
        )


def _validate_config(config: AdvisorConfig) -> None:
    settings = config.advisor
    if settings.theta_buy != settings.fee_assumption_pct:
        raise DatasetValidationError(
            "advisor_config.json[advisor]",
            "theta_buy must equal fee_assumption_pct (SCOPE D-12 coherence)",
        )
    if not config.config_version.strip():
        raise DatasetValidationError("advisor_config.json", "config_version must be non-empty")
    if settings.active_transient_min <= 0:
        raise DatasetValidationError(
            "advisor_config.json[advisor]", "active_transient_min must be positive"
        )

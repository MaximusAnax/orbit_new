"""Committed reference datasets: loading (filesystem) + FR-3 validation (pure).

`newsalpha init` loads and validates `data/*.json`.  Validation aborts naming the
offending record; the engine never touches the filesystem, it receives a
`Datasets` instance as an explicit input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    ATTRIBUTE_SCHEMA,
    SIGNAL_ROLES_BY_TYPE,
    Asset,
    AssetKind,
    BriefTemplate,
    Direction,
    EventPattern,
    EventPrior,
    EventType,
    SourceTierName,
    Stage,
)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class DatasetError(ValueError):
    """Raised when a committed dataset violates an FR-3 invariant."""


@dataclass(frozen=True, slots=True)
class Lexicons:
    """Shared lexicons carried by `patterns.json` (DATA_MODEL.md)."""

    negation_cues: tuple[str, ...]
    hedge_cues: tuple[str, ...]
    historical_guards: tuple[str, ...]
    metaphor_stoplists: dict[str, tuple[str, ...]]
    venue_lexicon: dict[str, str | None]
    abbreviations: tuple[str, ...]
    forbidden_lexicon: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TemplateCatalog:
    """`templates.json` -- the only source of brief prose (FR-7)."""

    templates: tuple[BriefTemplate, ...]
    footer: str
    trigger_summaries: dict[str, str]
    attribute_labels: dict[str, str]
    falsifiers: dict[str, str]
    already_priced_note: str

    def resolve(self, event_type: EventType, direction: Direction) -> BriefTemplate:
        for template in self.templates:
            if template.event_type is event_type and template.direction is direction:
                return template
        for template in self.templates:
            if template.event_type is event_type and template.direction is None:
                return template
        raise DatasetError(f"no brief template for ({event_type}, {direction})")


@dataclass(frozen=True, slots=True)
class Datasets:
    """Everything `init` validated, indexed for the engine."""

    assets: dict[str, Asset]
    patterns: tuple[EventPattern, ...]
    lexicons: Lexicons
    priors: dict[tuple[str, str], EventPrior]
    templates: TemplateCatalog
    source_tiers: dict[str, SourceTierName]
    default_tier: SourceTierName
    tier_weights: dict[SourceTierName, float]
    benchmarks: dict[str, list[str]]
    active_window_days: int = 30
    #: Derived lookup tables the gazetteer scan memoizes (pure functions of the
    #: fields above; never part of the dataset's meaning).
    gazetteer_cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # -- lookups ---------------------------------------------------------- #

    def tier_for(self, domain: str) -> SourceTierName:
        return self.source_tiers.get(domain.lower(), self.default_tier)

    def weight_for(self, tier: SourceTierName) -> float:
        return self.tier_weights[tier]

    def linkable_assets(self) -> list[Asset]:
        """Assets the gazetteer scan considers: equities and crypto, never indexes."""
        return [a for a in self.assets.values() if a.kind is not AssetKind.index]

    def patterns_for(self, event_type: EventType) -> list[EventPattern]:
        return [p for p in self.patterns if p.event_type is event_type]

    def resolve_prior(self, key: str, kind: str) -> EventPrior | None:
        """Most-specific-wins resolution over (key, kind), then polarity `*` (FR-3.3)."""
        event_type, role, polarity = key.split(".")
        for candidate_polarity in (polarity, "*"):
            candidate_key = f"{event_type}.{role}.{candidate_polarity}"
            for candidate_kind in (kind, "*"):
                prior = self.priors.get((candidate_key, candidate_kind))
                if prior is not None:
                    return prior
        return None


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetError(f"missing committed dataset: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path.name} is not valid JSON: {exc}") from exc


def load_datasets(data_dir: Path | str | None = None, *, active_window_days: int = 30) -> Datasets:
    """Load and validate every committed dataset. Raises `DatasetError` naming the record."""
    base = Path(data_dir) if data_dir is not None else DATA_DIR

    assets_raw = _read_json(base / "assets.json")["assets"]
    patterns_raw = _read_json(base / "patterns.json")
    priors_raw = _read_json(base / "priors.json")["priors"]
    templates_raw = _read_json(base / "templates.json")
    tiers_raw = _read_json(base / "source_tiers.json")
    benchmarks_raw = _read_json(base / "benchmarks.json")

    assets: dict[str, Asset] = {}
    for row in assets_raw:
        try:
            asset = Asset.model_validate(row)
        except ValueError as exc:
            raise DatasetError(f"assets.json record {row.get('id', row)!r}: {exc}") from exc
        if asset.id in assets:
            raise DatasetError(f"assets.json: duplicate asset id {asset.id!r}")
        assets[asset.id] = asset

    patterns: list[EventPattern] = []
    for row in patterns_raw["patterns"]:
        try:
            patterns.append(EventPattern.model_validate(row))
        except ValueError as exc:
            raise DatasetError(f"patterns.json record {row.get('id', row)!r}: {exc}") from exc

    lexicons = Lexicons(
        negation_cues=tuple(patterns_raw["negation_cues"]),
        hedge_cues=tuple(patterns_raw["hedge_cues"]),
        historical_guards=tuple(patterns_raw["historical_guards"]),
        metaphor_stoplists={k: tuple(v) for k, v in patterns_raw["metaphor_stoplists"].items()},
        venue_lexicon=dict(patterns_raw["venue_lexicon"]),
        abbreviations=tuple(patterns_raw["abbreviations"]),
        forbidden_lexicon=tuple(patterns_raw["forbidden_lexicon"]),
    )

    priors: dict[tuple[str, str], EventPrior] = {}
    for row in priors_raw:
        try:
            prior = EventPrior.model_validate(row)
        except ValueError as exc:
            raise DatasetError(f"priors.json record {row.get('key', row)!r}: {exc}") from exc
        pk = (prior.key, prior.kind)
        if pk in priors:
            raise DatasetError(f"priors.json: duplicate row for {pk}")
        priors[pk] = prior

    templates: list[BriefTemplate] = []
    for row in templates_raw["templates"]:
        try:
            templates.append(BriefTemplate.model_validate(row))
        except ValueError as exc:
            raise DatasetError(f"templates.json record {row.get('id', row)!r}: {exc}") from exc

    catalog = TemplateCatalog(
        templates=tuple(templates),
        footer=templates_raw["footer"],
        trigger_summaries=dict(templates_raw["trigger_summaries"]),
        attribute_labels=dict(templates_raw["attribute_labels"]),
        falsifiers=dict(templates_raw["falsifiers"]),
        already_priced_note=templates_raw["already_priced_note"],
    )

    tier_weights = {SourceTierName(k): float(v) for k, v in tiers_raw["tier_weights"].items()}
    source_tiers = {k.lower(): SourceTierName(v) for k, v in tiers_raw["domains"].items()}
    default_tier = SourceTierName(tiers_raw["default_tier"])

    datasets = Datasets(
        assets=assets,
        patterns=tuple(patterns),
        lexicons=lexicons,
        priors=priors,
        templates=catalog,
        source_tiers=source_tiers,
        default_tier=default_tier,
        tier_weights=tier_weights,
        benchmarks={k: list(v) for k, v in benchmarks_raw.items()},
        active_window_days=active_window_days,
    )
    validate_datasets(datasets)
    return datasets


# --------------------------------------------------------------------------- #
# FR-3 validation (pure)
# --------------------------------------------------------------------------- #


def validate_datasets(datasets: Datasets) -> None:
    """Every FR-3 check, in the documented order. Raises `DatasetError`."""
    _validate_assets(datasets)
    _validate_patterns(datasets)
    _validate_priors(datasets)
    _validate_templates(datasets)
    _validate_benchmarks(datasets)


def _validate_assets(datasets: Datasets) -> None:
    seen_alias: dict[str, str] = {}
    seen_symbol: dict[str, str] = {}
    for asset in datasets.assets.values():
        if asset.symbol in seen_symbol:
            raise DatasetError(
                f"assets.json: symbol {asset.symbol!r} used by {seen_symbol[asset.symbol]} "
                f"and {asset.id}"
            )
        seen_symbol[asset.symbol] = asset.id
        for alias in asset.aliases:
            if alias in seen_alias:
                raise DatasetError(
                    f"assets.json: alias {alias!r} used by {seen_alias[alias]} and {asset.id}"
                )
            seen_alias[alias] = asset.id
        if asset.benchmark_id is not None and asset.benchmark_id not in datasets.assets:
            raise DatasetError(
                f"assets.json: {asset.id} references unknown benchmark {asset.benchmark_id!r}"
            )
        if asset.benchmark_id is not None:
            benchmark = datasets.assets[asset.benchmark_id]
            if benchmark.kind is not AssetKind.index:
                raise DatasetError(
                    f"assets.json: {asset.id}'s benchmark {benchmark.id} is not an index"
                )


def _validate_patterns(datasets: Datasets) -> None:
    by_type: dict[EventType, list[EventPattern]] = {t: [] for t in EventType}
    trigger_owner: dict[str, tuple[str, EventType]] = {}
    for pattern in datasets.patterns:
        by_type[pattern.event_type].append(pattern)
        for trigger in pattern.triggers:
            folded = trigger.casefold()
            owner = trigger_owner.get(folded)
            if owner is not None and owner[1] is not pattern.event_type:
                raise DatasetError(
                    f"patterns.json: trigger {trigger!r} is claimed by both {owner[0]} "
                    f"({owner[1]}) and {pattern.id} ({pattern.event_type}); trigger sets must be "
                    "disjoint across event types"
                )
            trigger_owner[folded] = (pattern.id, pattern.event_type)
    for event_type, patterns in by_type.items():
        if len(patterns) < 2:
            raise DatasetError(
                f"patterns.json: event type {event_type} has {len(patterns)} pattern(s); "
                "at least 2 are required"
            )
    for surface, asset_id in datasets.lexicons.venue_lexicon.items():
        if not surface.strip():
            raise DatasetError("patterns.json: empty venue surface")
        if asset_id is not None and asset_id not in datasets.assets:
            raise DatasetError(
                f"patterns.json: venue {surface!r} maps to unknown asset {asset_id!r}"
            )
    if not datasets.lexicons.forbidden_lexicon:
        raise DatasetError("patterns.json: forbidden_lexicon must not be empty (FR-7)")
    for stoplist_type in datasets.lexicons.metaphor_stoplists:
        if stoplist_type not in {t.value for t in EventType}:
            raise DatasetError(
                f"patterns.json: metaphor_stoplists key {stoplist_type!r} is not an event type"
            )


def reachable_polarities(datasets: Datasets, event_type: EventType) -> set[str]:
    """Polarity tokens reachable from any pattern of this type (`*` when none)."""
    values: set[str] = set()
    for pattern in datasets.patterns_for(event_type):
        if pattern.polarity is not None:
            values.add(pattern.polarity)
        elif "polarity" in pattern.attribute_extractors:
            schema = ATTRIBUTE_SCHEMA[event_type].get("polarity")
            values.update(schema or ())
        else:
            values.add("*")
    return values or {"*"}


def reachable_stages(datasets: Datasets, event_type: EventType) -> set[Stage]:
    """Stages FR-4's suppression rules can produce for this type."""
    stages = {Stage.confirmed}
    if datasets.lexicons.hedge_cues:
        stages.add(Stage.rumored)
    if event_type is EventType.mna and datasets.lexicons.negation_cues:
        stages.add(Stage.denied)
    return stages


def _validate_priors(datasets: Datasets) -> None:
    for event_type in EventType:
        polarities = reachable_polarities(datasets, event_type)
        stages = reachable_stages(datasets, event_type)
        for role in SIGNAL_ROLES_BY_TYPE[event_type]:
            for polarity in polarities:
                key = f"{event_type.value}.{role.value}.{polarity}"
                for kind in ("equity", "crypto"):
                    # (key, kind) is a dict key, so at most one row exists per pair and
                    # `resolve_prior`'s most-specific-wins order makes resolution unique.
                    resolved = datasets.resolve_prior(key, kind)
                    if resolved is None:
                        raise DatasetError(
                            f"priors.json: no row resolves for ({key}, kind={kind}); FR-3.3 "
                            "requires exactly one"
                        )
                    for stage in stages:
                        try:
                            resolved.effective(stage)
                        except ValueError as exc:
                            raise DatasetError(
                                f"priors.json: {resolved.key}/{resolved.kind} stage override "
                                f"{stage} is invalid: {exc}"
                            ) from exc
    for (key, kind), prior in datasets.priors.items():
        if prior.event_type not in EventType:  # pragma: no cover - enum guarantees
            raise DatasetError(f"priors.json: {key} names an unknown event type")
        if prior.role not in SIGNAL_ROLES_BY_TYPE[prior.event_type]:
            raise DatasetError(
                f"priors.json: {key} names role {prior.role} which is not signal-bearing for "
                f"{prior.event_type}"
            )
        if kind not in {"equity", "crypto", "*"}:  # pragma: no cover - Literal guarantees
            raise DatasetError(f"priors.json: {key} has invalid kind {kind!r}")


def forbidden_regexes(lexicon: tuple[str, ...] | list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Word-boundary matchers for the frame check (FR-7).

    Hyphen counts as a word character on both sides, so "sell-off", "sell-side",
    "buy-side" and "buyout"/"buyer" never match while a bare imperative does.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for term in lexicon:
        body = r"[\s\-]+".join(re.escape(part) for part in term.split())
        compiled.append((term, re.compile(rf"(?<![\w\-]){body}(?![\w\-])", re.IGNORECASE)))
    return compiled


def _validate_templates(datasets: Datasets) -> None:
    catalog = datasets.templates
    if not catalog.footer.strip():
        raise DatasetError("templates.json: footer must be present and non-empty")
    matchers = forbidden_regexes(datasets.lexicons.forbidden_lexicon)
    for template in catalog.templates:
        blob = " ".join(
            [
                template.what_happened,
                template.why_it_matters,
                " ".join(template.what_to_watch),
                template.uncertainty_note,
            ]
        )
        for term, matcher in matchers:
            if matcher.search(blob):
                raise DatasetError(
                    f"templates.json: template {template.id} contains forbidden term {term!r}"
                )
    for term, matcher in matchers:
        if matcher.search(catalog.footer):
            raise DatasetError(f"templates.json: footer contains forbidden term {term!r}")
    for prior in datasets.priors.values():
        for term, matcher in matchers:
            if matcher.search(prior.rationale) or matcher.search(prior.source_note):
                raise DatasetError(
                    f"priors.json: rationale for {prior.key}/{prior.kind} contains forbidden "
                    f"term {term!r} (it is rendered verbatim into briefs)"
                )
    for event_type in EventType:
        if event_type.value not in catalog.falsifiers:
            raise DatasetError(f"templates.json: no falsifier for event type {event_type}")
        for direction in (Direction.bullish, Direction.bearish):
            catalog.resolve(event_type, direction)
    for prior in datasets.priors.values():
        stages = reachable_stages(datasets, prior.event_type)
        for stage in stages:
            eff = prior.effective(stage)
            if eff.direction is Direction.unclear:
                continue
            catalog.resolve(prior.event_type, eff.direction)
            if _trigger_summary_key(prior.key, stage, catalog) is None:
                raise DatasetError(
                    f"templates.json: no trigger_summaries entry for {prior.key} at stage {stage}"
                )


def _trigger_summary_key(prior_key: str, stage: Stage, catalog: TemplateCatalog) -> str | None:
    for candidate in (f"{prior_key}|{stage.value}", prior_key):
        if candidate in catalog.trigger_summaries:
            return candidate
    return None


def _validate_benchmarks(datasets: Datasets) -> None:
    for index_id, members in datasets.benchmarks.items():
        if index_id not in datasets.assets:
            raise DatasetError(f"benchmarks.json: unknown index {index_id!r}")
        for member in members:
            if member.startswith("live_proxy:"):
                continue
            if member not in datasets.assets:
                raise DatasetError(
                    f"benchmarks.json: {index_id} references unknown asset {member!r}"
                )


__all__ = [
    "DATA_DIR",
    "DatasetError",
    "Datasets",
    "Lexicons",
    "TemplateCatalog",
    "forbidden_regexes",
    "load_datasets",
    "reachable_polarities",
    "reachable_stages",
    "validate_datasets",
]

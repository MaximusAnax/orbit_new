"""Every EVALS.md metric, implemented with the exact formula the doc gives.

Pure functions over a :class:`evals.harness.PipelineResult`; both entry points
(``evals/run.py`` and ``evals/test_gates.py``) share them. Ground truth always
comes from the committed fixtures' construction tables, never from the system
under evaluation — including the FR-9 compliance check, which is re-implemented
here from the spec so the gate does not trust ``engine/frame.py`` to grade itself.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grailtrader.ids import listing_id
from grailtrader.models import (
    Advice,
    AdviceAction,
    AdviceDecision,
    AdviceTemplateCatalog,
    Garment,
)
from grailtrader.weeks import weeks_between

from .harness import FIXTURES, PipelineResult, Replay, ScenarioData

# --------------------------------------------------------------------------- #
# Gate table (EVALS.md "Naive baselines and gates")                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Gate:
    """One gated metric: how it is compared and what it must beat."""

    key: str
    label: str
    op: str  # ">=" | "<=" | "==" | "in"
    threshold: float | tuple[float, float]
    baseline: str = ""

    def passes(self, value: float) -> bool:
        if self.op == ">=":
            return value >= float(self.threshold) - 1e-12
        if self.op == "<=":
            return value <= float(self.threshold) + 1e-12
        if self.op == "==":
            return abs(value - float(self.threshold)) < 1e-12
        low, high = self.threshold  # type: ignore[misc]
        return low - 1e-12 <= value <= high + 1e-12

    def describe(self) -> str:
        if self.op == "in":
            low, high = self.threshold  # type: ignore[misc]
            return f"in [{low:g}, {high:g}]"
        return f"{self.op} {float(self.threshold):g}"


#: The gate thresholds, verbatim from EVALS.md's table.
GATES: dict[str, Gate] = {
    "M0a": Gate("M0a", "index coverage", ">=", 0.95, "min_sales=15 scores ~0.45"),
    "M0b": Gate("M0b", "N actionable", ">=", 400, "selective abstention: 12"),
    "M0c": Gate("M0c", "min candidates per bucket", ">=", 25, "abstention leaves lo empty: 0"),
    "M0d-sell": Gate("M0d-sell", "sell-into-decay count", ">=", 15, "scandal-only seller: 0"),
    "M0d-buy": Gate("M0d-buy", "phase-in buy count", ">=", 100, "-"),
    "M0e": Gate("M0e", "N actionable per placebo seed", ">=", 400, "-"),
    "M0f-m1a": Gate("M0f-m1a", "baseline band: naive index error", "in", (0.08, 0.20), "-"),
    "M0f-mom": Gate("M0f-mom", "baseline band: theta-momentum hit", "in", (0.42, 0.58), "-"),
    "M0f-evt": Gate("M0f-evt", "baseline band: event-naive hit", "in", (0.50, 0.66), "-"),
    "M0f-hold": Gate("M0f-hold", "baseline band: |always-hold spread|", "<=", 0.02, "-"),
    "M1a": Gate("M1a", "index error (median)", "<=", 0.05, "naive weekly mean ~0.12"),
    "M1a-p90": Gate("M1a-p90", "index error (p90)", "<=", 0.14, "naive weekly mean ~0.35"),
    "M1a-dense": Gate("M1a-dense", "max dense per-stratum median", "<=", 0.06, "~0.13"),
    "M1a-sparse": Gate("M1a-sparse", "max sparse per-stratum median", "<=", 0.12, "~0.22"),
    "M1b-recall": Gate("M1b-recall", "fence recall on planted outliers", ">=", 0.90, "no fence: 0"),
    "M1b-false": Gate(
        "M1b-false", "fence false-exclusion of clean sales", "<=", 0.05, "no fence: 0"
    ),
    "M2a": Gate("M2a", "directional hit rate", ">=", 0.70, "theta-momentum ~0.50"),
    "M2b": Gate("M2b", "buy-minus-sell spread", ">=", 0.08, "always-hold 0.00"),
    "M2-excl": Gate("M2-excl", "non-window exclusions / actionable", "<=", 0.05, "-"),
    "M3a": Gate("M3a", "calibration separation hi - lo", ">=", 0.15, "z-term only ~0.06"),
    "M3b": Gate("M3b", "anti-overclaim min(hit - (conf - 0.10))", ">=", 0.0, "stamp 0.85: -0.25"),
    "M3c": Gate("M3c", "hi-bucket hit rate", ">=", 0.80, "-"),
    "M4a": Gate("M4a", "placebo |hit - 0.5| (worst seed)", "<=", 0.07, "leaky harness >= 0.10"),
    "M4b": Gate("M4b", "placebo |spread| (worst seed)", "<=", 0.04, "leaky harness >= 0.06"),
    "M5a": Gate("M5a", "rendered advice compliance", "==", 1.0, "no frame check ~0.90"),
    "M5b": Gate("M5b", "frame-case verdict agreement", "==", 1.0, "over-strict matcher ~0.60"),
    "M6a": Gate("M6a", "index error, scenario B", "<=", 0.09, "naive mean ~0.16"),
    "M6b": Gate("M6b", "directional hit rate, scenario B", ">=", 0.60, "theta-momentum ~0.50"),
    "M6c": Gate("M6c", "anti-overclaim, scenario B", ">=", 0.0, "-"),
}

#: A confidence bucket needs this many candidates before its rate is gated. EVALS
#: sets the floor at 25 for scenario A (M0c); scenario B is a sixth of the size and
#: EVALS sets no floor for it, so M6c is computed over buckets that clear this
#: minimum and the skipped ones are printed (see docs/REVIEW.md finding G3).
BUCKET_MIN_N = 25
SCENARIO_B_BUCKET_MIN_N = 10


# --------------------------------------------------------------------------- #
# M1 — index fidelity                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IndexFidelity:
    median: float
    p90: float
    dense_max: float
    sparse_max: float
    parent_max: float
    n_cells: int
    n_strata: int
    per_stratum: dict[str, float]


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _median(values: Sequence[float]) -> float:
    return _percentile(values, 0.5)


def index_fidelity(result: PipelineResult) -> IndexFidelity:
    """M1a — scale-aligned percentage error against the planted truth, with tails.

    ``alpha_s = exp(mean_t ln(T/R))`` removes the base-week noise (a level offset
    is not tracking error), then ``err = |alpha * R / T - 1|`` per eligible cell.
    Leaf cells are graded against the planted latent level; parent cells against
    the generator's own chain-linked truth, so FR-4's parent implementation is
    graded rather than trusted.
    """
    data = result.data
    week_of = {week: position for position, week in enumerate(data.weeks)}
    density = data.coverage_truth["strata"]
    leaf_truth = data.index_truth["leaf_level_usd"]
    parent_truth = data.index_truth["parent_index"]

    all_errors: list[float] = []
    per_stratum: dict[str, float] = {}
    dense: list[float] = []
    sparse: list[float] = []
    parents: list[float] = []

    for stratum in result.index.strata:
        series = leaf_truth.get(stratum) or parent_truth.get(stratum)
        if series is None:
            continue
        pairs = [
            (point.index_value, series[week_of[point.week]])
            for point in result.index.points(stratum)
            if point.week in week_of
        ]
        if len(pairs) < 5:
            continue
        alpha = math.exp(sum(math.log(truth / rec) for rec, truth in pairs) / len(pairs))
        errors = [abs(alpha * rec / truth - 1.0) for rec, truth in pairs]
        all_errors.extend(errors)
        per_stratum[stratum] = _median(errors)
        if stratum in density:
            (dense if density[stratum]["density"] == "dense" else sparse).append(
                per_stratum[stratum]
            )
        else:
            parents.append(per_stratum[stratum])

    return IndexFidelity(
        median=_median(all_errors),
        p90=_percentile(all_errors, 0.90),
        dense_max=max(dense, default=0.0),
        sparse_max=max(sparse, default=0.0),
        parent_max=max(parents, default=0.0),
        n_cells=len(all_errors),
        n_strata=len(per_stratum),
        per_stratum=per_stratum,
    )


def naive_index_error(data: ScenarioData) -> float:
    """M1a's naive baseline: the weekly arithmetic mean of *raw* sold prices.

    No condition adjustment, no fence, no trailing window — scored with the same
    alpha-alignment and median as M1a so the two numbers are comparable.
    """
    week_of = {week: position for position, week in enumerate(data.weeks)}
    leaf_truth = data.index_truth["leaf_level_usd"]
    buckets: dict[tuple[str, str], list[float]] = {}
    for listing in data.listings:
        if listing.sold_price is None or listing.sold_week is None:
            continue
        buckets.setdefault((listing.stratum_path, listing.sold_week), []).append(listing.sold_price)

    errors: list[float] = []
    by_stratum: dict[str, list[tuple[float, float]]] = {}
    for (stratum, week), prices in buckets.items():
        if stratum not in leaf_truth or week not in week_of:
            continue
        by_stratum.setdefault(stratum, []).append(
            (sum(prices) / len(prices), leaf_truth[stratum][week_of[week]])
        )
    for pairs in by_stratum.values():
        if len(pairs) < 5:
            continue
        alpha = math.exp(sum(math.log(truth / rec) for rec, truth in pairs) / len(pairs))
        errors.extend(abs(alpha * rec / truth - 1.0) for rec, truth in pairs)
    return _median(errors)


# --------------------------------------------------------------------------- #
# M0a — coverage                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Coverage:
    value: float
    published: int
    eligible: int
    missed: list[tuple[str, str]]


def index_coverage(result: PipelineResult) -> Coverage:
    """M0a — published cells over *generator-derived* truth-eligible cells."""
    data = result.data
    published = 0
    missed: list[tuple[str, str]] = []
    eligible = 0
    for stratum, flags in data.coverage_truth["truth_eligible"].items():
        have = {point.week for point in result.index.points(stratum)}
        for position, flag in enumerate(flags):
            if not flag:
                continue
            eligible += 1
            week = data.weeks[position]
            if week in have:
                published += 1
            elif len(missed) < 20:
                missed.append((stratum, week))
    return Coverage(published / eligible if eligible else 0.0, published, eligible, missed)


# --------------------------------------------------------------------------- #
# M1b — outlier fence mechanism, plus the ungated D-fence diagnostic            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FenceMetrics:
    recall: float
    n_planted: int
    false_rate: float
    n_clean: int
    ambiguous_excluded: float
    n_ambiguous: int
    near_boundary_excluded: float
    n_near_boundary: int


#: A clean sale counts as "near boundary" when its own log deviation from the
#: latent level sits within this distance of the fence's floor cutoff (0.7885).
NEAR_BOUNDARY_MARGIN = 0.20


def fence_metrics(result: PipelineResult) -> FenceMetrics:
    """M1b (mechanism gate) and the ungated D-fence diagnostic."""
    data = result.data
    classes = data.listings_truth["classes"]
    planted_ids = {
        listing_id("fixture", external) for external in (*classes["fake"], *classes["mislabel"])
    }
    ambiguous_ids = {listing_id("fixture", external) for external in classes["ambiguous"]}

    excluded: set[str] = set()
    published_weeks: dict[str, set[str]] = {}
    for stratum in result.index.leaf_strata:
        weeks = {point.week for point in result.index.points(stratum)}
        published_weeks[stratum] = weeks
        for week in weeks:
            excluded.update(result.index.excluded_listing_ids(stratum, week))

    week_of = {week: position for position, week in enumerate(data.weeks)}
    leaf_truth = data.index_truth["leaf_level_usd"]
    window = result.data.ctx.index_config.window_weeks
    cutoff = result.data.ctx.index_config.fence_floor_log

    n_planted = n_clean = n_ambiguous = n_near = 0
    hit_planted = hit_clean = hit_ambiguous = hit_near = 0
    for listing in data.listings:
        if listing.sold_week is None or listing.sold_price is None:
            continue
        weeks = published_weeks.get(listing.stratum_path, set())
        if not any(
            data.weeks[position] in weeks
            for position in range(
                week_of[listing.sold_week],
                min(week_of[listing.sold_week] + window, len(data.weeks)),
            )
        ):
            continue  # the fence never ran on a window that produced a point
        was_excluded = listing.id in excluded
        if listing.id in planted_ids:
            n_planted += 1
            hit_planted += was_excluded
        elif listing.id in ambiguous_ids:
            n_ambiguous += 1
            hit_ambiguous += was_excluded
        else:
            n_clean += 1
            hit_clean += was_excluded
            adjusted = data.ctx.mapper.adjust(listing.sold_price, listing.condition)
            deviation = abs(
                math.log(adjusted / leaf_truth[listing.stratum_path][week_of[listing.sold_week]])
            )
            if cutoff - NEAR_BOUNDARY_MARGIN <= deviation <= cutoff + NEAR_BOUNDARY_MARGIN:
                n_near += 1
                hit_near += was_excluded

    return FenceMetrics(
        recall=hit_planted / n_planted if n_planted else 0.0,
        n_planted=n_planted,
        false_rate=hit_clean / n_clean if n_clean else 0.0,
        n_clean=n_clean,
        ambiguous_excluded=hit_ambiguous / n_ambiguous if n_ambiguous else 0.0,
        n_ambiguous=n_ambiguous,
        near_boundary_excluded=hit_near / n_near if n_near else 0.0,
        n_near_boundary=n_near,
    )


# --------------------------------------------------------------------------- #
# M2 / M3 / M4 — the replay                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Calibration:
    separation: float
    anti_overclaim: float
    hi_hit: float
    min_bucket_n: int
    buckets: dict[str, dict[str, float]]
    gated_buckets: tuple[str, ...]


def calibration(replay: Replay, *, min_n: int = BUCKET_MIN_N) -> Calibration:
    """M3a separation, M3b anti-overclaim and M3c hi-bucket floor, over candidates."""
    buckets = replay.aggregates["buckets"]
    gated = tuple(name for name in ("lo", "mid", "hi") if buckets[name]["n"] >= min_n)
    margins = [buckets[name]["hit_rate"] - (buckets[name]["mean_conf"] - 0.10) for name in gated]
    return Calibration(
        separation=buckets["hi"]["hit_rate"] - buckets["lo"]["hit_rate"],
        anti_overclaim=min(margins) if margins else 0.0,
        hi_hit=buckets["hi"]["hit_rate"],
        min_bucket_n=min(buckets[name]["n"] for name in ("lo", "mid", "hi")),
        buckets={name: dict(buckets[name]) for name in ("lo", "mid", "hi")},
        gated_buckets=gated,
    )


def exclusion_rate(replay: Replay) -> float:
    """M2's exclusion budget: genuine index gaps over actionable advice.

    ``out_of_window`` is a property of the scenario's edge, not a failure, and is
    excluded from both the numerator and the denominator (EVALS M2 / REVIEW E8).
    """
    excluded = replay.aggregates["excluded"]
    counted = sum(count for reason, count in excluded.items() if reason != "out_of_window")
    actionable = replay.aggregates["n_actionable"]
    return counted / actionable if actionable else 0.0


@dataclass(frozen=True)
class PlaceboMetrics:
    worst_hit_gap: float
    worst_spread: float
    min_actionable: int
    per_seed: dict[int, dict[str, float]]


def placebo_metrics(result: PipelineResult) -> PlaceboMetrics:
    """M4a/M4b over the three committed seeds — gated on the worst of them."""
    per_seed: dict[int, dict[str, float]] = {}
    for seed, replay in result.placebo.items():
        aggregates = replay.aggregates
        per_seed[seed] = {
            "n_actionable": aggregates["n_actionable"],
            "hit_rate": aggregates["hit_rate"],
            "hit_gap": abs(aggregates["hit_rate"] - 0.5),
            "spread": abs(aggregates["spread_buy_minus_sell"]),
        }
    return PlaceboMetrics(
        worst_hit_gap=max((row["hit_gap"] for row in per_seed.values()), default=0.0),
        worst_spread=max((row["spread"] for row in per_seed.values()), default=0.0),
        min_actionable=int(min((row["n_actionable"] for row in per_seed.values()), default=0)),
        per_seed=per_seed,
    )


def regime_counts(replay: Replay) -> dict[str, int]:
    return dict(replay.aggregates["regimes"])


def contradiction_diagnostic(result: PipelineResult) -> dict[str, float]:
    """D-contra (printed, ungated): skill and confidence on prior-contradicting events."""
    contradicting = {
        leg["event_id"] for leg in result.data.impact_truth["legs"] if leg.get("contradicts_prior")
    }
    hits = 0
    n = 0
    confidence = 0.0
    for row in result.real.results:
        if not row.is_candidate or row.hit is None or not row.driver_event_ids:
            continue
        if set(row.driver_event_ids) <= contradicting:
            n += 1
            hits += int(row.hit)
            confidence += row.confidence or 0.0
    return {
        "n": n,
        "hit_rate": hits / n if n else 0.0,
        "mean_conf": confidence / n if n else 0.0,
    }


# --------------------------------------------------------------------------- #
# M5 — FR-9 framing compliance, checked independently of engine/frame.py        #
# --------------------------------------------------------------------------- #

_OPEN_QUOTE = "“"
_CLOSE_QUOTE = "”"
_APOSTROPHES = {"\u2018": "'", "\u2019": "'"}


def strip_quoted(text: str) -> str:
    """Drop every typographically quoted span — user text is exempt (FR-9).

    Written as an explicit scanner rather than a regex so this reference checker
    shares no code path with ``engine/frame.py``.
    """
    out: list[str] = []
    inside = False
    for character in text:
        if character == _OPEN_QUOTE:
            inside = True
            continue
        if character == _CLOSE_QUOTE:
            inside = False
            continue
        if not inside:
            out.append(character)
    return "".join(out)


def _normalise(text: str) -> str:
    for source, target in _APOSTROPHES.items():
        text = text.replace(source, target)
    return text.casefold()


def _is_word_char(character: str) -> bool:
    return character.isalnum() or character == "_"


def contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-phrase, case-insensitive containment with word boundaries."""
    needle = _normalise(phrase.strip())
    if not needle:
        return False
    start = 0
    while True:
        position = haystack.find(needle, start)
        if position < 0:
            return False
        before = haystack[position - 1] if position else ""
        after_index = position + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else ""
        if not (before and _is_word_char(before)) and not (after and _is_word_char(after)):
            return True
        start = position + 1


def reference_frame_check(
    text: str,
    *,
    action: AdviceAction,
    expected_return: float | None,
    catalog: AdviceTemplateCatalog,
    fee_assumption_pct: float,
) -> list[str]:
    """An independent re-implementation of the FR-9 frame check.

    Returns the violations found; an empty list means compliant. Same spec as
    ``engine.frame.check_frame``, deliberately different code, so M5a does not
    ask the engine to grade itself.
    """
    violations: list[str] = []
    scannable = _normalise(strip_quoted(text))
    for phrase in catalog.forbidden_lexicon:
        if contains_phrase(scannable, phrase):
            violations.append(f"forbidden_lexicon:{phrase}")
    for marker in catalog.required_markers:
        if marker.marker not in text:
            violations.append(f"missing_section:{marker.section}")
    if not text.rstrip().endswith(catalog.footer):
        violations.append("footer_missing_or_tampered")
    percent = fee_assumption_pct * 100.0
    rendered_fee = (
        f"{round(percent):d}%" if abs(percent - round(percent)) < 1e-9 else f"{percent:.1f}%"
    )
    if rendered_fee not in text:
        violations.append("fee_assumption_not_rendered")
    if action in (AdviceAction.BUY, AdviceAction.SELL) and (
        expected_return is None or abs(expected_return) < fee_assumption_pct
    ):
        violations.append("expected_move_below_stated_fee")
    return violations


@dataclass(frozen=True)
class Compliance:
    value: float
    n_rendered: int
    n_refused: int
    failures: list[tuple[str, list[str]]]


def rendered_compliance(result: PipelineResult) -> Compliance:
    """M5a — every advice the replay rendered, graded by the reference checker."""
    catalog = result.data.ctx.templates
    fee = result.data.ctx.settings.fee_assumption_pct
    failures: list[tuple[str, list[str]]] = []
    for advice in result.advice:
        violations = reference_frame_check(
            advice.rendered_text,
            action=advice.action,
            expected_return=advice.expected_return,
            catalog=catalog,
            fee_assumption_pct=fee,
        )
        if violations:
            failures.append((advice.id, violations))
    total = len(result.advice)
    return Compliance(
        value=(total - len(failures)) / total if total else 0.0,
        n_rendered=total,
        n_refused=len(result.render_failures),
        failures=failures[:10],
    )


# --------------------------------------------------------------------------- #
# M5b — the hand-authored two-directional frame cases                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FrameCaseOutcome:
    case_id: str
    expected: str
    actual: str
    detail: str

    @property
    def agrees(self) -> bool:
        return self.expected == self.actual


def load_frame_cases(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or (FIXTURES / "frame_cases.json")
    with target.open(encoding="utf-8") as handle:
        return list(json.load(handle)["cases"])


def evaluate_frame_cases(
    result: PipelineResult, cases: Sequence[Mapping[str, Any]]
) -> list[FrameCaseOutcome]:
    """M5b — run each hand-authored case through the *engine's* render + check.

    Must-render cases inject user-supplied strings (a garment label, an event
    note) that contain forbidden words; the engine must still render them,
    because the quoting rule exempts quoted user text. Must-block cases tamper
    with the generated text (footer, a required section, a template-emitted
    certainty claim) or with the decision itself (an expected move below the fee
    the advice quotes); the engine must refuse.
    """
    from grailtrader.engine.frame import FrameCheckError, check_frame, render_advice

    ctx = result.data.ctx
    base_decision, base_garment, events_by_id = _frame_case_fixture(result)
    outcomes: list[FrameCaseOutcome] = []
    for case in cases:
        garment = base_garment.model_copy(update={"label": case.get("garment_label", "a piece")})
        decision = base_decision
        events = events_by_id
        note = case.get("event_notes")
        if note is not None:
            events = {
                event_id: event.model_copy(update={"notes": note})
                for event_id, event in events_by_id.items()
            }
        if case.get("expected_return_override") is not None:
            decision = decision.model_copy(
                update={"expected_return": case["expected_return_override"]}
            )
        try:
            rendered = render_advice(decision, garment=garment, events_by_id=events, ctx=ctx)
            text = _mutate(rendered.text, case.get("mutate"))
            verdict = check_frame(
                text,
                action=decision.action,
                expected_return=decision.expected_return,
                catalog=ctx.templates,
                fee_assumption_pct=ctx.settings.fee_assumption_pct,
            )
            actual = "render" if verdict.ok else "block"
            detail = ", ".join(verdict.violations)
        except FrameCheckError as exc:  # pragma: no cover - render_advice does not check
            actual, detail = "block", str(exc)
        except (KeyError, ValueError) as exc:
            actual, detail = "block", f"render error: {exc}"
        outcomes.append(FrameCaseOutcome(str(case["id"]), str(case["expect"]), actual, detail))
    return outcomes


def _mutate(text: str, mutation: Mapping[str, Any] | None) -> str:
    if not mutation:
        return text
    kind = mutation["kind"]
    if kind == "replace":
        return text.replace(mutation["find"], mutation["with"])
    if kind == "append":
        return f"{text}\n\n{mutation['text']}"
    if kind == "insert_before_footer":
        blocks = text.split("\n\n")
        return "\n\n".join([*blocks[:-1], mutation["text"], blocks[-1]])
    if kind == "drop_line":
        needle = mutation["find"]
        kept = [line for line in text.splitlines() if needle not in line]
        return "\n".join(kept)
    raise ValueError(f"unknown frame-case mutation {kind!r}")


def _frame_case_fixture(
    result: PipelineResult,
) -> tuple[AdviceDecision, Garment, dict[str, Any]]:
    """A real buy decision from the replay, used as the base for every case."""
    events_by_id = {event.id: event for event in result.data.events}
    garments = {garment.id: garment for garment in result.data.garments}
    for decision in result.decisions:
        if decision.action is AdviceAction.BUY and decision.drivers:
            return decision, garments[decision.garment_id], events_by_id
    raise RuntimeError("the replay produced no buy advice to build frame cases from")


# --------------------------------------------------------------------------- #
# Assembly                                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    key: str
    value: float
    n: int | None
    gate: Gate | None
    note: str = ""

    @property
    def passing(self) -> bool:
        return self.gate is None or self.gate.passes(self.value)


@dataclass
class Scorecard:
    rows: list[Row] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    baselines: dict[str, Any] = field(default_factory=dict)

    def add(
        self, key: str, value: float, *, n: int | None = None, gated: bool = True, note: str = ""
    ) -> Row:
        row = Row(key, float(value), n, GATES[key] if gated else None, note)
        self.rows.append(row)
        return row

    @property
    def failures(self) -> list[Row]:
        return [row for row in self.rows if not row.passing]

    def by_key(self, key: str) -> Row:
        for row in self.rows:
            if row.key == key:
                return row
        raise KeyError(key)


def build_scorecard(
    scenario_a_result: PipelineResult, scenario_b_result: PipelineResult
) -> Scorecard:
    """Compute every EVALS metric for both scenarios."""
    card = Scorecard()
    real = scenario_a_result.real
    aggregates = real.aggregates

    # -- M0 floors ---------------------------------------------------------- #
    coverage = index_coverage(scenario_a_result)
    card.add("M0a", coverage.value, n=coverage.eligible)
    card.add("M0b", aggregates["n_actionable"], n=aggregates["n_candidates"])
    calib = calibration(real)
    card.add("M0c", calib.min_bucket_n, n=aggregates["n_candidates"])
    regimes = regime_counts(real)
    card.add("M0d-sell", regimes["sell_into_decay"], n=aggregates["n_actionable"])
    card.add("M0d-buy", regimes["phase_in_buy"], n=aggregates["n_actionable"])
    placebo = placebo_metrics(scenario_a_result)
    card.add("M0e", placebo.min_actionable, n=len(placebo.per_seed))

    naive_a = naive_index_error(scenario_a_result.data)
    baselines = aggregates["baselines"]
    card.add("M0f-m1a", naive_a)
    card.add("M0f-mom", baselines["theta_momentum"]["hit_rate"], n=baselines["theta_momentum"]["n"])
    card.add("M0f-evt", baselines["event_naive"]["hit_rate"], n=baselines["event_naive"]["n"])
    card.add("M0f-hold", abs(baselines["always_hold"]["spread"]), n=baselines["always_hold"]["n"])

    # -- M1 index fidelity --------------------------------------------------- #
    fidelity = index_fidelity(scenario_a_result)
    card.add("M1a", fidelity.median, n=fidelity.n_cells)
    card.add("M1a-p90", fidelity.p90, n=fidelity.n_cells)
    card.add("M1a-dense", fidelity.dense_max, n=fidelity.n_strata)
    card.add("M1a-sparse", fidelity.sparse_max, n=fidelity.n_strata)
    fence = fence_metrics(scenario_a_result)
    card.add("M1b-recall", fence.recall, n=fence.n_planted)
    card.add("M1b-false", fence.false_rate, n=fence.n_clean)

    # -- M2 skill ------------------------------------------------------------ #
    card.add("M2a", aggregates["hit_rate"], n=aggregates["n_actionable"])
    card.add("M2b", aggregates["spread_buy_minus_sell"], n=aggregates["n_actionable"])
    card.add("M2-excl", exclusion_rate(real), n=aggregates["n_actionable"])

    # -- M3 calibration ------------------------------------------------------ #
    card.add("M3a", calib.separation, n=aggregates["n_candidates"])
    card.add("M3b", calib.anti_overclaim, n=aggregates["n_candidates"])
    card.add("M3c", calib.hi_hit, n=calib.buckets["hi"]["n"])

    # -- M4 placebo ---------------------------------------------------------- #
    card.add("M4a", placebo.worst_hit_gap, n=placebo.min_actionable)
    card.add("M4b", placebo.worst_spread, n=placebo.min_actionable)

    # -- M5 framing ---------------------------------------------------------- #
    compliance = rendered_compliance(scenario_a_result)
    card.add("M5a", compliance.value, n=compliance.n_rendered)
    outcomes = evaluate_frame_cases(scenario_a_result, load_frame_cases())
    agree = sum(1 for outcome in outcomes if outcome.agrees)
    card.add("M5b", agree / len(outcomes) if outcomes else 0.0, n=len(outcomes))

    # -- M6 mismatch scenario ------------------------------------------------ #
    fidelity_b = index_fidelity(scenario_b_result)
    card.add("M6a", fidelity_b.median, n=fidelity_b.n_cells)
    card.add(
        "M6b",
        scenario_b_result.real.aggregates["hit_rate"],
        n=scenario_b_result.real.aggregates["n_actionable"],
    )
    calib_b = calibration(scenario_b_result.real, min_n=SCENARIO_B_BUCKET_MIN_N)
    card.add(
        "M6c",
        calib_b.anti_overclaim,
        n=scenario_b_result.real.aggregates["n_candidates"],
        note=f"buckets gated: {', '.join(calib_b.gated_buckets) or 'none'}",
    )

    # -- diagnostics and baselines ------------------------------------------- #
    card.diagnostics = {
        "D-fence": {
            "ambiguous_band_excluded": fence.ambiguous_excluded,
            "n_ambiguous": fence.n_ambiguous,
            "near_boundary_clean_excluded": fence.near_boundary_excluded,
            "n_near_boundary": fence.n_near_boundary,
        },
        "D-contra": contradiction_diagnostic(scenario_b_result),
        "buckets_a": calib.buckets,
        "buckets_b": calib_b.buckets,
        "spread_by_horizon": aggregates["spread_by_horizon"],
        "by_event_type": aggregates["by_event_type"],
        "excluded": aggregates["excluded"],
        "parent_index_max_median_error": fidelity.parent_max,
        "placebo_per_seed": placebo.per_seed,
        "render_refusals": compliance.n_refused,
        "frame_cases": [
            {"id": o.case_id, "expected": o.expected, "actual": o.actual, "detail": o.detail}
            for o in outcomes
        ],
        "scenario_b": {
            "n_candidates": scenario_b_result.real.aggregates["n_candidates"],
            "n_actionable": scenario_b_result.real.aggregates["n_actionable"],
            "spread": scenario_b_result.real.aggregates["spread_buy_minus_sell"],
        },
    }
    card.baselines = {
        "M1a naive weekly mean (A)": naive_a,
        "M1a naive weekly mean (B)": naive_index_error(scenario_b_result.data),
        "theta-momentum": baselines["theta_momentum"],
        "event-naive": baselines["event_naive"],
        "always-hold": baselines["always_hold"],
        "theta-momentum (B)": scenario_b_result.real.aggregates["baselines"]["theta_momentum"],
    }
    return card


def advice_population(decisions: Iterable[AdviceDecision]) -> dict[str, int]:
    """Small helper for the scorecard's context block."""
    counts: dict[str, int] = {}
    for decision in decisions:
        key = decision.action.value
        if decision.hold_reason is not None:
            key = f"hold:{decision.hold_reason.value}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def staleness_profile(result: PipelineResult) -> dict[str, int]:
    """How stale the advisory stratum was, per decision (context for q_index)."""
    counts: dict[str, int] = {}
    for decision in result.decisions:
        if decision.stratum_staleness_weeks is None:
            continue
        bucket = (
            "0"
            if decision.stratum_staleness_weeks == 0
            else ("1-2" if decision.stratum_staleness_weeks <= 2 else "3-8")
        )
        counts[bucket] = counts.get(bucket, 0) + 1
    return dict(sorted(counts.items()))


def advice_ages(advice: Sequence[Advice], weeks: Sequence[str]) -> int:
    """Number of distinct garment-weeks the replay rendered advice for."""
    return len({(row.garment_id, row.as_of_week) for row in advice}) if weeks else 0


def horizon_mix(replay: Replay) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in replay.results:
        if row.is_candidate:
            counts[row.horizon_weeks] = counts.get(row.horizon_weeks, 0) + 1
    return dict(sorted(counts.items()))


def weeks_of(replay: Replay) -> int:
    return len({row.week for row in replay.results})


def entry_invariant_holds(replay: Replay) -> bool:
    """FR-10: entry is always the week after the advice week (asserted in code too)."""
    return all(weeks_between(row.week, row.entry_week) == 1 for row in replay.results)

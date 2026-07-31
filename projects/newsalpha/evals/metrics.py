"""Metric implementations for the NewsAlpha eval suite (EVALS.md).

Every metric is a pure function of (committed fixtures, one pipeline run, the
committed market series).  Ground truth never comes from the system under
evaluation: article labels come from `fixtures/articles_truth.json` (written by
the generator's construction parameters) and market labels from
`fixtures/market_truth.json` (a planted-effect table scaled from the event-study
literature, authored independently of `data/priors.json`).

Two independence rules are load-bearing and implemented here rather than
described:

* the frame-check grader embeds its own `REFERENCE_FORBIDDEN_LEXICON` **as a
  literal** and never reads `data/patterns.json`, so weakening the shipped
  lexicon cannot weaken the grader (a companion gate asserts the shipped lexicon
  is a superset);
* the rank correlation used by M5/M7b is implemented here rather than imported
  from `engine.backtest`, so the metric and the harness cannot share a bug.

Everything runs offline: `FixtureNewsFeed`, `FixtureMarketData` and an in-memory
store only.  No network, no clock, no unseeded randomness.
"""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from newsalpha.adapters.marketdata_fixture import FixtureMarketData
from newsalpha.adapters.newsfeed_fixture import FixtureNewsFeed
from newsalpha.datasets import Datasets, load_datasets
from newsalpha.engine import backtest as bt
from newsalpha.engine import frame, pipeline
from newsalpha.engine.link import TICKER_CONFIDENCE, scan_mentions
from newsalpha.engine.revise import latest_revisions
from newsalpha.models import (
    Article,
    Asset,
    BacktestParams,
    BacktestResult,
    Brief,
    Cluster,
    Event,
    EventLink,
    EventType,
    Signal,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EVENT_TYPES: tuple[str, ...] = tuple(t.value for t in EventType)
PLACEBO_SEEDS: tuple[int, ...] = (20260731, 20260801, 20260802)

#: The grader's own copy of the forbidden lexicon (EVALS.md M8, "Lexicon
#: independence").  This literal must never be replaced by a read of
#: `data/patterns.json`: the gate `test_gate_m8_lexicon_superset_fr7` asserts the
#: shipped lexicon is a superset of it, which is what keeps the two independent at
#: the data level and not merely at the code-path level.
REFERENCE_FORBIDDEN_LEXICON: tuple[str, ...] = (
    "buy",
    "sell",
    "short it",
    "you should",
    "act now",
    "guaranteed",
    "can't lose",
    "cannot lose",
    "sure thing",
    "must buy",
    "must sell",
    "load up",
    "strong buy",
    "easy money",
    "to the moon",
    "we recommend",
    "trade this",
    "you must",
)

_ATTRIBUTED_QUOTE_RE = re.compile(
    r"[\"“]([^\"“”]*)[\"”]\s*(?:[-–—]{1,2}\s*|\(\s*)"
    r"[A-Za-z0-9][A-Za-z0-9._\- ]*"
)


def _reference_matchers() -> list[tuple[str, re.Pattern[str]]]:
    """Word-boundary matchers with the hyphen treated as a word character.

    "buyout", "buyer", "sell-off" and "sell-side" therefore pass while a bare
    imperative fails -- the same rule FR-7 states, written independently here.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for term in REFERENCE_FORBIDDEN_LEXICON:
        body = r"[\s\-]+".join(re.escape(part) for part in term.split())
        compiled.append((term, re.compile(rf"(?<![\w\-]){body}(?![\w\-])", re.IGNORECASE)))
    return compiled


def reference_frame_violations(
    *,
    sections: dict[str, str],
    what_to_watch: list[str] | tuple[str, ...],
    rendered_text: str,
    footer: str,
) -> list[str]:
    """The grader's independent FR-7 verdict: lexicon, sections, footer."""
    violations: list[str] = []
    for name, value in sections.items():
        if not value or not value.strip():
            violations.append(f"section {name} is empty")
    if not what_to_watch or any(not item.strip() for item in what_to_watch):
        violations.append("section what_to_watch is empty")
    if not footer.strip() or footer.strip() not in rendered_text:
        violations.append("not-advice footer missing or altered")
    exempt = [(m.start(1), m.end(1)) for m in _ATTRIBUTED_QUOTE_RE.finditer(rendered_text)]
    for term, matcher in _reference_matchers():
        for match in matcher.finditer(rendered_text):
            if any(lo <= match.start() and match.end() <= hi for lo, hi in exempt):
                continue
            violations.append(f"forbidden term {term!r} at offset {match.start()}")
    return violations


def naive_frame_violations(rendered_text: str) -> list[str]:
    """The M8 baseline: naive case-insensitive substring matching, no exemptions."""
    lowered = rendered_text.casefold()
    return [term for term in REFERENCE_FORBIDDEN_LEXICON if term in lowered]


# --------------------------------------------------------------------------- #
# Statistics (implemented here, never imported from the harness)
# --------------------------------------------------------------------------- #


def average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2 + 1
        for index in range(position, end + 1):
            ranks[order[index]] = average
        position = end + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation with average ranks for ties; 0.0 when degenerate."""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    rx, ry = average_ranks(xs), average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / math.sqrt(vx * vy)


def f1(tp: int, fp: int, fn: int) -> float:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------- #
# Fixtures & one pipeline run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Corpus:
    truth: dict[str, Any]
    market_truth: dict[str, Any]
    frame_cases: dict[str, Any]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.truth["events"]

    @property
    def as_of(self) -> str:
        return self.truth["as_of"]


@lru_cache(maxsize=1)
def corpus() -> Corpus:
    return Corpus(
        truth=json.loads((FIXTURES / "articles_truth.json").read_text(encoding="utf-8")),
        market_truth=json.loads((FIXTURES / "market_truth.json").read_text(encoding="utf-8")),
        frame_cases=json.loads((FIXTURES / "frame_cases.json").read_text(encoding="utf-8")),
    )


@lru_cache(maxsize=1)
def datasets() -> Datasets:
    return load_datasets()


@dataclass(frozen=True, slots=True)
class Prediction:
    """One full pipeline run over the committed corpus."""

    articles: tuple[Article, ...]
    clusters: tuple[Cluster, ...]
    events: tuple[Event, ...]
    links: tuple[EventLink, ...]
    signals: tuple[Signal, ...]
    briefs: tuple[Brief, ...]
    notes: dict[str, tuple[str, ...]]
    by_external: dict[str, Article] = field(default_factory=dict)

    def article_ids(self, cluster: Cluster) -> frozenset[str]:
        lookup = {article.id: article.external_id for article in self.articles}
        return frozenset(lookup[member] for member in cluster.article_ids)


@lru_cache(maxsize=1)
def prediction() -> Prediction:
    feed = FixtureNewsFeed(FIXTURES / "articles.jsonl")
    raws = feed.load()
    articles, result = pipeline.ingest(raws, datasets(), corpus().as_of)
    return Prediction(
        articles=tuple(articles),
        clusters=result.clusters,
        events=result.events,
        links=result.links,
        signals=result.new_signals,
        briefs=result.briefs,
        notes=dict(result.notes),
        by_external={article.external_id: article for article in articles},
    )


@lru_cache(maxsize=1)
def alignment() -> dict[str, Event]:
    """Truth event key -> the predicted event that aligns to it (EVALS M1).

    A predicted cluster `P` aligns to a truth cluster `T` iff `|P and T| > |P|/2`
    and `> |T|/2` (majority overlap, unique by construction); a predicted event
    matches iff its cluster aligns and the event type is equal.
    """
    pred = prediction()
    external = {article.id: article.external_id for article in pred.articles}
    members = {
        cluster.id: frozenset(external[a] for a in cluster.article_ids)
        for cluster in pred.clusters
    }
    out: dict[str, Event] = {}
    for truth_event in corpus().events:
        want = frozenset(truth_event["article_external_ids"])
        for event in pred.events:
            got = members[event.cluster_id]
            overlap = len(got & want)
            if overlap * 2 > len(got) and overlap * 2 > len(want):
                if event.event_type.value == truth_event["event_type"]:
                    out[truth_event["key"]] = event
    return out


@lru_cache(maxsize=1)
def predicted_families() -> dict[str, str]:
    """Predicted event id -> the family of the majority of its cluster's articles."""
    pred = prediction()
    families = corpus().truth["families"]
    external = {article.id: article.external_id for article in pred.articles}
    members = {
        cluster.id: [external[a] for a in cluster.article_ids] for cluster in pred.clusters
    }
    out: dict[str, str] = {}
    for event in pred.events:
        labels = [families[m] for m in members[event.cluster_id]]
        out[event.id] = "val" if labels.count("val") * 2 >= len(labels) else "dev"
    return out


def links_by_event() -> dict[str, list[EventLink]]:
    grouped: dict[str, list[EventLink]] = {}
    for link in prediction().links:
        grouped.setdefault(link.event_id, []).append(link)
    return grouped


# --------------------------------------------------------------------------- #
# M1 -- event typing, stage and attributes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TypingScore:
    macro_f1: float
    floor: float
    per_type: dict[str, float]
    tp: dict[str, int]
    fp: dict[str, int]
    fn: dict[str, int]


def m1a(family: str) -> TypingScore:
    """Macro-F1 over the 7 event types, on one paraphrase family."""
    aligned = alignment()
    families = predicted_families()
    tp = dict.fromkeys(EVENT_TYPES, 0)
    fp = dict.fromkeys(EVENT_TYPES, 0)
    fn = dict.fromkeys(EVENT_TYPES, 0)
    matched: set[str] = set()
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        hit = aligned.get(truth_event["key"])
        if hit is None:
            fn[truth_event["event_type"]] += 1
        else:
            tp[truth_event["event_type"]] += 1
            matched.add(hit.id)
    for event in prediction().events:
        if families[event.id] != family or event.id in matched:
            continue
        fp[event.event_type.value] += 1
    per_type = {t: f1(tp[t], fp[t], fn[t]) for t in EVENT_TYPES}
    return TypingScore(
        macro_f1=mean(list(per_type.values())),
        floor=min(per_type.values()),
        per_type=per_type,
        tp=tp,
        fp=fp,
        fn=fn,
    )


def _attributes_match(truth_event: dict[str, Any], event: Event) -> bool:
    if event.stage.value != truth_event["stage"]:
        return False
    return {k: v for k, v in event.attributes.items()} == dict(truth_event["attributes"])


def m1b_m1c(family: str = "val") -> tuple[float, float, list[str]]:
    """(conditional, unconditional) stage+attribute accuracy, plus the failing keys."""
    aligned = alignment()
    conditional_ok = conditional_n = unconditional_ok = unconditional_n = 0
    failures: list[str] = []
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        unconditional_n += 1
        event = aligned.get(truth_event["key"])
        if event is None:
            failures.append(f"{truth_event['key']}: not extracted")
            continue
        conditional_n += 1
        if _attributes_match(truth_event, event):
            conditional_ok += 1
            unconditional_ok += 1
        else:
            failures.append(
                f"{truth_event['key']}: stage/attributes "
                f"{event.stage.value}/{dict(event.attributes)} != "
                f"{truth_event['stage']}/{truth_event['attributes']}"
            )
    return (
        conditional_ok / conditional_n if conditional_n else 0.0,
        unconditional_ok / unconditional_n if unconditional_n else 0.0,
        failures,
    )


# --------------------------------------------------------------------------- #
# M2 -- asset linking
# --------------------------------------------------------------------------- #


def m2a(family: str = "val") -> tuple[float, dict[str, int]]:
    """F1 over predicted vs truth (event, asset) links; role ignored (roles are M3)."""
    aligned = alignment()
    grouped = links_by_event()
    tp = fp = fn = 0
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        want = {link["asset_id"] for link in truth_event["links"]}
        event = aligned.get(truth_event["key"])
        got = {link.asset_id for link in grouped.get(event.id, [])} if event else set()
        tp += len(want & got)
        fp += len(got - want)
        fn += len(want - got)
    return f1(tp, fp, fn), {"tp": tp, "fp": fp, "fn": fn}


@lru_cache(maxsize=1)
def _mentions_by_article() -> dict[str, list[Any]]:
    pred = prediction()
    return {
        article.external_id: scan_mentions(article, datasets()) for article in pred.articles
    }


def m2b() -> tuple[float, list[dict[str, Any]]]:
    """Trap accuracy: the correct link / no-link decision on each annotated mention."""
    mentions = _mentions_by_article()
    wrong: list[dict[str, Any]] = []
    correct = 0
    traps = corpus().truth["trap_mentions"]
    for trap in traps:
        overlapping = [
            m
            for m in mentions[trap["external_id"]]
            if m.start < trap["end"] and trap["start"] < m.end
        ]
        linked = {m.asset_id for m in overlapping}
        if trap["asset_id"] is None:
            ok = not linked
        else:
            ok = trap["asset_id"] in linked
        if ok:
            correct += 1
        else:
            wrong.append({**trap, "predicted": sorted(linked)})
    return correct / len(traps), wrong


# --------------------------------------------------------------------------- #
# M3 -- role assignment
# --------------------------------------------------------------------------- #


def m3() -> tuple[float, list[dict[str, Any]]]:
    aligned = alignment()
    grouped = links_by_event()
    signals_by_asset: dict[tuple[str, str], list[Signal]] = {}
    for signal in prediction().signals:
        signals_by_asset.setdefault((signal.event_id, signal.asset_id), []).append(signal)

    wrong: list[dict[str, Any]] = []
    correct = 0
    decisions = corpus().truth["role_decisions"]
    for decision in decisions:
        event = aligned.get(decision["event_key"])
        roles = (
            {link.asset_id: link.role.value for link in grouped.get(event.id, [])}
            if event
            else {}
        )
        if event is None:
            ok = False
        elif decision["kind"] == "mna_role":
            ok = roles.get(decision["asset_id"]) == decision["expected_role"]
        elif decision["kind"] == "abstention":
            ok = not any(role in ("acquirer", "target") for role in roles.values()) and not any(
                signals_by_asset.get((event.id, asset_id))
                for asset_id in decision["asset_ids"]
            )
        else:
            ok = roles.get(decision["subject_asset_id"]) == "subject"
            venue = decision["venue_asset_id"]
            if venue:
                ok = ok and roles.get(venue) == "venue"
                ok = ok and not signals_by_asset.get((event.id, venue))
        if ok:
            correct += 1
        else:
            wrong.append({**decision, "predicted_roles": roles})
    return correct / len(decisions), wrong


# --------------------------------------------------------------------------- #
# Backtests over the committed market seeds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SeedRun:
    seed: int
    results: tuple[BacktestResult, ...]
    n: int
    n_excluded: int
    excluded_by_reason: dict[str, int]
    hit_rate: float
    ic: float
    buckets: dict[str, tuple[int, float | None]]


@lru_cache(maxsize=1)
def scored_signals() -> tuple[Signal, ...]:
    return tuple(latest_revisions(list(prediction().signals)))


@lru_cache(maxsize=8)
def _bars_for_seed(seed: int) -> tuple[Any, ...]:
    market = FixtureMarketData(FIXTURES / "market" / f"seed_{seed}")
    wanted = sorted({s.asset_id for s in scored_signals()}) + ["idx:US", "idx:CX"]
    start, end = date(2000, 1, 1), date(2100, 1, 1)
    bars: list[Any] = []
    for asset_id in wanted:
        bars.extend(market.daily_bars(asset_id, start, end))
    return tuple(bars)


def _params(placebo_seed: int | None = None) -> BacktestParams:
    return BacktestParams(
        start="1970-01-01", end="2999-12-31", min_confidence=0.0, placebo_seed=placebo_seed
    )


@lru_cache(maxsize=32)
def run_seed(seed: int, placebo_seed: int | None = None) -> SeedRun:
    signals = list(scored_signals())
    run, results = bt.run_backtest(
        signals, list(_bars_for_seed(seed)), datasets(), _params(placebo_seed), corpus().as_of
    )
    by_id = {s.id: s for s in signals}
    included = [r for r in results if r.excluded_reason is None]
    scores = [by_id[r.signal_id].score for r in included]
    ars = [r.ar for r in included if r.ar is not None]
    aggregates = run.aggregates
    return SeedRun(
        seed=seed,
        results=tuple(results),
        n=len(included),
        n_excluded=len(results) - len(included),
        excluded_by_reason=dict(aggregates.excluded_by_reason),
        hit_rate=sum(1 for r in included if r.hit) / len(included) if included else 0.0,
        ic=spearman(scores, ars),
        buckets={
            name: (stats.n, stats.hit) for name, stats in sorted(aggregates.buckets.items())
        },
    )


def market_seeds() -> tuple[int, ...]:
    return tuple(corpus().market_truth["seeds"])


def m4() -> tuple[float, list[float]]:
    per_seed = [run_seed(seed).hit_rate for seed in market_seeds()]
    return mean(per_seed), per_seed


def m4_transfer() -> tuple[float, float, float]:
    """M4 restricted to DEV-family events minus M4 restricted to VAL-family events."""
    families = _signal_family()
    dev_rates: list[float] = []
    val_rates: list[float] = []
    for seed in market_seeds():
        run = run_seed(seed)
        for family, sink in (("dev", dev_rates), ("val", val_rates)):
            subset = [
                r
                for r in run.results
                if r.excluded_reason is None and families.get(r.signal_id) == family
            ]
            sink.append(sum(1 for r in subset if r.hit) / len(subset) if subset else 0.0)
    return mean(dev_rates) - mean(val_rates), mean(dev_rates), mean(val_rates)


@lru_cache(maxsize=1)
def _signal_family() -> dict[str, str]:
    families = predicted_families()
    return {signal.id: families.get(signal.event_id, "val") for signal in scored_signals()}


def m5() -> tuple[float, list[float]]:
    per_seed = [run_seed(seed).ic for seed in market_seeds()]
    return mean(per_seed), per_seed


def m6() -> tuple[float, list[float], dict[str, int]]:
    per_seed: list[float] = []
    occupancy: dict[str, int] = {}
    for seed in market_seeds():
        run = run_seed(seed)
        lo_n, lo_hit = run.buckets["lo"]
        hi_n, hi_hit = run.buckets["hi"]
        per_seed.append((hi_hit or 0.0) - (lo_hit or 0.0))
        for name, (count, _hit) in run.buckets.items():
            occupancy[name] = count
    return mean(per_seed), per_seed, occupancy


def m6_occupancy() -> int:
    _, _, occupancy = m6()
    return min(occupancy.values()) if occupancy else 0


def m7() -> tuple[float, float, list[float], list[float]]:
    hits: list[float] = []
    ics: list[float] = []
    for seed in market_seeds():
        for placebo in PLACEBO_SEEDS:
            run = run_seed(seed, placebo)
            hits.append(run.hit_rate)
            ics.append(run.ic)
    return abs(mean(hits) - 0.5), abs(mean(ics)), hits, ics


def announcement_capture_rate() -> tuple[float, int, int]:
    """Share of evaluated signals whose window contains a planted announcement bar.

    The generator plants the full announcement jump on the reaction bar, which
    FR-10's strictly-after entry can never reach.  Anything above zero is a
    look-ahead leak, so this is reported as a hard canary beside M7.
    """
    effects: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for effect in corpus().market_truth["effects"]:
        effects.setdefault((effect["seed"], effect["asset_id"]), []).append(effect)
    signals = {s.id: s for s in scored_signals()}
    captured = considered = 0
    for seed in market_seeds():
        for result in run_seed(seed).results:
            if result.entry_date is None or result.exit_date is None:
                continue
            considered += 1
            signal = signals[result.signal_id]
            for effect in effects.get((seed, signal.asset_id), []):
                if result.entry_date <= effect["announcement_date"] <= result.exit_date:
                    captured += 1
                    break
    return (captured / considered if considered else 0.0), captured, considered


def placebo_window_overlap() -> int:
    """Placebo windows that still overlap a planted event window (EVALS' contamination bound)."""
    effects: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for effect in corpus().market_truth["effects"]:
        effects.setdefault((effect["seed"], effect["asset_id"]), []).append(effect)
    signals = {s.id: s for s in scored_signals()}
    overlaps = 0
    for seed in market_seeds():
        for placebo in PLACEBO_SEEDS:
            for result in run_seed(seed, placebo).results:
                if result.entry_date is None or result.exit_date is None:
                    continue
                signal = signals[result.signal_id]
                for effect in effects.get((seed, signal.asset_id), []):
                    if effect["fizzled"]:
                        continue
                    if (
                        result.entry_date <= effect["exit_date"]
                        and effect["entry_date"] <= result.exit_date
                    ):
                        overlaps += 1
                        break
    return overlaps


# --------------------------------------------------------------------------- #
# G1 / G2 -- denominator integrity
# --------------------------------------------------------------------------- #


def g1() -> int:
    return len(scored_signals())


def g2() -> tuple[float, dict[str, int]]:
    run = run_seed(market_seeds()[0])
    considered = run.n + run.n_excluded
    return (run.n_excluded / considered if considered else 0.0), run.excluded_by_reason


def g2_placebo() -> tuple[float, dict[str, int]]:
    """The placebo exclusion rate, reported only: `placebo_no_clean_window` is expected there."""
    totals: dict[str, int] = {}
    excluded = considered = 0
    for seed in market_seeds():
        for placebo in PLACEBO_SEEDS:
            run = run_seed(seed, placebo)
            excluded += run.n_excluded
            considered += run.n + run.n_excluded
            for reason, count in run.excluded_by_reason.items():
                totals[reason] = totals.get(reason, 0) + count
    return (excluded / considered if considered else 0.0), dict(sorted(totals.items()))


def abstentions() -> tuple[int, int, dict[str, int]]:
    """(expected, other, by note). Expected = the 4 symmetric M&A + 3 denied acquirers."""
    counts: dict[str, int] = {}
    for notes in prediction().notes.values():
        for note in notes:
            counts[note] = counts.get(note, 0) + 1
    expected = counts.get("link:mna_role_unresolved", 0) + counts.get("score:unclear_abstain", 0)
    other = sum(value for key, value in counts.items() if key not in
                {"link:mna_role_unresolved", "score:unclear_abstain"})
    return expected, other, dict(sorted(counts.items()))


# --------------------------------------------------------------------------- #
# M8 -- framing compliance
# --------------------------------------------------------------------------- #


def _render_case(case: dict[str, Any], footer: str) -> str:
    watch = "\n".join(f"- {item}" for item in case["what_to_watch"])
    return (
        "Northwind (fixture) | frame case | bearish | moderate | confidence 0.50 | 5 bars\n\n"
        f"What happened\n{case['what_happened']}\n\n"
        f"Why it matters\n{case['why_it_matters']}\n\n"
        f"What to watch\n{watch}\n\n"
        f"Uncertainty\n{case['uncertainty_note']}\n\n"
        f"{footer}"
    )


@dataclass(frozen=True, slots=True)
class FrameOutcome:
    subject: str
    expected: str
    engine: str
    reference: str
    persisted: bool
    correct: bool


def m8() -> tuple[float, list[FrameOutcome]]:
    """Verdict-match accuracy over every rendered brief plus the 16 adversarial cases."""
    catalog = datasets().templates
    lexicon = datasets().lexicons.forbidden_lexicon
    outcomes: list[FrameOutcome] = []

    for brief in prediction().briefs:
        reference = reference_frame_violations(
            sections={
                "what_happened": brief.what_happened,
                "why_it_matters": brief.why_it_matters,
                "uncertainty_note": brief.uncertainty_note,
            },
            what_to_watch=list(brief.what_to_watch),
            rendered_text=brief.rendered_text,
            footer=catalog.footer,
        )
        verdict = "pass" if not reference else "violation"
        outcomes.append(
            FrameOutcome(
                subject=f"brief:{brief.signal_id}",
                expected="pass",
                engine="pass",
                reference=verdict,
                persisted=True,
                correct=verdict == "pass",
            )
        )

    for case in corpus().frame_cases["cases"]:
        footer = case.get("footer_override")
        footer = catalog.footer if footer is None else footer
        rendered = _render_case(case, footer)
        result = frame.check_brief(
            what_happened=case["what_happened"],
            why_it_matters=case["why_it_matters"],
            what_to_watch=case["what_to_watch"],
            uncertainty_note=case["uncertainty_note"],
            rendered_text=rendered,
            footer=catalog.footer,
            forbidden_lexicon=lexicon,
        )
        persisted = False
        engine_verdict = "pass"
        try:
            frame.enforce(result)
            Brief(
                signal_id="0" * 16,
                template_id="frame-case",
                what_happened=case["what_happened"],
                why_it_matters=case["why_it_matters"],
                what_to_watch=tuple(case["what_to_watch"]),
                uncertainty_note=case["uncertainty_note"],
                rendered_text=rendered,
                frame_checked=True,
            )
            persisted = True
        except (frame.FrameCheckError, ValueError):
            engine_verdict = "violation"
        reference = reference_frame_violations(
            sections={
                "what_happened": case["what_happened"],
                "why_it_matters": case["why_it_matters"],
                "uncertainty_note": case["uncertainty_note"],
            },
            what_to_watch=case["what_to_watch"],
            rendered_text=rendered,
            footer=catalog.footer,
        )
        reference_verdict = "violation" if reference else "pass"
        expected = case["expected"]
        if expected == "violation":
            correct = (
                engine_verdict == "violation"
                and not persisted
                and reference_verdict == "violation"
            )
        else:
            correct = engine_verdict == "pass" and persisted and reference_verdict == "pass"
        outcomes.append(
            FrameOutcome(
                subject=f"case:{case['id']}",
                expected=expected,
                engine=engine_verdict,
                reference=reference_verdict,
                persisted=persisted,
                correct=correct,
            )
        )
    return sum(1 for o in outcomes if o.correct) / len(outcomes), outcomes


def lexicon_is_superset() -> bool:
    shipped = {term.casefold() for term in datasets().lexicons.forbidden_lexicon}
    return all(term.casefold() in shipped for term in REFERENCE_FORBIDDEN_LEXICON)


# --------------------------------------------------------------------------- #
# Naive baselines (computed live -- EVALS.md's "Naive baselines and gates")
# --------------------------------------------------------------------------- #

NAIVE_KEYWORDS: dict[str, str] = {
    "earnings_surprise": "beat",
    "guidance_change": "guidance",
    "mna": "acquir",
    "regulatory_action": "approv",
    "listing": "list",
    "delisting": "delist",
    "hack_exploit": "hack",
}

NAIVE_POLARITY: dict[str, dict[str, Any]] = {
    "earnings_surprise": {"polarity": "beat"},
    "guidance_change": {"polarity": "raise"},
    "regulatory_action": {"polarity": "adverse"},
    "mna": {},
    "listing": {},
    "delisting": {},
    "hack_exploit": {},
}


def _naive_events() -> dict[str, list[str]]:
    """One keyword per type, per article, no suppressors and no clustering."""
    out: dict[str, list[str]] = {}
    for article in prediction().articles:
        text = article.analysis_text.casefold()
        out[article.external_id] = [
            event_type for event_type, needle in NAIVE_KEYWORDS.items() if needle in text
        ]
    return out


def naive_m1a(family: str = "val") -> tuple[float, float]:
    naive = _naive_events()
    families = corpus().truth["families"]
    tp = dict.fromkeys(EVENT_TYPES, 0)
    fp = dict.fromkeys(EVENT_TYPES, 0)
    fn = dict.fromkeys(EVENT_TYPES, 0)
    matched: set[tuple[str, str]] = set()
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        want = truth_event["article_external_ids"]
        hit = None
        for external_id in want:
            # A single-article prediction can only align to a single-article truth
            # cluster (majority overlap on both sides), so duplicates triple-count.
            if len(want) == 1 and truth_event["event_type"] in naive[external_id]:
                hit = external_id
        if hit is not None:
            tp[truth_event["event_type"]] += 1
            matched.add((hit, truth_event["event_type"]))
        else:
            fn[truth_event["event_type"]] += 1
    for external_id, types in naive.items():
        if families[external_id] != family:
            continue
        for event_type in types:
            if (external_id, event_type) not in matched:
                fp[event_type] += 1
    per_type = {t: f1(tp[t], fp[t], fn[t]) for t in EVENT_TYPES}
    return mean(list(per_type.values())), min(per_type.values())


def naive_m1b_m1c(family: str = "val") -> tuple[float, float]:
    naive = _naive_events()
    conditional_ok = conditional_n = unconditional_ok = unconditional_n = 0
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        unconditional_n += 1
        want = truth_event["article_external_ids"]
        typed = len(want) == 1 and truth_event["event_type"] in naive[want[0]]
        if not typed:
            continue
        conditional_n += 1
        ok = truth_event["stage"] == "confirmed" and dict(truth_event["attributes"]) == (
            NAIVE_POLARITY[truth_event["event_type"]]
        )
        conditional_ok += ok
        unconditional_ok += ok
    return (
        conditional_ok / conditional_n if conditional_n else 0.0,
        unconditional_ok / unconditional_n if unconditional_n else 0.0,
    )


@lru_cache(maxsize=1)
def _naive_surface_matchers() -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for asset in datasets().assets.values():
        if asset.kind.value == "index":
            continue
        for surface in {asset.name, asset.symbol, *asset.aliases}:
            parts = r"[\s\-]+".join(re.escape(p) for p in surface.split())
            compiled.append((asset.id, re.compile(rf"(?<![\w]){parts}(?![\w])", re.IGNORECASE)))
    return compiled


def naive_link_scan(text: str) -> set[str]:
    """The M2 baseline: every alias occurrence, any case, no context rules."""
    return {asset_id for asset_id, matcher in _naive_surface_matchers() if matcher.search(text)}


def naive_m2a(family: str = "val") -> float:
    pred = prediction()
    tp = fp = fn = 0
    for truth_event in corpus().events:
        if truth_event["family"] != family:
            continue
        want = {link["asset_id"] for link in truth_event["links"]}
        got: set[str] = set()
        for external_id in truth_event["article_external_ids"]:
            got |= naive_link_scan(pred.by_external[external_id].analysis_text)
        tp += len(want & got)
        fp += len(got - want)
        fn += len(want - got)
    return f1(tp, fp, fn)


def naive_m2b() -> float:
    """The naive matcher links every colliding surface, so it is wrong on every no-link trap."""
    traps = corpus().truth["trap_mentions"]
    correct = sum(1 for trap in traps if trap["asset_id"] == trap["collides_with"])
    return correct / len(traps)


def naive_m3() -> float:
    """"The first linked asset in the trigger sentence takes the signal-bearing role"."""
    pred = prediction()
    correct = 0
    decisions = corpus().truth["role_decisions"]
    truth_by_key = {event["key"]: event for event in corpus().events}
    for decision in decisions:
        truth_event = truth_by_key[decision["event_key"]]
        article = pred.by_external[truth_event["article_external_ids"][0]]
        order = _first_positions(article.analysis_text, [
            link["asset_id"] for link in truth_event["links"]
        ])
        if decision["kind"] == "mna_role":
            guessed = "acquirer" if order and order[0] == decision["asset_id"] else "target"
            correct += guessed == decision["expected_role"]
        elif decision["kind"] == "abstention":
            correct += 0  # the naive rule always assigns roles, so it never abstains
        else:
            guessed_subject = order[0] if order else None
            correct += guessed_subject == decision["subject_asset_id"]
    return correct / len(decisions)


def _first_positions(text: str, asset_ids: list[str]) -> list[str]:
    positions: list[tuple[int, str]] = []
    catalog = datasets().assets
    for asset_id in asset_ids:
        asset: Asset | None = catalog.get(asset_id)
        if asset is None:
            continue
        best: int | None = None
        for surface in {asset.name, asset.symbol, *asset.aliases}:
            parts = r"[\s\-]+".join(re.escape(p) for p in surface.split())
            match = re.search(rf"(?<![\w]){parts}(?![\w])", text, re.IGNORECASE)
            if match and (best is None or match.start() < best):
                best = match.start()
        if best is not None:
            positions.append((best, asset_id))
    return [asset_id for _, asset_id in sorted(positions)]


def naive_m4() -> float:
    """Always-bullish: every signal is called bullish, so a positive AR is a hit."""
    rates: list[float] = []
    for seed in market_seeds():
        included = [r for r in run_seed(seed).results if r.excluded_reason is None]
        rates.append(
            sum(1 for r in included if (r.ar or 0.0) > 0) / len(included) if included else 0.0
        )
    return mean(rates)


def naive_m5() -> float:
    """Shuffled scores: the same magnitudes, permuted, so any rank signal is destroyed."""
    signals = {s.id: s for s in scored_signals()}
    rates: list[float] = []
    for seed in market_seeds():
        included = [r for r in run_seed(seed).results if r.excluded_reason is None]
        scores = [signals[r.signal_id].score for r in included]
        rng = random.Random(f"naive-m5|{seed}")
        rng.shuffle(scores)
        rates.append(spearman(scores, [r.ar or 0.0 for r in included]))
    return mean(rates)


def naive_m6() -> tuple[float, dict[str, int]]:
    """Confidence = extraction confidence only, with no stage/tier/corroboration modifiers.

    This is the degenerate confidence function M6's occupancy sub-gate exists for:
    extraction confidence only ever takes three values, all at or above the 0.70
    bucket edge, so the `lo` and `mid` buckets are empty and the separation is not
    merely small -- it is undefined, which the sub-gate must treat as a failure
    rather than a vacuous pass.
    """
    signals = {s.id: s for s in scored_signals()}
    naive_confidence = {
        signal.id: signal.event_snapshot.extraction_confidence for signal in signals.values()
    }
    separations: list[float] = []
    occupancy: dict[str, int] = {"lo": 0, "mid": 0, "hi": 0}
    for seed in market_seeds():
        included = [r for r in run_seed(seed).results if r.excluded_reason is None]
        buckets: dict[str, list[bool]] = {"lo": [], "mid": [], "hi": []}
        for result in included:
            buckets[bt.bucket_of(naive_confidence[result.signal_id])].append(bool(result.hit))
        for name, values in buckets.items():
            occupancy[name] = len(values)
        if buckets["lo"] and buckets["hi"]:
            separations.append(
                sum(buckets["hi"]) / len(buckets["hi"]) - sum(buckets["lo"]) / len(buckets["lo"])
            )
        else:
            separations.append(0.0)
    return mean(separations), occupancy


def naive_m7() -> tuple[float, float]:
    """A leaky placebo: the displacement is announced but the window never moves.

    This is the failure mode M7 exists to catch -- a harness whose "placebo" still
    measures the real event window shows the real skill, so `|mean hit - 0.5|` and
    `|mean IC|` stay far above the gates instead of collapsing to noise.
    """
    hits = [run_seed(seed).hit_rate for seed in market_seeds() for _ in PLACEBO_SEEDS]
    ics = [run_seed(seed).ic for seed in market_seeds() for _ in PLACEBO_SEEDS]
    return abs(mean(hits) - 0.5), abs(mean(ics))


def naive_m8() -> float:
    """Templated briefs with no frame check and naive substring matching."""
    catalog = datasets().templates
    correct = total = 0
    for brief in prediction().briefs:
        total += 1
        correct += not naive_frame_violations(brief.rendered_text)
    for case in corpus().frame_cases["cases"]:
        total += 1
        footer = case.get("footer_override")
        footer = catalog.footer if footer is None else footer
        rendered = _render_case(case, footer)
        flagged = bool(naive_frame_violations(rendered))
        # No frame check runs, so nothing is ever blocked: the observed verdict is
        # always "pass", and the naive substring matcher additionally mislabels any
        # brief containing "buyout" / "sell-off".
        observed = "violation" if flagged else "pass"
        correct += observed == case["expected"] and case["expected"] == "pass" and not flagged
    return correct / total


def naive_g1() -> int:
    """The abstention dodge: score only the cases where nothing was hard.

    Concretely: a confirmed stage (no rumor or denial to interpret), a plain
    `subject` role (no acquirer/target resolution to get wrong) and a link from a
    strong evidence class (>= the plain-ticker 0.85).  An implementation that
    resolves everything else `unclear` emits no signal and therefore leaves no
    exclusion row behind, which is exactly why G1 gates the count directly.
    """
    return sum(
        1
        for signal in scored_signals()
        if signal.event_snapshot.stage.value == "confirmed"
        and signal.role.value == "subject"
        and signal.event_snapshot.link_confidence >= TICKER_CONFIDENCE
    )


def naive_g2() -> float:
    """Drop what you cannot price: exclude every signal below the lo/mid bucket edge."""
    run = run_seed(market_seeds()[0])
    signals = {s.id: s for s in scored_signals()}
    considered = run.n + run.n_excluded
    dropped = run.n_excluded + sum(
        1
        for r in run.results
        if r.excluded_reason is None and signals[r.signal_id].confidence < bt.BUCKET_EDGES[0]
    )
    return dropped / considered if considered else 0.0


__all__ = [
    "PLACEBO_SEEDS",
    "REFERENCE_FORBIDDEN_LEXICON",
    "Corpus",
    "FrameOutcome",
    "Prediction",
    "SeedRun",
    "abstentions",
    "alignment",
    "announcement_capture_rate",
    "corpus",
    "datasets",
    "f1",
    "g1",
    "g2",
    "g2_placebo",
    "lexicon_is_superset",
    "m1a",
    "m1b_m1c",
    "m2a",
    "m2b",
    "m3",
    "m4",
    "m4_transfer",
    "m5",
    "m6",
    "m6_occupancy",
    "m7",
    "m8",
    "market_seeds",
    "mean",
    "naive_g1",
    "naive_g2",
    "naive_m1a",
    "naive_m1b_m1c",
    "naive_m2a",
    "naive_m2b",
    "naive_m3",
    "naive_m4",
    "naive_m5",
    "naive_m6",
    "naive_m7",
    "naive_m8",
    "placebo_window_overlap",
    "prediction",
    "reference_frame_violations",
    "run_seed",
    "scored_signals",
    "spearman",
]

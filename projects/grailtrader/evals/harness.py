"""Hermetic pipeline runner shared by ``evals/run.py`` and ``evals/test_gates.py``.

Loads a committed scenario with the **offline adapters and the in-memory store
only** (EVALS "How the suite runs"), builds the indices, replays the FR-10
backtest against the planted truth index, and renders every advice the replay
decided so the FR-9 compliance metric has a real population to score.

No network, no wall clock: every timestamp comes from the fixture, and the only
randomness is the three committed placebo seeds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from grailtrader.adapters import FixtureListingsFeed, FixtureNewsFeed, FixtureSocialFeed
from grailtrader.datasets import load_conditions, load_config, load_priors, load_templates
from grailtrader.engine.advisor import advise_garment, build_advice
from grailtrader.engine.backtest import ReferenceIndex, run_backtest
from grailtrader.engine.context import EngineContext
from grailtrader.engine.events import ingest_events
from grailtrader.engine.index import IndexView, build_index
from grailtrader.engine.ingest import normalize_listings
from grailtrader.engine.validate import validate_datasets
from grailtrader.models import (
    Advice,
    AdviceDecision,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    Brand,
    FashionEvent,
    Garment,
    IngestReport,
    Listing,
)
from grailtrader.store import InMemoryRepository

FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The three committed placebo seeds (EVALS M4); the gate is the worst of them.
PLACEBO_SEEDS: tuple[int, ...] = (20260731, 20260801, 20260802)
#: The replay starts here so every stratum has index history behind it (EVALS M2).
REPLAY_START_WEEK_INDEX = 12


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fixture_context(fixture_dir: Path) -> EngineContext:
    """The engine context for a scenario: fixture gazetteer, committed everything else.

    The priors, conditions, templates and config are the *shipped* ones — the
    scenario is graded against the product's real data, not a tuned copy.
    """
    brands = tuple(
        Brand.model_validate(row)
        for row in _read_json(fixture_dir / "brands_fixture.json")["brands"]
    )
    priors = load_priors()
    conditions = load_conditions()
    templates = load_templates()
    config = load_config()
    validate_datasets(
        brands=brands, priors=priors, conditions=conditions, templates=templates, config=config
    )
    return EngineContext.build(
        brands=brands, priors=priors, conditions=conditions, templates=templates, config=config
    )


@dataclass
class ScenarioData:
    """A loaded scenario: fixtures plus everything ingested through the real path."""

    name: str
    ctx: EngineContext
    repo: InMemoryRepository
    listings: list[Listing]
    ingest_report: IngestReport
    events: list[FashionEvent]
    garments: list[Garment]
    weeks: list[str]
    index_truth: dict[str, Any]
    coverage_truth: dict[str, Any]
    listings_truth: dict[str, Any]
    impact_truth: dict[str, Any]

    @property
    def start_week(self) -> str:
        return self.weeks[REPLAY_START_WEEK_INDEX]

    @property
    def end_week(self) -> str:
        return self.weeks[-1]

    @property
    def as_of(self) -> str:
        return f"{self.end_week}T23:59:59Z"

    def truth_series(self) -> dict[str, dict[str, float]]:
        """``I_ref`` for eval runs: planted leaf levels and chain-linked parent truth."""
        series: dict[str, dict[str, float]] = {}
        for stratum, values in self.index_truth["leaf_level_usd"].items():
            series[stratum] = dict(zip(self.weeks, values, strict=True))
        for stratum, values in self.index_truth["parent_index"].items():
            series[stratum] = dict(zip(self.weeks, values, strict=True))
        return series


def load_scenario(name: str, *, root: Path = FIXTURES) -> ScenarioData:
    """Ingest a committed scenario exactly as the product would (FR-2/FR-5)."""
    fixture_dir = root / name
    ctx = fixture_context(fixture_dir)
    repo = InMemoryRepository()
    repo.initialize(reset=True)
    repo.replace_gazetteer(list(ctx.gazetteer.brands))

    raw = FixtureListingsFeed(fixture_dir / "listings.jsonl").fetch()
    listings, report = normalize_listings(raw, gazetteer=ctx.gazetteer, mapper=ctx.mapper)
    repo.add_listings(listings)

    # Both offline event feeds read the committed typed events; the second pass is
    # a no-op that exercises the FR-5 dedup path (and the four twice-reported
    # events resolve to one row each with corroboration 2).
    events_path = fixture_dir / "events.jsonl"
    news = FixtureNewsFeed(events_path).fetch("1970-01-01", "2999-12-31")
    social = FixtureSocialFeed(events_path).fetch("1970-01-01", "2999-12-31")
    merged, _ = ingest_events([], news)
    merged, _ = ingest_events(merged.values(), social)
    repo.upsert_events(merged.values())

    garments = [
        Garment.model_validate(row)
        for row in _read_json(fixture_dir / "eval_portfolio.json")["garments"]
    ]
    for garment in garments:
        repo.add_garment(garment)

    index_truth = _read_json(fixture_dir / "index_truth.json")
    return ScenarioData(
        name=name,
        ctx=ctx,
        repo=repo,
        listings=repo.list_listings(),
        ingest_report=report,
        events=repo.list_events(),
        garments=garments,
        weeks=list(index_truth["weeks"]),
        index_truth=index_truth,
        coverage_truth=_read_json(fixture_dir / "coverage_truth.json"),
        listings_truth=_read_json(fixture_dir / "listings_truth.json"),
        impact_truth=_read_json(fixture_dir / "impact_truth.json"),
    )


@dataclass
class Replay:
    """One backtest run plus its per-decision results."""

    run: BacktestRun
    results: list[BacktestResult]

    @property
    def aggregates(self) -> dict[str, Any]:
        return self.run.aggregates


@dataclass
class PipelineResult:
    data: ScenarioData
    index: IndexView
    real: Replay
    placebo: dict[int, Replay]
    decisions: list[AdviceDecision]
    advice: list[Advice]
    render_failures: list[tuple[str, str]]


def run_pipeline(data: ScenarioData, *, placebo_seeds: tuple[int, ...] = ()) -> PipelineResult:
    """Build the index, replay the backtest, and render every decided advice."""
    index = build_index(
        data.listings,
        mapper=data.ctx.mapper,
        config=data.ctx.index_config,
        as_of=data.as_of,
        built_as_of=data.as_of,
    )
    reference = ReferenceIndex.from_truth(data.truth_series())
    params = BacktestParams(
        start_week=data.start_week,
        end_week=data.end_week,
        placebo_seed=None,
        reference="truth",
        scenario=data.name,
    )
    run, results = run_backtest(
        params=params,
        garments=data.garments,
        events=data.events,
        index=index,
        reference=reference,
        ctx=data.ctx,
        as_of=data.as_of,
    )
    real = Replay(run, results)

    placebo: dict[int, Replay] = {}
    for seed in placebo_seeds:
        seeded = params.model_copy(update={"placebo_seed": seed})
        p_run, p_results = run_backtest(
            params=seeded,
            garments=data.garments,
            events=data.events,
            index=index,
            reference=reference,
            ctx=data.ctx,
            as_of=data.as_of,
        )
        placebo[seed] = Replay(p_run, p_results)

    decisions, advice, failures = _render_replay(data, index)
    return PipelineResult(
        data=data,
        index=index,
        real=real,
        placebo=placebo,
        decisions=decisions,
        advice=advice,
        render_failures=failures,
    )


def _render_replay(
    data: ScenarioData, index: IndexView
) -> tuple[list[AdviceDecision], list[Advice], list[tuple[str, str]]]:
    """Re-run the advisor over the replay weeks and render each decision (FR-9 / M5a).

    ``run_backtest`` returns decisions but not renderings; the advisor is a pure
    function of the same inputs, so replaying it here reproduces exactly the
    advice the backtest decided, and every rendering goes through the real
    ``build_advice`` (render + frame check + package). A rendering that the engine
    *refuses* is recorded rather than swallowed — over-blocking is a compliance
    failure too (EVALS M5b).
    """
    events_by_id = {event.id: event for event in data.events}
    from grailtrader.weeks import week_range

    decisions: list[AdviceDecision] = []
    advice: list[Advice] = []
    failures: list[tuple[str, str]] = []
    for week in week_range(data.start_week, data.end_week):
        sliced = index.restrict(week)
        visible = [event for event in data.events if event.week <= week]
        for garment in data.garments:
            decision = advise_garment(
                garment, as_of_week=week, index=sliced, events=visible, ctx=data.ctx
            )
            decisions.append(decision)
            try:
                advice.append(
                    build_advice(
                        decision,
                        garment=garment,
                        events_by_id=events_by_id,
                        ctx=data.ctx,
                        created_as_of=f"{week}T00:00:00Z",
                    )
                )
            except ValueError as exc:  # FrameCheckError is a ValueError
                failures.append((f"{garment.id}@{week}", str(exc)))
    return decisions, advice, failures


@lru_cache(maxsize=4)
def scenario_a() -> PipelineResult:
    """Scenario A with the three committed placebo seeds (cached per process)."""
    return run_pipeline(load_scenario("scenario_a"), placebo_seeds=PLACEBO_SEEDS)


@lru_cache(maxsize=4)
def scenario_b() -> PipelineResult:
    """Scenario B, the mismatch scenario (no placebo run, per EVALS)."""
    return run_pipeline(load_scenario("scenario_b"))

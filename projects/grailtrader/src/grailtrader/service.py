"""The application service the API and the CLI both call.

Everything here is orchestration: load committed data, ask the engine, persist
through the repository. No business rule lives in this module — thresholds,
formulas and framing all sit in ``engine/`` and ``data/`` (FR-12/FR-13).

The one thing the edges are allowed to do that the engine is not is read a clock,
and even that is injected (``clock``) so tests and evals stay deterministic.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .datasets import build_context
from .engine.advisor import advise_garment, build_advice
from .engine.backtest import ReferenceIndex, run_backtest
from .engine.context import EngineContext
from .engine.events import corroboration_of, ingest_events, make_event, target_strata
from .engine.impact import legs_for_event, retirement_age
from .engine.index import IndexView, build_index
from .engine.ingest import normalize_listings
from .engine.strata import UnknownReferenceError, ancestors, is_prefix
from .engine.valuation import value_garment
from .ids import garment_id
from .models import (
    Advice,
    AdviceAction,
    AdviceDecision,
    BacktestParams,
    BacktestResult,
    BacktestRun,
    Brand,
    Category,
    ConditionGrade,
    EventIngestReport,
    EventSource,
    EventStatus,
    EventType,
    FashionEvent,
    Garment,
    GarmentStatus,
    IndexPoint,
    IngestReport,
    Listing,
    ListingSource,
    ListingStatus,
    ValuationResult,
)
from .store import InMemoryRepository, Repository, SQLiteRepository
from .weeks import week_key

__all__ = [
    "EventDetail",
    "GrailTraderService",
    "IndexBuildReport",
    "InitReport",
    "NotFoundError",
    "PreconditionError",
    "open_repository",
]


class NotFoundError(LookupError):
    """A requested entity does not exist."""

    def __init__(self, kind: str, identifier: str) -> None:
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier}")


class PreconditionError(RuntimeError):
    """A pipeline step was requested before the step it depends on (e.g. no index yet)."""


@dataclass(frozen=True)
class InitReport:
    brands: int
    eras: int
    priors: int
    templates: int
    config_version: str
    reset: bool


@dataclass(frozen=True)
class IndexBuildReport:
    as_of: str
    strata_built: int
    points_written: int
    excluded_total: int
    leaf_strata: int


@dataclass(frozen=True)
class EventDetail:
    """``events show`` / ``GET /events/{id}``: the event plus everything FR-5/FR-6 derive."""

    event: FashionEvent
    targets: tuple[dict[str, str], ...]
    priors: tuple[dict[str, Any], ...]
    retirement_age_weeks: float
    source_refs: tuple[str, ...]
    corroboration: int


def open_repository(db_path: str | os.PathLike[str] | None = None) -> Repository:
    """The default backend: SQLite at ``GRAILTRADER_DB`` or ``~/.grailtrader``."""
    if db_path is not None and str(db_path) == ":memory:":
        return InMemoryRepository()
    return SQLiteRepository(db_path)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GrailTraderService:
    """Stateless-ish façade over the repository and the engine."""

    def __init__(
        self,
        repo: Repository,
        ctx: EngineContext | None = None,
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self.repo = repo
        self.ctx = ctx or build_context()
        self.clock = clock
        self._index_cache: tuple[str, int, IndexView] | None = None

    # -- FR-1 init ----------------------------------------------------------- #

    def initialize(self, *, reset: bool = False) -> InitReport:
        """Validate the committed datasets and materialise the gazetteer (FR-1)."""
        self.ctx = build_context()
        self.repo.initialize(reset=reset)
        brands = list(self.ctx.gazetteer.brands)
        self.repo.replace_gazetteer(brands)
        return InitReport(
            brands=len(brands),
            eras=sum(len(brand.eras) for brand in brands),
            priors=len(self.ctx.priors),
            templates=len(self.ctx.templates.templates),
            config_version=self.ctx.config.config_version,
            reset=reset,
        )

    # -- FR-2 listings -------------------------------------------------------- #

    def load_listings(
        self, *, source: ListingSource | str = ListingSource.FIXTURE, path: str | os.PathLike[str]
    ) -> IngestReport:
        """Ingest a feed of raw listings (FR-2). Idempotent: re-ingesting changes nothing."""
        kind = ListingSource(source)
        if kind is ListingSource.FIXTURE:
            from .adapters.listings_fixture import FixtureListingsFeed

            feed: Any = FixtureListingsFeed(path)
        else:
            from .adapters.listings_csv import CsvListingsFeed

            feed = CsvListingsFeed(path)
        listings, report = normalize_listings(
            feed.fetch(),
            gazetteer=self.ctx.gazetteer,
            mapper=self.ctx.mapper,
            known_ids=self.repo.listing_ids(),
        )
        self.repo.add_listings(listings)
        self._index_cache = None
        return report

    def list_listings(
        self,
        *,
        stratum: str | None = None,
        status: ListingStatus | None = None,
        limit: int | None = None,
    ) -> list[Listing]:
        if stratum:
            self.ctx.gazetteer.validate_stratum(stratum)
        return self.repo.list_listings(stratum=stratum, status=status, limit=limit)

    def get_listing(self, listing_id: str) -> Listing:
        listing = self.repo.get_listing(listing_id)
        if listing is None:
            raise NotFoundError("listing", listing_id)
        return listing

    # -- FR-4 index ----------------------------------------------------------- #

    def build_index(self, *, as_of: str | None = None) -> IndexBuildReport:
        """Rebuild every index series and replace the derived rows (FR-4)."""
        listings = self.repo.list_listings()
        if not listings:
            raise PreconditionError("no listings loaded: run `listings load` first")
        stamp = self._stamp(as_of) if as_of else self._latest_listing_stamp(listings)
        view = build_index(
            listings,
            mapper=self.ctx.mapper,
            config=self.ctx.index_config,
            as_of=stamp,
            built_as_of=stamp,
        )
        points = view.all_points()
        self.repo.replace_index_points(points)
        self._index_cache = (stamp, len(listings), view)
        return IndexBuildReport(
            as_of=stamp,
            strata_built=len(view.strata),
            points_written=len(points),
            excluded_total=sum(point.n_excluded for point in points),
            leaf_strata=len(view.leaf_strata),
        )

    def index_view(self) -> IndexView:
        """The current index as an ``IndexView``, rebuilt from listings at the stored stamp.

        Rebuilding (rather than re-hydrating persisted rows) keeps the trailing
        sale weights and the fence's exclusion lists available, and it is exact:
        FR-4's build is a pure function of (listings, as_of, config), and it is
        pinned to the ``built_as_of`` of the last ``index build`` so that FR-8's
        ``inputs_hash`` — and therefore advice identity — does not drift.
        """
        stored = self.repo.list_index_points()
        if not stored:
            raise PreconditionError("no index points: run `index build` first")
        stamp = max(point.built_as_of for point in stored)
        listings = self.repo.list_listings()
        if (
            self._index_cache is not None
            and self._index_cache[0] == stamp
            and self._index_cache[1] == len(listings)
        ):
            return self._index_cache[2]
        view = build_index(
            listings,
            mapper=self.ctx.mapper,
            config=self.ctx.index_config,
            as_of=stamp,
            built_as_of=stamp,
        )
        self._index_cache = (stamp, len(listings), view)
        return view

    def index_points(
        self,
        stratum_id: str,
        *,
        from_week: str | None = None,
        to_week: str | None = None,
        weeks: int | None = None,
    ) -> list[IndexPoint]:
        self.ctx.gazetteer.validate_stratum(stratum_id)
        rows = self.repo.list_index_points(
            stratum_id=stratum_id,
            from_week=week_key(from_week) if from_week else None,
            to_week=week_key(to_week) if to_week else None,
        )
        if not rows:
            raise NotFoundError("index series", stratum_id)
        return rows[-weeks:] if weeks else rows

    def excluded_listings(self, stratum_id: str, week: str) -> list[Listing]:
        """The listings the FR-3 fence removed from one window (``index show --excluded``)."""
        view = self.index_view()
        ids = view.excluded_listing_ids(stratum_id, week_key(week))
        return [listing for listing in (self.repo.get_listing(i) for i in ids) if listing]

    def strata(self, *, level: str | None = None, brand: str | None = None) -> list[str]:
        """Every stratum the index knows, optionally filtered by depth and brand."""
        depths = {"brand": 1, "era": 2, "leaf": 3}
        try:
            view = self.index_view()
            paths = list(view.strata)
        except PreconditionError:
            paths = []
        if brand:
            self.ctx.gazetteer.brand(brand)
            paths = [path for path in paths if is_prefix(brand, path)]
        if level:
            if level not in depths:
                raise ValueError(f"level must be one of {sorted(depths)}, got {level!r}")
            paths = [path for path in paths if len(path.split("/")) == depths[level]]
        return sorted(paths)

    # -- FR-5 events ---------------------------------------------------------- #

    def ingest_event_feed(
        self,
        *,
        source: str = "fixture",
        path: str | os.PathLike[str] | None = None,
        since: str = "1970-01-01",
        until: str = "2999-12-31",
        social: bool = False,
    ) -> EventIngestReport:
        """Ingest typed events from a feed (FR-5). RSS candidates arrive as ``pending``."""
        if source == "fixture":
            if path is None:
                raise ValueError("the fixture event feed needs --path")
            if social:
                from .adapters.social_fixture import FixtureSocialFeed

                feed: Any = FixtureSocialFeed(path)
            else:
                from .adapters.news_fixture import FixtureNewsFeed

                feed = FixtureNewsFeed(path)
        elif source == "rss":
            from .adapters.news_rss import RssNewsFeed

            feed = RssNewsFeed(gazetteer=self.ctx.gazetteer)
        else:
            raise ValueError(f"unknown event source {source!r}; use 'fixture' or 'rss'")
        incoming = feed.fetch(since, until)
        merged, report = ingest_events(self.repo.list_events(), incoming)
        self.repo.upsert_events(merged.values())
        return report

    def add_event(
        self,
        *,
        event_type: EventType | str,
        brand: str,
        occurred_on: str,
        era: str | None = None,
        source: EventSource | str = EventSource.MANUAL,
        source_ref: str | None = None,
        attributes: dict[str, Any] | None = None,
        notes: str = "",
    ) -> tuple[FashionEvent, bool]:
        """Manual typed entry (FR-5). Returns ``(event, created)``; a repeat corroborates."""
        kind = EventType(event_type)
        brand_id = self.ctx.gazetteer.resolve_brand(brand)
        era_id = self.ctx.gazetteer.resolve_era(brand_id, era) if era else None
        origin = EventSource(source)
        ref = source_ref or f"{origin.value}:{brand_id}-{week_key(occurred_on)}"
        if origin is EventSource.SOCIAL:
            # SCOPE's adapter table: manual social entry is a seam, not a scraper.
            from .adapters.social_manual import ManualSocialEntry

            event = ManualSocialEntry(self.ctx.gazetteer).add(
                event_type=kind,
                brand_ref=brand_id,
                occurred_on=occurred_on,
                attributes=attributes or {},
                era_id=era_id,
                notes=notes,
                source_refs=(ref,),
            )
        else:
            event = make_event(
                event_type=kind,
                brand_id=brand_id,
                occurred_on=occurred_on,
                source=origin,
                source_refs=(ref,),
                status=EventStatus.CONFIRMED,
                era_id=era_id,
                attributes=attributes or {},
                notes=notes,
            )
        existing = self.repo.get_event(event.id)
        merged, _ = ingest_events(self.repo.list_events(), [event])
        self.repo.upsert_events(merged.values())
        return merged[event.id], existing is None

    def set_event_status(self, event_id: str, status: EventStatus | str) -> FashionEvent:
        """``events review --confirm/--reject`` (FR-5): the only permitted transition."""
        from .engine.events import transition_status

        event = self.get_event(event_id)
        updated = transition_status(event, EventStatus(status))
        self.repo.upsert_events([updated])
        return updated

    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        brand: str | None = None,
        status: EventStatus | None = None,
        since: str | None = None,
    ) -> list[FashionEvent]:
        brand_id = self.ctx.gazetteer.resolve_brand(brand) if brand else None
        return self.repo.list_events(
            event_type=event_type, brand_id=brand_id, status=status, since=since
        )

    def get_event(self, event_id: str) -> FashionEvent:
        event = self.repo.get_event(event_id)
        if event is None:
            raise NotFoundError("event", event_id)
        return event

    def event_detail(self, event_id: str) -> EventDetail:
        """Resolved scopes, the prior rows applied, corroboration and ``A_e`` (FR-5/FR-6)."""
        event = self.get_event(event_id)
        targets = target_strata(event, self.ctx.gazetteer)
        legs = legs_for_event(event, self.ctx.gazetteer, self.ctx.priors)
        priors = [
            {
                "key": leg.prior_key,
                "target_stratum": leg.target.stratum,
                "direction": leg.direction.value,
                "permanent_pct": leg.permanent,
                "transient_pct": leg.transient,
                "half_life_weeks": leg.half_life,
                "base_conf": leg.base_conf,
                "rationale": leg.prior.rationale,
                "source_note": leg.prior.source_note,
                "retirement_age_weeks": retirement_age(leg, self.ctx.settings),
            }
            for leg in legs
        ]
        return EventDetail(
            event=event,
            targets=tuple(
                {"kind": target.kind.value, "stratum": target.stratum} for target in targets
            ),
            priors=tuple(priors),
            retirement_age_weeks=max((row["retirement_age_weeks"] for row in priors), default=0.0),
            source_refs=event.source_refs,
            corroboration=corroboration_of(event.source_refs),
        )

    # -- FR-11 portfolio ------------------------------------------------------ #

    def add_garment(
        self,
        *,
        label: str,
        brand: str,
        era: str,
        category: Category | str,
        condition: ConditionGrade | str,
        status: GarmentStatus | str = GarmentStatus.OWNED,
        price: float,
        date: str,
        size: str | None = None,
        notes: str = "",
        added_at: str | None = None,
    ) -> Garment:
        """Add a garment (FR-11). ``price``/``date`` are the anchor pair for its status."""
        brand_id = self.ctx.gazetteer.resolve_brand(brand)
        era_id = self.ctx.gazetteer.resolve_era(brand_id, era)
        try:
            kind = Category(category)
        except ValueError:
            raise UnknownReferenceError(
                "category", str(category), self.ctx.gazetteer.suggest_category(str(category))
            ) from None
        grade = ConditionGrade(condition)
        state = GarmentStatus(status)
        stamp = added_at or self.clock()
        stratum = f"{brand_id}/{era_id.split(':', 1)[1]}/{kind.value}"
        fields: dict[str, Any] = {
            "id": garment_id(stratum, date, price, stamp),
            "label": label,
            "brand_id": brand_id,
            "era_id": era_id,
            "category": kind,
            "condition": grade,
            "anchor_condition": grade,
            "size": size,
            "status": state,
            "added_at": stamp,
            "notes": notes,
        }
        if state is GarmentStatus.WATCHING:
            fields["reference_price"] = price
            fields["reference_date"] = date
        else:
            fields["acquisition_price"] = price
            fields["acquired_on"] = date
            if state is GarmentStatus.SOLD_ARCHIVED:
                fields["disposed_price"] = price
                fields["disposed_on"] = date
        garment = Garment(**fields)
        self.repo.add_garment(garment)
        return garment

    def edit_garment(self, garment_id_: str, **changes: Any) -> Garment:
        """Edit the mutable fields only (FR-11: brand/era/category/anchor are immutable)."""
        garment = self.get_garment(garment_id_)
        editable = {
            "label",
            "condition",
            "size",
            "notes",
            "status",
            "disposed_price",
            "disposed_on",
        }
        unknown = set(changes) - editable
        if unknown:
            raise ValueError(
                f"immutable or unknown garment fields {sorted(unknown)}; editable: "
                f"{sorted(editable)}"
            )
        updates = {key: value for key, value in changes.items() if value is not None}
        if "condition" in updates:
            updates["condition"] = ConditionGrade(updates["condition"])
        if "status" in updates:
            updates["status"] = GarmentStatus(updates["status"])
        updated = garment.model_copy(update=updates)
        Garment.model_validate(updated.model_dump())
        self.repo.update_garment(updated)
        return updated

    def remove_garment(self, garment_id_: str, *, deleted_at: str | None = None) -> Garment:
        """Soft delete (FR-11): the garment leaves the pipeline, its advice stays readable."""
        garment = self.get_garment(garment_id_)
        updated = garment.model_copy(update={"deleted_at": deleted_at or self.clock()})
        self.repo.update_garment(updated)
        return updated

    def get_garment(self, garment_id_: str) -> Garment:
        garment = self.repo.get_garment(garment_id_)
        if garment is None:
            raise NotFoundError("garment", garment_id_)
        return garment

    def list_garments(
        self, *, include_deleted: bool = False, status: GarmentStatus | None = None
    ) -> list[Garment]:
        return self.repo.list_garments(include_deleted=include_deleted, status=status)

    # -- FR-7 valuation ------------------------------------------------------- #

    def value_one(self, garment: Garment, *, as_of: str | None = None) -> ValuationResult:
        view = self.index_view()
        week = week_key(as_of) if as_of else self._latest_index_week(view)
        return value_garment(garment, index=view, as_of_week=week, ctx=self.ctx)

    def value_portfolio(self, *, as_of: str | None = None) -> list[tuple[Garment, ValuationResult]]:
        view = self.index_view()
        week = week_key(as_of) if as_of else self._latest_index_week(view)
        return [
            (garment, value_garment(garment, index=view, as_of_week=week, ctx=self.ctx))
            for garment in self.repo.list_garments()
        ]

    # -- FR-8 advice ---------------------------------------------------------- #

    def advise(self, *, as_of: str | None = None) -> list[Advice]:
        """Run the advisor for every active garment and persist the results (FR-8/FR-9)."""
        view = self.index_view()
        week = week_key(as_of) if as_of else self._latest_index_week(view)
        events = self.repo.list_events(status=EventStatus.CONFIRMED)
        events_by_id = {event.id: event for event in events}
        created_as_of = self.clock()
        out: list[Advice] = []
        for garment in self.repo.list_garments():
            if garment.status is GarmentStatus.SOLD_ARCHIVED:
                continue
            decision = advise_garment(
                garment, as_of_week=week, index=view, events=events, ctx=self.ctx
            )
            advice = build_advice(
                decision,
                garment=garment,
                events_by_id=events_by_id,
                ctx=self.ctx,
                created_as_of=created_as_of,
            )
            self.repo.add_advice(advice)
            out.append(advice)
        return out

    def decide(self, garment: Garment, *, as_of: str | None = None) -> AdviceDecision:
        """The pure decision for one garment (used by ``advice show --explain``)."""
        view = self.index_view()
        week = week_key(as_of) if as_of else self._latest_index_week(view)
        return advise_garment(
            garment,
            as_of_week=week,
            index=view,
            events=self.repo.list_events(status=EventStatus.CONFIRMED),
            ctx=self.ctx,
        )

    def list_advice(
        self,
        *,
        garment: str | None = None,
        action: AdviceAction | None = None,
        as_of: str | None = None,
        history: bool = False,
    ) -> list[Advice]:
        return self.repo.list_advice(
            garment_id=garment,
            action=action,
            as_of_week=week_key(as_of) if as_of else None,
            history=history,
        )

    def get_advice(self, advice_id: str) -> Advice:
        advice = self.repo.get_advice(advice_id)
        if advice is None:
            raise NotFoundError("advice", advice_id)
        return advice

    # -- FR-10 backtests ------------------------------------------------------ #

    def backtest(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        placebo_seed: int | None = None,
        reference: str = "recovered",
        scenario: str = "",
    ) -> tuple[BacktestRun, list[BacktestResult]]:
        """Replay the advisor week by week and persist the run (FR-10)."""
        view = self.index_view()
        weeks = sorted({point.week for point in view.all_points()})
        if not weeks:
            raise PreconditionError("no index points: run `index build` first")
        start_week = week_key(start) if start else weeks[0]
        end_week = week_key(end) if end else weeks[-1]
        garments = [
            garment
            for garment in self.repo.list_garments()
            if garment.status is not GarmentStatus.SOLD_ARCHIVED
        ]
        if not garments:
            raise PreconditionError("no active garments: add one with `portfolio add`")
        params = BacktestParams(
            start_week=start_week,
            end_week=end_week,
            placebo_seed=placebo_seed,
            reference=reference,
            scenario=scenario,
        )
        run, results = run_backtest(
            params=params,
            garments=garments,
            events=self.repo.list_events(status=EventStatus.CONFIRMED),
            index=view,
            reference=ReferenceIndex.from_index(
                view, carry_back_weeks=self.ctx.index_config.stale_max_weeks
            ),
            ctx=self.ctx,
            as_of=self.clock(),
        )
        self.repo.add_backtest(run, results)
        return run, results

    def get_backtest(self, run_id: str) -> tuple[BacktestRun, list[BacktestResult]]:
        run = self.repo.get_backtest_run(run_id)
        if run is None:
            raise NotFoundError("backtest run", run_id)
        return run, self.repo.list_backtest_results(run_id)

    def list_backtests(self, *, limit: int | None = None) -> list[BacktestRun]:
        return self.repo.list_backtest_runs(limit=limit)

    # -- gazetteer ------------------------------------------------------------ #

    def brands(self) -> list[Brand]:
        stored = self.repo.list_brands()
        return stored or list(self.ctx.gazetteer.brands)

    def brand(self, brand_id: str) -> Brand:
        resolved = self.ctx.gazetteer.resolve_brand(brand_id)
        stored = self.repo.get_brand(resolved)
        return stored or self.ctx.gazetteer.brand(resolved)

    # -- helpers -------------------------------------------------------------- #

    @staticmethod
    def _stamp(as_of: str) -> str:
        """Normalise an ``--as-of`` date to the deterministic end-of-week-day stamp."""
        return as_of if "T" in as_of else f"{as_of}T23:59:59Z"

    @staticmethod
    def _latest_listing_stamp(listings: Sequence[Listing]) -> str:
        latest = max(
            (listing.sold_at or listing.listed_at for listing in listings), default="1970-01-01"
        )
        return latest if "T" in latest else f"{latest}T23:59:59Z"

    @staticmethod
    def _latest_index_week(view: IndexView) -> str:
        weeks = [point.week for point in view.all_points()]
        if not weeks:
            raise PreconditionError("no index points: run `index build` first")
        return max(weeks)

    def latest_index_week(self) -> str:
        """The newest week the current index covers — the default ``as_of`` everywhere."""
        return self._latest_index_week(self.index_view())

    def stratum_context(self, leaf: str) -> list[str]:
        """The leaf and its ancestors, most specific first (used by ``portfolio show``)."""
        self.ctx.gazetteer.validate_stratum(leaf)
        return ancestors(leaf)

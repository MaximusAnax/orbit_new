"""In-memory repository — the backend tests and evals use exclusively."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..models import (
    Advice,
    AdviceAction,
    BacktestResult,
    BacktestRun,
    Brand,
    EventStatus,
    EventType,
    FashionEvent,
    Garment,
    GarmentStatus,
    IndexPoint,
    Listing,
    ListingStatus,
)
from ..weeks import week_key
from .base import RepositoryError, current_advice

__all__ = ["InMemoryRepository"]


class InMemoryRepository:
    """A dict-backed ``Repository`` with the same invariants as the SQLite one."""

    def __init__(self) -> None:
        self._brands: dict[str, Brand] = {}
        self._listings: dict[str, Listing] = {}
        self._index: dict[tuple[str, str], IndexPoint] = {}
        self._events: dict[str, FashionEvent] = {}
        self._garments: dict[str, Garment] = {}
        self._advice: dict[str, Advice] = {}
        self._runs: dict[str, BacktestRun] = {}
        self._results: dict[str, list[BacktestResult]] = {}

    # -- lifecycle ----------------------------------------------------------- #

    def initialize(self, *, reset: bool = False) -> None:
        if reset:
            self.__init__()

    def close(self) -> None:
        return None

    # -- gazetteer ----------------------------------------------------------- #

    def replace_gazetteer(self, brands: Sequence[Brand]) -> None:
        self._brands = {brand.id: brand for brand in brands}

    def list_brands(self) -> list[Brand]:
        return [self._brands[key] for key in sorted(self._brands)]

    def get_brand(self, brand_id: str) -> Brand | None:
        return self._brands.get(brand_id)

    # -- listings ------------------------------------------------------------ #

    def add_listings(self, listings: Iterable[Listing]) -> int:
        added = 0
        for listing in listings:
            if listing.id in self._listings:
                continue
            self._listings[listing.id] = listing
            added += 1
        return added

    def listing_ids(self) -> set[str]:
        return set(self._listings)

    def get_listing(self, listing_id: str) -> Listing | None:
        return self._listings.get(listing_id)

    def list_listings(
        self,
        *,
        stratum: str | None = None,
        status: ListingStatus | None = None,
        limit: int | None = None,
    ) -> list[Listing]:
        rows = [
            listing
            for listing in self._listings.values()
            if (stratum is None or listing.stratum_path.startswith(stratum))
            and (status is None or listing.status is status)
        ]
        rows.sort(key=lambda listing: (listing.sold_at or listing.listed_at, listing.id))
        return rows[:limit] if limit is not None else rows

    # -- index --------------------------------------------------------------- #

    def replace_index_points(self, points: Iterable[IndexPoint]) -> None:
        self._index = {(point.stratum_id, point.week): point for point in points}

    def list_index_points(
        self,
        *,
        stratum_id: str | None = None,
        from_week: str | None = None,
        to_week: str | None = None,
    ) -> list[IndexPoint]:
        rows = [
            point
            for (stratum, week), point in self._index.items()
            if (stratum_id is None or stratum == stratum_id)
            and (from_week is None or week >= from_week)
            and (to_week is None or week <= to_week)
        ]
        rows.sort(key=lambda point: (point.stratum_id, point.week))
        return rows

    # -- events -------------------------------------------------------------- #

    def upsert_events(self, events: Iterable[FashionEvent]) -> None:
        for event in events:
            self._events[event.id] = event

    def get_event(self, event_id: str) -> FashionEvent | None:
        return self._events.get(event_id)

    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        brand_id: str | None = None,
        status: EventStatus | None = None,
        since: str | None = None,
    ) -> list[FashionEvent]:
        rows = [
            event
            for event in self._events.values()
            if (event_type is None or event.event_type is event_type)
            and (brand_id is None or event.brand_id == brand_id)
            and (status is None or event.status is status)
            and (since is None or event.occurred_on >= since)
        ]
        rows.sort(key=lambda event: (event.occurred_on, event.id))
        return rows

    # -- garments ------------------------------------------------------------ #

    def add_garment(self, garment: Garment) -> None:
        if garment.id in self._garments:
            raise RepositoryError(f"garment {garment.id} already exists")
        self._garments[garment.id] = garment

    def update_garment(self, garment: Garment) -> None:
        if garment.id not in self._garments:
            raise RepositoryError(f"unknown garment {garment.id}")
        self._garments[garment.id] = garment

    def get_garment(self, garment_id: str) -> Garment | None:
        return self._garments.get(garment_id)

    def list_garments(
        self, *, include_deleted: bool = False, status: GarmentStatus | None = None
    ) -> list[Garment]:
        rows = [
            garment
            for garment in self._garments.values()
            if (include_deleted or garment.deleted_at is None)
            and (status is None or garment.status is status)
        ]
        rows.sort(key=lambda garment: (garment.added_at, garment.id))
        return rows

    # -- advice --------------------------------------------------------------- #

    def add_advice(self, advice: Advice) -> bool:
        if not advice.frame_checked:
            raise RepositoryError("FR-9: a non-frame-checked advice cannot be stored")
        if advice.id in self._advice:
            return False
        self._advice[advice.id] = advice
        return True

    def get_advice(self, advice_id: str) -> Advice | None:
        return self._advice.get(advice_id)

    def list_advice(
        self,
        *,
        garment_id: str | None = None,
        action: AdviceAction | None = None,
        as_of_week: str | None = None,
        history: bool = False,
    ) -> list[Advice]:
        rows = [
            advice
            for advice in self._advice.values()
            if (garment_id is None or advice.garment_id == garment_id)
            and (as_of_week is None or advice.as_of_week == week_key(as_of_week))
        ]
        if not history:
            rows = current_advice(rows)
        if action is not None:
            rows = [advice for advice in rows if advice.action is action]
        rows.sort(key=lambda advice: (advice.as_of_week, advice.garment_id, advice.created_as_of))
        return rows

    # -- backtests ------------------------------------------------------------ #

    def add_backtest(self, run: BacktestRun, results: Iterable[BacktestResult]) -> None:
        if run.id in self._runs:
            raise RepositoryError(f"backtest run {run.id} already exists")
        self._runs[run.id] = run
        self._results[run.id] = list(results)

    def get_backtest_run(self, run_id: str) -> BacktestRun | None:
        return self._runs.get(run_id)

    def list_backtest_runs(self, *, limit: int | None = None) -> list[BacktestRun]:
        rows = sorted(self._runs.values(), key=lambda run: (run.as_of, run.id), reverse=True)
        return rows[:limit] if limit is not None else rows

    def list_backtest_results(self, run_id: str) -> list[BacktestResult]:
        rows = list(self._results.get(run_id, ()))
        rows.sort(key=lambda result: (result.week, result.garment_id))
        return rows

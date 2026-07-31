"""The ``Repository`` interface every backend implements.

Append-only tables (listings, events, advice, backtests) expose no update path
beyond the mutations DATA_MODEL.md permits: event status transitions,
``source_refs`` appends with the derived corroboration recount, and garment edits
(including the soft delete). ``index_point`` is the one derived table and is
replaced wholesale by a rebuild.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

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

__all__ = ["Repository", "RepositoryError"]


class RepositoryError(RuntimeError):
    """A store-level violation (duplicate append-only row, unknown entity, ...)."""


@runtime_checkable
class Repository(Protocol):
    """Storage for listings, indices, events, garments, advice and backtests."""

    def initialize(self, *, reset: bool = False) -> None:
        """Create the schema; ``reset=True`` recreates it wholesale (``init --reset``)."""
        ...

    def close(self) -> None: ...

    # -- gazetteer (materialised from brands.json by `init`) ----------------- #
    def replace_gazetteer(self, brands: Sequence[Brand]) -> None: ...
    def list_brands(self) -> list[Brand]: ...
    def get_brand(self, brand_id: str) -> Brand | None: ...

    # -- listings (append-only) --------------------------------------------- #
    def add_listings(self, listings: Iterable[Listing]) -> int: ...
    def listing_ids(self) -> set[str]: ...
    def get_listing(self, listing_id: str) -> Listing | None: ...
    def list_listings(
        self,
        *,
        stratum: str | None = None,
        status: ListingStatus | None = None,
        limit: int | None = None,
    ) -> list[Listing]: ...

    # -- index points (derived; replaced by a rebuild) ----------------------- #
    def replace_index_points(self, points: Iterable[IndexPoint]) -> None: ...
    def list_index_points(
        self,
        *,
        stratum_id: str | None = None,
        from_week: str | None = None,
        to_week: str | None = None,
    ) -> list[IndexPoint]: ...

    # -- events -------------------------------------------------------------- #
    def upsert_events(self, events: Iterable[FashionEvent]) -> None: ...
    def get_event(self, event_id: str) -> FashionEvent | None: ...
    def list_events(
        self,
        *,
        event_type: EventType | None = None,
        brand_id: str | None = None,
        status: EventStatus | None = None,
        since: str | None = None,
    ) -> list[FashionEvent]: ...

    # -- garments ------------------------------------------------------------ #
    def add_garment(self, garment: Garment) -> None: ...
    def update_garment(self, garment: Garment) -> None: ...
    def get_garment(self, garment_id: str) -> Garment | None: ...
    def list_garments(
        self, *, include_deleted: bool = False, status: GarmentStatus | None = None
    ) -> list[Garment]: ...

    # -- advice (immutable, append-only, superseding) ------------------------ #
    def add_advice(self, advice: Advice) -> bool: ...
    def get_advice(self, advice_id: str) -> Advice | None: ...
    def list_advice(
        self,
        *,
        garment_id: str | None = None,
        action: AdviceAction | None = None,
        as_of_week: str | None = None,
        history: bool = False,
    ) -> list[Advice]: ...

    # -- backtests (append-only) --------------------------------------------- #
    def add_backtest(self, run: BacktestRun, results: Iterable[BacktestResult]) -> None: ...
    def get_backtest_run(self, run_id: str) -> BacktestRun | None: ...
    def list_backtest_runs(self, *, limit: int | None = None) -> list[BacktestRun]: ...
    def list_backtest_results(self, run_id: str) -> list[BacktestResult]: ...


def current_advice(rows: Sequence[Advice]) -> list[Advice]:
    """Keep only the current advice per ``(garment_id, as_of_week)`` (greatest created_as_of)."""
    best: dict[tuple[str, str], Advice] = {}
    for row in rows:
        key = (row.garment_id, row.as_of_week)
        held = best.get(key)
        if held is None or (row.created_as_of, row.id) > (held.created_as_of, held.id):
            best[key] = row
    return sorted(best.values(), key=lambda a: (a.as_of_week, a.garment_id, a.id))

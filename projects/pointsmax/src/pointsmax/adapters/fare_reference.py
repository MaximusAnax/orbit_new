"""``FareReference``: the "reasonable cash price" yardstick (FR-8)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Cabin, OfferKind, World


@runtime_checkable
class FareReference(Protocol):
    """Resolves a reference fare in integer cents, or None when unknown."""

    def fare(
        self,
        kind: OfferKind,
        *,
        month: str,
        origin_city: str | None = None,
        dest_city: str | None = None,
        cabin: Cabin | None = None,
        round_trip: bool | None = None,
        city: str | None = None,
    ) -> int | None:
        """Reference fare for the lookup key, keyed on the *goal's* travel month."""
        ...


class CommittedFareReference:
    """Offline default: ``data/world/reference_fares.json``.

    The engine reads the same table directly through :meth:`World.find_fare` so
    it stays pure; this adapter is the seam a live fare API would replace.
    """

    def __init__(self, world: World) -> None:
        self._world = world

    def fare(
        self,
        kind: OfferKind,
        *,
        month: str,
        origin_city: str | None = None,
        dest_city: str | None = None,
        cabin: Cabin | None = None,
        round_trip: bool | None = None,
        city: str | None = None,
    ) -> int | None:
        return self._world.find_fare(
            kind,
            month=month,
            origin_city=origin_city,
            dest_city=dest_city,
            cabin=cabin,
            round_trip=round_trip,
            city=city,
        )

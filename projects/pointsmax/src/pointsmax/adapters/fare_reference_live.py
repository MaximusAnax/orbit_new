"""Live ``FareReference``: reference fares served over HTTP.

A live flight-offers API (the Amadeus Self-Service analog) is deferred for this
pass, so this adapter consumes the same ``reference_fares.json`` schema the
committed dataset uses, from an operator-controlled URL, and validates every row
through :class:`ReferenceFare`.  Rows are indexed once per instance, so a lookup
is a dict hit rather than a request.

Configuration (documented in the project README):

* ``POINTSMAX_FARE_FEED_URL`` — URL returning a ``reference_fares.json``-shaped array.
* ``POINTSMAX_AMADEUS_KEY`` — optional bearer token for that endpoint.
"""

from __future__ import annotations

import os
from typing import Any

from ..models import Cabin, OfferKind, ReferenceFare
from ._http import LiveAdapterUnavailable, fetch_json


class HttpFareReference:
    """Fetch and index reference fares from ``POINTSMAX_FARE_FEED_URL``."""

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.url = url or os.environ.get("POINTSMAX_FARE_FEED_URL", "")
        self.token = token or os.environ.get("POINTSMAX_AMADEUS_KEY")
        self.timeout = timeout
        self._index: dict[tuple, int] | None = None
        if not self.url:
            raise LiveAdapterUnavailable(
                "HttpFareReference needs POINTSMAX_FARE_FEED_URL (or an explicit url); "
                "use CommittedFareReference for the shipped table."
            )

    def _load(self) -> dict[tuple, int]:
        if self._index is not None:
            return self._index
        payload: Any = fetch_json(self.url, token=self.token, timeout=self.timeout)
        if isinstance(payload, dict) and "reference_fares" in payload:
            payload = payload["reference_fares"]
        if not isinstance(payload, list):
            raise LiveAdapterUnavailable(f"{self.url} did not return a fare array")
        index: dict[tuple, int] = {}
        for row in payload:
            fare = ReferenceFare.model_validate(row)
            index[_key(fare)] = fare.fare_cents
        self._index = index
        return index

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
        if kind is OfferKind.FLIGHT:
            key = (
                "flight",
                origin_city,
                dest_city,
                str(cabin) if cabin else None,
                bool(round_trip),
                month,
            )
        else:
            key = ("stay", city, month)
        return self._load().get(key)


def _key(fare: ReferenceFare) -> tuple:
    if fare.kind is OfferKind.FLIGHT:
        return (
            "flight",
            fare.origin_city,
            fare.dest_city,
            str(fare.cabin) if fare.cabin else None,
            bool(fare.round_trip),
            fare.month,
        )
    return ("stay", fare.city, fare.month)

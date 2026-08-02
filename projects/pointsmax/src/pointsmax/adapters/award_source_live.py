"""Live ``AwardSource``: award snapshots served over HTTP.

Live award-space search (a seats.aero-style API) is Non-goal 3 for this pass, so
this adapter deliberately does **not** invent a third-party response shape: it
consumes the same ``awards.json`` schema the committed dataset uses, from a URL
the operator controls, and validates every row through :class:`AwardOffer`.
Slotting in a vendor-specific client later means replacing ``_rows``.

Configuration (documented in the project README):

* ``POINTSMAX_AWARD_FEED_URL`` — URL returning an ``awards.json``-shaped array.
* ``POINTSMAX_SEATSAERO_KEY`` — optional bearer token for that endpoint.
"""

from __future__ import annotations

import os
from typing import Any

from ..models import AwardOffer
from ._http import LiveAdapterUnavailable, fetch_json


class HttpAwardSource:
    """Fetch award offers from ``POINTSMAX_AWARD_FEED_URL``."""

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.url = url or os.environ.get("POINTSMAX_AWARD_FEED_URL", "")
        self.token = token or os.environ.get("POINTSMAX_SEATSAERO_KEY")
        self.timeout = timeout
        if not self.url:
            raise LiveAdapterUnavailable(
                "HttpAwardSource needs POINTSMAX_AWARD_FEED_URL (or an explicit url); "
                "use CommittedAwardSource for the shipped snapshot."
            )

    def _rows(self) -> list[Any]:
        payload = fetch_json(self.url, token=self.token, timeout=self.timeout)
        if isinstance(payload, dict) and "offers" in payload:
            payload = payload["offers"]
        if not isinstance(payload, list):
            raise LiveAdapterUnavailable(f"{self.url} did not return an award array")
        return payload

    def offers(self) -> list[AwardOffer]:
        offers = [AwardOffer.model_validate(row) for row in self._rows()]
        offers.sort(key=lambda offer: offer.id)
        return offers

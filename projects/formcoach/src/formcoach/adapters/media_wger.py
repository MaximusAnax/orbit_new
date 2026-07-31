"""Live :class:`MediaResolver` — refresh and HTTP-verify against wger.de.

Activation is credential/environment gated: the resolver refuses to do any
network I/O unless ``FORMCOACH_MEDIA_LIVE=1`` is set, so importing it can never
make a test or eval reach the network.  The base URL is overridable with
``FORMCOACH_WGER_BASE_URL`` and no credentials are required — wger's exercise
image endpoint is public and CC-licensed, which is why it is the documented
refresh source (SCOPE design decision 12).

Only the standard library is used (``urllib.request``); there is no extra
dependency to install.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from formcoach.models import MediaAsset, MediaKind, MediaSource

LIVE_ENV_VAR = "FORMCOACH_MEDIA_LIVE"
BASE_URL_ENV_VAR = "FORMCOACH_WGER_BASE_URL"
DEFAULT_BASE_URL = "https://wger.de/api/v2"
DEFAULT_TIMEOUT_S = 10.0
WGER_LICENSE = "CC-BY-SA-4.0"
WGER_ATTRIBUTION = "wger.de exercise database"


class MediaResolverDisabledError(RuntimeError):
    """Raised when the live resolver is used without being enabled."""


class WgerMediaResolver:
    """Refreshes URL assets from wger and verifies them with a real request."""

    name = "wger"

    def __init__(
        self,
        fallback: Sequence[MediaAsset] = (),
        *,
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV_VAR, DEFAULT_BASE_URL)).rstrip("/")
        self.timeout_s = timeout_s
        self._fallback: dict[str, list[MediaAsset]] = {}
        for asset in fallback:
            self._fallback.setdefault(asset.exercise_id, []).append(asset)

    @staticmethod
    def is_enabled() -> bool:
        """Whether the operator has opted this resolver into network access."""
        return os.environ.get(LIVE_ENV_VAR, "") == "1"

    def _require_enabled(self) -> None:
        if not self.is_enabled():
            raise MediaResolverDisabledError(
                f"set {LIVE_ENV_VAR}=1 to allow the wger resolver to make requests; "
                "the offline LocalMediaResolver is the default"
            )

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}/{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def resolve(self, exercise_id: str) -> list[MediaAsset]:
        """Fetch fresh image URLs for an exercise, falling back to the manifest.

        The exercise slug is used as the wger search term; any image the search
        returns becomes a ``source = url`` asset carrying wger's license and
        attribution, so the manifest can be refreshed without touching code.
        """
        self._require_enabled()
        term = exercise_id.replace("-", " ")
        try:
            payload = self._get_json("exerciseimage", {"format": "json", "limit": "10"})
            results = payload.get("results", [])
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return list(self._fallback.get(exercise_id, []))

        assets: list[MediaAsset] = []
        for index, row in enumerate(results):
            image = row.get("image")
            if not image or not str(image).startswith("https://"):
                continue
            if term.split()[0] not in str(row.get("exercise_base_uuid", term)) and index > 3:
                continue
            assets.append(
                MediaAsset(
                    id=f"{exercise_id}#wger{index}",
                    exercise_id=exercise_id,
                    kind=MediaKind.IMAGE,
                    source=MediaSource.URL,
                    ref=str(image),
                    license=WGER_LICENSE,
                    attribution=WGER_ATTRIBUTION,
                )
            )
        return assets or list(self._fallback.get(exercise_id, []))

    def verify(self, asset: MediaAsset) -> bool:
        """HTTP-verify a URL asset; local assets keep the offline file rule."""
        self._require_enabled()
        if asset.source is not MediaSource.URL:
            return False
        request = urllib.request.Request(asset.ref, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

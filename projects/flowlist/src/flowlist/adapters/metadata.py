"""``MetadataProvider`` implementations.

:class:`FixtureMetadataProvider` is the offline default (committed JSON
catalog, no network, deterministic).  :class:`SpotifyMetadataProvider` is the
live path and activates only when ``SPOTIFY_CLIENT_ID`` and
``SPOTIFY_CLIENT_SECRET` are set.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flowlist.engine.models import AudioFeatures, FeatureSource, Track
from flowlist.errors import AdapterUnavailableError

#: Timestamp used for fixture-sourced rows when the caller supplies none.  The
#: engine never reads a clock; adapters may, but the offline one must not, so
#: its default is a literal (EVALS.md 1: "no wall clock").
FIXTURE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

_FEATURE_KEYS = ("bpm", "key_pc", "mode", "energy", "danceability", "loudness_db", "valence")


def _coerce_features(
    payload: dict[str, Any],
    track_id: str,
    source: FeatureSource,
    analyzed_at: datetime,
) -> AudioFeatures:
    """Build an :class:`AudioFeatures` row from a catalog dict."""
    values = {key: payload.get(key) for key in _FEATURE_KEYS}
    return AudioFeatures(
        track_id=track_id,
        source=source,
        confidence=payload.get("confidence", 1.0),
        analyzed_at=analyzed_at,
        **values,
    )


class FixtureMetadataProvider:
    """Serves a committed JSON catalog (offline default).

    The catalog maps a track id to a feature dict, or is a list of
    ``{"id": ..., "features": {...}}`` objects — the shape ``generate.py``
    emits.  Unknown tracks are simply absent from the result, which is how
    coverage stays honest (D10).
    """

    name = "fixture"
    source = FeatureSource.FIXTURE

    def __init__(
        self,
        catalog: dict[str, dict[str, Any]] | None = None,
        *,
        analyzed_at: datetime = FIXTURE_EPOCH,
        source: FeatureSource = FeatureSource.FIXTURE,
    ) -> None:
        self._catalog = dict(catalog or {})
        self._analyzed_at = analyzed_at
        self.source = source
        self.name = source.value

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        analyzed_at: datetime = FIXTURE_EPOCH,
        source: FeatureSource = FeatureSource.FIXTURE,
    ) -> FixtureMetadataProvider:
        """Load a catalog file (``evals/fixtures/catalog.json`` or a user's own)."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(cls._normalize_catalog(data), analyzed_at=analyzed_at, source=source)

    @staticmethod
    def _normalize_catalog(data: Any) -> dict[str, dict[str, Any]]:
        if isinstance(data, dict) and "tracks" in data:
            data = data["tracks"]
        catalog: dict[str, dict[str, Any]] = {}
        if isinstance(data, dict):
            for track_id, payload in data.items():
                catalog[track_id] = dict(payload.get("features", payload))
        elif isinstance(data, list):
            for row in data:
                track_id = row.get("id") or row.get("track_id")
                if track_id:
                    catalog[track_id] = dict(row.get("features", row))
        else:
            raise ValueError("catalog must be a mapping or a list of track objects")
        return catalog

    def available(self) -> bool:
        return True

    def get_features(self, tracks: Sequence[Track]) -> dict[str, AudioFeatures]:
        found: dict[str, AudioFeatures] = {}
        for track in tracks:
            payload = self._catalog.get(track.id)
            if payload is None:
                continue
            found[track.id] = _coerce_features(payload, track.id, self.source, self._analyzed_at)
        return found


class SpotifyMetadataProvider:
    """Live Spotify Web API provider (best-effort, not eval-gated).

    Activates only when ``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET`` are
    set; authenticates with the client-credentials flow and batches up to 100
    ids per ``GET /v1/audio-features`` call.  API errors degrade to "unknown"
    per track rather than failing the command (D10).

    Marked best-effort in SCOPE.md's non-goals: Spotify deprecated this
    endpoint for *new* third-party apps in November 2024, which is exactly why
    the core loop is import/offline-first.  The interface is provider-agnostic,
    so another backend can be slotted in without engine changes.
    """

    name = "streaming"
    source = FeatureSource.STREAMING
    token_url = "https://accounts.spotify.com/api/token"
    features_url = "https://api.spotify.com/v1/audio-features"
    batch_size = 100

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        timeout: float = 10.0,
        now: datetime | None = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("SPOTIFY_CLIENT_ID")
        self._client_secret = client_secret or os.environ.get("SPOTIFY_CLIENT_SECRET")
        self._timeout = timeout
        self._now = now
        self._token: str | None = None
        self._token_expiry = 0.0

    def available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _require_credentials(self) -> tuple[str, str]:
        if not self.available():
            raise AdapterUnavailableError(
                "SpotifyMetadataProvider needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET",
                provider=self.name,
            )
        return str(self._client_id), str(self._client_secret)

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        client_id, client_secret = self._require_credentials()
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            }
        ).encode("ascii")
        request = urllib.request.Request(
            self.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._token = str(payload["access_token"])
        self._token_expiry = time.monotonic() + float(payload.get("expires_in", 3600)) - 60.0
        return self._token

    def _fetch_batch(self, spotify_ids: Sequence[str]) -> list[dict[str, Any] | None]:
        query = urllib.parse.urlencode({"ids": ",".join(spotify_ids)})
        request = urllib.request.Request(
            f"{self.features_url}?{query}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            # Degrade to "unknown" for the whole batch (D10) rather than
            # failing a command the user ran for other reasons.
            return [None] * len(spotify_ids)
        return list(payload.get("audio_features") or [None] * len(spotify_ids))

    @staticmethod
    def _translate(payload: dict[str, Any]) -> dict[str, Any]:
        """Spotify's audio-features JSON -> flowlist's vocabulary (D1).

        Applies the same sentinel rules as import (FR-1): ``key = -1`` means
        "no key detected" and ``tempo = 0`` means "no tempo", both of which
        become nulls rather than fabricated values.
        """
        key = payload.get("key")
        mode = payload.get("mode")
        if key is None or key < 0 or mode is None:
            key = mode = None
        tempo = payload.get("tempo")
        if tempo is not None and not 40.0 <= float(tempo) <= 260.0:
            tempo = None
        loudness = payload.get("loudness")
        if loudness is not None and not -60.0 <= float(loudness) <= 0.0:
            loudness = None
        return {
            "bpm": tempo,
            "key_pc": key,
            "mode": mode,
            "energy": payload.get("energy"),
            "danceability": payload.get("danceability"),
            "loudness_db": loudness,
            "valence": payload.get("valence"),
        }

    def get_features(self, tracks: Sequence[Track]) -> dict[str, AudioFeatures]:
        self._require_credentials()
        by_spotify_id = {t.spotify_id: t for t in tracks if t.spotify_id}
        analyzed_at = self._now or datetime.now(UTC)
        found: dict[str, AudioFeatures] = {}
        ids = list(by_spotify_id)
        for start in range(0, len(ids), self.batch_size):
            batch = ids[start : start + self.batch_size]
            for spotify_id, payload in zip(batch, self._fetch_batch(batch), strict=False):
                if not payload:
                    continue
                track = by_spotify_id[spotify_id]
                found[track.id] = _coerce_features(
                    self._translate(payload), track.id, self.source, analyzed_at
                )
        return found


class ChainedMetadataProvider:
    """Query several providers in order, first non-null per track wins.

    Used by ``analyze`` so a user can configure ``--providers fixture,streaming``
    without the service layer growing provider-specific branches.
    """

    name = "chain"

    def __init__(self, providers: Iterable[Any]) -> None:
        self._providers = list(providers)

    def available(self) -> bool:
        return any(p.available() for p in self._providers)

    def get_features(self, tracks: Sequence[Track]) -> dict[str, AudioFeatures]:
        found: dict[str, AudioFeatures] = {}
        for provider in self._providers:
            if not provider.available():
                continue
            pending = [t for t in tracks if t.id not in found]
            if not pending:
                break
            found.update(provider.get_features(pending))
        return found


__all__ = [
    "FIXTURE_EPOCH",
    "ChainedMetadataProvider",
    "FixtureMetadataProvider",
    "SpotifyMetadataProvider",
]

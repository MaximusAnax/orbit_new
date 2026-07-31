"""``LocalAudioAnalyzer`` implementations (US-6).

:class:`FixtureLocalAnalyzer` is the offline default: precomputed features
keyed by file basename from a committed JSON map.  :class:`LibrosaLocalAnalyzer`
is the live path behind the optional ``audio`` extra; it imports librosa lazily
so that a missing extra produces a clear message instead of breaking unrelated
commands.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flowlist.engine.models import AudioFeatures, FeatureSource
from flowlist.errors import AdapterUnavailableError

FIXTURE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: Krumhansl-Kessler major/minor key profiles (Krumhansl & Kessler 1982), the
#: reference correlation templates of the Krumhansl-Schmuckler key-finding
#: algorithm used by :class:`LibrosaLocalAnalyzer`.
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def _correlate(chroma: list[float], profile: tuple[float, ...], rotation: int) -> float:
    """Pearson correlation of a chroma histogram against a rotated profile."""
    rotated = [profile[(i - rotation) % 12] for i in range(12)]
    mean_c = sum(chroma) / 12.0
    mean_p = sum(rotated) / 12.0
    num = sum((chroma[i] - mean_c) * (rotated[i] - mean_p) for i in range(12))
    den_c = math.sqrt(sum((chroma[i] - mean_c) ** 2 for i in range(12)))
    den_p = math.sqrt(sum((rotated[i] - mean_p) ** 2 for i in range(12)))
    if den_c == 0.0 or den_p == 0.0:
        return 0.0
    return num / (den_c * den_p)


def estimate_key(chroma: list[float]) -> tuple[int, int, float]:
    """Krumhansl-Schmuckler key finding: ``(pitch class, mode, correlation)``.

    Pure and testable without librosa: the caller supplies a 12-bin chroma
    histogram.  Ties break to the lowest pitch class, then major.
    """
    if len(chroma) != 12:
        raise ValueError("chroma histogram must have 12 bins")
    best = (0, 1, -2.0)
    for rotation in range(12):
        for mode, profile in ((1, KK_MAJOR), (0, KK_MINOR)):
            score = _correlate(chroma, profile, rotation)
            if score > best[2] + 1e-12:
                best = (rotation, mode, score)
    return best


class FixtureLocalAnalyzer:
    """Offline analyzer: a committed ``basename -> features`` JSON map."""

    name = "local_analysis"
    source = FeatureSource.LOCAL_ANALYSIS

    def __init__(
        self,
        features_by_basename: dict[str, dict[str, Any]] | None = None,
        *,
        analyzed_at: datetime = FIXTURE_EPOCH,
    ) -> None:
        self._map = dict(features_by_basename or {})
        self._analyzed_at = analyzed_at

    @classmethod
    def from_path(
        cls, path: str | Path, *, analyzed_at: datetime = FIXTURE_EPOCH
    ) -> FixtureLocalAnalyzer:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data, analyzed_at=analyzed_at)

    def available(self) -> bool:
        return True

    def analyze(self, file_path: str) -> AudioFeatures | None:
        payload = self._map.get(os.path.basename(file_path))
        if payload is None:
            return None
        track_id = payload.get("track_id") or file_path
        return AudioFeatures(
            track_id=track_id,
            source=self.source,
            bpm=payload.get("bpm"),
            key_pc=payload.get("key_pc"),
            mode=payload.get("mode"),
            energy=payload.get("energy"),
            danceability=payload.get("danceability"),
            loudness_db=payload.get("loudness_db"),
            valence=payload.get("valence"),
            confidence=payload.get("confidence", 1.0),
            analyzed_at=self._analyzed_at,
        )


class LibrosaLocalAnalyzer:
    """Live analyzer behind the optional ``audio`` extra.

    * BPM from ``librosa.beat.beat_track`` (Ellis 2007 dynamic-programming beat
      tracker).
    * Key from a chroma histogram correlated against the 24 Krumhansl-Kessler
      profiles (:func:`estimate_key`); the winning correlation becomes
      ``confidence``.
    * Energy from RMS normalised into [0, 1].
    * Loudness as dBFS RMS, an approximation of integrated loudness.

    librosa is imported inside :meth:`analyze`/:meth:`available` only, so the
    offline path never loads it (CONVENTIONS 3).
    """

    name = "local_analysis"
    source = FeatureSource.LOCAL_ANALYSIS
    #: RMS treated as full-scale energy 1.0; ~-6 dBFS, a loud modern master.
    energy_reference = 0.5

    def __init__(self, *, sample_rate: int = 22050, now: datetime | None = None) -> None:
        self._sample_rate = sample_rate
        self._now = now

    @staticmethod
    def _import_librosa() -> Any:
        try:
            import librosa
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise AdapterUnavailableError(
                "local analysis needs the optional audio extra: "
                "install it with `uv pip install 'flowlist[audio]'`",
                provider="local_analysis",
                extra="audio",
            ) from exc
        return librosa

    def available(self) -> bool:
        try:
            self._import_librosa()
        except AdapterUnavailableError:
            return False
        return True

    def analyze(self, file_path: str) -> AudioFeatures | None:
        librosa = self._import_librosa()
        try:
            samples, sample_rate = librosa.load(file_path, sr=self._sample_rate, mono=True)
        except Exception:
            return None
        if samples.size == 0:
            return None

        tempo, _ = librosa.beat.beat_track(y=samples, sr=sample_rate)
        bpm = float(tempo if not hasattr(tempo, "item") else tempo.item())
        while bpm and bpm < 40.0:
            bpm *= 2.0
        while bpm > 260.0:
            bpm /= 2.0

        chroma = librosa.feature.chroma_cqt(y=samples, sr=sample_rate)
        histogram = [float(value) for value in chroma.mean(axis=1)]
        key_pc, mode, correlation = estimate_key(histogram)

        rms = float((samples**2).mean() ** 0.5)
        energy = min(1.0, max(0.0, rms / self.energy_reference))
        loudness = max(-60.0, min(0.0, 20.0 * math.log10(rms))) if rms > 0 else -60.0

        return AudioFeatures(
            track_id=file_path,
            source=self.source,
            bpm=round(bpm, 2) if 40.0 <= bpm <= 260.0 else None,
            key_pc=key_pc,
            mode=mode,
            energy=round(energy, 4),
            loudness_db=round(loudness, 2),
            confidence=round(max(0.0, min(1.0, correlation)), 4),
            analyzed_at=self._now or datetime.now(UTC),
        )


__all__ = [
    "KK_MAJOR",
    "KK_MINOR",
    "FixtureLocalAnalyzer",
    "LibrosaLocalAnalyzer",
    "estimate_key",
]

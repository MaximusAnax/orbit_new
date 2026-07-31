"""Corpus access shared by the calibration script, the metrics and the runner.

Materializes the seeded fixture corpus into ``evals/fixtures/.cache/voices/`` on
first use (REVIEW deviation 1) and caches the **raw** 16 measured features per
clip. Raw features are cached rather than embeddings because they depend only on
the DSP code, never on the calibration constants — so ``calibrate.py`` (which
derives those constants) and the metrics (which consume them) share one cache.

The cache key includes a hash of every analysis module's source, so editing the
DSP invalidates it automatically instead of silently serving stale features.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from voicekin.engine import audio as audio_engine
from voicekin.engine import dsp as dsp_engine
from voicekin.engine.audio import AudioClip, normalize_intake
from voicekin.engine.dsp import VoiceFeatures, analyze_voice
from voicekin.models import EMBEDDING_DIM, Calibration

from . import fixtures as _fixtures_pkg  # noqa: F401  (namespace anchor)
from .fixtures import generate_voices

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CACHE_DIR = FIXTURES_DIR / ".cache"
VOICES_DIR = CACHE_DIR / "voices"

_ANALYSIS_MODULES = (dsp_engine, audio_engine)


@lru_cache(maxsize=1)
def analysis_fingerprint() -> str:
    """Hash of the analysis code, so the feature cache cannot go stale."""
    digest = hashlib.sha256()
    for module in _ANALYSIS_MODULES:
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()[:16]


@lru_cache(maxsize=1)
def load_labels() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "labels.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "corpus_manifest.json").read_text(encoding="utf-8"))


def load_fixture(name: str) -> Any:
    """Read a committed derived fixture (``trials.json`` and friends)."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def ensure_corpus() -> dict[str, Any]:
    """Materialize any missing WAV and return the labels."""
    labels = load_labels()
    generate_voices.materialize(labels, VOICES_DIR)
    return labels


@lru_cache(maxsize=1)
def _utterance_index() -> dict[str, dict[str, Any]]:
    return {u["role"]: u for u in load_labels()["utterances"]}


@lru_cache(maxsize=1)
def _speaker_index() -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in load_labels()["speakers"]}


def utterance(role: str) -> dict[str, Any]:
    try:
        return _utterance_index()[role]
    except KeyError as exc:  # pragma: no cover - fixture authoring error
        raise KeyError(f"no corpus utterance with role {role!r}") from exc


def speaker(speaker_id: str) -> dict[str, Any]:
    return _speaker_index()[speaker_id]


def speakers(*, split: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
    return [
        s
        for s in load_labels()["speakers"]
        if (split is None or s["split"] == split) and (role is None or s["role"] == role)
    ]


def roles(
    speaker_id: str, kind: str | None = None, *, variant: str | None = None
) -> list[str]:
    """Every labelled take of one speaker, in label order."""
    return [
        u["role"]
        for u in load_labels()["utterances"]
        if u["speaker_id"] == speaker_id
        and (kind is None or u["kind"] == kind)
        and (variant is None or u["variant"] == variant)
    ]


def wav_path(role: str) -> Path:
    ensure_corpus()
    return CACHE_DIR / utterance(role)["path"]


def wav_bytes(role: str) -> bytes:
    return wav_path(role).read_bytes()


def clip(role: str) -> AudioClip:
    """The clip exactly as the service would see it after FR-2 intake."""
    return normalize_intake(wav_bytes(role))


# --------------------------------------------------------------------------- #
# Cached raw features
# --------------------------------------------------------------------------- #


@dataclass
class _FeatureCache:
    path: Path
    table: dict[str, np.ndarray]
    dirty: bool = False

    def get(self, role: str) -> np.ndarray:
        vector = self.table.get(role)
        if vector is None:
            vector = analyze_voice(clip(role)).to_vector()
            self.table[role] = vector
            self.dirty = True
        return vector

    def flush(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, **self.table)
        self.dirty = False


@lru_cache(maxsize=1)
def _feature_cache() -> _FeatureCache:
    path = CACHE_DIR / f"features-{analysis_fingerprint()}.npz"
    table: dict[str, np.ndarray] = {}
    if path.exists():
        with np.load(path) as data:
            table = {key: data[key] for key in data.files}
    return _FeatureCache(path=path, table=table)


def raw_features(role: str) -> np.ndarray:
    """The 16 un-normalized source-filter features of one corpus take (FR-4)."""
    return _feature_cache().get(role)


def features(role: str) -> VoiceFeatures:
    vector = raw_features(role)
    return VoiceFeatures(
        log_f0_median=float(vector[0]),
        log_f0_iqr=float(vector[1]),
        voiced_ratio=float(vector[2]),
        f1_median=float(vector[3]),
        f2_median=float(vector[4]),
        tilt_db_oct=float(vector[5]),
        centroid_hz=float(vector[6]),
        rolloff_hz=float(vector[7]),
        band_ratios=tuple(float(v) for v in vector[8:]),
    )


def warm_features(role_list: Iterable[str]) -> None:
    """Measure a batch of takes and persist the cache once."""
    cache = _feature_cache()
    for role in role_list:
        cache.get(role)
    cache.flush()


def all_roles() -> list[str]:
    return [u["role"] for u in load_labels()["utterances"]]


# --------------------------------------------------------------------------- #
# Embeddings (a pure function of raw features and the calibration constants)
# --------------------------------------------------------------------------- #


def normalize_vector(raw: Sequence[float], calibration: Calibration) -> np.ndarray:
    """Affine normalization, exactly as ``SpectralStatsEmbedder`` does."""
    array = np.asarray(raw, dtype=np.float64)
    if array.shape[0] != EMBEDDING_DIM:  # pragma: no cover - guarded upstream
        raise ValueError(f"expected {EMBEDDING_DIM} raw features, got {array.shape[0]}")
    means = np.array([n.mean for n in calibration.feature_norms], dtype=np.float64)
    scales = np.array([n.scale for n in calibration.feature_norms], dtype=np.float64)
    return (array - means) / scales


def embedding(role: str, calibration: Calibration) -> np.ndarray:
    return normalize_vector(raw_features(role), calibration)


def centroid_of(role_list: Sequence[str], calibration: Calibration) -> np.ndarray:
    """Mean of per-sample embeddings (FR-3; not L2-normalized — see REVIEW.md)."""
    return np.stack([embedding(role, calibration) for role in role_list]).mean(axis=0)


def similarity(a: Sequence[float], b: Sequence[float], calibration: Calibration) -> float:
    """The shipped scoring rule: calibrated distance similarity."""
    diff = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return 1.0 - float(np.dot(diff, diff)) / calibration.score_scale


__all__ = [
    "CACHE_DIR",
    "FIXTURES_DIR",
    "VOICES_DIR",
    "all_roles",
    "centroid_of",
    "clip",
    "embedding",
    "ensure_corpus",
    "features",
    "load_fixture",
    "load_labels",
    "load_manifest",
    "normalize_vector",
    "raw_features",
    "roles",
    "similarity",
    "speaker",
    "speakers",
    "utterance",
    "warm_features",
    "wav_bytes",
    "wav_path",
]

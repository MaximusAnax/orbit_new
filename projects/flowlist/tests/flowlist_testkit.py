"""Deterministic helpers shared by the flowlist tests."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from flowlist.engine.models import FeatureSnapshot, Track

#: A literal timestamp: nothing in the suite reads a clock (CONVENTIONS 3).
NOW = datetime(2026, 7, 31, 18, 2, 11, tzinfo=UTC)

#: Genre clusters mirroring EVALS.md 4's generator ranges.
CLUSTERS: tuple[tuple[str, float, float, float, float], ...] = (
    ("house", 120.0, 128.0, 0.60, 0.90),
    ("dnb", 170.0, 178.0, 0.70, 0.95),
    ("hiphop", 82.0, 96.0, 0.40, 0.70),
    ("indie", 96.0, 120.0, 0.30, 0.70),
)


def make_features(
    n: int,
    seed: int,
    clusters: tuple[tuple[str, float, float, float, float], ...] = CLUSTERS,
) -> list[FeatureSnapshot]:
    """Genre-realistic synthetic features; loudness correlates with energy."""
    rng = random.Random(seed)
    out: list[FeatureSnapshot] = []
    for _ in range(n):
        _, bpm_lo, bpm_hi, e_lo, e_hi = rng.choice(clusters)
        energy = round(rng.uniform(e_lo, e_hi), 3)
        out.append(
            FeatureSnapshot(
                bpm=round(rng.uniform(bpm_lo, bpm_hi), 1),
                key_pc=rng.randrange(12),
                mode=rng.randrange(2),
                energy=energy,
                danceability=round(rng.uniform(0.3, 0.95), 3),
                loudness_db=round(-14.0 + 10.0 * energy + rng.uniform(-1.0, 1.0), 2),
            )
        )
    return out


def random_matrix(n: int, seed: int) -> list[list[float]]:
    """A plain random score matrix — asymmetric, zero diagonal."""
    rng = random.Random(seed)
    return [[round(rng.random(), 9) if i != j else 0.0 for j in range(n)] for i in range(n)]


def make_track(track_id: str, **overrides: object) -> Track:
    payload: dict[str, object] = {
        "id": track_id,
        "title": f"Title {track_id}",
        "artist": f"Artist {track_id}",
        "created_at": NOW,
    }
    payload.update(overrides)
    return Track(**payload)  # type: ignore[arg-type]

"""Committed, deterministic eval fixtures.

Every file in this package is data produced by a committed seeded generator in
the same directory.  Loading is pure filesystem reads; nothing here imports a
live adapter, touches the network or reads the clock.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = ["FIXTURE_DIR", "hash64", "load_fixture", "write_fixture"]

FIXTURE_DIR = Path(__file__).resolve().parent


def hash64(*parts: int) -> int:
    """A stable 64-bit hash of a tuple of ints.

    ``random.seed`` and ``hash()`` are not stable across processes/versions, so
    the fixture seeds are derived with BLAKE2b instead: the calibration record
    and every metric are then identical for any worker count (FR-5, EVALS.md).
    """
    payload = b"".join(int(part).to_bytes(8, "big", signed=True) for part in parts)
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def load_fixture(name: str) -> Any:
    """Read one committed fixture file."""
    path = FIXTURE_DIR / name
    if not path.exists():  # pragma: no cover - a missing fixture is a build error
        raise FileNotFoundError(f"missing eval fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_fixture(name: str, payload: Any) -> Path:
    """Write one fixture file in the committed canonical form (generators only)."""
    path = FIXTURE_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path

"""Stateless seeded jitter (SCOPE.md D4, FR-17).

All randomness in Almanac flows from the stored seed through SHA-256 keyed
jitter.  There is deliberately no use of ``random`` anywhere in ``engine/``:
a hash of (seed, date, slot, entity id) is order-independent, reproducible
without PRNG state plumbing, and identical across processes and platforms.
"""

from __future__ import annotations

import datetime as dt
import hashlib

_TWO_64 = float(1 << 64)


def _part(value: object) -> str:
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def hash_u64(*parts: object) -> int:
    """The first 8 bytes of ``SHA-256(parts joined)`` as a big-endian u64."""
    payload = "\x00".join(_part(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def jitter(amplitude: float, *parts: object) -> float:
    """Deterministic value in ``[0, amplitude)`` keyed by ``parts``."""
    return amplitude * (hash_u64(*parts) / _TWO_64)
</content>

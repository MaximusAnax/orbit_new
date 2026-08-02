"""Identifier minting (SCOPE.md D17).

Ids are 26-character ULIDs minted at the edges by an injected factory; the
engine treats them as opaque strings and orders by them only for tie-breaks.
Because ULIDs sort by creation time, those tie-breaks are stable and
meaningful.  Tests inject the deterministic sequence factory.
"""

from __future__ import annotations

import os
import time
from typing import Protocol, runtime_checkable

#: Crockford base32 — no I, L, O or U, so ids survive transcription.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH = 26
_RANDOM_BITS = 80


@runtime_checkable
class IdFactory(Protocol):
    def new_id(self) -> str:  # pragma: no cover - protocol definition
        ...


def encode_crockford(value: int, length: int = ULID_LENGTH) -> str:
    """Big-endian Crockford base32 encoding, zero padded to ``length``."""
    if value < 0:
        raise ValueError("cannot encode a negative value")
    out: list[str] = []
    for _ in range(length):
        value, remainder = divmod(value, 32)
        out.append(CROCKFORD[remainder])
    if value:
        raise ValueError(f"value does not fit in {length} base32 characters")
    return "".join(reversed(out))


class UlidFactory:
    """Live factory: 48-bit millisecond timestamp + 80 bits of entropy."""

    def new_id(self) -> str:
        timestamp = int(time.time() * 1000) & ((1 << 48) - 1)
        randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
        return encode_crockford((timestamp << _RANDOM_BITS) | randomness)


class SequenceIdFactory:
    """Deterministic factory for tests and evals.

    Emits ULID-shaped, lexicographically increasing ids so that id tie-breaks
    behave exactly as they do in production.
    """

    def __init__(self, start: int = 0, prefix: str = "") -> None:
        self._counter = start
        self._prefix = prefix

    def new_id(self) -> str:
        value = self._counter
        self._counter += 1
        body = encode_crockford(value, ULID_LENGTH - len(self._prefix))
        return f"{self._prefix}{body}"

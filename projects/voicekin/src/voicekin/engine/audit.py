"""FR-12 tamper-evident audit chain: canonical JSON, hashing, verification.

A linear hash chain in the spirit of RFC 6962's transparency logs, reduced to a
single-writer chain. Records carry hashes of audio, never audio, so FR-7 erasure
and chain verification coexist.

Stated limit (SCOPE FR-12, EVALS M5 case 7): the chain is unkeyed, so an
adversary with database write access who truncates the tail and re-appends
correctly chained records produces a log that verifies clean. Anchoring the
``head_hash`` this module reports is the entire mitigation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from voicekin.models import AuditEvent, AuditRecord

GENESIS_PREV_HASH = hashlib.sha256(b"voicekin-genesis").hexdigest()
FLOAT_PLACES = 6
"""Floats are rounded before canonicalization so chain hashes are platform-stable."""


def round_floats(value: Any, places: int = FLOAT_PLACES) -> Any:
    """Recursively round floats and normalize ``-0.0`` to ``0.0``."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        rounded = round(float(value), places)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, Mapping):
        return {str(k): round_floats(v, places) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(v, places) for v in value]
    return value


def _fallback(value: Any) -> Any:
    """Serialize the few non-JSON scalars that reach canonical JSON."""
    if hasattr(value, "item"):  # numpy scalars
        return round_floats(value.item())
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not canonically serializable")


def canonical_json(obj: Any) -> bytes:
    """The FR-12 canonical encoding: sorted keys, tight separators, UTF-8."""
    return json.dumps(
        round_floats(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_fallback,
    ).encode("utf-8")


def compute_record_hash(
    *,
    seq: int,
    ts: str,
    event: AuditEvent | str,
    profile_id: str | None,
    consent_id: str | None,
    utterance_id: str | None,
    detail: Mapping[str, Any],
    prev_hash: str,
) -> str:
    """``sha256(canonical_json(fields) ‖ prev_hash)`` (FR-12)."""
    payload = canonical_json(
        {
            "seq": seq,
            "ts": ts,
            "event": str(event),
            "profile_id": profile_id,
            "consent_id": consent_id,
            "utterance_id": utterance_id,
            "detail": dict(detail),
        }
    )
    return hashlib.sha256(payload + prev_hash.encode("ascii")).hexdigest()


def next_link(head: AuditRecord | None) -> tuple[int, str]:
    """``(seq, prev_hash)`` for the record that would follow ``head``."""
    if head is None:
        return 1, GENESIS_PREV_HASH
    return head.seq + 1, head.record_hash


def build_record(
    head: AuditRecord | None,
    *,
    ts: str,
    event: AuditEvent,
    profile_id: str | None = None,
    consent_id: str | None = None,
    utterance_id: str | None = None,
    detail: Mapping[str, Any] | None = None,
) -> AuditRecord:
    """Link a new record onto ``head`` (or onto genesis when the log is empty)."""
    seq, prev_hash = next_link(head)
    payload = round_floats(dict(detail or {}))
    return AuditRecord(
        seq=seq,
        ts=ts,
        event=event,
        profile_id=profile_id,
        consent_id=consent_id,
        utterance_id=utterance_id,
        detail=payload,
        prev_hash=prev_hash,
        record_hash=compute_record_hash(
            seq=seq,
            ts=ts,
            event=event,
            profile_id=profile_id,
            consent_id=consent_id,
            utterance_id=utterance_id,
            detail=payload,
            prev_hash=prev_hash,
        ),
    )


@dataclass(frozen=True)
class ChainVerification:
    """Outcome of walking the chain from genesis (FR-12)."""

    ok: bool
    head_seq: int
    head_hash: str
    bad_seq: int | None = None
    problem: str | None = None


def verify_chain(records: Iterable[AuditRecord]) -> ChainVerification:
    """Recompute the chain from genesis and report the first divergent ``seq``.

    Detects content edits, timestamp edits, deletions, reorderings and rehashes.
    It cannot detect a tail truncation that is re-appended with a correctly
    recomputed chain — see the module docstring.
    """
    ordered: Sequence[AuditRecord] = list(records)
    prev_hash = GENESIS_PREV_HASH
    expected_seq = 1
    for record in ordered:
        if record.seq != expected_seq:
            return ChainVerification(
                ok=False,
                head_seq=expected_seq - 1,
                head_hash=prev_hash,
                bad_seq=record.seq,
                problem=f"expected seq {expected_seq}, found {record.seq}",
            )
        if record.prev_hash != prev_hash:
            return ChainVerification(
                ok=False,
                head_seq=expected_seq - 1,
                head_hash=prev_hash,
                bad_seq=record.seq,
                problem="prev_hash does not match the preceding record",
            )
        recomputed = compute_record_hash(
            seq=record.seq,
            ts=record.ts,
            event=record.event,
            profile_id=record.profile_id,
            consent_id=record.consent_id,
            utterance_id=record.utterance_id,
            detail=record.detail,
            prev_hash=record.prev_hash,
        )
        if recomputed != record.record_hash:
            return ChainVerification(
                ok=False,
                head_seq=expected_seq - 1,
                head_hash=prev_hash,
                bad_seq=record.seq,
                problem="record_hash does not match the record's contents",
            )
        prev_hash = record.record_hash
        expected_seq += 1
    return ChainVerification(ok=True, head_seq=expected_seq - 1, head_hash=prev_hash)


__all__ = [
    "FLOAT_PLACES",
    "GENESIS_PREV_HASH",
    "ChainVerification",
    "build_record",
    "canonical_json",
    "compute_record_hash",
    "next_link",
    "round_floats",
    "verify_chain",
]

"""FR-15 derived identifiers and consent nonces.

Nothing in VoiceKin is randomly identified: every row id is a sha256 prefix over
its own material, so replaying the same ordered operation sequence against an
empty data home reproduces identical ids, identical audio and an identical audit
chain. The single entropy draw in production is the ``consent draft``
``nonce_seed``, which is persisted and from which the nonce is derived.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from typing import Any

from voicekin.engine.audit import canonical_json

ID_LENGTH = 32
NONCE_LENGTH = 8
NONCE_DOMAIN = b"voicekin-nonce"


def derive_id(material: Sequence[Any]) -> str:
    """``sha256(canonical_json(id_material))[:32]`` — 32 lowercase hex chars."""
    return hashlib.sha256(canonical_json(list(material))).hexdigest()[:ID_LENGTH]


def sample_id(profile_id: str, payload_sha256: str, added_at: str, sample_index: int) -> str:
    return derive_id(["sample", profile_id, payload_sha256, added_at, sample_index])


def consent_id(profile_id: str, drafted_at: str, draft_index: int) -> str:
    return derive_id(["consent", profile_id, drafted_at, draft_index])


def utterance_id(
    profile_id: str, text: str, context: str, requested_at: str, attempt_index: int
) -> str:
    return derive_id(["utterance", profile_id, text, str(context), requested_at, attempt_index])


def delivery_id(
    utterance_id_value: str, target_id: str, requested_at: str, delivery_index: int
) -> str:
    return derive_id(["delivery", utterance_id_value, target_id, requested_at, delivery_index])


def consent_nonce(consent_id_value: str, nonce_seed: int) -> str:
    """``base32(sha256("voicekin-nonce" ‖ consent_id ‖ nonce_seed))[:8]`` (FR-15)."""
    digest = hashlib.sha256(
        NONCE_DOMAIN + consent_id_value.encode("ascii") + str(int(nonce_seed)).encode("ascii")
    ).digest()
    return base64.b32encode(digest).decode("ascii")[:NONCE_LENGTH]


__all__ = [
    "ID_LENGTH",
    "NONCE_DOMAIN",
    "NONCE_LENGTH",
    "consent_id",
    "consent_nonce",
    "delivery_id",
    "derive_id",
    "sample_id",
    "utterance_id",
]

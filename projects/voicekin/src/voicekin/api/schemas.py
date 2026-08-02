"""Pydantic request/response schemas for the FR-13 automation surface.

Profile responses deliberately exclude the biometric-derived vectors
(``centroid``, ``voice_params``, per-sample embeddings): the API reads state and
authorizes actions, it never ships voiceprints — a voiceprint that never crosses
a socket is one nobody has to protect in transit.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voicekin.models import (
    ConsentRejectReason,
    ConsentStatus,
    Context,
    DeliveryStatus,
    IsoTs,
    ProfileStatus,
    RefusalReason,
    Sha256,
    UtteranceStatus,
)

# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


class RevokeRequest(BaseModel):
    reason: str | None = None
    now: IsoTs | None = None


class SynthesizeRequest(BaseModel):
    profile_id: str
    text: str = Field(min_length=1)
    context: Context
    seed: int = 0
    now: IsoTs | None = None


class DeliverRequest(BaseModel):
    target_id: str
    now: IsoTs | None = None


class VerifyOutputRequest(BaseModel):
    output_sha256: Sha256


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class HealthOut(BaseModel):
    status: str = "ok"
    initialized: bool
    operator_name: str | None = None


class ProfileOut(BaseModel):
    id: str
    display_name: str
    relationship: str
    status: ProfileStatus
    enabled: bool
    embedder_id: str | None
    enrollment_fingerprint: str | None
    enrolled_at: str | None
    awaiting_consent: bool
    consent_status: ConsentStatus | None
    created_at: str
    updated_at: str


class ConsentOut(BaseModel):
    id: str
    profile_id: str
    draft_index: int
    status: ConsentStatus
    scope_contexts: list[Context]
    expires_at: str | None
    nonce: str
    statement_text: str
    audio_sha256: str | None
    similarity: float | None
    threshold: float | None
    embedder_id: str | None
    enrollment_fingerprint: str | None
    reject_reason: ConsentRejectReason | None
    drafted_at: str
    decided_at: str | None
    revoked_at: str | None
    revocation_reason: str | None


class UtteranceOut(BaseModel):
    id: str
    profile_id: str
    attempt_index: int
    consent_id: str | None
    text: str
    text_raw: str
    context: Context
    status: UtteranceStatus
    refusal_reason: RefusalReason | None
    synth_id: str | None
    seed: int | None
    output_sha256: str | None
    duration_s: float | None
    requested_at: str


class DeliveryOut(BaseModel):
    id: str
    utterance_id: str
    delivery_index: int
    target_id: str
    status: DeliveryStatus
    refusal_reason: RefusalReason | None
    detail: dict[str, Any]
    requested_at: str


class AuditRecordOut(BaseModel):
    seq: int
    ts: str
    event: str
    profile_id: str | None
    consent_id: str | None
    utterance_id: str | None
    detail: dict[str, Any]
    prev_hash: str
    record_hash: str


class AuditVerifyOut(BaseModel):
    ok: bool
    head_seq: int
    head_hash: str
    bad_seq: int | None = None
    problem: str | None = None


class ProvenanceOut(BaseModel):
    utterance: UtteranceOut
    profile_id: str
    profile_display_name: str
    audit_records: list[AuditRecordOut]


class RefusalOut(BaseModel):
    """The FR-13 error body for a 403: the gate's exact reason."""

    error: str = "refused"
    reason: RefusalReason
    utterance_id: str | None = None
    delivery_id: str | None = None


__all__ = [
    "AuditRecordOut",
    "AuditVerifyOut",
    "ConsentOut",
    "DeliverRequest",
    "DeliveryOut",
    "HealthOut",
    "ProfileOut",
    "ProvenanceOut",
    "RefusalOut",
    "RevokeRequest",
    "SynthesizeRequest",
    "UtteranceOut",
    "VerifyOutputRequest",
]

"""Pydantic v2 domain models for VoiceKin.

Every entity, enumeration, and invariant here comes from ``docs/DATA_MODEL.md``.
Enumeration *order* is load-bearing: ``RefusalReason`` is the FR-6 evaluation
order and ``ConsentRejectReason`` is the FR-5(b) check order.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# --------------------------------------------------------------------------- #
# Constrained scalar types
# --------------------------------------------------------------------------- #

RowId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
"""Derived row identifier: ``sha256(canonical_json(id_material))[:32]`` (FR-15)."""

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
"""Operator-supplied identifier for profiles and device targets."""

IsoTs = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"),
]
"""ISO-8601 timestamp supplied by the caller. The engine never reads the clock."""

Nonce = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{8}$")]
"""8 base32 characters derived from the consent id and the persisted nonce seed."""


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class ProfileStatus(StrEnum):
    ACTIVE = "active"
    PURGED = "purged"


class SampleStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SampleRejectReason(StrEnum):
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    CLIPPED = "clipped"
    LOW_SNR = "low_snr"
    LOW_VOICED_RATIO = "low_voiced_ratio"
    BAD_FORMAT = "bad_format"
    INCOHERENT_ENROLLMENT = "incoherent_enrollment"


class ConsentStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ConsentRejectReason(StrEnum):
    """Declaration order **is** the FR-5(b) check order."""

    NOT_ENROLLED = "not_enrolled"
    AUDIO_QUALITY = "audio_quality"
    REUSED_ENROLLMENT_AUDIO = "reused_enrollment_audio"
    SPEAKER_MISMATCH = "speaker_mismatch"


class Context(StrEnum):
    ANNOUNCEMENT = "announcement"
    REMINDER = "reminder"
    ALARM = "alarm"
    DOORBELL = "doorbell"
    TIMER = "timer"
    STATUS = "status"


class RefusalReason(StrEnum):
    """Declaration order **is** the FR-6 evaluation order."""

    PROFILE_PURGED = "profile_purged"
    PROFILE_DISABLED = "profile_disabled"
    NO_ENROLLMENT = "no_enrollment"
    NO_CONSENT = "no_consent"
    CONSENT_REJECTED = "consent_rejected"
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_EXPIRED = "consent_expired"
    ENROLLMENT_CHANGED = "enrollment_changed"
    SCOPE_MISMATCH = "scope_mismatch"


class UtteranceStatus(StrEnum):
    REFUSED = "refused"
    RENDERED = "rendered"


class DeliveryStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"


class TargetKind(StrEnum):
    FILE_SINK = "file_sink"
    HOME_ASSISTANT = "home_assistant"


class AuditEvent(StrEnum):
    PROFILE_CREATED = "profile_created"
    PROFILE_DISABLED = "profile_disabled"
    PROFILE_ENABLED = "profile_enabled"
    PROFILE_PURGED = "profile_purged"
    SAMPLE_ADDED = "sample_added"
    SAMPLE_REMOVED = "sample_removed"
    SAMPLE_REJECTED = "sample_rejected"
    CONSENT_DRAFTED = "consent_drafted"
    CONSENT_VERIFIED = "consent_verified"
    CONSENT_REJECTED = "consent_rejected"
    CONSENT_REVOKED = "consent_revoked"
    SYNTHESIS_AUTHORIZED = "synthesis_authorized"
    SYNTHESIS_REFUSED = "synthesis_refused"
    UTTERANCE_RENDERED = "utterance_rendered"
    DELIVERY_SUCCEEDED = "delivery_succeeded"
    DELIVERY_FAILED = "delivery_failed"
    DELIVERY_REFUSED = "delivery_refused"


#: The four consent reject reasons that short-circuit *before* an embedding is
#: computed. Records carrying one of them have null score fields (DATA_MODEL).
PRE_SCORING_CONSENT_REJECTIONS: frozenset[ConsentRejectReason] = frozenset(
    {
        ConsentRejectReason.NOT_ENROLLED,
        ConsentRejectReason.AUDIO_QUALITY,
        ConsentRejectReason.REUSED_ENROLLMENT_AUDIO,
    }
)


# --------------------------------------------------------------------------- #
# Committed dataset models (data/calibration.json)
# --------------------------------------------------------------------------- #

#: Dimensionality of the offline ``spectral-v1`` embedding (FR-4).
EMBEDDING_DIM = 16


class FeatureNorm(BaseModel):
    """Affine normalization constants for one embedding dimension (FR-4)."""

    model_config = ConfigDict(frozen=True)

    mean: float
    scale: float = Field(gt=0.0)


class ScreeningLimits(BaseModel):
    """FR-2 audio quality screening thresholds."""

    model_config = ConfigDict(frozen=True)

    enroll_min_duration_s: float = Field(gt=0.0)
    enroll_max_duration_s: float = Field(gt=0.0)
    consent_min_duration_s: float = Field(gt=0.0)
    consent_max_duration_s: float = Field(gt=0.0)
    clipping_level: float = Field(gt=0.0, le=1.0)
    max_clipping_fraction: float = Field(ge=0.0, le=1.0)
    min_snr_db: float
    min_voiced_ratio: float = Field(ge=0.0, le=1.0)
    voiced_nac_threshold: float = Field(ge=0.0, le=1.0)
    voiced_energy_margin_db: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _durations_ordered(self) -> ScreeningLimits:
        if self.enroll_min_duration_s >= self.enroll_max_duration_s:
            raise ValueError("enroll_min_duration_s must be < enroll_max_duration_s")
        if self.consent_min_duration_s >= self.consent_max_duration_s:
            raise ValueError("consent_min_duration_s must be < consent_max_duration_s")
        return self


class Calibration(BaseModel):
    """``data/calibration.json`` — every tunable decision constant (DATA_MODEL)."""

    model_config = ConfigDict(frozen=True)

    embedder_id: str
    theta_verify: float = Field(ge=-1.0, le=1.0)
    theta_enroll: float = Field(ge=-1.0, le=1.0)
    score_scale: float = Field(gt=0.0)
    feature_norms: list[FeatureNorm]
    screening: ScreeningLimits
    unit_duration_ms: int = Field(gt=0)
    consent_grace_days: int = Field(ge=0)
    provenance: str

    @model_validator(mode="after")
    def _norm_count(self) -> Calibration:
        if len(self.feature_norms) != EMBEDDING_DIM:
            raise ValueError(f"feature_norms must hold exactly {EMBEDDING_DIM} entries")
        return self


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #


class Instance(BaseModel):
    """Single-row instance record created by ``voicekin init`` (FR-1)."""

    model_config = ConfigDict(frozen=True)

    id: int = 1
    operator_name: str = Field(min_length=1, max_length=200)
    data_home: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    created_at: IsoTs

    @model_validator(mode="after")
    def _single_row(self) -> Instance:
        if self.id != 1:
            raise ValueError("instance.id must always be 1 (exactly one row)")
        return self


class VoiceParams(BaseModel):
    """Synthesizer parameters derived from the enrollment analysis (FR-3)."""

    model_config = ConfigDict(frozen=True)

    f0_base_hz: float = Field(gt=0.0)
    f0_range_hz: float = Field(ge=0.0)
    formant_scale: float = Field(gt=0.0)
    tilt_db_oct: float


class VoiceProfile(BaseModel):
    """A voice we may be asked to speak in."""

    id: Slug
    display_name: str = Field(min_length=1, max_length=200)
    relationship: str = ""
    status: ProfileStatus = ProfileStatus.ACTIVE
    enabled: bool = True
    embedder_id: str | None = None
    centroid: list[float] | None = None
    enrollment_fingerprint: Sha256 | None = None
    voice_params: VoiceParams | None = None
    enrolled_at: IsoTs | None = None
    next_sample_index: int = Field(default=0, ge=0)
    next_draft_index: int = Field(default=0, ge=0)
    next_attempt_index: int = Field(default=0, ge=0)
    created_at: IsoTs
    updated_at: IsoTs

    @model_validator(mode="after")
    def _check(self) -> VoiceProfile:
        if self.centroid is not None and len(self.centroid) == 0:
            raise ValueError("centroid must be non-empty when present")
        if self.centroid is not None and self.embedder_id is None:
            raise ValueError("a centroid requires the embedder_id that produced it")
        if self.status is ProfileStatus.PURGED:
            # FR-7(b,c): purge keeps the fingerprint (a hash) but nothing derived
            # from the audio itself.
            if self.centroid is not None or self.voice_params is not None:
                raise ValueError("a purged profile keeps no centroid or voice_params")
            if self.enrolled_at is not None:
                raise ValueError("a purged profile keeps no enrolled_at")
        return self

    @property
    def is_enrolled(self) -> bool:
        return self.enrollment_fingerprint is not None


class EnrollmentSample(BaseModel):
    """One screened WAV contributed to a profile's enrollment (FR-2/FR-3)."""

    id: RowId
    profile_id: Slug
    sample_index: int = Field(ge=0)
    path: str | None = None
    sha256: Sha256
    duration_s: float = Field(ge=0.0)
    snr_db: float
    voiced_ratio: float = Field(ge=0.0, le=1.0)
    embedding: list[float] | None = None
    status: SampleStatus
    reject_reason: SampleRejectReason | None = None
    added_at: IsoTs

    @model_validator(mode="after")
    def _check(self) -> EnrollmentSample:
        rejected = self.status is SampleStatus.REJECTED
        if rejected and self.reject_reason is None:
            raise ValueError("a rejected sample must carry a reject_reason")
        if not rejected and self.reject_reason is not None:
            raise ValueError("an accepted sample must not carry a reject_reason")
        if rejected and self.embedding is not None:
            raise ValueError("rejected samples never contribute an embedding")
        if rejected and self.path is not None:
            raise ValueError("rejected sample audio is not retained")
        return self

    @property
    def voiced_seconds(self) -> float:
        return self.duration_s * self.voiced_ratio


class ConsentRecord(BaseModel):
    """The consent artifact: a recording verified to be the voice owner's."""

    id: RowId
    profile_id: Slug
    draft_index: int = Field(ge=0)
    status: ConsentStatus
    scope_contexts: list[Context] = Field(min_length=1)
    expires_at: IsoTs | None = None
    nonce_seed: int = Field(ge=0)
    nonce: Nonce
    statement_text: str = Field(min_length=1)
    audio_path: str | None = None
    audio_sha256: Sha256 | None = None
    similarity: float | None = None
    threshold: float | None = None
    embedder_id: str | None = None
    enrollment_fingerprint: Sha256 | None = None
    reject_reason: ConsentRejectReason | None = None
    drafted_at: IsoTs
    decided_at: IsoTs | None = None
    revoked_at: IsoTs | None = None
    revocation_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ConsentRecord:
        if len(set(self.scope_contexts)) != len(self.scope_contexts):
            raise ValueError("scope_contexts must not repeat a context")

        rejected = self.status is ConsentStatus.REJECTED
        if rejected and self.reject_reason is None:
            raise ValueError("a rejected consent must carry a reject_reason")
        if not rejected and self.reject_reason is not None:
            raise ValueError("only a rejected consent carries a reject_reason")

        # Score fields are non-null exactly when a score was actually computed.
        scored = self.status in (ConsentStatus.VERIFIED, ConsentStatus.REVOKED) or (
            rejected and self.reject_reason is ConsentRejectReason.SPEAKER_MISMATCH
        )
        score_fields = (
            self.similarity,
            self.threshold,
            self.embedder_id,
            self.enrollment_fingerprint,
        )
        if scored and any(f is None for f in score_fields):
            raise ValueError(
                "similarity/threshold/embedder_id/enrollment_fingerprint are required "
                "whenever a speaker score was computed"
            )
        if not scored and any(f is not None for f in score_fields):
            raise ValueError(
                "similarity/threshold/embedder_id/enrollment_fingerprint must be null "
                "when no speaker score was computed"
            )

        # Audio retention: only verified/revoked records keep the recording.
        retains_audio = self.status in (ConsentStatus.VERIFIED, ConsentStatus.REVOKED)
        if self.audio_path is not None and not retains_audio:
            raise ValueError("rejected and draft consent recordings are never retained")

        pre_read = self.status is ConsentStatus.DRAFT or (
            rejected and self.reject_reason is ConsentRejectReason.NOT_ENROLLED
        )
        if pre_read and self.audio_sha256 is not None:
            raise ValueError("no recording has been read yet for this status")
        if not pre_read and self.audio_sha256 is None:
            raise ValueError("audio_sha256 is required once a recording has been read")

        if self.status is ConsentStatus.DRAFT and self.decided_at is not None:
            raise ValueError("a draft has not been decided")
        if self.status is not ConsentStatus.DRAFT and self.decided_at is None:
            raise ValueError("a decided consent must carry decided_at")

        revoked = self.status is ConsentStatus.REVOKED
        if revoked != (self.revoked_at is not None):
            raise ValueError("revoked_at is set exactly for revoked consents")
        if self.revocation_reason is not None and not revoked:
            raise ValueError("only a revoked consent carries a revocation_reason")
        return self


class Utterance(BaseModel):
    """One synthesis *attempt* — refusals included (DATA_MODEL)."""

    id: RowId
    profile_id: Slug
    attempt_index: int = Field(ge=0)
    consent_id: RowId | None = None
    text: str = Field(max_length=500)
    text_raw: str
    context: Context
    status: UtteranceStatus
    refusal_reason: RefusalReason | None = None
    synth_id: str | None = None
    seed: int | None = None
    next_delivery_index: int = Field(default=0, ge=0)
    output_path: str | None = None
    output_sha256: Sha256 | None = None
    duration_s: float | None = None
    requested_at: IsoTs

    @model_validator(mode="after")
    def _check(self) -> Utterance:
        refused = self.status is UtteranceStatus.REFUSED
        if refused != (self.refusal_reason is not None):
            raise ValueError("refusal_reason is set exactly for refused utterances")
        rendered_only = (self.synth_id, self.seed, self.duration_s, self.consent_id)
        if refused:
            if any(f is not None for f in rendered_only):
                raise ValueError("a refused utterance carries no synthesis facts")
            if self.output_sha256 is not None or self.output_path is not None:
                raise ValueError("a refused utterance produced no audio")
        else:
            if any(f is None for f in rendered_only):
                raise ValueError(
                    "consent_id/synth_id/seed/duration_s are required on a rendered utterance"
                )
            if self.output_sha256 is None:
                raise ValueError("a rendered utterance must record its output_sha256")
            # output_path may be null after purge; output_sha256 survives.
        return self


class Delivery(BaseModel):
    """A delivery receipt for a rendered utterance (FR-11)."""

    id: RowId
    utterance_id: RowId
    delivery_index: int = Field(ge=0)
    target_id: Slug
    status: DeliveryStatus
    refusal_reason: RefusalReason | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    requested_at: IsoTs

    @model_validator(mode="after")
    def _check(self) -> Delivery:
        refused = self.status is DeliveryStatus.REFUSED
        if refused != (self.refusal_reason is not None):
            raise ValueError("refusal_reason is set exactly for refused deliveries")
        return self


class DeviceTarget(BaseModel):
    """Where a rendered utterance can be sent (FR-11)."""

    id: Slug
    kind: TargetKind
    config: dict[str, Any]
    enabled: bool = True
    created_at: IsoTs

    @model_validator(mode="after")
    def _check(self) -> DeviceTarget:
        required = ("dir",) if self.kind is TargetKind.FILE_SINK else ("entity_id", "media_dir")
        missing = [k for k in required if not self.config.get(k)]
        if missing:
            raise ValueError(f"{self.kind} target requires config keys: {', '.join(missing)}")
        for secret in ("token", "url", "password"):
            if secret in self.config:
                raise ValueError("device target config must not hold credentials")
        return self


class AuditRecord(BaseModel):
    """One link of the append-only hash chain (FR-12). Immutable by construction."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    ts: IsoTs
    event: AuditEvent
    profile_id: str | None = None
    consent_id: str | None = None
    utterance_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    prev_hash: Sha256
    record_hash: Sha256


__all__ = [
    "EMBEDDING_DIM",
    "PRE_SCORING_CONSENT_REJECTIONS",
    "AuditEvent",
    "AuditRecord",
    "Calibration",
    "ConsentRecord",
    "ConsentRejectReason",
    "ConsentStatus",
    "Context",
    "Delivery",
    "DeliveryStatus",
    "DeviceTarget",
    "EnrollmentSample",
    "FeatureNorm",
    "Instance",
    "IsoTs",
    "Nonce",
    "ProfileStatus",
    "RefusalReason",
    "RowId",
    "SampleRejectReason",
    "SampleStatus",
    "ScreeningLimits",
    "Sha256",
    "Slug",
    "TargetKind",
    "Utterance",
    "UtteranceStatus",
    "VoiceParams",
    "VoiceProfile",
]

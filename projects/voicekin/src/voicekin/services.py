"""Orchestration: engine + adapters + store. All filesystem I/O lives here.

The engine below this layer is pure (FR-15). Every method takes ``now`` and any
seed explicitly, so replaying an operation sequence against an empty data home
reproduces identical row ids, identical rendered bytes and an identical audit
chain.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voicekin.adapters.deliver import DeliveryPayload, DeviceDeliverer
from voicekin.adapters.deliver_filesink import FileSinkDeliverer
from voicekin.adapters.embedder import SpeakerEmbedder
from voicekin.adapters.embedder_spectral import SpectralStatsEmbedder
from voicekin.adapters.synth import Synthesizer
from voicekin.adapters.synth_stub import FormantStubSynthesizer
from voicekin.engine import audit as audit_engine
from voicekin.engine import enrollment as enroll_engine
from voicekin.engine.audio import (
    TARGET_SAMPLE_RATE,
    AudioClip,
    AudioFormatError,
    normalize_intake,
    payload_sha256,
)
from voicekin.engine.consent import (
    Refusal,
    SynthesisRequest,
    authorize,
    awaiting_consent,
    governing_consent,
    is_effective,
    render_statement,
)
from voicekin.engine.dsp import analyze_frames, analyze_voice
from voicekin.engine.ids import consent_id, consent_nonce, delivery_id, sample_id, utterance_id
from voicekin.engine.quality import ClipKind, QualityReport, screen_clip
from voicekin.engine.synthesis import normalize_text, package_output
from voicekin.engine.verification import evaluate_consent_grant
from voicekin.models import (
    PRE_SCORING_CONSENT_REJECTIONS,
    AuditEvent,
    AuditRecord,
    Calibration,
    ConsentRecord,
    ConsentRejectReason,
    ConsentStatus,
    Context,
    Delivery,
    DeliveryStatus,
    DeviceTarget,
    EnrollmentSample,
    Instance,
    ProfileStatus,
    SampleRejectReason,
    SampleStatus,
    TargetKind,
    Utterance,
    UtteranceStatus,
    VoiceProfile,
)
from voicekin.store.base import SCHEMA_VERSION, Repository

DEFAULT_DATA_HOME = Path.home() / ".voicekin"
DATA_DIR_ENV = "VOICEKIN_DATA_DIR"
DB_FILENAME = "voicekin.db"

ENROLL_AUDIO_DIR = "audio/enroll"
CONSENT_AUDIO_DIR = "audio/consent"
OUTPUT_AUDIO_DIR = "audio/out"


class ServiceError(Exception):
    """A precondition the caller can fix (API 409, CLI exit 2)."""


class NotFoundError(ServiceError):
    """The referenced entity does not exist (API 404)."""


# --------------------------------------------------------------------------- #
# Committed dataset loading
# --------------------------------------------------------------------------- #


def default_data_dir() -> Path:
    """Where ``calibration.json`` and ``consent_statement.txt`` live."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data"


def load_calibration(path: Path | None = None) -> Calibration:
    source = path or (default_data_dir() / "calibration.json")
    return Calibration.model_validate_json(source.read_text(encoding="utf-8"))


def load_statement_template(path: Path | None = None) -> str:
    source = path or (default_data_dir() / "consent_statement.txt")
    return source.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- #
# Result values
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SampleIntake:
    """Per-file outcome of ``enroll`` (FR-2/FR-3)."""

    source: str
    accepted: bool
    sample: EnrollmentSample
    quality: QualityReport | None
    loo_similarity: float | None = None


@dataclass(frozen=True)
class SynthesisResult:
    """Either a rendered utterance or a persisted refusal (FR-6/FR-9)."""

    utterance: Utterance
    refusal: Refusal | None
    audit_seq: int

    @property
    def refused(self) -> bool:
        return self.refusal is not None


@dataclass(frozen=True)
class DeliveryResult:
    delivery: Delivery
    refusal: Refusal | None


@dataclass(frozen=True)
class ProfileView:
    """A profile plus the FR-1 display annotations."""

    profile: VoiceProfile
    awaiting_consent: bool
    consent_status: ConsentStatus | None


@dataclass(frozen=True)
class OutputProvenance:
    """FR-10 ``verify-output`` answer."""

    utterance: Utterance
    profile: VoiceProfile
    audit_records: list[AuditRecord]


@dataclass(frozen=True)
class PurgeReport:
    """FR-7 purge counts, exactly the ``profile_purged`` audit detail."""

    files_deleted: int = 0
    files_missing: int = 0
    files_failed: int = 0
    delivered_deleted: int = 0
    delivered_missing: int = 0
    delivered_failed: int = 0

    def as_detail(self) -> dict[str, int]:
        return {
            "files_deleted": self.files_deleted,
            "files_missing": self.files_missing,
            "files_failed": self.files_failed,
            "delivered_deleted": self.delivered_deleted,
            "delivered_missing": self.delivered_missing,
            "delivered_failed": self.delivered_failed,
        }


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class VoiceKinService:
    """Every operation the API and CLI expose, in one place."""

    def __init__(
        self,
        repository: Repository,
        data_home: Path,
        calibration: Calibration,
        statement_template: str,
        *,
        embedder: SpeakerEmbedder | None = None,
        synthesizer: Synthesizer | None = None,
        deliverers: Mapping[TargetKind, DeviceDeliverer] | None = None,
        sample_rate: int = TARGET_SAMPLE_RATE,
    ) -> None:
        self.repository = repository
        self.data_home = Path(data_home)
        self.calibration = calibration
        self.statement_template = statement_template
        self.embedder = embedder or SpectralStatsEmbedder(calibration)
        if self.embedder.embedder_id != calibration.embedder_id:
            raise ServiceError(
                f"calibration is for embedder {calibration.embedder_id!r} but "
                f"{self.embedder.embedder_id!r} is configured — refusing to score"
            )
        self.synthesizer = synthesizer or FormantStubSynthesizer(calibration.unit_duration_ms)
        self.deliverers: dict[TargetKind, DeviceDeliverer] = dict(
            deliverers or {TargetKind.FILE_SINK: FileSinkDeliverer()}
        )
        self.sample_rate = sample_rate

    # ------------------------------------------------------------------ #
    # Paths
    # ------------------------------------------------------------------ #

    def absolute(self, relative: str) -> Path:
        return self.data_home / relative

    def _write_managed(self, relative: str, payload: bytes) -> None:
        target = self.absolute(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    # ------------------------------------------------------------------ #
    # FR-1 instance & profiles
    # ------------------------------------------------------------------ #

    def initialize(self, operator_name: str, now: str) -> Instance:
        """``voicekin init``: data home, schema, and the single instance row."""
        self.repository.initialize()
        existing = self.repository.get_instance()
        if existing is not None:
            raise ServiceError(
                f"this data home is already initialized for {existing.operator_name}"
            )
        instance = Instance(
            operator_name=operator_name,
            data_home=str(self.data_home.resolve()),
            schema_version=SCHEMA_VERSION,
            created_at=now,
        )
        for folder in (ENROLL_AUDIO_DIR, CONSENT_AUDIO_DIR, OUTPUT_AUDIO_DIR):
            (self.data_home / folder).mkdir(parents=True, exist_ok=True)
        with self.repository.transaction():
            self.repository.put_instance(instance)
        return instance

    def require_instance(self) -> Instance:
        instance = self.repository.get_instance()
        if instance is None:
            raise ServiceError("this data home is not initialized — run `voicekin init` first")
        return instance

    def require_profile(self, profile_id: str) -> VoiceProfile:
        profile = self.repository.get_profile(profile_id)
        if profile is None:
            raise NotFoundError(f"no such voice profile: {profile_id}")
        return profile

    def create_profile(
        self, profile_id: str, display_name: str, relationship: str, now: str
    ) -> VoiceProfile:
        self.require_instance()
        if self.repository.get_profile(profile_id) is not None:
            raise ServiceError(f"voice profile {profile_id} already exists")
        profile = VoiceProfile(
            id=profile_id,
            display_name=display_name,
            relationship=relationship,
            created_at=now,
            updated_at=now,
        )
        with self.repository.transaction():
            self.repository.save_profile(profile)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.PROFILE_CREATED,
                profile_id=profile.id,
                detail={"display_name": display_name, "relationship": relationship},
            )
        return profile

    def list_profiles(self, now: str) -> list[ProfileView]:
        self.require_instance()
        views: list[ProfileView] = []
        for profile in self.repository.list_profiles():
            consents = self.repository.list_consents(profile.id)
            governing = governing_consent(consents)
            views.append(
                ProfileView(
                    profile=profile,
                    awaiting_consent=awaiting_consent(
                        profile, consents, now, self.calibration.consent_grace_days
                    ),
                    consent_status=governing.status if governing else None,
                )
            )
        return views

    def set_enabled(self, profile_id: str, enabled: bool, now: str) -> VoiceProfile:
        """FR-1 kill switch — independent of consent."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        if profile.status is ProfileStatus.PURGED:
            raise ServiceError("a purged profile cannot be re-enabled")
        updated = profile.model_copy(update={"enabled": enabled, "updated_at": now})
        with self.repository.transaction():
            self.repository.save_profile(updated)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.PROFILE_ENABLED if enabled else AuditEvent.PROFILE_DISABLED,
                profile_id=profile.id,
                detail={},
            )
        return updated

    # ------------------------------------------------------------------ #
    # FR-2/FR-3 enrollment
    # ------------------------------------------------------------------ #

    def _screen_bytes(
        self, raw: bytes, kind: ClipKind
    ) -> tuple[AudioClip | None, QualityReport | None]:
        try:
            clip = normalize_intake(raw)
        except AudioFormatError:
            return None, None
        frames = analyze_frames(
            clip,
            nac_threshold=self.calibration.screening.voiced_nac_threshold,
            energy_margin_db=self.calibration.screening.voiced_energy_margin_db,
        )
        return clip, screen_clip(clip, kind, self.calibration.screening, frames)

    def _recompute_enrollment(self, profile: VoiceProfile, now: str) -> VoiceProfile:
        """Re-derive centroid, fingerprint and voice params from the accepted set."""
        samples = enroll_engine.accepted_samples(self.repository.list_samples(profile.id))
        update: dict[str, Any] = {"updated_at": now}
        if enroll_engine.is_enrolled_complete(samples):
            embeddings = [s.embedding for s in samples if s.embedding is not None]
            update["centroid"] = enroll_engine.centroid(embeddings)
            update["embedder_id"] = self.embedder.embedder_id
            update["enrollment_fingerprint"] = enroll_engine.enrollment_fingerprint(
                self.embedder.embedder_id, [s.sha256 for s in samples]
            )
            update["voice_params"] = self._derive_voice_params(samples)
            if profile.enrolled_at is None:
                update["enrolled_at"] = now
        else:
            update["centroid"] = None
            update["enrollment_fingerprint"] = None
            update["voice_params"] = None
        updated = profile.model_copy(update=update)
        self.repository.save_profile(updated)
        return updated

    def _derive_voice_params(self, samples: Sequence[EnrollmentSample]):
        features = []
        for sample in samples:
            if sample.path is None:
                continue
            clip = normalize_intake(self.absolute(sample.path).read_bytes())
            features.append(analyze_voice(clip))
        if not features:
            raise ServiceError("enrollment audio is missing from the data home")
        return enroll_engine.derive_voice_params(features)

    def add_samples(
        self, profile_id: str, wav_paths: Sequence[Path], now: str
    ) -> list[SampleIntake]:
        """Screen, embed and enroll WAV files in order (FR-2/FR-3)."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        if profile.status is ProfileStatus.PURGED:
            raise ServiceError("a purged profile cannot be enrolled again")
        results: list[SampleIntake] = []
        for wav_path in wav_paths:
            results.append(self._add_one_sample(profile_id, Path(wav_path), now))
        return results

    def _reject_sample(
        self,
        profile: VoiceProfile,
        source: str,
        digest: str,
        reason: SampleRejectReason,
        report: QualityReport | None,
        now: str,
        extra_detail: Mapping[str, Any] | None = None,
    ) -> SampleIntake:
        index = profile.next_sample_index
        sample = EnrollmentSample(
            id=sample_id(profile.id, digest, now, index),
            profile_id=profile.id,
            sample_index=index,
            path=None,
            sha256=digest,
            duration_s=report.duration_s if report else 0.0,
            snr_db=report.snr_db if report else 0.0,
            voiced_ratio=report.voiced_ratio if report else 0.0,
            embedding=None,
            status=SampleStatus.REJECTED,
            reject_reason=reason,
            added_at=now,
        )
        detail: dict[str, Any] = {"sample_sha256": digest, "reason": str(reason)}
        detail.update(extra_detail or {})
        with self.repository.transaction():
            self.repository.save_sample(sample)
            # The rejected row consumes an index so ids stay unique, but nothing
            # derived from the accepted set moves (DATA_MODEL rollback rule).
            self.repository.save_profile(
                profile.model_copy(update={"next_sample_index": index + 1, "updated_at": now})
            )
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.SAMPLE_REJECTED,
                profile_id=profile.id,
                detail=detail,
            )
        return SampleIntake(source=source, accepted=False, sample=sample, quality=report)

    def _add_one_sample(self, profile_id: str, wav_path: Path, now: str) -> SampleIntake:
        profile = self.require_profile(profile_id)
        raw = wav_path.read_bytes()
        clip, report = self._screen_bytes(raw, ClipKind.ENROLLMENT)
        if clip is None or report is None:
            digest = hashlib.sha256(raw).hexdigest()
            return self._reject_sample(
                profile, str(wav_path), digest, SampleRejectReason.BAD_FORMAT, None, now
            )
        digest = clip.payload_sha256()
        if not report.ok:
            assert report.reason is not None
            return self._reject_sample(profile, str(wav_path), digest, report.reason, report, now)

        existing = enroll_engine.accepted_samples(self.repository.list_samples(profile.id))
        if any(s.sha256 == digest for s in existing):
            raise ServiceError(f"{wav_path} duplicates an enrollment sample already on file")

        embedding = self.embedder.embed(clip)
        candidate = [s.embedding for s in existing if s.embedding is not None] + [embedding]
        loo_similarity: float | None = None
        if len(candidate) >= 2:
            coherence = enroll_engine.check_coherence(
                candidate,
                self.calibration.theta_enroll,
                score_scale=self.calibration.score_scale,
            )
            loo_similarity = coherence.min_score
            if not coherence.coherent:
                intake = self._reject_sample(
                    profile,
                    str(wav_path),
                    digest,
                    SampleRejectReason.INCOHERENT_ENROLLMENT,
                    report,
                    now,
                    {
                        "loo_similarity": coherence.min_score,
                        "theta_enroll": self.calibration.theta_enroll,
                    },
                )
                return SampleIntake(
                    source=intake.source,
                    accepted=False,
                    sample=intake.sample,
                    quality=report,
                    loo_similarity=coherence.min_score,
                )

        index = profile.next_sample_index
        new_id = sample_id(profile.id, digest, now, index)
        relative = f"{ENROLL_AUDIO_DIR}/{profile.id}/{new_id}.wav"
        sample = EnrollmentSample(
            id=new_id,
            profile_id=profile.id,
            sample_index=index,
            path=relative,
            sha256=digest,
            duration_s=report.duration_s,
            snr_db=report.snr_db,
            voiced_ratio=report.voiced_ratio,
            embedding=embedding,
            status=SampleStatus.ACCEPTED,
            added_at=now,
        )
        self._write_managed(relative, raw)
        with self.repository.transaction():
            self.repository.save_sample(sample)
            advanced = profile.model_copy(
                update={"next_sample_index": index + 1, "updated_at": now}
            )
            self.repository.save_profile(advanced)
            updated = self._recompute_enrollment(advanced, now)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.SAMPLE_ADDED,
                profile_id=profile.id,
                detail={
                    "sample_sha256": digest,
                    "duration_s": report.duration_s,
                    "snr_db": report.snr_db,
                    "voiced_ratio": report.voiced_ratio,
                    "enrollment_fingerprint": updated.enrollment_fingerprint,
                },
            )
        return SampleIntake(
            source=str(wav_path),
            accepted=True,
            sample=sample,
            quality=report,
            loo_similarity=loo_similarity,
        )

    def remove_sample(self, profile_id: str, sample_id_value: str, now: str) -> VoiceProfile:
        """Hard-delete a sample and its file, then re-derive the enrollment (FR-3)."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        sample = self.repository.get_sample(sample_id_value)
        if sample is None or sample.profile_id != profile_id:
            raise NotFoundError(f"no such sample for {profile_id}: {sample_id_value}")
        if sample.path is not None:
            self.absolute(sample.path).unlink(missing_ok=True)
        with self.repository.transaction():
            self.repository.delete_sample(sample.id)
            updated = self._recompute_enrollment(profile, now)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.SAMPLE_REMOVED,
                profile_id=profile.id,
                detail={
                    "sample_sha256": sample.sha256,
                    "enrollment_fingerprint": updated.enrollment_fingerprint,
                },
            )
        return updated

    # ------------------------------------------------------------------ #
    # FR-5 consent
    # ------------------------------------------------------------------ #

    def draft_consent(
        self,
        profile_id: str,
        contexts: Sequence[Context],
        expires_at: str | None,
        nonce_seed: int,
        now: str,
    ) -> ConsentRecord:
        """FR-5(a). Refuses without creating a record when a precondition fails."""
        instance = self.require_instance()
        profile = self.require_profile(profile_id)
        if profile.status is ProfileStatus.PURGED:
            raise ServiceError("cannot draft consent for a purged profile")
        if not profile.enabled:
            raise ServiceError("cannot draft consent for a disabled profile")
        if profile.enrollment_fingerprint is None:
            raise ServiceError("cannot draft consent before the profile is enrolled")
        if not contexts:
            raise ServiceError("consent must name at least one context")

        consents = self.repository.list_consents(profile.id)
        governing = governing_consent(consents)
        if governing is not None and is_effective(governing, profile, now):
            raise ServiceError(
                "a consent is already effective for this profile — revoke it before drafting another"
            )

        index = profile.next_draft_index
        new_id = consent_id(profile.id, now, index)
        nonce = consent_nonce(new_id, nonce_seed)
        statement = render_statement(
            self.statement_template,
            owner_name=profile.display_name,
            operator=instance.operator_name,
            contexts=list(contexts),
            expires_at=expires_at,
            nonce=nonce,
            now=now,
        )
        record = ConsentRecord(
            id=new_id,
            profile_id=profile.id,
            draft_index=index,
            status=ConsentStatus.DRAFT,
            scope_contexts=list(contexts),
            expires_at=expires_at,
            nonce_seed=nonce_seed,
            nonce=nonce,
            statement_text=statement,
            drafted_at=now,
        )
        with self.repository.transaction():
            for existing in consents:
                if existing.status is ConsentStatus.DRAFT:
                    self.repository.delete_consent(existing.id)
            self.repository.save_consent(record)
            self.repository.save_profile(
                profile.model_copy(update={"next_draft_index": index + 1, "updated_at": now})
            )
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.CONSENT_DRAFTED,
                profile_id=profile.id,
                consent_id=record.id,
                detail={
                    "scope": [str(c) for c in contexts],
                    "expires_at": expires_at,
                    "nonce": nonce,
                    "nonce_seed": nonce_seed,
                    "statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
                },
            )
        return record

    def consent_statement(self, profile_id: str) -> str:
        """The exact text the voice owner must read aloud."""
        self.require_instance()
        self.require_profile(profile_id)
        for record in self.repository.list_consents(profile_id):
            if record.status is ConsentStatus.DRAFT:
                return record.statement_text
        raise ServiceError(f"no consent draft for {profile_id} — run `consent draft` first")

    def grant_consent(self, profile_id: str, wav_path: Path, now: str) -> ConsentRecord:
        """FR-5(b), in the documented check order."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        draft = next(
            (
                c
                for c in self.repository.list_consents(profile_id)
                if c.status is ConsentStatus.DRAFT
            ),
            None,
        )
        if draft is None:
            raise ServiceError(f"no consent draft for {profile_id} — run `consent draft` first")

        samples = enroll_engine.accepted_samples(self.repository.list_samples(profile.id))
        enrolled = enroll_engine.is_enrolled_complete(samples) and profile.centroid is not None

        clip: AudioClip | None = None
        report: QualityReport | None = None
        digest: str | None = None
        raw = b""
        if enrolled:
            raw = Path(wav_path).read_bytes()
            clip, report = self._screen_bytes(raw, ClipKind.CONSENT)
            digest = clip.payload_sha256() if clip is not None else None
            if clip is None:
                report = None

        def embed_probe():
            assert clip is not None
            return self.embedder.embed(clip)

        decision = evaluate_consent_grant(
            enrolled_complete=enrolled,
            quality=report,
            payload_sha256=digest,
            enrollment_sha256s={s.sha256 for s in samples},
            embed_probe=embed_probe,
            centroid=profile.centroid,
            theta_verify=self.calibration.theta_verify,
            score_scale=self.calibration.score_scale,
        )

        update: dict[str, Any] = {"decided_at": now, "audio_sha256": digest}
        detail: dict[str, Any] = {"audio_sha256": digest}
        if decision.verified:
            relative = f"{CONSENT_AUDIO_DIR}/{profile.id}/{draft.id}.wav"
            self._write_managed(relative, raw)
            update.update(
                status=ConsentStatus.VERIFIED,
                audio_path=relative,
                similarity=decision.similarity,
                threshold=decision.threshold,
                embedder_id=self.embedder.embedder_id,
                enrollment_fingerprint=profile.enrollment_fingerprint,
            )
            detail.update(
                similarity=decision.similarity,
                threshold=decision.threshold,
                embedder_id=self.embedder.embedder_id,
                enrollment_fingerprint=profile.enrollment_fingerprint,
            )
            event = AuditEvent.CONSENT_VERIFIED
        else:
            reason = decision.reject_reason
            assert reason is not None
            if reason is ConsentRejectReason.NOT_ENROLLED:
                update["audio_sha256"] = None
                detail["audio_sha256"] = None
            update.update(status=ConsentStatus.REJECTED, reject_reason=reason)
            detail["reason"] = str(reason)
            if reason not in PRE_SCORING_CONSENT_REJECTIONS:
                update.update(
                    similarity=decision.similarity,
                    threshold=decision.threshold,
                    embedder_id=self.embedder.embedder_id,
                    enrollment_fingerprint=profile.enrollment_fingerprint,
                )
                detail.update(
                    similarity=decision.similarity,
                    threshold=decision.threshold,
                    embedder_id=self.embedder.embedder_id,
                    enrollment_fingerprint=profile.enrollment_fingerprint,
                )
            event = AuditEvent.CONSENT_REJECTED

        record = draft.model_copy(update=update)
        with self.repository.transaction():
            self.repository.save_consent(record)
            self.repository.append_audit(
                ts=now,
                event=event,
                profile_id=profile.id,
                consent_id=record.id,
                detail=detail,
            )
        return record

    def revoke_consent(self, profile_id: str, reason: str | None, now: str) -> ConsentRecord:
        """FR-7 revocation. Idempotent: revoking twice writes one audit record."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        governing = governing_consent(self.repository.list_consents(profile.id))
        if governing is None:
            raise ServiceError(f"no consent record to revoke for {profile_id}")
        if governing.status is ConsentStatus.REVOKED:
            return governing
        if governing.status is not ConsentStatus.VERIFIED:
            raise ServiceError(
                f"the governing consent for {profile_id} is {governing.status}, not verified"
            )
        record = governing.model_copy(
            update={
                "status": ConsentStatus.REVOKED,
                "revoked_at": now,
                "revocation_reason": reason,
            }
        )
        with self.repository.transaction():
            self.repository.save_consent(record)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.CONSENT_REVOKED,
                profile_id=profile.id,
                consent_id=record.id,
                detail={"reason": reason},
            )
        return record

    # ------------------------------------------------------------------ #
    # FR-6/FR-9 synthesis
    # ------------------------------------------------------------------ #

    def synthesize(
        self, profile_id: str, text: str, context: Context, seed: int, now: str
    ) -> SynthesisResult:
        """Gate, then render. A refusal is persisted as an utterance row."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        normalized = normalize_text(text)
        consents = self.repository.list_consents(profile.id)
        request = SynthesisRequest(profile_id=profile.id, context=context)
        outcome = authorize(profile, consents, request, now)

        index = profile.next_attempt_index
        new_id = utterance_id(profile.id, normalized, str(context), now, index)
        advanced = profile.model_copy(update={"next_attempt_index": index + 1, "updated_at": now})

        if isinstance(outcome, Refusal):
            utterance = Utterance(
                id=new_id,
                profile_id=profile.id,
                attempt_index=index,
                text=normalized,
                text_raw=text,
                context=context,
                status=UtteranceStatus.REFUSED,
                refusal_reason=outcome.reason,
                requested_at=now,
            )
            with self.repository.transaction():
                self.repository.save_profile(advanced)
                self.repository.save_utterance(utterance)
                record = self.repository.append_audit(
                    ts=now,
                    event=AuditEvent.SYNTHESIS_REFUSED,
                    profile_id=profile.id,
                    utterance_id=utterance.id,
                    detail={"context": str(context), "reason": str(outcome.reason)},
                )
            return SynthesisResult(utterance=utterance, refusal=outcome, audit_seq=record.seq)

        governing = governing_consent(consents)
        assert governing is not None and profile.voice_params is not None
        clip = self.synthesizer.synthesize(
            normalized, profile.voice_params, sample_rate=self.sample_rate, seed=seed
        )
        wav_bytes, output_hash, duration_s = package_output(clip)
        relative = f"{OUTPUT_AUDIO_DIR}/{new_id}.wav"
        self._write_managed(relative, wav_bytes)
        utterance = Utterance(
            id=new_id,
            profile_id=profile.id,
            attempt_index=index,
            consent_id=outcome.consent_id,
            text=normalized,
            text_raw=text,
            context=context,
            status=UtteranceStatus.RENDERED,
            synth_id=self.synthesizer.synth_id,
            seed=seed,
            output_path=relative,
            output_sha256=output_hash,
            duration_s=duration_s,
            requested_at=now,
        )
        with self.repository.transaction():
            self.repository.save_profile(advanced)
            self.repository.save_utterance(utterance)
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.SYNTHESIS_AUTHORIZED,
                profile_id=profile.id,
                consent_id=outcome.consent_id,
                utterance_id=utterance.id,
                detail={
                    "context": str(context),
                    "scope": [str(c) for c in governing.scope_contexts],
                    "enrollment_fingerprint": outcome.enrollment_fingerprint,
                },
            )
            record = self.repository.append_audit(
                ts=now,
                event=AuditEvent.UTTERANCE_RENDERED,
                profile_id=profile.id,
                consent_id=outcome.consent_id,
                utterance_id=utterance.id,
                detail={
                    "synth_id": self.synthesizer.synth_id,
                    "seed": seed,
                    "output_sha256": output_hash,
                    "duration_s": duration_s,
                },
            )
        return SynthesisResult(utterance=utterance, refusal=None, audit_seq=record.seq)

    # ------------------------------------------------------------------ #
    # FR-11 delivery
    # ------------------------------------------------------------------ #

    def add_target(
        self, target_id: str, kind: TargetKind, config: Mapping[str, Any], now: str
    ) -> DeviceTarget:
        self.require_instance()
        target = DeviceTarget(id=target_id, kind=kind, config=dict(config), created_at=now)
        with self.repository.transaction():
            self.repository.save_target(target)
        return target

    def list_targets(self) -> list[DeviceTarget]:
        self.require_instance()
        return self.repository.list_targets()

    def deliver(self, utterance_id_value: str, target_id: str, now: str) -> DeliveryResult:
        """Re-authorize, then hand the WAV to the target (FR-6/FR-11)."""
        self.require_instance()
        utterance = self.repository.get_utterance(utterance_id_value)
        if utterance is None:
            raise NotFoundError(f"no such utterance: {utterance_id_value}")
        if utterance.status is not UtteranceStatus.RENDERED:
            raise ServiceError("only a rendered utterance can be delivered")
        target = self.repository.get_target(target_id)
        if target is None:
            raise NotFoundError(f"no such device target: {target_id}")
        if not target.enabled:
            raise ServiceError(f"device target {target_id} is disabled")

        profile = self.require_profile(utterance.profile_id)
        consents = self.repository.list_consents(profile.id)
        outcome = authorize(
            profile,
            consents,
            SynthesisRequest(profile_id=profile.id, context=utterance.context),
            now,
        )
        index = utterance.next_delivery_index
        new_id = delivery_id(utterance.id, target.id, now, index)
        advanced = utterance.model_copy(update={"next_delivery_index": index + 1})

        if isinstance(outcome, Refusal):
            delivery = Delivery(
                id=new_id,
                utterance_id=utterance.id,
                delivery_index=index,
                target_id=target.id,
                status=DeliveryStatus.REFUSED,
                refusal_reason=outcome.reason,
                detail={"reason": str(outcome.reason)},
                requested_at=now,
            )
            with self.repository.transaction():
                self.repository.save_utterance(advanced)
                self.repository.save_delivery(delivery)
                self.repository.append_audit(
                    ts=now,
                    event=AuditEvent.DELIVERY_REFUSED,
                    profile_id=profile.id,
                    utterance_id=utterance.id,
                    detail={
                        "target_id": target.id,
                        "target_kind": str(target.kind),
                        "output_sha256": utterance.output_sha256,
                        "status": str(DeliveryStatus.REFUSED),
                        "reason": str(outcome.reason),
                    },
                )
            return DeliveryResult(delivery=delivery, refusal=outcome)

        deliverer = self.deliverers.get(target.kind)
        if deliverer is None:
            raise ServiceError(f"no deliverer configured for {target.kind} targets")
        if utterance.output_path is None:
            raise ServiceError("this utterance's audio has been purged")
        wav_bytes = self.absolute(utterance.output_path).read_bytes()
        rendered_records = [
            r
            for r in self.repository.list_audit_for_utterance(utterance.id)
            if r.event is AuditEvent.UTTERANCE_RENDERED
        ]
        payload = DeliveryPayload(
            utterance_id=utterance.id,
            wav_bytes=wav_bytes,
            output_sha256=utterance.output_sha256 or "",
            manifest={
                "utterance_id": utterance.id,
                "profile_id": profile.id,
                "consent_id": utterance.consent_id,
                "output_sha256": utterance.output_sha256,
                "audit_seq": rendered_records[-1].seq if rendered_records else None,
                "rendered_at": utterance.requested_at,
            },
        )
        receipt = deliverer.deliver(payload, target, authorization=outcome)
        delivery = Delivery(
            id=new_id,
            utterance_id=utterance.id,
            delivery_index=index,
            target_id=target.id,
            status=receipt.status,
            detail=dict(receipt.detail),
            requested_at=now,
        )
        event = (
            AuditEvent.DELIVERY_SUCCEEDED
            if receipt.status is DeliveryStatus.SUCCEEDED
            else AuditEvent.DELIVERY_FAILED
        )
        detail: dict[str, Any] = {
            "target_id": target.id,
            "target_kind": str(target.kind),
            "output_sha256": utterance.output_sha256,
            "status": str(receipt.status),
        }
        if receipt.status is DeliveryStatus.FAILED:
            detail["error"] = receipt.detail.get("error")
        with self.repository.transaction():
            self.repository.save_utterance(advanced)
            self.repository.save_delivery(delivery)
            self.repository.append_audit(
                ts=now,
                event=event,
                profile_id=profile.id,
                utterance_id=utterance.id,
                detail=detail,
            )
        return DeliveryResult(delivery=delivery, refusal=None)

    # ------------------------------------------------------------------ #
    # FR-7 purge
    # ------------------------------------------------------------------ #

    def purge_profile(self, profile_id: str, now: str) -> PurgeReport:
        """Terminal erasure of everything VoiceKin controls (FR-7)."""
        self.require_instance()
        profile = self.require_profile(profile_id)
        if profile.status is ProfileStatus.PURGED:
            raise ServiceError(f"{profile_id} is already purged")

        deleted = missing = failed = 0
        samples = self.repository.list_samples(profile.id)
        consents = self.repository.list_consents(profile.id)
        utterances = self.repository.list_utterances(profile.id)

        def erase(relative: str | None) -> None:
            nonlocal deleted, missing, failed
            if relative is None:
                return
            path = self.absolute(relative)
            try:
                if path.exists():
                    path.unlink()
                    deleted += 1
                else:
                    missing += 1
            except OSError:
                failed += 1

        governing = governing_consent(consents)
        with self.repository.transaction():
            # (a) revoke the governing consent if it is still live
            if (
                governing is not None
                and governing.status is ConsentStatus.VERIFIED
                and governing.revoked_at is None
            ):
                revoked = governing.model_copy(
                    update={
                        "status": ConsentStatus.REVOKED,
                        "revoked_at": now,
                        "revocation_reason": "profile purged",
                    }
                )
                self.repository.save_consent(revoked)
                self.repository.append_audit(
                    ts=now,
                    event=AuditEvent.CONSENT_REVOKED,
                    profile_id=profile.id,
                    consent_id=revoked.id,
                    detail={"reason": "profile purged"},
                )
                consents = self.repository.list_consents(profile.id)

            # (b)/(c) delete managed audio; keep hashes, drop embeddings
            for sample in samples:
                erase(sample.path)
                self.repository.save_sample(
                    sample.model_copy(update={"path": None, "embedding": None})
                    if sample.status is SampleStatus.ACCEPTED
                    else sample.model_copy(update={"path": None})
                )
            for consent in consents:
                erase(consent.audio_path)
                if consent.audio_path is not None:
                    self.repository.save_consent(consent.model_copy(update={"audio_path": None}))
            for utterance in utterances:
                erase(utterance.output_path)
                if utterance.output_path is not None:
                    self.repository.save_utterance(
                        utterance.model_copy(update={"output_path": None})
                    )

            # (d) best-effort erasure of delivered copies
            delivered = self._purge_delivered(profile.id)

            # (e) terminal status
            purged = profile.model_copy(
                update={
                    "status": ProfileStatus.PURGED,
                    "enabled": False,
                    "centroid": None,
                    "voice_params": None,
                    "enrolled_at": None,
                    "updated_at": now,
                }
            )
            self.repository.save_profile(purged)
            report = PurgeReport(
                files_deleted=deleted,
                files_missing=missing,
                files_failed=failed,
                delivered_deleted=delivered[0],
                delivered_missing=delivered[1],
                delivered_failed=delivered[2],
            )
            self.repository.append_audit(
                ts=now,
                event=AuditEvent.PROFILE_PURGED,
                profile_id=profile.id,
                detail={
                    **report.as_detail(),
                    "enrollment_fingerprint": profile.enrollment_fingerprint,
                },
            )
        return report

    def _purge_delivered(self, profile_id: str) -> tuple[int, int, int]:
        """FR-7(d): delete delivered copies VoiceKin itself wrote, and only those."""
        deleted = missing = failed = 0
        for delivery in self.repository.list_profile_deliveries(profile_id):
            paths = [k for k in ("path", "manifest_path") if delivery.detail.get(k)]
            if not paths:
                continue
            detail = dict(delivery.detail)
            utterance = self.repository.get_utterance(delivery.utterance_id)
            expected = utterance.output_sha256 if utterance else None
            for key in paths:
                path = Path(str(detail.pop(key)))
                detail[f"{key}_sha256"] = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
                try:
                    if not path.exists():
                        missing += 1
                        continue
                    # Never delete a file VoiceKin did not write.
                    if key == "path" and (
                        expected is None or payload_sha256(path.read_bytes()) != expected
                    ):
                        missing += 1
                        continue
                    path.unlink()
                    deleted += 1
                except (OSError, AudioFormatError):
                    failed += 1
            self.repository.save_delivery(delivery.model_copy(update={"detail": detail}))
        return deleted, missing, failed

    # ------------------------------------------------------------------ #
    # FR-10/FR-12 provenance and audit
    # ------------------------------------------------------------------ #

    def verify_output(self, wav_bytes: bytes) -> OutputProvenance | None:
        """Resolve a WAV to its utterance by PCM-payload hash (FR-10)."""
        self.require_instance()
        digest = payload_sha256(wav_bytes)
        utterance = self.repository.find_utterance_by_output_sha256(digest)
        if utterance is None:
            return None
        profile = self.require_profile(utterance.profile_id)
        return OutputProvenance(
            utterance=utterance,
            profile=profile,
            audit_records=self.repository.list_audit_for_utterance(utterance.id),
        )

    def read_manifest(self, path: Path) -> dict[str, Any]:
        """Read a delivered sidecar manifest (FR-10)."""
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def list_audit(self, since_seq: int = 0) -> list[AuditRecord]:
        self.require_instance()
        return self.repository.list_audit(since_seq)

    def verify_audit(self) -> audit_engine.ChainVerification:
        """Walk the chain from genesis (FR-12)."""
        self.require_instance()
        return audit_engine.verify_chain(self.repository.iter_audit())


__all__ = [
    "CONSENT_AUDIO_DIR",
    "DB_FILENAME",
    "DEFAULT_DATA_HOME",
    "ENROLL_AUDIO_DIR",
    "OUTPUT_AUDIO_DIR",
    "DeliveryResult",
    "NotFoundError",
    "OutputProvenance",
    "ProfileView",
    "PurgeReport",
    "SampleIntake",
    "ServiceError",
    "SynthesisResult",
    "VoiceKinService",
    "default_data_dir",
    "load_calibration",
    "load_statement_template",
]

"""FR-13: the FastAPI automation surface — 12 routes, no file upload.

Thin by design (CONVENTIONS): every handler validates, calls one service
method, and serializes. The error catalog is fixed:

* gate refusals -> **403** with ``{"error": "refused", "reason": <RefusalReason>}``
  (the refusal is still persisted and audited by the service — FR-6);
* precondition failures (:class:`~voicekin.services.ServiceError`) -> **409**;
* unknown entities (:class:`~voicekin.services.NotFoundError`) -> **404**;
* ``POST /verify-output`` with an unknown hash -> **404** ``unknown_output``;
* malformed bodies -> FastAPI's standard **422**.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from voicekin import __version__
from voicekin.api.schemas import (
    AuditRecordOut,
    AuditVerifyOut,
    ConsentOut,
    DeliverRequest,
    DeliveryOut,
    HealthOut,
    ProfileOut,
    ProvenanceOut,
    RefusalOut,
    RevokeRequest,
    SynthesizeRequest,
    UtteranceOut,
    VerifyOutputRequest,
)
from voicekin.services import (
    NotFoundError,
    OutputProvenance,
    ProfileView,
    ServiceError,
    VoiceKinService,
)


def _profile_out(view: ProfileView) -> ProfileOut:
    profile = view.profile
    return ProfileOut(
        id=profile.id,
        display_name=profile.display_name,
        relationship=profile.relationship,
        status=profile.status,
        enabled=profile.enabled,
        embedder_id=profile.embedder_id,
        enrollment_fingerprint=profile.enrollment_fingerprint,
        enrolled_at=profile.enrolled_at,
        awaiting_consent=view.awaiting_consent,
        consent_status=view.consent_status,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _provenance_out(provenance: OutputProvenance) -> ProvenanceOut:
    return ProvenanceOut(
        utterance=UtteranceOut.model_validate(provenance.utterance, from_attributes=True),
        profile_id=provenance.profile.id,
        profile_display_name=provenance.profile.display_name,
        audit_records=[
            AuditRecordOut.model_validate(record, from_attributes=True)
            for record in provenance.audit_records
        ],
    )


def create_app(
    service: VoiceKinService | None = None, *, data_home: Path | str | None = None
) -> FastAPI:
    """Build the app around an existing service (tests) or a data home (deploy)."""
    if service is None:
        from voicekin.bootstrap import build_service

        service = build_service(data_home)

    app = FastAPI(
        title="VoiceKin",
        version=__version__,
        description=(
            "Consent-gated personal voice synthesis for the smart home. "
            "This is the automation surface: it reads state, synthesizes, "
            "delivers, revokes and audits. Enrollment and consent recordings "
            "are handled by the CLI with WAV files in hand — there is no file "
            "upload here by design."
        ),
    )

    def _now(value: str | None) -> str:
        if value is not None:
            return value
        from voicekin.bootstrap import now_utc

        return now_utc()

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

    @app.exception_handler(ServiceError)
    async def _precondition(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "precondition_failed", "message": str(exc)}
        )

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #

    @app.get("/health", response_model=HealthOut)
    def health() -> HealthOut:
        instance = service.repository.get_instance()
        return HealthOut(
            initialized=instance is not None,
            operator_name=instance.operator_name if instance else None,
        )

    @app.get("/profiles", response_model=list[ProfileOut])
    def list_profiles(now: str | None = None) -> list[ProfileOut]:
        return [_profile_out(view) for view in service.list_profiles(_now(now))]

    @app.get("/profiles/{profile_id}", response_model=ProfileOut)
    def get_profile(profile_id: str, now: str | None = None) -> ProfileOut:
        return _profile_out(service.profile_view(profile_id, _now(now)))

    @app.get("/profiles/{profile_id}/consents", response_model=list[ConsentOut])
    def list_consents(profile_id: str) -> list[ConsentOut]:
        return [
            ConsentOut.model_validate(record, from_attributes=True)
            for record in service.list_consents(profile_id)
        ]

    # ------------------------------------------------------------------ #
    # Act
    # ------------------------------------------------------------------ #

    @app.post("/consents/{consent_id}/revoke", response_model=ConsentOut)
    def revoke_consent(consent_id: str, body: RevokeRequest) -> ConsentOut:
        record = service.revoke_consent_by_id(consent_id, body.reason, _now(body.now))
        return ConsentOut.model_validate(record, from_attributes=True)

    @app.post(
        "/synthesize",
        response_model=UtteranceOut,
        responses={403: {"model": RefusalOut}},
    )
    def synthesize(body: SynthesizeRequest) -> UtteranceOut | JSONResponse:
        result = service.synthesize(
            body.profile_id, body.text, body.context, body.seed, _now(body.now)
        )
        if result.refused:
            assert result.refusal is not None
            payload = RefusalOut(
                reason=result.refusal.reason, utterance_id=result.utterance.id
            )
            return JSONResponse(status_code=403, content=payload.model_dump(mode="json"))
        return UtteranceOut.model_validate(result.utterance, from_attributes=True)

    @app.get("/utterances/{utterance_id}", response_model=UtteranceOut)
    def get_utterance(utterance_id: str) -> UtteranceOut:
        return UtteranceOut.model_validate(
            service.get_utterance(utterance_id), from_attributes=True
        )

    @app.get("/utterances/{utterance_id}/audio")
    def get_utterance_audio(utterance_id: str) -> Response:
        utterance = service.get_utterance(utterance_id)
        if utterance.output_path is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "audio_unavailable",
                    "message": "this utterance was refused or its audio was purged",
                },
            )
        wav_bytes = service.absolute(utterance.output_path).read_bytes()
        return Response(content=wav_bytes, media_type="audio/wav")

    @app.post(
        "/utterances/{utterance_id}/deliver",
        response_model=DeliveryOut,
        responses={403: {"model": RefusalOut}},
    )
    def deliver(utterance_id: str, body: DeliverRequest) -> DeliveryOut | JSONResponse:
        result = service.deliver(utterance_id, body.target_id, _now(body.now))
        if result.refusal is not None:
            payload = RefusalOut(
                reason=result.refusal.reason, delivery_id=result.delivery.id
            )
            return JSONResponse(status_code=403, content=payload.model_dump(mode="json"))
        return DeliveryOut.model_validate(result.delivery, from_attributes=True)

    # ------------------------------------------------------------------ #
    # Prove
    # ------------------------------------------------------------------ #

    @app.post(
        "/verify-output",
        response_model=ProvenanceOut,
        responses={404: {"description": "unknown_output"}},
    )
    def verify_output(body: VerifyOutputRequest) -> ProvenanceOut | JSONResponse:
        provenance = service.provenance_by_sha256(body.output_sha256)
        if provenance is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "unknown_output",
                    "message": "no rendered utterance matches this payload hash",
                },
            )
        return _provenance_out(provenance)

    @app.get("/audit", response_model=list[AuditRecordOut])
    def list_audit(since_seq: int = 0) -> list[AuditRecordOut]:
        return [
            AuditRecordOut.model_validate(record, from_attributes=True)
            for record in service.list_audit(since_seq)
        ]

    @app.get("/audit/verify", response_model=AuditVerifyOut)
    def verify_audit() -> AuditVerifyOut:
        outcome = service.verify_audit()
        return AuditVerifyOut(
            ok=outcome.ok,
            head_seq=outcome.head_seq,
            head_hash=outcome.head_hash,
            bad_seq=outcome.bad_seq,
            problem=outcome.problem,
        )

    return app


__all__ = ["create_app"]

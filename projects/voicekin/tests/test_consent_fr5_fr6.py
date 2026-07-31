"""FR-5 statement rendering and consent decision; FR-6 the authorization gate.

The gate is the safety-critical part of the product: every path that could lead
to unauthorized synthesis must be blocked with the exact documented reason, in
the exact documented precedence.
"""

from __future__ import annotations

import pytest
from voicekin.engine.consent import (
    Authorization,
    Refusal,
    SynthesisRequest,
    UnauthorizedConstruction,
    authorize,
    awaiting_consent,
    expiry_clause,
    format_contexts,
    governing_consent,
    is_effective,
    is_expired,
    iso_date,
    parse_ts,
    pluralize_context,
    render_statement,
)
from voicekin.engine.quality import ClipKind, QualityReport
from voicekin.engine.verification import ConsentDecision, cosine_similarity, evaluate_consent_grant
from voicekin.models import (
    ConsentRecord,
    ConsentRejectReason,
    ConsentStatus,
    Context,
    ProfileStatus,
    RefusalReason,
    SampleRejectReason,
    VoiceParams,
    VoiceProfile,
)

FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
T0 = "2026-07-31T12:00:00Z"
T1 = "2026-08-01T12:00:00Z"


def make_profile(**overrides) -> VoiceProfile:
    base = {
        "id": "partner",
        "display_name": "Amina",
        "relationship": "partner",
        "embedder_id": "spectral-v1",
        "centroid": [1.0] + [0.0] * 15,
        "enrollment_fingerprint": FINGERPRINT,
        "voice_params": VoiceParams(
            f0_base_hz=204.0, f0_range_hz=46.0, formant_scale=1.13, tilt_db_oct=-11.2
        ),
        "enrolled_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    base.update(overrides)
    return VoiceProfile(**base)


def make_consent(index: int = 0, **overrides) -> ConsentRecord:
    base = {
        "id": f"{index:032x}",
        "profile_id": "partner",
        "draft_index": index,
        "status": ConsentStatus.VERIFIED,
        "scope_contexts": [Context.ANNOUNCEMENT, Context.REMINDER],
        "nonce_seed": 4815162342,
        "nonce": "K3TQ7WZP",
        "statement_text": "I, Amina, consent ...",
        "audio_path": "audio/consent/partner/x.wav",
        "audio_sha256": "c" * 64,
        "similarity": 0.99,
        "threshold": 0.98,
        "embedder_id": "spectral-v1",
        "enrollment_fingerprint": FINGERPRINT,
        "drafted_at": T0,
        "decided_at": T0,
    }
    base.update(overrides)
    return ConsentRecord(**base)


def gate(profile, consents, context=Context.ANNOUNCEMENT, now=T1):
    return authorize(profile, consents, SynthesisRequest(profile.id, context), now)


# --------------------------------------------------------------------------- #
# FR-5(a) statement
# --------------------------------------------------------------------------- #


def test_fr5_statement_renders_every_placeholder(statement_template):
    text = render_statement(
        statement_template,
        owner_name="Amina",
        operator="Abdoul",
        contexts=[Context.ANNOUNCEMENT, Context.REMINDER],
        expires_at="2027-06-30T00:00:00Z",
        nonce="K3TQ7WZP",
        now="2026-07-31T18:08:19Z",
    )
    assert text == (
        "I, Amina, consent to the VoiceKin system operated by Abdoul reproducing my voice "
        "for: announcements, reminders. This consent expires on 2027-06-30 and I may revoke "
        "it at any time, effective immediately. Verification code: K3TQ7WZP. Date: 2026-07-31."
    )


def test_fr5_statement_without_expiry(statement_template):
    text = render_statement(
        statement_template,
        owner_name="Amina",
        operator="Abdoul",
        contexts=[Context.STATUS],
        expires_at=None,
        nonce="AAAAAAAA",
        now=T0,
    )
    assert "does not expire" in text
    assert "statuses" in text


def test_fr5_statement_template_must_carry_every_placeholder():
    with pytest.raises(ValueError, match="missing"):
        render_statement(
            "I, {owner_name}, consent.",
            owner_name="Amina",
            operator="Abdoul",
            contexts=[Context.ALARM],
            expires_at=None,
            nonce="AAAAAAAA",
            now=T0,
        )


def test_fr5_context_pluralization():
    assert pluralize_context(Context.ALARM) == "alarms"
    assert pluralize_context(Context.STATUS) == "statuses"
    assert format_contexts([Context.TIMER, Context.DOORBELL]) == "timers, doorbells"
    with pytest.raises(ValueError, match="at least one"):
        format_contexts([])


def test_fr5_expiry_clause_and_date_helpers():
    assert expiry_clause(None) == "does not expire"
    assert expiry_clause("2027-06-30T00:00:00Z") == "expires on 2027-06-30"
    assert iso_date("2026-07-31T23:59:59Z") == "2026-07-31"
    assert parse_ts("2026-07-31T12:00:00").isoformat() == "2026-07-31T12:00:00+00:00"


# --------------------------------------------------------------------------- #
# FR-5(b) grant decision order
# --------------------------------------------------------------------------- #


def _quality(ok: bool = True) -> QualityReport:
    return QualityReport(
        kind=ClipKind.CONSENT,
        duration_s=9.0,
        clipping_fraction=0.0,
        snr_db=25.0,
        voiced_ratio=0.7,
        reason=None if ok else SampleRejectReason.LOW_SNR,
    )


def _decide(**overrides) -> ConsentDecision:
    kwargs = {
        "enrolled_complete": True,
        "quality": _quality(),
        "payload_sha256": "d" * 64,
        "enrollment_sha256s": {"e" * 64},
        "embed_probe": lambda: [1.0] + [0.0] * 15,
        "centroid": [1.0] + [0.0] * 15,
        "theta_verify": 0.9,
    }
    kwargs.update(overrides)
    return evaluate_consent_grant(**kwargs)


def test_fr5_not_enrolled_short_circuits_before_any_score():
    decision = _decide(enrolled_complete=False)
    assert decision.reject_reason is ConsentRejectReason.NOT_ENROLLED
    assert decision.similarity is None and decision.threshold is None


def test_fr5_audio_quality_short_circuits_before_any_score():
    decision = _decide(quality=_quality(ok=False))
    assert decision.reject_reason is ConsentRejectReason.AUDIO_QUALITY
    assert decision.similarity is None


def test_fr5_replayed_enrollment_audio_is_rejected():
    decision = _decide(payload_sha256="e" * 64, enrollment_sha256s={"e" * 64})
    assert decision.reject_reason is ConsentRejectReason.REUSED_ENROLLMENT_AUDIO
    assert decision.similarity is None


def test_fr5_speaker_mismatch_records_the_score_it_computed():
    decision = _decide(embed_probe=lambda: [0.0, 1.0] + [0.0] * 14)
    assert decision.reject_reason is ConsentRejectReason.SPEAKER_MISMATCH
    assert decision.similarity == pytest.approx(0.0)
    assert decision.threshold == 0.9


def test_fr5_verification_at_the_threshold_accepts():
    decision = _decide(theta_verify=1.0)
    assert decision.verified and decision.similarity == pytest.approx(1.0)


def test_fr5_the_probe_is_not_embedded_when_an_earlier_check_fails():
    def explode():
        raise AssertionError("embed_probe must not run after a short circuit")

    assert _decide(enrolled_complete=False, embed_probe=explode).reject_reason is (
        ConsentRejectReason.NOT_ENROLLED
    )
    assert _decide(quality=_quality(ok=False), embed_probe=explode).reject_reason is (
        ConsentRejectReason.AUDIO_QUALITY
    )


def test_fr5_check_order_is_the_enum_order():
    """A recording that fails several checks reports the first one."""
    decision = _decide(enrolled_complete=False, quality=_quality(ok=False), payload_sha256="e" * 64)
    assert decision.reject_reason is ConsentRejectReason.NOT_ENROLLED
    decision = _decide(quality=_quality(ok=False), payload_sha256="e" * 64)
    assert decision.reject_reason is ConsentRejectReason.AUDIO_QUALITY


def test_fr5_cosine_similarity_rejects_shape_mismatch():
    with pytest.raises(ValueError, match="shape mismatch"):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# --------------------------------------------------------------------------- #
# FR-6 gate: each refusal reason
# --------------------------------------------------------------------------- #


def test_fr6_authorized_path_returns_a_capability():
    result = gate(make_profile(), [make_consent()])
    assert isinstance(result, Authorization)
    assert result.consent_id == f"{0:032x}"
    assert result.enrollment_fingerprint == FINGERPRINT
    assert result.issued_at == T1


def test_fr6_purged_profile():
    profile = make_profile(
        status=ProfileStatus.PURGED, centroid=None, voice_params=None, enrolled_at=None
    )
    assert gate(profile, [make_consent()]).reason is RefusalReason.PROFILE_PURGED


def test_fr6_disabled_profile():
    assert gate(make_profile(enabled=False), [make_consent()]).reason is (
        RefusalReason.PROFILE_DISABLED
    )


def test_fr6_no_enrollment():
    profile = make_profile(enrollment_fingerprint=None, centroid=None, voice_params=None)
    assert gate(profile, []).reason is RefusalReason.NO_ENROLLMENT


def test_fr6_no_consent_record_at_all():
    assert gate(make_profile(), []).reason is RefusalReason.NO_CONSENT


def test_fr6_draft_only_is_no_consent():
    draft = make_consent(
        status=ConsentStatus.DRAFT,
        audio_path=None,
        audio_sha256=None,
        similarity=None,
        threshold=None,
        embedder_id=None,
        enrollment_fingerprint=None,
        decided_at=None,
    )
    assert gate(make_profile(), [draft]).reason is RefusalReason.NO_CONSENT


def test_fr6_rejected_consent():
    rejected = make_consent(
        status=ConsentStatus.REJECTED,
        reject_reason=ConsentRejectReason.SPEAKER_MISMATCH,
        similarity=0.1,
        audio_path=None,
    )
    assert gate(make_profile(), [rejected]).reason is RefusalReason.CONSENT_REJECTED


def test_fr6_revoked_consent():
    revoked = make_consent(status=ConsentStatus.REVOKED, revoked_at=T0, revocation_reason="done")
    assert gate(make_profile(), [revoked]).reason is RefusalReason.CONSENT_REVOKED


def test_fr6_expiry_boundary_refuses():
    consent = make_consent(expires_at=T1)
    assert is_expired(consent, T1)
    assert gate(make_profile(), [consent], now=T1).reason is RefusalReason.CONSENT_EXPIRED
    assert isinstance(gate(make_profile(), [consent], now=T0), Authorization)


def test_fr6_after_expiry_refuses():
    consent = make_consent(expires_at=T0)
    assert gate(make_profile(), [consent], now=T1).reason is RefusalReason.CONSENT_EXPIRED


def test_fr6_enrollment_drift_refuses():
    consent = make_consent(enrollment_fingerprint=OTHER_FINGERPRINT)
    assert gate(make_profile(), [consent]).reason is RefusalReason.ENROLLMENT_CHANGED


def test_fr6_context_outside_scope_refuses():
    assert gate(make_profile(), [make_consent()], context=Context.ALARM).reason is (
        RefusalReason.SCOPE_MISMATCH
    )


# --------------------------------------------------------------------------- #
# FR-6 precedence and the governing record
# --------------------------------------------------------------------------- #


def test_fr6_precedence_disabled_beats_revoked():
    revoked = make_consent(status=ConsentStatus.REVOKED, revoked_at=T0)
    assert gate(make_profile(enabled=False), [revoked]).reason is RefusalReason.PROFILE_DISABLED


def test_fr6_precedence_purged_beats_revoked():
    profile = make_profile(
        status=ProfileStatus.PURGED,
        enabled=False,
        centroid=None,
        voice_params=None,
        enrolled_at=None,
    )
    revoked = make_consent(status=ConsentStatus.REVOKED, revoked_at=T0)
    assert gate(profile, [revoked]).reason is RefusalReason.PROFILE_PURGED


def test_fr6_precedence_expired_beats_enrollment_changed():
    consent = make_consent(expires_at=T0, enrollment_fingerprint=OTHER_FINGERPRINT)
    assert gate(make_profile(), [consent]).reason is RefusalReason.CONSENT_EXPIRED


def test_fr6_precedence_enrollment_changed_beats_scope_mismatch():
    consent = make_consent(enrollment_fingerprint=OTHER_FINGERPRINT)
    assert gate(make_profile(), [consent], context=Context.ALARM).reason is (
        RefusalReason.ENROLLMENT_CHANGED
    )


def test_fr6_only_the_governing_record_is_consulted():
    older_rejected = make_consent(
        0,
        status=ConsentStatus.REJECTED,
        reject_reason=ConsentRejectReason.SPEAKER_MISMATCH,
        similarity=0.2,
        audio_path=None,
        drafted_at="2026-07-01T00:00:00Z",
    )
    newer_revoked = make_consent(
        1, status=ConsentStatus.REVOKED, revoked_at=T0, drafted_at="2026-07-20T00:00:00Z"
    )
    assert governing_consent([older_rejected, newer_revoked]) is newer_revoked
    assert gate(make_profile(), [older_rejected, newer_revoked]).reason is (
        RefusalReason.CONSENT_REVOKED
    )


def test_fr6_governing_breaks_timestamp_ties_by_draft_index():
    first = make_consent(0, drafted_at=T0)
    second = make_consent(1, status=ConsentStatus.REVOKED, revoked_at=T0, drafted_at=T0)
    assert governing_consent([second, first]) is second
    assert gate(make_profile(), [first, second]).reason is RefusalReason.CONSENT_REVOKED


def test_fr6_older_revoked_newer_expired_reports_expiry():
    older = make_consent(
        0, status=ConsentStatus.REVOKED, revoked_at=T0, drafted_at="2026-07-01T00:00:00Z"
    )
    newer = make_consent(1, expires_at=T0, drafted_at="2026-07-20T00:00:00Z")
    assert gate(make_profile(), [older, newer]).reason is RefusalReason.CONSENT_EXPIRED


def test_fr6_superseded_consent_does_not_resurrect():
    """FR-6 governing rule: reverting an enrollment does not revive an old consent."""
    old = make_consent(0, drafted_at="2026-07-01T00:00:00Z")
    new = make_consent(
        1, drafted_at="2026-07-20T00:00:00Z", enrollment_fingerprint=OTHER_FINGERPRINT
    )
    assert gate(make_profile(), [old, new]).reason is RefusalReason.ENROLLMENT_CHANGED


def test_fr6_governing_of_nothing_is_none():
    assert governing_consent([]) is None


def test_fr6_request_must_match_the_profile():
    with pytest.raises(ValueError, match="does not match"):
        authorize(make_profile(), [], SynthesisRequest("someone-else", Context.ALARM), T1)


# --------------------------------------------------------------------------- #
# The capability itself
# --------------------------------------------------------------------------- #


def test_fr6_authorization_cannot_be_forged():
    with pytest.raises(UnauthorizedConstruction):
        Authorization(
            profile_id="partner",
            consent_id="c" * 32,
            context=Context.ANNOUNCEMENT,
            issued_at=T1,
            enrollment_fingerprint=FINGERPRINT,
        )


def test_fr6_refusal_carries_the_request_context():
    refusal = gate(make_profile(enabled=False), [make_consent()], context=Context.TIMER)
    assert isinstance(refusal, Refusal)
    assert refusal.context is Context.TIMER
    assert refusal.refused_at == T1
    assert refusal.profile_id == "partner"


# --------------------------------------------------------------------------- #
# Effectiveness and the FR-1 awaiting-consent flag
# --------------------------------------------------------------------------- #


def test_fr5_effective_predicate_matches_the_gate():
    profile = make_profile()
    assert is_effective(make_consent(), profile, T1)
    assert not is_effective(make_consent(expires_at=T0), profile, T1)
    assert not is_effective(make_consent(status=ConsentStatus.REVOKED, revoked_at=T0), profile, T1)
    assert not is_effective(make_consent(enrollment_fingerprint=OTHER_FINGERPRINT), profile, T1)


def test_fr1_awaiting_consent_flag_after_the_grace_window():
    profile = make_profile(enrolled_at=T0)
    assert not awaiting_consent(profile, [], "2026-08-20T12:00:00Z", 30)
    assert awaiting_consent(profile, [], "2026-09-15T12:00:00Z", 30)


def test_fr1_awaiting_consent_clears_once_consent_ever_existed():
    profile = make_profile(enrolled_at=T0)
    revoked = make_consent(status=ConsentStatus.REVOKED, revoked_at=T0)
    assert not awaiting_consent(profile, [revoked], "2026-09-15T12:00:00Z", 30)


def test_fr1_awaiting_consent_is_never_set_for_unenrolled_or_purged():
    unenrolled = make_profile(
        enrolled_at=None, enrollment_fingerprint=None, centroid=None, voice_params=None
    )
    assert not awaiting_consent(unenrolled, [], "2026-09-15T12:00:00Z", 30)
    purged = make_profile(
        status=ProfileStatus.PURGED, centroid=None, voice_params=None, enrolled_at=None
    )
    assert not awaiting_consent(purged, [], "2026-09-15T12:00:00Z", 30)

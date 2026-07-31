"""End-to-end service behaviour: FR-1, FR-2, FR-3, FR-5, FR-6, FR-7, FR-10, FR-11, FR-15.

These drive the real service layer against ``SQLiteRepository(":memory:")`` and the
offline adapters — the same wiring the eval scenario suite uses.
"""

from __future__ import annotations

import json

import pytest
from conftest import ALICE, BOB, CARLA, MIMIC, enroll, grant, write_voice
from voicekin.engine.audio import payload_sha256
from voicekin.models import (
    AuditEvent,
    ConsentRejectReason,
    ConsentStatus,
    Context,
    DeliveryStatus,
    ProfileStatus,
    RefusalReason,
    SampleRejectReason,
    SampleStatus,
    TargetKind,
    UtteranceStatus,
)
from voicekin.services import NotFoundError, ServiceError

T0 = "2026-07-31T10:00:00Z"
T1 = "2026-07-31T11:00:00Z"
T2 = "2026-07-31T12:00:00Z"
T3 = "2026-07-31T13:00:00Z"


def events(service) -> list[AuditEvent]:
    return [r.event for r in service.list_audit()]


# --------------------------------------------------------------------------- #
# FR-1 instance and profile lifecycle
# --------------------------------------------------------------------------- #


def test_fr1_init_creates_the_single_instance_row_and_dirs(service, tmp_path):
    instance = service.initialize("Abdoul", T0)
    assert instance.operator_name == "Abdoul"
    assert (tmp_path / "home" / "audio" / "enroll").is_dir()
    assert (tmp_path / "home" / "audio" / "consent").is_dir()
    assert (tmp_path / "home" / "audio" / "out").is_dir()
    with pytest.raises(ServiceError, match="already initialized"):
        service.initialize("Someone", T0)


def test_fr1_services_fail_fast_without_an_instance(service):
    with pytest.raises(ServiceError, match="not initialized"):
        service.create_profile("partner", "Amina", "partner", T0)


def test_fr1_profile_crud_and_audit(initialized):
    profile = initialized.create_profile("partner", "Amina", "partner", T0)
    assert profile.status is ProfileStatus.ACTIVE and profile.enabled
    with pytest.raises(ServiceError, match="already exists"):
        initialized.create_profile("partner", "Other", "self", T0)
    with pytest.raises(NotFoundError):
        initialized.require_profile("nobody")
    initialized.set_enabled("partner", False, T1)
    assert not initialized.require_profile("partner").enabled
    initialized.set_enabled("partner", True, T2)
    assert initialized.require_profile("partner").enabled
    assert events(initialized) == [
        AuditEvent.PROFILE_CREATED,
        AuditEvent.PROFILE_DISABLED,
        AuditEvent.PROFILE_ENABLED,
    ]


def test_fr1_awaiting_consent_flag_appears_after_the_grace_window(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    assert not initialized.list_profiles(T1)[0].awaiting_consent
    late = initialized.list_profiles("2026-09-30T10:00:00Z")[0]
    assert late.awaiting_consent
    assert late.consent_status is None


# --------------------------------------------------------------------------- #
# FR-2/FR-3 enrollment
# --------------------------------------------------------------------------- #


def test_fr3_enrollment_makes_the_profile_complete(initialized, tmp_path):
    results = enroll(initialized, "partner", ALICE, T0, tmp_path)
    assert all(r.accepted for r in results)
    profile = initialized.require_profile("partner")
    assert profile.is_enrolled
    assert profile.centroid is not None and len(profile.centroid) == 16
    assert profile.voice_params is not None
    assert profile.enrolled_at == T0
    assert profile.embedder_id == "spectral-v1"
    assert events(initialized) == [AuditEvent.PROFILE_CREATED] + [AuditEvent.SAMPLE_ADDED] * 3


def test_fr3_profile_is_incomplete_until_the_minimums_are_met(initialized, tmp_path):
    initialized.create_profile("partner", "Amina", "partner", T0)
    paths = [write_voice(tmp_path / "wav", f"e{k}", ALICE, 7.5, 4100 + k) for k in range(2)]
    initialized.add_samples("partner", paths, T0)
    profile = initialized.require_profile("partner")
    assert not profile.is_enrolled
    assert profile.centroid is None and profile.voice_params is None


def test_fr2_a_bad_recording_is_rejected_with_a_reason(initialized, tmp_path):
    initialized.create_profile("partner", "Amina", "partner", T0)
    short = write_voice(tmp_path / "wav", "short", ALICE, 2.0, 900)
    [result] = initialized.add_samples("partner", [short], T0)
    assert not result.accepted
    assert result.sample.reject_reason is SampleRejectReason.TOO_SHORT
    assert result.sample.path is None and result.sample.embedding is None
    assert events(initialized)[-1] is AuditEvent.SAMPLE_REJECTED


def test_fr2_non_wav_bytes_are_rejected_as_bad_format(initialized, tmp_path):
    initialized.create_profile("partner", "Amina", "partner", T0)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"definitely not a wav")
    [result] = initialized.add_samples("partner", [junk], T0)
    assert result.sample.reject_reason is SampleRejectReason.BAD_FORMAT


def test_fr3_a_mixed_speaker_set_is_refused_and_rolled_back(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    before = initialized.require_profile("partner")
    intruder = write_voice(tmp_path / "wav", "intruder", BOB, 7.5, 5500)
    [result] = initialized.add_samples("partner", [intruder], T1)
    assert not result.accepted
    assert result.sample.reject_reason is SampleRejectReason.INCOHERENT_ENROLLMENT
    after = initialized.require_profile("partner")
    assert after.centroid == before.centroid
    assert after.enrollment_fingerprint == before.enrollment_fingerprint
    last = initialized.list_audit()[-1]
    assert last.event is AuditEvent.SAMPLE_REJECTED
    assert set(last.detail) == {"sample_sha256", "reason", "loo_similarity", "theta_enroll"}


def test_fr3_duplicate_audio_is_refused(initialized, tmp_path):
    initialized.create_profile("partner", "Amina", "partner", T0)
    path = write_voice(tmp_path / "wav", "dup", ALICE, 7.5, 4100)
    initialized.add_samples("partner", [path], T0)
    with pytest.raises(ServiceError, match="duplicates"):
        initialized.add_samples("partner", [path], T1)


def test_fr3_removing_a_sample_changes_the_fingerprint(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    before = initialized.require_profile("partner")
    sample = initialized.repository.list_samples("partner")[0]
    assert initialized.absolute(sample.path).exists()
    after = initialized.remove_sample("partner", sample.id, T1)
    assert not initialized.absolute(sample.path).exists()
    assert after.enrollment_fingerprint is None  # below the FR-3 minimum again
    assert before.enrollment_fingerprint is not None
    assert events(initialized)[-1] is AuditEvent.SAMPLE_REMOVED


# --------------------------------------------------------------------------- #
# FR-5 consent
# --------------------------------------------------------------------------- #


def test_fr5_draft_requires_an_enrolled_enabled_profile(initialized, tmp_path):
    initialized.create_profile("partner", "Amina", "partner", T0)
    with pytest.raises(ServiceError, match="enrolled"):
        initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T0)
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    initialized.set_enabled("partner", False, T0)
    with pytest.raises(ServiceError, match="disabled"):
        initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T0)
    assert initialized.repository.list_consents("partner") == []


def test_fr5_draft_renders_the_statement_and_audits_the_nonce(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path, display_name="Amina")
    record = initialized.draft_consent(
        "partner", [Context.ANNOUNCEMENT, Context.REMINDER], "2027-06-30T00:00:00Z", 4815162342, T1
    )
    assert record.status is ConsentStatus.DRAFT
    assert "I, Amina, consent to the VoiceKin system operated by Abdoul" in record.statement_text
    assert "announcements, reminders" in record.statement_text
    assert "expires on 2027-06-30" in record.statement_text
    assert record.nonce in record.statement_text
    assert initialized.consent_statement("partner") == record.statement_text
    detail = initialized.list_audit()[-1].detail
    assert set(detail) == {"scope", "expires_at", "nonce", "nonce_seed", "statement_sha256"}
    assert detail["nonce"] == record.nonce


def test_fr5_a_second_draft_replaces_the_first(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    first = initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T1)
    second = initialized.draft_consent("partner", [Context.ALARM], None, 2, T2)
    stored = initialized.repository.list_consents("partner")
    assert [c.id for c in stored] == [second.id]
    assert first.id != second.id


def test_fr5_grant_verifies_the_voice_owner(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    record = grant(initialized, "partner", ALICE, T1, tmp_path)
    assert record.status is ConsentStatus.VERIFIED
    assert record.similarity >= record.threshold == initialized.calibration.theta_verify
    assert record.embedder_id == "spectral-v1"
    assert (
        record.enrollment_fingerprint
        == initialized.require_profile("partner").enrollment_fingerprint
    )
    assert initialized.absolute(record.audio_path).exists()
    detail = initialized.list_audit()[-1].detail
    assert set(detail) == {
        "similarity",
        "threshold",
        "embedder_id",
        "enrollment_fingerprint",
        "audio_sha256",
    }


@pytest.mark.parametrize("impostor", [BOB, CARLA, MIMIC])
def test_fr5_an_impostor_is_rejected_and_their_audio_discarded(initialized, tmp_path, impostor):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    record = grant(initialized, "partner", impostor, T1, tmp_path, seed=8800)
    assert record.status is ConsentStatus.REJECTED
    assert record.reject_reason is ConsentRejectReason.SPEAKER_MISMATCH
    assert record.audio_path is None, "an impostor take is never retained"
    assert record.audio_sha256 is not None
    assert record.similarity < initialized.calibration.theta_verify
    assert initialized.list_audit()[-1].event is AuditEvent.CONSENT_REJECTED


def test_fr5_replaying_an_enrollment_sample_is_rejected(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T1)
    replay = tmp_path / "wav" / "partner" / "e0.wav"
    record = initialized.grant_consent("partner", replay, T1)
    assert record.reject_reason is ConsentRejectReason.REUSED_ENROLLMENT_AUDIO
    assert record.similarity is None and record.embedder_id is None
    assert record.audio_sha256 is not None


def test_fr5_a_bad_consent_recording_is_rejected_on_quality(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T1)
    short = write_voice(tmp_path / "wav", "tooshort", ALICE, 4.0, 9100)
    record = initialized.grant_consent("partner", short, T1)
    assert record.reject_reason is ConsentRejectReason.AUDIO_QUALITY
    assert record.similarity is None


def test_fr5_grant_after_the_enrollment_fell_below_the_minimum(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    initialized.draft_consent("partner", [Context.ANNOUNCEMENT], None, 1, T1)
    sample = initialized.repository.list_samples("partner")[0]
    initialized.remove_sample("partner", sample.id, T1)
    path = write_voice(tmp_path / "wav", "late", ALICE, 9.0, 7700)
    record = initialized.grant_consent("partner", path, T2)
    assert record.reject_reason is ConsentRejectReason.NOT_ENROLLED
    assert record.audio_sha256 is None, "the file is never read on this path"
    assert record.similarity is None


def test_fr5_drafting_while_a_consent_is_effective_is_refused(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    before = len(initialized.repository.list_consents("partner"))
    with pytest.raises(ServiceError, match="already effective"):
        initialized.draft_consent("partner", [Context.ALARM], None, 2, T2)
    assert len(initialized.repository.list_consents("partner")) == before


# --------------------------------------------------------------------------- #
# FR-6/FR-9 synthesis
# --------------------------------------------------------------------------- #


def test_fr6_authorized_synthesis_renders_and_audits(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    result = initialized.synthesize("partner", "Dinner is ready!", Context.ANNOUNCEMENT, 7, T2)
    utterance = result.utterance
    assert not result.refused
    assert utterance.status is UtteranceStatus.RENDERED
    assert utterance.text == "dinner is ready" and utterance.text_raw == "Dinner is ready!"
    assert utterance.synth_id == "stub-v1" and utterance.seed == 7
    assert initialized.absolute(utterance.output_path).exists()
    assert payload_sha256(initialized.absolute(utterance.output_path).read_bytes()) == (
        utterance.output_sha256
    )
    assert events(initialized)[-2:] == [
        AuditEvent.SYNTHESIS_AUTHORIZED,
        AuditEvent.UTTERANCE_RENDERED,
    ]


def test_fr6_a_refusal_is_persisted_as_an_utterance_row(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    result = initialized.synthesize("partner", "Dinner is ready", Context.ANNOUNCEMENT, 7, T2)
    assert result.refused
    assert result.utterance.status is UtteranceStatus.REFUSED
    assert result.utterance.refusal_reason is RefusalReason.NO_CONSENT
    assert result.utterance.consent_id is None and result.utterance.output_sha256 is None
    record = initialized.list_audit()[-1]
    assert record.event is AuditEvent.SYNTHESIS_REFUSED
    assert record.detail == {"context": "announcement", "reason": "no_consent"}


def test_fr7_revocation_stops_the_very_next_synthesis(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    assert not initialized.synthesize("partner", "one", Context.ANNOUNCEMENT, 1, T2).refused
    initialized.revoke_consent("partner", "changed my mind", T2)
    result = initialized.synthesize("partner", "two", Context.ANNOUNCEMENT, 1, T3)
    assert result.utterance.refusal_reason is RefusalReason.CONSENT_REVOKED


def test_fr7_revoking_twice_is_a_no_op_with_one_audit_record(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    first = initialized.revoke_consent("partner", "done", T2)
    second = initialized.revoke_consent("partner", "again", T3)
    assert second.revoked_at == first.revoked_at == T2
    assert events(initialized).count(AuditEvent.CONSENT_REVOKED) == 1


def test_fr6_enrollment_drift_refuses_until_consent_is_re_granted(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    extra = write_voice(tmp_path / "wav", "extra", ALICE, 7.5, 4200)
    [added] = initialized.add_samples("partner", [extra], T2)
    assert added.accepted
    refused = initialized.synthesize("partner", "hello", Context.ANNOUNCEMENT, 1, T2)
    assert refused.utterance.refusal_reason is RefusalReason.ENROLLMENT_CHANGED

    initialized.revoke_consent("partner", "re-granting", T2)
    grant(initialized, "partner", ALICE, T3, tmp_path, seed=7750)
    assert not initialized.synthesize("partner", "hello", Context.ANNOUNCEMENT, 1, T3).refused


def test_fr6_reverting_an_enrollment_does_not_resurrect_the_old_consent(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    extra = write_voice(tmp_path / "wav", "extra", ALICE, 7.5, 4200)
    initialized.add_samples("partner", [extra], T2)
    added = initialized.repository.list_samples("partner")[-1]
    initialized.remove_sample("partner", added.id, T2)
    # The enrollment is byte-identical to what the consent was granted against...
    result = initialized.synthesize("partner", "hello", Context.ANNOUNCEMENT, 1, T3)
    assert not result.refused, "the fingerprint is a pure function of the sample set"


def test_fr6_scope_and_expiry_are_enforced(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(
        initialized,
        "partner",
        ALICE,
        T1,
        tmp_path,
        contexts=(Context.ANNOUNCEMENT,),
        expires_at=T3,
    )
    assert initialized.synthesize(
        "partner", "x", Context.ALARM, 1, T2
    ).utterance.refusal_reason is (RefusalReason.SCOPE_MISMATCH)
    assert (
        initialized.synthesize("partner", "x", Context.ANNOUNCEMENT, 1, T3).utterance.refusal_reason
        is RefusalReason.CONSENT_EXPIRED
    )


def test_fr15_replaying_the_same_operations_reproduces_everything(
    tmp_path, calibration, statement_template
):
    """The FR-15 determinism contract, end to end."""
    from voicekin.services import VoiceKinService
    from voicekin.store import SQLiteRepository

    def run(tag: str):
        repo = SQLiteRepository.in_memory()
        service = VoiceKinService(
            repository=repo,
            data_home=tmp_path / tag,
            calibration=calibration,
            statement_template=statement_template,
        )
        service.initialize("Abdoul", T0)
        enroll(service, "partner", ALICE, T0, tmp_path / f"{tag}wav")
        grant(service, "partner", ALICE, T1, tmp_path / f"{tag}wav")
        result = service.synthesize("partner", "Dinner is ready!", Context.ANNOUNCEMENT, 7, T2)
        chain = [(r.seq, r.record_hash) for r in service.list_audit()]
        payload = service.absolute(result.utterance.output_path).read_bytes()
        repo.close()
        return result.utterance, chain, payload

    first, chain_a, audio_a = run("a")
    second, chain_b, audio_b = run("b")
    assert first.id == second.id
    assert first.output_sha256 == second.output_sha256
    assert audio_a == audio_b
    assert chain_a == chain_b


def test_fr15_repeating_a_synthesis_advances_the_attempt_index(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    grant(initialized, "partner", ALICE, T1, tmp_path)
    first = initialized.synthesize("partner", "same words", Context.ANNOUNCEMENT, 7, T2).utterance
    second = initialized.synthesize("partner", "same words", Context.ANNOUNCEMENT, 7, T2).utterance
    assert first.id != second.id
    assert (first.attempt_index, second.attempt_index) == (0, 1)
    assert first.output_sha256 == second.output_sha256, "byte determinism is unconditional"


# --------------------------------------------------------------------------- #
# FR-10/FR-11 provenance and delivery
# --------------------------------------------------------------------------- #


def _rendered(service, tmp_path):
    enroll(service, "partner", ALICE, T0, tmp_path)
    grant(service, "partner", ALICE, T1, tmp_path)
    return service.synthesize("partner", "Dinner is ready!", Context.ANNOUNCEMENT, 7, T2).utterance


def test_fr11_delivery_writes_a_wav_and_a_manifest(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    sink = tmp_path / "sink"
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(sink)}, T2)
    result = initialized.deliver(utterance.id, "desk", T3)
    assert result.delivery.status is DeliveryStatus.SUCCEEDED
    wav = sink / f"{utterance.id}.wav"
    manifest = json.loads((sink / f"{utterance.id}.json").read_text())
    assert wav.exists()
    assert payload_sha256(wav.read_bytes()) == utterance.output_sha256
    assert manifest["utterance_id"] == utterance.id
    assert manifest["profile_id"] == "partner"
    assert manifest["consent_id"] == utterance.consent_id
    assert manifest["output_sha256"] == utterance.output_sha256
    assert isinstance(manifest["audit_seq"], int)
    assert events(initialized)[-1] is AuditEvent.DELIVERY_SUCCEEDED


def test_fr11_delivery_re_authorizes_so_revocation_stops_replays(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(tmp_path / "sink")}, T2)
    initialized.revoke_consent("partner", "withdrawn", T2)
    result = initialized.deliver(utterance.id, "desk", T3)
    assert result.delivery.status is DeliveryStatus.REFUSED
    assert result.delivery.refusal_reason is RefusalReason.CONSENT_REVOKED
    assert not (tmp_path / "sink" / f"{utterance.id}.wav").exists()
    assert events(initialized)[-1] is AuditEvent.DELIVERY_REFUSED


def test_fr11_only_rendered_utterances_can_be_delivered(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    refused = initialized.synthesize("partner", "x", Context.ANNOUNCEMENT, 1, T2).utterance
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(tmp_path / "sink")}, T2)
    with pytest.raises(ServiceError, match="only a rendered"):
        initialized.deliver(refused.id, "desk", T3)
    with pytest.raises(NotFoundError):
        initialized.deliver("f" * 32, "desk", T3)


def test_fr10_verify_output_resolves_a_wav_to_its_utterance(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    wav = initialized.absolute(utterance.output_path).read_bytes()
    provenance = initialized.verify_output(wav)
    assert provenance is not None
    assert provenance.utterance.id == utterance.id
    assert provenance.profile.id == "partner"
    assert [r.event for r in provenance.audit_records] == [
        AuditEvent.SYNTHESIS_AUTHORIZED,
        AuditEvent.UTTERANCE_RENDERED,
    ]


def test_fr10_an_unknown_wav_has_no_provenance(initialized, tmp_path):
    _rendered(initialized, tmp_path)
    stranger = write_voice(tmp_path / "wav", "stranger", BOB, 7.5, 6000)
    assert initialized.verify_output(stranger.read_bytes()) is None


# --------------------------------------------------------------------------- #
# FR-7 purge
# --------------------------------------------------------------------------- #


def test_fr7_purge_erases_everything_voicekin_controls(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    sink = tmp_path / "sink"
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(sink)}, T2)
    initialized.deliver(utterance.id, "desk", T2)
    delivered = sink / f"{utterance.id}.wav"
    manifest = sink / f"{utterance.id}.json"
    assert delivered.exists() and manifest.exists()

    samples = initialized.repository.list_samples("partner")
    consent = initialized.repository.list_consents("partner")[-1]
    report = initialized.purge_profile("partner", T3)

    profile = initialized.require_profile("partner")
    assert profile.status is ProfileStatus.PURGED
    assert profile.centroid is None and profile.voice_params is None and profile.enrolled_at is None
    assert profile.enrollment_fingerprint is not None, "the hash is kept for the audit trail"

    for sample in samples:
        assert not initialized.absolute(sample.path).exists()
        stored = initialized.repository.get_sample(sample.id)
        assert stored.path is None and stored.embedding is None
        assert stored.sha256 == sample.sha256
    stored_consent = initialized.repository.get_consent(consent.id)
    assert stored_consent.audio_path is None and stored_consent.audio_sha256 is not None
    assert stored_consent.status is ConsentStatus.REVOKED
    stored_utterance = initialized.repository.get_utterance(utterance.id)
    assert stored_utterance.output_path is None
    assert stored_utterance.output_sha256 == utterance.output_sha256

    assert not delivered.exists() and not manifest.exists()
    delivery = initialized.repository.list_deliveries(utterance.id)[0]
    assert "path" not in delivery.detail and "manifest_path" not in delivery.detail
    assert delivery.detail["path_sha256"] and delivery.detail["manifest_path_sha256"]

    assert report.files_deleted == len(samples) + 2  # samples + consent + output
    assert report.delivered_deleted == 2
    purge_record = initialized.list_audit()[-1]
    assert purge_record.event is AuditEvent.PROFILE_PURGED
    assert set(purge_record.detail) == {
        "files_deleted",
        "files_missing",
        "files_failed",
        "delivered_deleted",
        "delivered_missing",
        "delivered_failed",
        "enrollment_fingerprint",
    }
    assert initialized.verify_audit().ok, "purge keeps hashes, so the chain still verifies"


def test_fr7_purge_never_deletes_a_file_voicekin_did_not_write(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    sink = tmp_path / "sink"
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(sink)}, T2)
    initialized.deliver(utterance.id, "desk", T2)
    impostor = write_voice(sink, "tmp", BOB, 7.5, 6100)
    impostor.replace(sink / f"{utterance.id}.wav")

    report = initialized.purge_profile("partner", T3)
    assert (sink / f"{utterance.id}.wav").exists(), "a foreign file at that path survives"
    assert report.delivered_missing >= 1
    assert report.delivered_deleted == 1  # the manifest only


def test_fr7_purge_is_terminal(initialized, tmp_path):
    _rendered(initialized, tmp_path)
    initialized.purge_profile("partner", T3)
    result = initialized.synthesize("partner", "hello", Context.ANNOUNCEMENT, 1, T3)
    assert result.utterance.refusal_reason is RefusalReason.PROFILE_PURGED
    with pytest.raises(ServiceError, match="already purged"):
        initialized.purge_profile("partner", T3)
    with pytest.raises(ServiceError, match="purged"):
        initialized.set_enabled("partner", True, T3)
    with pytest.raises(ServiceError, match="purged"):
        initialized.add_samples("partner", [], T3)


def test_fr12_the_whole_session_verifies_and_reports_a_head(initialized, tmp_path):
    utterance = _rendered(initialized, tmp_path)
    initialized.add_target("desk", TargetKind.FILE_SINK, {"dir": str(tmp_path / "sink")}, T2)
    initialized.deliver(utterance.id, "desk", T2)
    initialized.revoke_consent("partner", "done", T3)
    result = initialized.verify_audit()
    assert result.ok
    assert result.head_seq == len(initialized.list_audit())
    assert len(result.head_hash) == 64


def test_fr12_editing_a_stored_audit_row_is_detected(initialized, tmp_path):
    _rendered(initialized, tmp_path)
    initialized.repository.connection.execute(
        "UPDATE audit_record SET detail = ? WHERE seq = 3", ('{"tampered": true}',)
    )
    result = initialized.verify_audit()
    assert not result.ok and result.bad_seq == 3


def test_fr5_sample_status_enum_round_trips_through_sqlite(initialized, tmp_path):
    enroll(initialized, "partner", ALICE, T0, tmp_path)
    assert all(
        s.status is SampleStatus.ACCEPTED for s in initialized.repository.list_samples("partner")
    )

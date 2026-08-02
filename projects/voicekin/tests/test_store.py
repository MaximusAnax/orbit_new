"""Repository behaviour: round trips, ordering, transactions, append-only audit."""

from __future__ import annotations

import pytest
from voicekin.engine.audit import verify_chain
from voicekin.models import (
    AuditEvent,
    ConsentRecord,
    ConsentStatus,
    Context,
    Delivery,
    DeliveryStatus,
    DeviceTarget,
    EnrollmentSample,
    Instance,
    SampleStatus,
    TargetKind,
    Utterance,
    UtteranceStatus,
    VoiceParams,
    VoiceProfile,
)
from voicekin.store import SCHEMA_VERSION, Repository, SQLiteRepository

TS = "2026-07-31T12:00:00Z"


def _profile(**overrides) -> VoiceProfile:
    return VoiceProfile(
        **{
            "id": "partner",
            "display_name": "Amina",
            "relationship": "partner",
            "created_at": TS,
            "updated_at": TS,
            **overrides,
        }
    )


def _consent(index: int = 0, **overrides) -> ConsentRecord:
    return ConsentRecord(
        **{
            "id": f"{index:032x}",
            "profile_id": "partner",
            "draft_index": index,
            "status": ConsentStatus.DRAFT,
            "scope_contexts": [Context.ANNOUNCEMENT],
            "nonce_seed": 1,
            "nonce": "AAAAAAAA",
            "statement_text": "I, Amina, consent ...",
            "drafted_at": TS,
            **overrides,
        }
    )


def test_sqlite_repository_satisfies_the_protocol(repository):
    assert isinstance(repository, Repository)


def test_in_memory_backend_is_the_same_class():
    repo = SQLiteRepository.in_memory()
    assert repo.database == ":memory:"
    assert repo.get_instance() is None
    repo.close()


def test_instance_is_a_single_row(repository):
    instance = Instance(
        operator_name="Abdoul", data_home="/tmp/vk", schema_version=SCHEMA_VERSION, created_at=TS
    )
    with repository.transaction():
        repository.put_instance(instance)
    assert repository.get_instance() == instance
    with repository.transaction():
        repository.put_instance(instance.model_copy(update={"operator_name": "Someone"}))
    assert repository.get_instance().operator_name == "Someone"
    assert repository.connection.execute("SELECT COUNT(*) FROM instance").fetchone()[0] == 1


def test_instance_schema_version_is_guarded(repository):
    with pytest.raises(ValueError, match="schema_version"):
        repository.put_instance(
            Instance(operator_name="A", data_home="/tmp", schema_version=99, created_at=TS)
        )


def test_profile_round_trips_with_json_columns(repository):
    profile = _profile(
        embedder_id="spectral-v1",
        centroid=[0.1, -0.2, 0.3],
        enrollment_fingerprint="a" * 64,
        voice_params=VoiceParams(
            f0_base_hz=204.0, f0_range_hz=46.0, formant_scale=1.13, tilt_db_oct=-11.2
        ),
        enrolled_at=TS,
    )
    with repository.transaction():
        repository.save_profile(profile)
    stored = repository.get_profile("partner")
    assert stored == profile
    assert stored.voice_params.f0_base_hz == 204.0
    assert repository.list_profiles() == [profile]
    assert repository.get_profile("nobody") is None


def test_samples_are_ordered_by_index_and_deletable(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        for index in (2, 0, 1):
            repository.save_sample(
                EnrollmentSample(
                    id=f"{index:032x}",
                    profile_id="partner",
                    sample_index=index,
                    path=f"audio/enroll/partner/{index}.wav",
                    sha256=f"{index:064x}",
                    duration_s=6.0,
                    snr_db=25.0,
                    voiced_ratio=0.7,
                    embedding=[0.5, 0.5],
                    status=SampleStatus.ACCEPTED,
                    added_at=TS,
                )
            )
    assert [s.sample_index for s in repository.list_samples("partner")] == [0, 1, 2]
    assert repository.get_sample(f"{1:032x}").embedding == [0.5, 0.5]
    with repository.transaction():
        repository.delete_sample(f"{1:032x}")
    assert [s.sample_index for s in repository.list_samples("partner")] == [0, 2]


def test_duplicate_accepted_audio_is_rejected_by_the_schema(repository):
    import sqlite3

    with repository.transaction():
        repository.save_profile(_profile())
    sample = EnrollmentSample(
        id="a" * 32,
        profile_id="partner",
        sample_index=0,
        path="audio/enroll/partner/a.wav",
        sha256="c" * 64,
        duration_s=6.0,
        snr_db=25.0,
        voiced_ratio=0.7,
        embedding=[1.0],
        status=SampleStatus.ACCEPTED,
        added_at=TS,
    )
    with repository.transaction():
        repository.save_sample(sample)
    with pytest.raises(sqlite3.IntegrityError), repository.transaction():
        repository.save_sample(sample.model_copy(update={"id": "b" * 32, "sample_index": 1}))


def test_consents_are_ordered_and_drafts_deletable(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        repository.save_consent(_consent(1, drafted_at="2026-08-02T00:00:00Z"))
        repository.save_consent(_consent(0))
    assert [c.draft_index for c in repository.list_consents("partner")] == [0, 1]
    with repository.transaction():
        repository.delete_consent(f"{0:032x}")
    assert [c.draft_index for c in repository.list_consents("partner")] == [1]


def test_utterance_lookup_by_output_hash(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        repository.save_consent(
            _consent(
                0,
                status=ConsentStatus.VERIFIED,
                decided_at=TS,
                audio_path="audio/consent/partner/x.wav",
                audio_sha256="d" * 64,
                similarity=0.99,
                threshold=0.98,
                embedder_id="spectral-v1",
                enrollment_fingerprint="a" * 64,
            )
        )
        repository.save_utterance(
            Utterance(
                id="e" * 32,
                profile_id="partner",
                attempt_index=0,
                consent_id=f"{0:032x}",
                text="dinner is ready",
                text_raw="Dinner is ready!",
                context=Context.ANNOUNCEMENT,
                status=UtteranceStatus.RENDERED,
                synth_id="stub-v1",
                seed=7,
                output_path="audio/out/e.wav",
                output_sha256="f" * 64,
                duration_s=2.9,
                requested_at=TS,
            )
        )
    assert repository.find_utterance_by_output_sha256("f" * 64).id == "e" * 32
    assert repository.find_utterance_by_output_sha256("0" * 64) is None
    assert [u.id for u in repository.list_utterances("partner")] == ["e" * 32]


def test_targets_round_trip(repository):
    target = DeviceTarget(
        id="living-room",
        kind=TargetKind.HOME_ASSISTANT,
        config={"entity_id": "media_player.living_room", "media_dir": "/mnt/ha"},
        created_at=TS,
    )
    with repository.transaction():
        repository.save_target(target)
    assert repository.get_target("living-room") == target
    assert repository.list_targets() == [target]


def test_deliveries_are_listed_per_utterance_and_per_profile(repository):
    test_utterance_lookup_by_output_hash(repository)
    with repository.transaction():
        repository.save_target(
            DeviceTarget(
                id="desk", kind=TargetKind.FILE_SINK, config={"dir": "/tmp/x"}, created_at=TS
            )
        )
        for index in range(2):
            repository.save_delivery(
                Delivery(
                    id=f"{index:032x}",
                    utterance_id="e" * 32,
                    delivery_index=index,
                    target_id="desk",
                    status=DeliveryStatus.SUCCEEDED,
                    detail={"path": f"/tmp/x/{index}.wav"},
                    requested_at=TS,
                )
            )
    assert [d.delivery_index for d in repository.list_deliveries("e" * 32)] == [0, 1]
    assert len(repository.list_profile_deliveries("partner")) == 2


def test_transaction_rolls_back_on_error(repository):
    with pytest.raises(RuntimeError), repository.transaction():
        repository.save_profile(_profile())
        raise RuntimeError("boom")
    assert repository.get_profile("partner") is None


def test_nested_transactions_commit_once(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        with repository.transaction():
            repository.save_profile(_profile(display_name="Renamed"))
    assert repository.get_profile("partner").display_name == "Renamed"


def test_fr12_append_audit_chains_inside_the_transaction(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        first = repository.append_audit(
            ts=TS, event=AuditEvent.PROFILE_CREATED, profile_id="partner", detail={"n": 1}
        )
        second = repository.append_audit(
            ts=TS, event=AuditEvent.PROFILE_DISABLED, profile_id="partner", detail={"n": 2}
        )
    assert (first.seq, second.seq) == (1, 2)
    assert second.prev_hash == first.record_hash
    assert repository.audit_head() == second
    assert verify_chain(repository.iter_audit()).ok


def test_fr12_audit_reads_filter_and_order(repository):
    test_fr12_append_audit_chains_inside_the_transaction(repository)
    with repository.transaction():
        repository.append_audit(
            ts=TS, event=AuditEvent.UTTERANCE_RENDERED, profile_id="partner", utterance_id="u" * 32
        )
    assert [r.seq for r in repository.list_audit()] == [1, 2, 3]
    assert [r.seq for r in repository.list_audit(since_seq=2)] == [3]
    assert [r.seq for r in repository.list_audit_for_utterance("u" * 32)] == [3]


def test_fr12_repository_exposes_no_audit_mutation():
    """Append-only is enforced by the absence of an API, not by convention."""
    forbidden = {"update_audit", "delete_audit", "save_audit", "remove_audit"}
    assert forbidden.isdisjoint(dir(SQLiteRepository))
    assert forbidden.isdisjoint(dir(Repository))


def test_fr12_audit_rollback_leaves_no_gap(repository):
    with repository.transaction():
        repository.save_profile(_profile())
        repository.append_audit(ts=TS, event=AuditEvent.PROFILE_CREATED, profile_id="partner")
    with pytest.raises(RuntimeError), repository.transaction():
        repository.append_audit(ts=TS, event=AuditEvent.PROFILE_DISABLED, profile_id="partner")
        raise RuntimeError("boom")
    assert [r.seq for r in repository.list_audit()] == [1]
    assert verify_chain(repository.iter_audit()).ok

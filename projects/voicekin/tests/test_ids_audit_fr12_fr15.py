"""FR-15 derived identifiers and FR-12 the tamper-evident audit chain."""

from __future__ import annotations

import hashlib
import json

import pytest
from voicekin.engine.audit import (
    GENESIS_PREV_HASH,
    build_record,
    canonical_json,
    compute_record_hash,
    next_link,
    round_floats,
    verify_chain,
)
from voicekin.engine.ids import (
    ID_LENGTH,
    NONCE_LENGTH,
    consent_id,
    consent_nonce,
    delivery_id,
    derive_id,
    sample_id,
    utterance_id,
)
from voicekin.models import AuditEvent, AuditRecord

TS = "2026-07-31T18:30:00Z"


# --------------------------------------------------------------------------- #
# FR-15 identifiers
# --------------------------------------------------------------------------- #


def test_fr15_ids_are_32_lowercase_hex_and_deterministic():
    value = sample_id("partner", "a" * 64, TS, 0)
    assert len(value) == ID_LENGTH
    assert value == sample_id("partner", "a" * 64, TS, 0)
    assert set(value) <= set("0123456789abcdef")


def test_fr15_each_id_material_field_changes_the_id():
    base = sample_id("partner", "a" * 64, TS, 0)
    assert base != sample_id("other", "a" * 64, TS, 0)
    assert base != sample_id("partner", "b" * 64, TS, 0)
    assert base != sample_id("partner", "a" * 64, "2026-07-31T18:30:01Z", 0)
    assert base != sample_id("partner", "a" * 64, TS, 1)


def test_fr15_id_kinds_do_not_collide():
    ids = {
        sample_id("p", "a" * 64, TS, 0),
        consent_id("p", TS, 0),
        utterance_id("p", "text", "announcement", TS, 0),
        delivery_id("a" * 32, "living-room", TS, 0),
    }
    assert len(ids) == 4


def test_fr15_derive_id_is_canonical_json_of_the_material():
    material = ["sample", "partner", "a" * 64, TS, 0]
    assert derive_id(material) == hashlib.sha256(canonical_json(material)).hexdigest()[:32]


def test_fr15_nonce_is_base32_derived_from_the_persisted_seed():
    cid = consent_id("partner", TS, 0)
    nonce = consent_nonce(cid, 4815162342)
    assert len(nonce) == NONCE_LENGTH
    assert set(nonce) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert nonce == consent_nonce(cid, 4815162342)
    assert nonce != consent_nonce(cid, 4815162343)
    assert nonce != consent_nonce(consent_id("partner", TS, 1), 4815162342)


# --------------------------------------------------------------------------- #
# FR-12 canonical JSON
# --------------------------------------------------------------------------- #


def test_fr12_canonical_json_sorts_keys_and_omits_whitespace():
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_fr12_canonical_json_rounds_floats_to_six_places():
    assert canonical_json({"x": 0.1234567891}) == b'{"x":0.123457}'
    assert canonical_json({"x": -0.0}) == b'{"x":0.0}'
    assert canonical_json([{"y": [1.0000000001]}]) == b'[{"y":[1.0]}]'


def test_fr12_canonical_json_keeps_unicode_and_booleans():
    assert canonical_json({"n": "Amina Ndiaye", "ok": True}) == b'{"n":"Amina Ndiaye","ok":true}'


def test_fr12_round_floats_leaves_ints_and_none_alone():
    assert round_floats({"a": 3, "b": None, "c": True}) == {"a": 3, "b": None, "c": True}


def test_fr12_canonical_json_refuses_unserializable_values():
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


# --------------------------------------------------------------------------- #
# FR-12 chain construction
# --------------------------------------------------------------------------- #


def _chain(count: int = 6) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    head: AuditRecord | None = None
    for index in range(count):
        head = build_record(
            head,
            ts=f"2026-07-31T18:0{index}:00Z",
            event=AuditEvent.SYNTHESIS_AUTHORIZED,
            profile_id="partner",
            detail={"context": "announcement", "score": 0.123456789},
        )
        records.append(head)
    return records


def test_fr12_genesis_and_linkage():
    assert hashlib.sha256(b"voicekin-genesis").hexdigest() == GENESIS_PREV_HASH
    assert next_link(None) == (1, GENESIS_PREV_HASH)
    records = _chain(3)
    assert [r.seq for r in records] == [1, 2, 3]
    assert records[0].prev_hash == GENESIS_PREV_HASH
    assert records[1].prev_hash == records[0].record_hash
    assert next_link(records[-1]) == (4, records[-1].record_hash)


def test_fr12_record_hash_covers_every_audited_field():
    record = _chain(1)[0]
    assert record.record_hash == compute_record_hash(
        seq=record.seq,
        ts=record.ts,
        event=record.event,
        profile_id=record.profile_id,
        consent_id=record.consent_id,
        utterance_id=record.utterance_id,
        detail=record.detail,
        prev_hash=record.prev_hash,
    )


def test_fr12_detail_floats_are_stored_rounded():
    assert _chain(1)[0].detail["score"] == 0.123457


def test_fr12_a_clean_chain_verifies_and_reports_its_head():
    records = _chain(5)
    result = verify_chain(records)
    assert result.ok
    assert result.head_seq == 5
    assert result.head_hash == records[-1].record_hash
    assert result.bad_seq is None


def test_fr12_an_empty_chain_verifies_at_genesis():
    result = verify_chain([])
    assert result.ok and result.head_seq == 0 and result.head_hash == GENESIS_PREV_HASH


# --------------------------------------------------------------------------- #
# FR-12 tamper detection — the EVALS M5 case list
# --------------------------------------------------------------------------- #


def _rehash(record: AuditRecord, **changes) -> AuditRecord:
    """Recompute a record's own hash after a change (a competent forger)."""
    fields = record.model_dump() | changes
    fields["record_hash"] = compute_record_hash(
        seq=fields["seq"],
        ts=fields["ts"],
        event=fields["event"],
        profile_id=fields["profile_id"],
        consent_id=fields["consent_id"],
        utterance_id=fields["utterance_id"],
        detail=fields["detail"],
        prev_hash=fields["prev_hash"],
    )
    return AuditRecord(**fields)


def test_fr12_case1_mutated_detail_is_rejected_at_that_seq():
    records = _chain(6)
    records[2] = records[2].model_copy(update={"detail": {"context": "alarm", "score": 0.5}})
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 3


def test_fr12_case2_mutated_timestamp_is_rejected_at_that_seq():
    records = _chain(6)
    records[3] = records[3].model_copy(update={"ts": "2020-01-01T00:00:00Z"})
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 4


def test_fr12_case3_a_deleted_record_is_rejected_at_the_following_seq():
    records = _chain(6)
    del records[3]
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 5


def test_fr12_case4_reordering_is_rejected_at_the_earlier_seq():
    records = _chain(6)
    third, fourth = records[2], records[3]
    records[2] = fourth.model_copy(update={"seq": 3})
    records[3] = third.model_copy(update={"seq": 4})
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 3


def test_fr12_case5_a_rehash_that_ignores_prev_hash_is_rejected():
    records = _chain(6)
    forged = compute_record_hash(
        seq=records[2].seq,
        ts=records[2].ts,
        event=records[2].event,
        profile_id=records[2].profile_id,
        consent_id=records[2].consent_id,
        utterance_id=records[2].utterance_id,
        detail={"context": "alarm"},
        prev_hash=GENESIS_PREV_HASH,
    )
    records[2] = records[2].model_copy(update={"record_hash": forged})
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 3


def test_fr12_case6_truncate_and_append_a_badly_chained_record_is_rejected():
    records = _chain(6)[:4]
    forged = records[-1].model_copy(
        update={"seq": 5, "detail": {"context": "alarm"}, "prev_hash": GENESIS_PREV_HASH}
    )
    records.append(forged)
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 5


def test_fr12_case7_truncate_and_recompute_verifies_clean_by_design():
    """The stated limit of an unkeyed linear chain (FR-12, EVALS M5 case 7).

    An adversary with database write access who re-appends *correctly chained*
    records produces a log that verifies. Printing the head hash for out-of-band
    anchoring is the whole mitigation, and this test pins the claim so the suite
    never over-promises.
    """
    original = _chain(6)
    truncated = original[:3]
    head = truncated[-1]
    for index in range(3):
        head = build_record(
            head,
            ts=f"2027-01-0{index + 1}T00:00:00Z",
            event=AuditEvent.SYNTHESIS_REFUSED,
            profile_id="partner",
            detail={"context": "alarm", "reason": "fabricated"},
        )
        truncated.append(head)

    result = verify_chain(truncated)
    assert result.ok, "an unkeyed chain cannot detect a full recompute"
    assert result.head_hash != verify_chain(original).head_hash, (
        "the head hash *does* change — anchoring it out of band is the mitigation"
    )


def test_fr12_a_competent_forger_who_rewrites_the_tail_is_still_detected_at_the_edit():
    """Rewriting one record without fixing its successors breaks the next link."""
    records = _chain(6)
    records[2] = _rehash(records[2], detail={"context": "alarm"})
    result = verify_chain(records)
    assert not result.ok and result.bad_seq == 4


def test_fr12_audit_records_are_immutable():
    record = _chain(1)[0]
    with pytest.raises(ValueError, match="frozen"):
        record.seq = 99


def test_fr12_detail_survives_a_json_round_trip_unchanged():
    record = _chain(1)[0]
    revived = AuditRecord(
        **(record.model_dump() | {"detail": json.loads(json.dumps(record.detail))})
    )
    assert verify_chain([revived]).ok

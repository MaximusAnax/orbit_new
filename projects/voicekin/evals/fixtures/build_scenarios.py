"""Author the M4 consent-gate scenarios and M5 tamper cases (EVALS.md).

The 35 scenarios and 7 tamper cases are hand-authored here — every op, every
expected decision, and every expected audit event with its exact ``detail`` key
set is written out below, reviewed against FR-5/FR-6/FR-7/FR-11/FR-12 clause by
clause — and emitted as committed JSON. Authoring through a script rather than
by editing raw JSON keeps the DATA_MODEL detail-key lists in one place, so a
typo cannot silently weaken an expectation.

Every op carries an explicit ``now``; every draft carries a ``nonce_seed``;
every synthesize carries a ``seed`` (FR-15 — the runs are fully deterministic).
Corpus WAVs are referenced by role (``"S03/enroll/0"``).

Usage::

    uv run python voicekin/evals/fixtures/build_scenarios.py          # verify
    uv run python voicekin/evals/fixtures/build_scenarios.py --write  # rewrite
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent

# Fixed timestamps: T(k) is k minutes after the epoch of the run.
def T(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"2026-07-01T{10 + hour:02d}:{minute:02d}:00Z"


OPERATOR = "Abdoul"
P = "partner"  # default profile slug used by most scenarios
ENROLL3 = ["S03/enroll/0", "S03/enroll/1", "S03/enroll/2"]
CONSENT_OK = "S03/consent/0"
CONSENT_OK_2 = "S03/consent/1"  # the harsh take — verified, distinct audio
CONSENT_IMPOSTOR = "S03-sib-joint/consent/0"
CONSENT_TOO_SHORT = "S01/support/0"
EXTRA_SAMPLE = "S03/probe/0"
FOREIGN_SIBLING = "S03-sib-f0/probe/0"
FOREIGN_SPEAKER = "S05/enroll/0"

# ``detail`` key sets per audit event (DATA_MODEL "notable detail payloads").
K_PROFILE_CREATED = ["display_name", "relationship"]
K_SAMPLE_ADDED = ["duration_s", "enrollment_fingerprint", "sample_sha256", "snr_db", "voiced_ratio"]
K_SAMPLE_REJECTED = ["reason", "sample_sha256"]
K_SAMPLE_REJECTED_LOO = ["loo_similarity", "reason", "sample_sha256", "theta_enroll"]
K_SAMPLE_REMOVED = ["enrollment_fingerprint", "sample_sha256"]
K_DRAFTED = ["expires_at", "nonce", "nonce_seed", "scope", "statement_sha256"]
K_VERIFIED = ["audio_sha256", "embedder_id", "enrollment_fingerprint", "similarity", "threshold"]
K_REJECTED_SCORED = [*K_VERIFIED, "reason"]
K_REJECTED_EARLY = ["audio_sha256", "reason"]
K_REVOKED = ["reason"]
K_AUTHORIZED = ["context", "enrollment_fingerprint", "scope"]
K_REFUSED = ["context", "reason"]
K_RENDERED = ["duration_s", "output_sha256", "seed", "synth_id"]
K_DELIVERY_OK = ["output_sha256", "status", "target_id", "target_kind"]
K_DELIVERY_REFUSED = ["output_sha256", "reason", "status", "target_id", "target_kind"]
K_PURGED = [
    "delivered_deleted", "delivered_failed", "delivered_missing",
    "enrollment_fingerprint", "files_deleted", "files_failed", "files_missing",
]


def A(event: str, keys: list[str], match: dict[str, Any] | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {"event": event, "keys": sorted(keys)}
    if match:
        record["match"] = match
    return record


def init_op(minute: int = 0) -> dict[str, Any]:
    return {"op": "init", "operator": OPERATOR, "now": T(minute)}


def create_op(minute: int = 1, profile: str = P) -> dict[str, Any]:
    return {
        "op": "create_profile", "profile": profile, "display_name": "Amina",
        "relationship": "partner", "now": T(minute),
    }


def enroll_op(minute: int = 2, takes: list[str] | None = None, profile: str = P,
              expect: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "op": "enroll", "profile": profile, "takes": takes or list(ENROLL3), "now": T(minute),
        "expect": expect or {"accepted": 3, "rejected": 0, "enrolled": True},
    }


def draft_op(minute: int, *, scope: list[str] | None = None, expires_at: str | None = None,
             nonce_seed: int = 4815162342, profile: str = P,
             expect: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "op": "draft", "profile": profile, "scope": scope or ["announcement", "reminder"],
        "expires_at": expires_at, "nonce_seed": nonce_seed, "now": T(minute),
        "expect": expect or {"status": "draft"},
    }


def grant_op(minute: int, take: str = CONSENT_OK, profile: str = P,
             expect: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "op": "grant", "profile": profile, "take": take, "now": T(minute),
        "expect": expect or {"status": "verified"},
    }


def say_op(minute: int, *, context: str = "announcement", profile: str = P,
           expect: dict[str, Any], save_as: str | None = None,
           text: str = "dinner is ready", seed: int = 7) -> dict[str, Any]:
    op = {
        "op": "synthesize", "profile": profile, "text": text, "context": context,
        "seed": seed, "now": T(minute), "expect": expect,
    }
    if save_as:
        op["save_as"] = save_as
    return op


def standard_grant(minute_from: int = 3) -> list[dict[str, Any]]:
    """init .. verified consent, minutes ``minute_from``/``minute_from+1``."""
    return [
        init_op(), create_op(), enroll_op(),
        draft_op(minute_from), grant_op(minute_from + 1),
    ]


AUDIT_GRANTED = [
    A("profile_created", K_PROFILE_CREATED),
    A("sample_added", K_SAMPLE_ADDED),
    A("sample_added", K_SAMPLE_ADDED),
    A("sample_added", K_SAMPLE_ADDED),
    A("consent_drafted", K_DRAFTED),
    A("consent_verified", K_VERIFIED),
]


def scenario(sid: int, name: str, ops: list[dict[str, Any]],
             audit: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": sid, "name": name, "ops": ops, "audit": audit}


def build_scenarios() -> list[dict[str, Any]]:
    s: list[dict[str, Any]] = []

    s.append(scenario(1, "grant then synthesize -> authorized", [
        *standard_grant(),
        say_op(5, expect={"decision": "authorized"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED, {"context": "announcement"}),
        A("utterance_rendered", K_RENDERED, {"synth_id": "stub-v1", "seed": 7}),
    ]))

    s.append(scenario(2, "no consent record -> no_consent", [
        init_op(), create_op(), enroll_op(),
        say_op(4, expect={"decision": "refused", "reason": "no_consent"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "no_consent"}),
    ]))

    s.append(scenario(3, "draft only, never granted -> no_consent", [
        init_op(), create_op(), enroll_op(), draft_op(3),
        say_op(4, expect={"decision": "refused", "reason": "no_consent"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("synthesis_refused", K_REFUSED, {"reason": "no_consent"}),
    ]))

    s.append(scenario(4, "rejected consent -> consent_rejected", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        grant_op(4, CONSENT_IMPOSTOR,
                 expect={"status": "rejected", "reject_reason": "speaker_mismatch"}),
        say_op(5, expect={"decision": "refused", "reason": "consent_rejected"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_rejected", K_REJECTED_SCORED, {"reason": "speaker_mismatch"}),
        A("synthesis_refused", K_REFUSED, {"reason": "consent_rejected"}),
    ]))

    s.append(scenario(5, "grant, revoke, synthesize -> consent_revoked", [
        *standard_grant(),
        {"op": "revoke", "profile": P, "reason": "changed my mind", "now": T(5),
         "expect": {"status": "revoked"}},
        say_op(6, expect={"decision": "refused", "reason": "consent_revoked"}),
    ], [
        *AUDIT_GRANTED,
        A("consent_revoked", K_REVOKED, {"reason": "changed my mind"}),
        A("synthesis_refused", K_REFUSED, {"reason": "consent_revoked"}),
    ]))

    s.append(scenario(6, "revoking twice is a no-op with one audit record", [
        *standard_grant(),
        {"op": "revoke", "profile": P, "reason": "first", "now": T(5),
         "expect": {"status": "revoked"}},
        {"op": "revoke", "profile": P, "reason": "second", "now": T(6),
         "expect": {"status": "revoked", "noop": True}},
    ], [
        *AUDIT_GRANTED,
        A("consent_revoked", K_REVOKED, {"reason": "first"}),
    ]))

    s.append(scenario(7, "unexpired consent -> authorized", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, expires_at="2027-06-30T00:00:00Z"),
        grant_op(4),
        say_op(5, expect={"decision": "authorized"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
    ]))

    s.append(scenario(8, "now == expires_at -> consent_expired (boundary refuses)", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, expires_at=T(30)),
        grant_op(4),
        say_op(30, expect={"decision": "refused", "reason": "consent_expired"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_refused", K_REFUSED, {"reason": "consent_expired"}),
    ]))

    s.append(scenario(9, "now past expiry -> consent_expired", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, expires_at=T(30)),
        grant_op(4),
        say_op(45, expect={"decision": "refused", "reason": "consent_expired"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_refused", K_REFUSED, {"reason": "consent_expired"}),
    ]))

    s.append(scenario(10, "grant, add sample -> enrollment_changed", [
        *standard_grant(),
        {"op": "enroll", "profile": P, "takes": [EXTRA_SAMPLE], "now": T(5),
         "expect": {"accepted": 1, "rejected": 0, "fingerprint_changed": True}},
        say_op(6, expect={"decision": "refused", "reason": "enrollment_changed"}),
    ], [
        *AUDIT_GRANTED,
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "enrollment_changed"}),
    ]))

    s.append(scenario(11, "grant, remove sample -> enrollment_changed", [
        init_op(), create_op(),
        enroll_op(2, [*ENROLL3, EXTRA_SAMPLE],
                  expect={"accepted": 4, "rejected": 0, "enrolled": True}),
        draft_op(3), grant_op(4),
        {"op": "remove_sample", "profile": P, "sample_index": 3, "now": T(5),
         "expect": {"enrolled": True, "fingerprint_changed": True}},
        say_op(6, expect={"decision": "refused", "reason": "enrollment_changed"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_verified", K_VERIFIED),
        A("sample_removed", K_SAMPLE_REMOVED),
        A("synthesis_refused", K_REFUSED, {"reason": "enrollment_changed"}),
    ]))

    s.append(scenario(12, "re-grant after enrollment change -> authorized", [
        *standard_grant(),
        {"op": "enroll", "profile": P, "takes": [EXTRA_SAMPLE], "now": T(5),
         "expect": {"accepted": 1, "rejected": 0}},
        say_op(6, expect={"decision": "refused", "reason": "enrollment_changed"}),
        draft_op(7, nonce_seed=90210),
        grant_op(8, CONSENT_OK_2),
        say_op(9, expect={"decision": "authorized"}),
    ], [
        *AUDIT_GRANTED,
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "enrollment_changed"}),
        A("consent_drafted", K_DRAFTED),
        A("consent_verified", K_VERIFIED),
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
    ]))

    s.append(scenario(13, "context outside scope -> scope_mismatch", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, scope=["announcement"]),
        grant_op(4),
        say_op(5, context="doorbell",
               expect={"decision": "refused", "reason": "scope_mismatch"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_refused", K_REFUSED, {"reason": "scope_mismatch", "context": "doorbell"}),
    ]))

    s.append(scenario(14, "context inside scope -> authorized", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, scope=["announcement", "reminder"]),
        grant_op(4),
        say_op(5, context="reminder", expect={"decision": "authorized"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED, {"context": "reminder"}),
        A("utterance_rendered", K_RENDERED),
    ]))

    s.append(scenario(15, "disabled profile with valid consent -> profile_disabled", [
        *standard_grant(),
        {"op": "set_enabled", "profile": P, "enabled": False, "now": T(5)},
        say_op(6, expect={"decision": "refused", "reason": "profile_disabled"}),
    ], [
        *AUDIT_GRANTED,
        A("profile_disabled", []),
        A("synthesis_refused", K_REFUSED, {"reason": "profile_disabled"}),
    ]))

    s.append(scenario(16, "purge then synthesize -> profile_purged; files gone, hashes kept", [
        *standard_grant(),
        say_op(5, expect={"decision": "authorized"}, save_as="u1"),
        {"op": "purge", "profile": P, "now": T(6),
         "expect": {"files_deleted": 5, "files_missing": 0, "files_failed": 0,
                    "delivered_deleted": 0, "delivered_missing": 0, "delivered_failed": 0}},
        {"op": "check_purged", "profile": P},
        say_op(7, expect={"decision": "refused", "reason": "profile_purged"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
        A("consent_revoked", K_REVOKED, {"reason": "profile purged"}),
        A("profile_purged", K_PURGED,
          {"files_deleted": 5, "delivered_deleted": 0}),
        A("synthesis_refused", K_REFUSED, {"reason": "profile_purged"}),
    ]))

    s.append(scenario(17, "render, revoke, deliver -> delivery refused consent_revoked", [
        *standard_grant(),
        {"op": "add_target", "target": "sink", "kind": "file_sink", "now": T(5)},
        say_op(6, expect={"decision": "authorized"}, save_as="u1"),
        {"op": "revoke", "profile": P, "reason": None, "now": T(7),
         "expect": {"status": "revoked"}},
        {"op": "deliver", "utterance": "$u1", "target": "sink", "now": T(8),
         "expect": {"status": "refused", "reason": "consent_revoked"}},
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
        A("consent_revoked", K_REVOKED),
        A("delivery_refused", K_DELIVERY_REFUSED, {"reason": "consent_revoked"}),
    ]))

    s.append(scenario(18, "render then deliver -> succeeded, receipt and manifest written", [
        *standard_grant(),
        {"op": "add_target", "target": "sink", "kind": "file_sink", "now": T(5)},
        say_op(6, expect={"decision": "authorized"}, save_as="u1"),
        {"op": "deliver", "utterance": "$u1", "target": "sink", "now": T(7),
         "expect": {"status": "succeeded", "files_exist": True}, "save_as": "d1"},
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
        A("delivery_succeeded", K_DELIVERY_OK, {"target_id": "sink", "target_kind": "file_sink"}),
    ]))

    s.append(scenario(19, "consent audio identical to an enrollment sample -> reused_enrollment_audio", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        grant_op(4, "S03/enroll/0",
                 expect={"status": "rejected", "reject_reason": "reused_enrollment_audio",
                         "score_fields_null": True}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_rejected", K_REJECTED_EARLY, {"reason": "reused_enrollment_audio"}),
    ]))

    s.append(scenario(20, "consent audio fails screening -> audio_quality", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        grant_op(4, CONSENT_TOO_SHORT,
                 expect={"status": "rejected", "reject_reason": "audio_quality",
                         "score_fields_null": True}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_rejected", K_REJECTED_EARLY, {"reason": "audio_quality"}),
    ]))

    s.append(scenario(21, "joint-sibling impostor consent -> speaker_mismatch", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        grant_op(4, CONSENT_IMPOSTOR,
                 expect={"status": "rejected", "reject_reason": "speaker_mismatch",
                         "score_fields_null": False}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_rejected", K_REJECTED_SCORED, {"reason": "speaker_mismatch"}),
    ]))

    s.append(scenario(22, "zero-sample profile -> no_enrollment", [
        init_op(), create_op(),
        say_op(2, expect={"decision": "refused", "reason": "no_enrollment"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("synthesis_refused", K_REFUSED, {"reason": "no_enrollment"}),
    ]))

    s.append(scenario(23, "second draft while a consent is effective -> error, no row", [
        *standard_grant(),
        draft_op(5, nonce_seed=777,
                 expect={"error": "precondition", "consent_rows": 1}),
    ], [
        *AUDIT_GRANTED,
    ]))

    s.append(scenario(24, "refusal persisted as an utterance row", [
        init_op(), create_op(), enroll_op(),
        say_op(3, expect={"decision": "refused", "reason": "no_consent"}, save_as="u1"),
        {"op": "check_utterance", "utterance": "$u1",
         "expect": {"status": "refused", "refusal_reason": "no_consent",
                    "consent_id": None}},
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "no_consent"}),
    ]))

    s.append(scenario(25, "precedence: disabled beats revoked", [
        *standard_grant(),
        {"op": "revoke", "profile": P, "reason": None, "now": T(5),
         "expect": {"status": "revoked"}},
        {"op": "set_enabled", "profile": P, "enabled": False, "now": T(6)},
        say_op(7, expect={"decision": "refused", "reason": "profile_disabled"}),
    ], [
        *AUDIT_GRANTED,
        A("consent_revoked", K_REVOKED),
        A("profile_disabled", []),
        A("synthesis_refused", K_REFUSED, {"reason": "profile_disabled"}),
    ]))

    s.append(scenario(26, "history: older rejected + newer revoked -> consent_revoked", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        grant_op(4, CONSENT_IMPOSTOR,
                 expect={"status": "rejected", "reject_reason": "speaker_mismatch"}),
        draft_op(5, nonce_seed=90210),
        grant_op(6),
        {"op": "revoke", "profile": P, "reason": None, "now": T(7),
         "expect": {"status": "revoked"}},
        say_op(8, expect={"decision": "refused", "reason": "consent_revoked"}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_rejected", K_REJECTED_SCORED),
        A("consent_drafted", K_DRAFTED),
        A("consent_verified", K_VERIFIED),
        A("consent_revoked", K_REVOKED),
        A("synthesis_refused", K_REFUSED, {"reason": "consent_revoked"}),
    ]))

    s.append(scenario(27, "history: older revoked + newer expired -> consent_expired", [
        *standard_grant(),
        {"op": "revoke", "profile": P, "reason": None, "now": T(5),
         "expect": {"status": "revoked"}},
        draft_op(6, nonce_seed=90210, expires_at=T(30)),
        grant_op(7, CONSENT_OK_2),
        say_op(45, expect={"decision": "refused", "reason": "consent_expired"}),
    ], [
        *AUDIT_GRANTED,
        A("consent_revoked", K_REVOKED),
        A("consent_drafted", K_DRAFTED),
        A("consent_verified", K_VERIFIED),
        A("synthesis_refused", K_REFUSED, {"reason": "consent_expired"}),
    ]))

    s.append(scenario(28, "enroll 2xS + 1x S's sibling -> incoherent_enrollment, rolled back", [
        init_op(), create_op(),
        {"op": "enroll", "profile": P,
         "takes": ["S03/enroll/0", "S03/enroll/1", FOREIGN_SIBLING], "now": T(2),
         "expect": {"accepted": 2, "rejected": 1,
                    "reject_reasons": ["incoherent_enrollment"],
                    "enrolled": False, "centroid_unchanged": True}},
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("sample_rejected", K_SAMPLE_REJECTED_LOO, {"reason": "incoherent_enrollment"}),
    ]))

    s.append(scenario(29, "enroll 2xS + 1x unrelated speaker -> incoherent_enrollment", [
        init_op(), create_op(),
        {"op": "enroll", "profile": P,
         "takes": ["S03/enroll/0", "S03/enroll/1", FOREIGN_SPEAKER], "now": T(2),
         "expect": {"accepted": 2, "rejected": 1,
                    "reject_reasons": ["incoherent_enrollment"],
                    "enrolled": False, "centroid_unchanged": True}},
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("sample_rejected", K_SAMPLE_REJECTED_LOO, {"reason": "incoherent_enrollment"}),
    ]))

    s.append(scenario(30, "draft, drop below the FR-3 minimum, grant -> not_enrolled", [
        init_op(), create_op(), enroll_op(),
        draft_op(3),
        {"op": "remove_sample", "profile": P, "sample_index": 2, "now": T(4),
         "expect": {"enrolled": False}},
        grant_op(5, CONSENT_OK,
                 expect={"status": "rejected", "reject_reason": "not_enrolled",
                         "score_fields_null": True, "audio_sha256_null": True}),
    ], [
        A("profile_created", K_PROFILE_CREATED),
        A("sample_added", K_SAMPLE_ADDED), A("sample_added", K_SAMPLE_ADDED),
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("sample_removed", K_SAMPLE_REMOVED),
        A("consent_rejected", K_REJECTED_EARLY, {"reason": "not_enrolled"}),
    ]))

    s.append(scenario(31, "precedence: expired beats enrollment_changed", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, expires_at=T(30)),
        grant_op(4),
        {"op": "enroll", "profile": P, "takes": [EXTRA_SAMPLE], "now": T(5),
         "expect": {"accepted": 1, "rejected": 0}},
        say_op(45, expect={"decision": "refused", "reason": "consent_expired"}),
    ], [
        *AUDIT_GRANTED,
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "consent_expired"}),
    ]))

    s.append(scenario(32, "precedence: purged beats revoked", [
        *standard_grant(),
        {"op": "revoke", "profile": P, "reason": None, "now": T(5),
         "expect": {"status": "revoked"}},
        {"op": "purge", "profile": P, "now": T(6),
         "expect": {"files_deleted": 4}},
        say_op(7, expect={"decision": "refused", "reason": "profile_purged"}),
    ], [
        *AUDIT_GRANTED,
        A("consent_revoked", K_REVOKED),
        A("profile_purged", K_PURGED),
        A("synthesis_refused", K_REFUSED, {"reason": "profile_purged"}),
    ]))

    s.append(scenario(33, "precedence: enrollment_changed beats scope_mismatch", [
        init_op(), create_op(), enroll_op(),
        draft_op(3, scope=["announcement"]),
        grant_op(4),
        {"op": "enroll", "profile": P, "takes": [EXTRA_SAMPLE], "now": T(5),
         "expect": {"accepted": 1, "rejected": 0}},
        say_op(6, context="doorbell",
               expect={"decision": "refused", "reason": "enrollment_changed"}),
    ], [
        *AUDIT_GRANTED,
        A("sample_added", K_SAMPLE_ADDED),
        A("synthesis_refused", K_REFUSED, {"reason": "enrollment_changed"}),
    ]))

    s.append(scenario(34, "deliver then purge: delivered copies erased, receipts de-pathed", [
        *standard_grant(),
        {"op": "add_target", "target": "sink", "kind": "file_sink", "now": T(5)},
        say_op(6, expect={"decision": "authorized"}, save_as="u1"),
        {"op": "deliver", "utterance": "$u1", "target": "sink", "now": T(7),
         "expect": {"status": "succeeded", "files_exist": True}, "save_as": "d1"},
        {"op": "purge", "profile": P, "now": T(8),
         "expect": {"files_deleted": 5, "delivered_deleted": 2,
                    "delivered_missing": 0, "delivered_failed": 0}},
        {"op": "check_delivery", "delivery": "$d1",
         "expect": {"path_gone": True, "path_sha256_present": True,
                    "delivered_files_exist": False}},
        say_op(9, expect={"decision": "refused", "reason": "profile_purged"}),
    ], [
        *AUDIT_GRANTED,
        A("synthesis_authorized", K_AUTHORIZED),
        A("utterance_rendered", K_RENDERED),
        A("delivery_succeeded", K_DELIVERY_OK),
        A("consent_revoked", K_REVOKED, {"reason": "profile purged"}),
        A("profile_purged", K_PURGED, {"delivered_deleted": 2, "files_deleted": 5}),
        A("synthesis_refused", K_REFUSED, {"reason": "profile_purged"}),
    ]))

    s.append(scenario(35, "enrollment revert does not resurrect the superseded consent", [
        *standard_grant(),
        {"op": "enroll", "profile": P, "takes": [EXTRA_SAMPLE], "now": T(5),
         "expect": {"accepted": 1, "rejected": 0}},
        draft_op(6, nonce_seed=90210),
        grant_op(7, CONSENT_OK_2),
        {"op": "remove_sample", "profile": P, "sample_index": 3, "now": T(8),
         "expect": {"enrolled": True, "fingerprint_changed": True}},
        say_op(9, expect={"decision": "refused", "reason": "enrollment_changed"}),
    ], [
        *AUDIT_GRANTED,
        A("sample_added", K_SAMPLE_ADDED),
        A("consent_drafted", K_DRAFTED),
        A("consent_verified", K_VERIFIED),
        A("sample_removed", K_SAMPLE_REMOVED),
        A("synthesis_refused", K_REFUSED, {"reason": "enrollment_changed"}),
    ]))

    assert [entry["id"] for entry in s] == list(range(1, 36))
    return s


# --------------------------------------------------------------------------- #
# M5 tamper cases
# --------------------------------------------------------------------------- #

SESSION_EVENTS = 30
"""The scripted session in ``evals/metrics.py`` produces exactly this many
audit records; the case seq numbers below index into it."""


def build_tamper_cases() -> dict[str, Any]:
    return {
        "session_events": SESSION_EVENTS,
        "cases": [
            {"case": 1, "name": "mutate a detail field",
             "mutation": {"kind": "edit_detail", "seq": 6, "key": "similarity",
                          "value": 0.123456},
             "expected": {"ok": False, "bad_seq": 6}},
            {"case": 2, "name": "mutate ts",
             "mutation": {"kind": "edit_ts", "seq": 15, "value": "2031-01-01T00:00:00Z"},
             "expected": {"ok": False, "bad_seq": 15}},
            {"case": 3, "name": "delete a mid-chain record",
             "mutation": {"kind": "delete", "seq": 9},
             "expected": {"ok": False, "bad_seq": 10}},
            {"case": 4, "name": "reorder two records",
             "mutation": {"kind": "swap_contents", "seq_a": 7, "seq_b": 8},
             "expected": {"ok": False, "bad_seq": 7}},
            {"case": 5, "name": "rehash ignoring prev_hash",
             "mutation": {"kind": "rehash_without_prev", "seq": 23},
             "expected": {"ok": False, "bad_seq": 23}},
            {"case": 6, "name": "truncate the tail, append a forged record",
             "mutation": {"kind": "truncate_and_forge", "from_seq": 27},
             "expected": {"ok": False, "bad_seq": 27}},
            {"case": 7, "name": "truncate and re-append correctly chained records "
                                "(undetectable by design; FR-12 stated limit)",
             "mutation": {"kind": "truncate_and_recompute", "from_seq": 27,
                          "edit_key": "reason", "edit_value": "cover story"},
             "expected": {"ok": True}},
        ],
    }


def dump(path: Path, payload: Any, write: bool) -> bool:
    text = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if write:
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
        return True
    if not path.exists():
        print(f"MISSING {path}")
        return False
    if path.read_text(encoding="utf-8") != text:
        print(f"STALE {path} (re-run with --write)")
        return False
    print(f"ok {path.name}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Author the M4/M5 fixtures.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    ok = dump(FIXTURES_DIR / "consent_scenarios.json", build_scenarios(), args.write)
    ok &= dump(FIXTURES_DIR / "tamper_cases.json", build_tamper_cases(), args.write)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

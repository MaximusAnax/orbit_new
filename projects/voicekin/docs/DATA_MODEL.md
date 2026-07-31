# VoiceKin — Data Model

Three storage classes:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/voicekin/data/`): the consent statement template and the
  calibration file (decision thresholds, embedder normalization constants,
  screening limits, with a provenance note describing how each value was
  derived from the dev fixture split). Loaded and validated at `voicekin
  init`; the files remain the source of truth.
- **SQLite** (user state, default `~/.voicekin/voicekin.db`, path
  configurable; in-memory backend for tests): profiles, samples, consents,
  utterances, deliveries, targets, audit chain. Repository pattern, stdlib
  `sqlite3`.
- **Managed audio files** (blobs stay out of the DB): under the data home,
  `audio/enroll/<profile_id>/<sample_id>.wav`,
  `audio/consent/<profile_id>/<consent_id>.wav`,
  `audio/out/<utterance_id>.wav`. Rows store relative paths + sha256; purge
  (FR-1) deletes files and blanks paths but keeps hashes.

All models are Pydantic v2 in `src/voicekin/models.py`; the store maps them to
the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers — the
engine never reads the clock (FR-15). All audio is 16 kHz mono PCM16 after
intake (FR-2).

## Enumerations

| Enum | Values |
|---|---|
| `ProfileStatus` | `active`, `purged` |
| `SampleStatus` | `accepted`, `rejected` |
| `SampleRejectReason` | `too_short`, `too_long`, `clipped`, `low_snr`, `low_voiced_ratio`, `bad_format`, `incoherent_enrollment` |
| `ConsentStatus` | `draft`, `verified`, `rejected`, `revoked` |
| `ConsentRejectReason` | `speaker_mismatch`, `audio_quality`, `reused_enrollment_audio` |
| `Context` | `announcement`, `reminder`, `alarm`, `doorbell`, `timer`, `status` |
| `RefusalReason` | `profile_purged`, `profile_disabled`, `no_enrollment`, `no_consent`, `consent_rejected`, `consent_revoked`, `consent_expired`, `enrollment_changed`, `scope_mismatch` |
| `UtteranceStatus` | `refused`, `rendered` |
| `DeliveryStatus` | `succeeded`, `failed`, `refused` |
| `TargetKind` | `file_sink`, `home_assistant` |
| `AuditEvent` | `profile_created`, `profile_disabled`, `profile_enabled`, `profile_purged`, `sample_added`, `sample_removed`, `sample_rejected`, `consent_drafted`, `consent_verified`, `consent_rejected`, `consent_revoked`, `synthesis_authorized`, `synthesis_refused`, `utterance_rendered`, `delivery_succeeded`, `delivery_failed`, `delivery_refused` |

`RefusalReason` order above **is** the FR-6 precedence order.

## Entities

### VoiceProfile — SQLite `voice_profile`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug, e.g. `abdoul`, `partner` |
| `display_name` | str | |
| `relationship` | str | free text: `self`, `partner`, `parent`, … |
| `status` | ProfileStatus | `purged` is terminal (invariant: no transition out) |
| `enabled` | bool | operator kill-switch independent of consent (FR-1) |
| `embedder_id` | str \| null | adapter version that produced the centroid, e.g. `spectral-v1` |
| `centroid` | JSON list[float] \| null | L2-normalized mean of accepted-sample embeddings; null until FR-3 minimums met; **derived** — recomputed on any sample mutation |
| `enrollment_fingerprint` | str \| null | `sha256(embedder_id ‖ sorted accepted-sample sha256s)`; **derived**; null until enrolled |
| `voice_params` | JSON \| null | `{f0_base_hz, f0_range_hz, formant_scale, tilt_db_oct}` **derived** from enrollment analysis (FR-3); feeds the stub synthesizer |
| `created_at`, `updated_at` | str | ISO ts, caller-supplied |

Invariants: `id` immutable; `centroid`, `enrollment_fingerprint`,
`voice_params` are always mutually consistent with the current accepted
sample set (recomputed in the same transaction as any sample change); purge
nulls `centroid` and `voice_params` and deletes files but **keeps**
`enrollment_fingerprint` (it is a hash, needed for audit interpretation).

### EnrollmentSample — SQLite `enrollment_sample`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | uuid4 |
| `profile_id` | str FK → voice_profile | |
| `path` | str \| null | relative path under audio home; nulled on purge |
| `sha256` | str | of the *normalized* 16 kHz mono PCM payload (stable across container quirks) |
| `duration_s` | float | after intake |
| `snr_db` | float | FR-2 proxy |
| `voiced_ratio` | float | 0–1 |
| `embedding` | JSON list[float] \| null | per-sample; nulled on purge |
| `status` | SampleStatus | |
| `reject_reason` | SampleRejectReason \| null | set iff `rejected` (invariant) |
| `added_at` | str | ISO ts |

Invariants: `(profile_id, sha256)` unique among accepted samples (no
duplicate audio in one enrollment); rejected samples persist for the audit
trail but never contribute to centroid/fingerprint; removing a sample is a
hard delete of the row + file, audited with the sample's hash.

### ConsentRecord — SQLite `consent_record`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | uuid4 |
| `profile_id` | str FK | |
| `status` | ConsentStatus | state machine: `draft → verified \| rejected`; `verified → revoked`; no other transitions (invariant) |
| `scope_contexts` | JSON list[Context] | non-empty; from the draft |
| `expires_at` | str \| null | ISO ts; null = until revoked |
| `nonce` | str | 8 random base32 chars, generated at draft; stored for future ASR check (SCOPE non-goal 4) |
| `statement_text` | str | fully rendered statement the owner read (template + name + scope + expiry + nonce + date) |
| `audio_path` | str \| null | consent recording; null while draft, nulled on purge |
| `audio_sha256` | str \| null | of normalized payload |
| `similarity` | float \| null | cosine(consent embedding, profile centroid) at grant time |
| `threshold` | float \| null | θ_verify used (copied from calibration — record stays interpretable if calibration changes) |
| `embedder_id` | str \| null | embedder at grant time |
| `enrollment_fingerprint` | str \| null | profile fingerprint at grant time — the **binding** (FR-5/6) |
| `reject_reason` | ConsentRejectReason \| null | set iff `rejected` |
| `drafted_at`, `decided_at`, `revoked_at` | str \| null | ISO ts |
| `revocation_reason` | str \| null | free text |

Invariants: verified/rejected records are immutable except the single
`verified → revoked` transition; **at most one draft per profile** (a new
draft deletes the old draft — drafts are the only deletable consent rows);
**at most one *effective* consent per profile**, where effective :=
`verified ∧ revoked_at is null ∧ (expires_at is null ∨ now < expires_at) ∧
enrollment_fingerprint = profile.enrollment_fingerprint` — enforced at draft
time (drafting refused while one is effective) and re-evaluated by the FR-6
gate at every authorization; `similarity`, `threshold`, `embedder_id`,
`enrollment_fingerprint` are non-null iff status ∈ {verified, rejected}.

### Utterance — SQLite `utterance`

One row per synthesis *attempt* — refusals included ("audit log of every
synthesis" means failed ones too).

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | uuid4; watermark payload carries its first 64 bits |
| `profile_id` | str FK | |
| `consent_id` | str FK \| null | the consent that authorized it; null iff refused before an effective consent was found |
| `text` | str | normalized text actually rendered (≤ 500 chars); original kept in `text_raw` |
| `text_raw` | str | as submitted |
| `context` | Context | |
| `status` | UtteranceStatus | |
| `refusal_reason` | RefusalReason \| null | set iff `refused` (invariant) |
| `synth_id` | str \| null | adapter version, e.g. `stub-v1` |
| `seed` | int \| null | synthesis seed |
| `output_path` | str \| null | `audio/out/<id>.wav`; nulled on purge |
| `output_sha256` | str \| null | of the watermarked WAV payload |
| `duration_s` | float \| null | |
| `requested_at` | str | ISO ts (the `now` passed to the gate) |

Invariants: rendered rows are immutable; `output_sha256` non-null iff
`rendered`; byte determinism — identical (profile state, text, seed,
synth_id) ⇒ identical `output_sha256` (FR-9, eval-relevant).

### Delivery — SQLite `delivery`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | uuid4 |
| `utterance_id` | str FK | must reference a `rendered` utterance (invariant) |
| `target_id` | str FK → device_target | |
| `status` | DeliveryStatus | `refused` = FR-6 re-authorization failed at delivery time |
| `refusal_reason` | RefusalReason \| null | set iff `refused` |
| `detail` | JSON | receipt: file path written, or HA HTTP status + entity_id; error text on `failed` |
| `requested_at` | str | ISO ts |

### DeviceTarget — SQLite `device_target`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug, e.g. `living-room` |
| `kind` | TargetKind | |
| `config` | JSON | `file_sink`: `{dir}`; `home_assistant`: `{entity_id, media_dir}` (URL + token come from env, never stored — no secrets in DB) |
| `enabled` | bool | |
| `created_at` | str | ISO ts |

### AuditRecord — SQLite `audit_record` (append-only hash chain)

| Field | Type | Notes |
|---|---|---|
| `seq` | int PK | monotonic from 1, no gaps (invariant) |
| `ts` | str | ISO ts, caller-supplied |
| `event` | AuditEvent | |
| `profile_id` | str \| null | subject refs — ids only, never content |
| `consent_id` | str \| null | |
| `utterance_id` | str \| null | |
| `detail` | JSON | event-specific facts: hashes, scores, reasons, thresholds; **never audio, never paths that outlive purge** |
| `prev_hash` | str | previous record's `record_hash`; genesis uses `sha256("voicekin-genesis")` |
| `record_hash` | str | `sha256(canonical_json({seq, ts, event, profile_id, consent_id, utterance_id, detail}) ‖ prev_hash)`; canonical JSON = sorted keys, separators `(",", ":")`, UTF-8 |

Invariants: append-only — the Repository exposes no UPDATE/DELETE for this
table; every FR-listed event writes exactly one record in the same
transaction as its state change; `audit verify` recomputes the chain from
genesis and reports the first mismatching `seq` (FR-12).

### Committed dataset files

`data/consent_statement.txt` — template with `{owner_name}`, `{operator}`,
`{contexts}`, `{expiry_clause}`, `{nonce}`, `{date}` placeholders:

```
I, {owner_name}, consent to the VoiceKin system operated by {operator}
reproducing my voice for: {contexts}. This consent {expiry_clause} and I may
revoke it at any time, effective immediately. Verification code: {nonce}.
Date: {date}.
```

`data/calibration.json` — every tunable decision constant, with provenance:

| Field | Type | Notes |
|---|---|---|
| `embedder_id` | str | constants are only valid for this embedder (checked at load) |
| `theta_verify` | float | consent decision threshold (FR-5) |
| `theta_enroll` | float | leave-one-out coherence threshold (FR-3) |
| `feature_norms` | list[{mean, scale}] | 16 per-dimension affine constants (FR-4) |
| `screening` | object | FR-2 limits: durations, clipping frac, SNR dB, voiced ratio |
| `provenance` | str | how/when values were derived from the **dev** fixture split (EVALS.md) |

Invariant: services refuse to combine a calibration file with a mismatched
`embedder_id` (prevents scoring `spectral-v1` embeddings with ECAPA
thresholds).

## Relationships (summary)

```
VoiceProfile (1) ──▶ EnrollmentSample (N)          [centroid/fingerprint derived]
VoiceProfile (1) ──▶ ConsentRecord (N)             [≤1 draft; ≤1 effective]
VoiceProfile (1) ──▶ Utterance (N) ──▶ Delivery (N) ──▶ DeviceTarget
Utterance (0..1) ──▶ ConsentRecord                 [the consent that authorized it]
AuditRecord (chain) ── references ids of all of the above; owns nothing
Authorization (FR-6): in-memory value only — never stored; its facts land in
  the utterance/delivery row and the audit record
```

## Example records

`voice_profile`:

```json
{
  "id": "partner", "display_name": "Amina", "relationship": "partner",
  "status": "active", "enabled": true,
  "embedder_id": "spectral-v1",
  "centroid": [0.212, -0.094, 0.371, 0.052, -0.188, 0.301, 0.144, -0.077,
               0.256, 0.033, -0.145, 0.209, 0.118, -0.062, 0.174, 0.089],
  "enrollment_fingerprint": "9f2c41d8a06e5b17c3aa804d2f96e1b0d4c7358a12ef6690b8d3a45c7e01f2ab",
  "voice_params": {"f0_base_hz": 204.0, "f0_range_hz": 46.0,
                    "formant_scale": 1.13, "tilt_db_oct": -11.2},
  "created_at": "2026-07-31T18:02:11Z", "updated_at": "2026-07-31T18:12:40Z"
}
```

`enrollment_sample` (one accepted, one rejected):

```json
{
  "id": "6a8f0c2e-93d1-4a56-8f0b-2c7d5e1a94b3", "profile_id": "partner",
  "path": "audio/enroll/partner/6a8f0c2e-93d1-4a56-8f0b-2c7d5e1a94b3.wav",
  "sha256": "3d7a9c...e41f", "duration_s": 8.4, "snr_db": 27.3,
  "voiced_ratio": 0.63, "embedding": [0.198, -0.101, 0.355, "..."],
  "status": "accepted", "reject_reason": null,
  "added_at": "2026-07-31T18:05:02Z"
}
```

```json
{
  "id": "b91e33f7-5c04-4d1a-a2e8-77c6d9f01245", "profile_id": "partner",
  "path": null, "sha256": "77b2f0...9a3c", "duration_s": 1.9, "snr_db": 31.0,
  "voiced_ratio": 0.71, "embedding": null,
  "status": "rejected", "reject_reason": "too_short",
  "added_at": "2026-07-31T18:04:41Z"
}
```

`consent_record` (verified):

```json
{
  "id": "c4d2a7e1-08bb-4f63-9d15-3e6a80c2f974", "profile_id": "partner",
  "status": "verified",
  "scope_contexts": ["announcement", "reminder"],
  "expires_at": "2027-06-30T00:00:00Z",
  "nonce": "K3TQ7WZP",
  "statement_text": "I, Amina, consent to the VoiceKin system operated by Abdoul reproducing my voice for: announcements, reminders. This consent expires on 2027-06-30 and I may revoke it at any time, effective immediately. Verification code: K3TQ7WZP. Date: 2026-07-31.",
  "audio_path": "audio/consent/partner/c4d2a7e1-08bb-4f63-9d15-3e6a80c2f974.wav",
  "audio_sha256": "aa41cc...07de",
  "similarity": 0.83, "threshold": 0.62, "embedder_id": "spectral-v1",
  "enrollment_fingerprint": "9f2c41d8a06e5b17c3aa804d2f96e1b0d4c7358a12ef6690b8d3a45c7e01f2ab",
  "reject_reason": null,
  "drafted_at": "2026-07-31T18:08:19Z", "decided_at": "2026-07-31T18:12:40Z",
  "revoked_at": null, "revocation_reason": null
}
```

`utterance` (one rendered, one refused after revocation):

```json
{
  "id": "e7b19a04-2f6c-45d8-b3a1-90cd5e82f716", "profile_id": "partner",
  "consent_id": "c4d2a7e1-08bb-4f63-9d15-3e6a80c2f974",
  "text": "dinner is ready", "text_raw": "Dinner is ready!",
  "context": "announcement", "status": "rendered", "refusal_reason": null,
  "synth_id": "stub-v1", "seed": 7,
  "output_path": "audio/out/e7b19a04-2f6c-45d8-b3a1-90cd5e82f716.wav",
  "output_sha256": "51f8d2...c9a0", "duration_s": 2.9,
  "requested_at": "2026-07-31T18:30:00Z"
}
```

```json
{
  "id": "f02c6d81-9e4a-47b2-8c50-1ab3d7e94c28", "profile_id": "partner",
  "consent_id": null, "text": "the laundry is done", "text_raw": "The laundry is done",
  "context": "announcement", "status": "refused",
  "refusal_reason": "consent_revoked", "synth_id": null, "seed": null,
  "output_path": null, "output_sha256": null, "duration_s": null,
  "requested_at": "2026-08-02T09:15:00Z"
}
```

`device_target`:

```json
{
  "id": "living-room", "kind": "home_assistant",
  "config": {"entity_id": "media_player.living_room_speaker",
              "media_dir": "/mnt/ha-media/voicekin"},
  "enabled": true, "created_at": "2026-07-31T17:55:00Z"
}
```

`audit_record` (consecutive pair showing the chain):

```json
{
  "seq": 41, "ts": "2026-07-31T18:30:00Z", "event": "synthesis_authorized",
  "profile_id": "partner", "consent_id": "c4d2a7e1-08bb-4f63-9d15-3e6a80c2f974",
  "utterance_id": "e7b19a04-2f6c-45d8-b3a1-90cd5e82f716",
  "detail": {"context": "announcement", "scope": ["announcement", "reminder"],
              "enrollment_fingerprint": "9f2c41...f2ab"},
  "prev_hash": "b0c95a...4e12",
  "record_hash": "6de1f7...a883"
}
```

```json
{
  "seq": 42, "ts": "2026-07-31T18:30:01Z", "event": "utterance_rendered",
  "profile_id": "partner", "consent_id": "c4d2a7e1-08bb-4f63-9d15-3e6a80c2f974",
  "utterance_id": "e7b19a04-2f6c-45d8-b3a1-90cd5e82f716",
  "detail": {"synth_id": "stub-v1", "seed": 7,
              "output_sha256": "51f8d2...c9a0", "duration_s": 2.9},
  "prev_hash": "6de1f7...a883",
  "record_hash": "170b4c...59fe"
}
```

## Derived values (never stored, computed on demand)

- **Effective consent** for a profile at `now` (FR-6 predicate) — always
  evaluated fresh; caching it would reintroduce the invalidation problem the
  fingerprint design eliminates.
- **Authorization** — in-memory capability value; its evidence is persisted
  in utterance/delivery/audit rows, the value itself never is.
- **Watermark payload** — recomputable from `utterance.id`; the extractor
  (FR-10) resolves extracted ids against `utterance` + `audit_record`.

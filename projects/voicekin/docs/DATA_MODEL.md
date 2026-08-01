# VoiceKin — Data Model

Three storage classes:

- **Committed datasets** (read-only at runtime, versioned in git under
  `projects/voicekin/data/`): the consent statement template and the
  calibration file (decision thresholds, embedder normalization constants,
  screening limits, synthesis constants, with a provenance note describing how
  each value was derived from the dev fixture split). Loaded and validated at
  `voicekin init`; the files remain the source of truth.
- **SQLite** (user state, default `~/.voicekin/voicekin.db`, path
  configurable): instance, profiles, samples, consents, utterances,
  deliveries, targets, audit chain. Repository pattern, stdlib `sqlite3`. The
  test backend is the *same* `SQLiteRepository` opened on `":memory:"` — one
  implementation, per SCOPE's budget cuts.
- **Managed audio files** (blobs stay out of the DB): under the data home,
  `audio/enroll/<profile_id>/<sample_id>.wav`,
  `audio/consent/<profile_id>/<consent_id>.wav`,
  `audio/out/<utterance_id>.wav`. Rows store relative paths + sha256; purge
  (FR-7) deletes files and blanks paths but keeps hashes.

All models are Pydantic v2 in `src/voicekin/models.py`; the store maps them to
the tables below. Enumerations are Python `StrEnum`s; SQLite stores their
string values. Timestamps are ISO-8601 UTC strings supplied by callers — the
engine never reads the clock (FR-15). All audio is 16 kHz mono PCM16 after
intake (FR-2). **Row ids are derived, never random** (FR-15): each is
`sha256(canonical_json(id_material))[:32]`, 32 lowercase hex chars, over the
material listed per entity below.

## Enumerations

| Enum | Values |
|---|---|
| `ProfileStatus` | `active`, `purged` |
| `SampleStatus` | `accepted`, `rejected` |
| `SampleRejectReason` | `too_short`, `too_long`, `clipped`, `low_snr`, `low_voiced_ratio`, `bad_format`, `incoherent_enrollment` |
| `ConsentStatus` | `draft`, `verified`, `rejected`, `revoked` |
| `ConsentRejectReason` | `not_enrolled`, `audio_quality`, `reused_enrollment_audio`, `speaker_mismatch` |
| `Context` | `announcement`, `reminder`, `alarm`, `doorbell`, `timer`, `status` |
| `RefusalReason` | `profile_purged`, `profile_disabled`, `no_enrollment`, `no_consent`, `consent_rejected`, `consent_revoked`, `consent_expired`, `enrollment_changed`, `scope_mismatch` |
| `UtteranceStatus` | `refused`, `rendered` |
| `DeliveryStatus` | `succeeded`, `failed`, `refused` |
| `TargetKind` | `file_sink`, `home_assistant` |
| `AuditEvent` | `profile_created`, `profile_disabled`, `profile_enabled`, `profile_purged`, `sample_added`, `sample_removed`, `sample_rejected`, `consent_drafted`, `consent_verified`, `consent_rejected`, `consent_revoked`, `synthesis_authorized`, `synthesis_refused`, `utterance_rendered`, `delivery_succeeded`, `delivery_failed`, `delivery_refused` |

`RefusalReason` order above **is** the FR-6 evaluation order, and
`ConsentRejectReason` order **is** the FR-5(b) check order.

## Entities

### Instance — SQLite `instance` (exactly one row)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | always `1`; invariant: no second row |
| `operator_name` | str | who runs this system; renders as `{operator}` in the consent statement (FR-5) |
| `data_home` | str | absolute path of the managed data home |
| `schema_version` | int | migration guard |
| `created_at` | str | ISO ts, caller-supplied |

Created by `voicekin init`; every service call fails fast if it is missing.

### VoiceProfile — SQLite `voice_profile`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | slug supplied by the operator, e.g. `abdoul`, `partner` |
| `display_name` | str | renders as `{owner_name}` in the consent statement |
| `relationship` | str | free text: `self`, `partner`, `parent`, … |
| `status` | ProfileStatus | `purged` is terminal (invariant: no transition out) |
| `enabled` | bool | operator kill-switch independent of consent (FR-1) |
| `embedder_id` | str \| null | adapter version that produced the centroid, e.g. `spectral-v1` |
| `centroid` | JSON list[float] \| null | mean of accepted-sample embeddings (plain, not L2-normalized — the shipped distance scoring measures against the true center; REVIEW.md deviation 3); null until FR-3 minimums met; **derived** — recomputed on any sample mutation |
| `enrollment_fingerprint` | str \| null | `sha256(embedder_id ‖ sorted accepted-sample sha256s)`; **derived**; null until enrolled-complete |
| `voice_params` | JSON \| null | `{f0_base_hz, f0_range_hz, formant_scale, tilt_db_oct, band_gains_db[8]}` **derived** from enrollment analysis (FR-3; the band gains are Klatt-style per-band amplitude controls, REVIEW.md deviation 4); feeds the stub synthesizer |
| `enrolled_at` | str \| null | ISO ts at which the profile first became enrolled-complete; drives the `awaiting-consent` flag (FR-1) |
| `next_sample_index` | int | monotonic counter for FR-15 sample id derivation |
| `next_draft_index` | int | monotonic counter for FR-15 consent id derivation and FR-6 governing-record ordering |
| `next_attempt_index` | int | monotonic counter for FR-15 utterance id derivation |
| `created_at`, `updated_at` | str | ISO ts, caller-supplied |

Invariants: `id` immutable; `centroid`, `enrollment_fingerprint`,
`voice_params` are always mutually consistent with the current accepted
sample set (recomputed in the same transaction as any sample change); the
three `next_*_index` counters only increase and are never reused (they make
replays reproducible and give FR-6 a total order); purge nulls `centroid`,
`voice_params`, and `enrolled_at`, deletes files, but **keeps**
`enrollment_fingerprint` (it is a hash, needed for audit interpretation).

### EnrollmentSample — SQLite `enrollment_sample`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `sha256(["sample", profile_id, sha256, added_at, sample_index])[:32]` |
| `profile_id` | str FK → voice_profile | |
| `sample_index` | int | value of `voice_profile.next_sample_index` at insert |
| `path` | str \| null | relative path under audio home; nulled on purge |
| `sha256` | str | of the *normalized* 16 kHz mono PCM payload (stable across container quirks) |
| `duration_s` | float | after intake |
| `snr_db` | float | FR-2 proxy |
| `voiced_ratio` | float | 0–1 |
| `embedding` | JSON list[float] \| null | per-sample; null for rejected samples and after purge |
| `status` | SampleStatus | |
| `reject_reason` | SampleRejectReason \| null | set iff `rejected` (invariant) |
| `added_at` | str | ISO ts |

Invariants: `(profile_id, sha256)` unique among accepted samples (no
duplicate audio in one enrollment); rejected samples persist for the audit
trail but never contribute to centroid/fingerprint; removing a sample is a
hard delete of the row + file, audited with the sample's hash. A sample
rejected by the FR-3 leave-one-out coherence check is stored with
`reject_reason = incoherent_enrollment` and the *whole mutation is rolled
back* — the profile's centroid, fingerprint, and counters are unchanged
except for the rejected row and its `sample_rejected` audit record.

### ConsentRecord — SQLite `consent_record`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `sha256(["consent", profile_id, drafted_at, draft_index])[:32]` |
| `profile_id` | str FK | |
| `draft_index` | int | value of `voice_profile.next_draft_index` at draft; total-orders records within a profile |
| `status` | ConsentStatus | state machine: `draft → verified \| rejected`; `verified → revoked`; no other transitions (invariant) |
| `scope_contexts` | JSON list[Context] | non-empty; from the draft |
| `expires_at` | str \| null | ISO ts; null = until revoked |
| `nonce_seed` | int | 63-bit; supplied by the caller or drawn once by the CLI (FR-15) |
| `nonce` | str | 8 base32 chars = `base32(sha256("voicekin-nonce" ‖ id ‖ nonce_seed))[:8]`; **derived**, stored for the future ASR check (SCOPE non-goal 4) |
| `statement_text` | str | fully rendered statement the owner read (template + owner name + operator name + scope + expiry + nonce + date) |
| `audio_path` | str \| null | consent recording; null while draft, nulled on purge |
| `audio_sha256` | str \| null | of normalized payload |
| `similarity` | float \| null | `1 − ‖embedding − centroid‖²/score_scale` at grant time (the shipped scoring rule, REVIEW.md deviation 3) |
| `threshold` | float \| null | θ_verify used (copied from calibration — record stays interpretable if calibration changes) |
| `embedder_id` | str \| null | embedder at grant time |
| `enrollment_fingerprint` | str \| null | profile fingerprint at grant time — the **binding** (FR-5/6) |
| `reject_reason` | ConsentRejectReason \| null | set iff `rejected` |
| `drafted_at`, `decided_at`, `revoked_at` | str \| null | ISO ts |
| `revocation_reason` | str \| null | free text |

Invariants:

- verified/rejected records are immutable except the single
  `verified → revoked` transition;
- **at most one draft per profile** (a new draft deletes the old draft —
  drafts are the only deletable consent rows);
- **drafting is refused while a consent is effective**, where effective :=
  `status = verified ∧ revoked_at is null ∧ (expires_at is null ∨
  now < expires_at) ∧ enrollment_fingerprint =
  profile.enrollment_fingerprint`. Effectiveness is never cached; the FR-6
  gate re-evaluates it against the **governing** record (greatest
  `(drafted_at, draft_index)`) at every authorization. Consequence: at most
  one record can be effective *and* governing, and only that one authorizes;
- `similarity`, `threshold`, `embedder_id`, and `enrollment_fingerprint` are
  non-null **iff** `status ∈ {verified, revoked}` **or**
  (`status = rejected` ∧ `reject_reason = speaker_mismatch`) — i.e. exactly
  when a score was actually computed. Rejections for `not_enrolled`,
  `audio_quality`, or `reused_enrollment_audio` short-circuit before any
  embedding exists and leave all four null;
- `audio_sha256` is non-null once a recording has been read and normalized —
  i.e. for every status except `draft` and rejections with reason
  `not_enrolled` (which short-circuits before the file is read).
  `audio_path` is non-null only for `verified`/`revoked` records: **rejected
  consent recordings are hashed and discarded, never stored** (VoiceKin does
  not retain an impostor's or a failed take's audio), and purge nulls the
  path on the records that do have one.

### Utterance — SQLite `utterance`

One row per synthesis *attempt* — refusals included ("audit log of every
synthesis" means failed ones too).

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `sha256(["utterance", profile_id, text, context, requested_at, attempt_index])[:32]` |
| `profile_id` | str FK | |
| `attempt_index` | int | value of `voice_profile.next_attempt_index` at insert |
| `consent_id` | str FK \| null | the consent that authorized it; null iff refused |
| `text` | str | normalized text actually rendered (≤ 500 chars) |
| `text_raw` | str | as submitted |
| `context` | Context | |
| `status` | UtteranceStatus | |
| `refusal_reason` | RefusalReason \| null | set iff `refused` (invariant) |
| `synth_id` | str \| null | adapter version, e.g. `stub-v1` |
| `seed` | int \| null | synthesis seed |
| `next_delivery_index` | int | monotonic counter for FR-15 delivery id derivation |
| `output_path` | str \| null | `audio/out/<id>.wav`; nulled on purge |
| `output_sha256` | str \| null | sha256 of the WAV **data-chunk bytes only** (FR-10) |
| `duration_s` | float \| null | |
| `requested_at` | str | ISO ts (the `now` passed to the gate) |

Invariants: rendered rows are immutable; `output_sha256`, `output_path`,
`synth_id`, `seed`, `duration_s`, `consent_id` are non-null iff `rendered`,
and all null iff `refused`; **byte determinism** — the rendered WAV payload
is a pure function of `(profile.voice_params, text, seed, synth_id,
sample_rate)`, so any two renders sharing those five values have identical
`output_sha256`, regardless of ids or timestamps (FR-9/FR-15, eval-relevant).

### Delivery — SQLite `delivery`

| Field | Type | Notes |
|---|---|---|
| `id` | str PK | `sha256(["delivery", utterance_id, target_id, requested_at, delivery_index])[:32]` |
| `utterance_id` | str FK | must reference a `rendered` utterance (invariant) |
| `delivery_index` | int | value of `utterance.next_delivery_index` at insert |
| `target_id` | str FK → device_target | |
| `status` | DeliveryStatus | `refused` = FR-6 re-authorization failed at delivery time |
| `refusal_reason` | RefusalReason \| null | set iff `refused` |
| `detail` | JSON | receipt: `{path, manifest_path}` for `file_sink`; `{path, entity_id, http_status}` for `home_assistant`; `{error}` on `failed`. **After purge**, `path`/`manifest_path` are replaced by `path_sha256`/`manifest_path_sha256` (FR-7d) so the receipt stays auditable without pointing at a location |
| `requested_at` | str | ISO ts |

Invariant: `detail.path` exists exactly while the delivered copy is still
VoiceKin's responsibility; purge either deletes the file and drops the path,
or records the deletion failure in the `profile_purged` audit detail and
still drops the path.

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
| `detail` | JSON | event-specific facts: hashes, scores, reasons, thresholds, counts; **never audio, never paths that outlive purge**. Floats are rounded to 6 dp before hashing |
| `prev_hash` | str | previous record's `record_hash`; genesis uses `sha256("voicekin-genesis")` |
| `record_hash` | str | `sha256(canonical_json({seq, ts, event, profile_id, consent_id, utterance_id, detail}) ‖ prev_hash)`; canonical JSON = `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded UTF-8, floats pre-rounded to 6 dp |

Invariants: append-only — the Repository exposes no UPDATE/DELETE for this
table; every FR-listed event writes exactly one record in the same
transaction as its state change; `audit verify` recomputes the chain from
genesis, reports the first mismatching `seq`, and on success prints
`head_seq`/`head_hash` for out-of-band anchoring (FR-12). The chain detects
edits, deletions, and reorderings; it cannot detect a tail truncation with a
correctly recomputed re-append (FR-12 stated limit, EVALS M5 case 7).

Notable `detail` payloads (fixed keys, so scenario expectations are exact):

| Event | `detail` keys |
|---|---|
| `sample_added` | `sample_sha256`, `duration_s`, `snr_db`, `voiced_ratio`, `enrollment_fingerprint` |
| `sample_rejected` | `sample_sha256`, `reason`, and for `incoherent_enrollment` also `loo_similarity`, `theta_enroll` |
| `consent_drafted` | `scope`, `expires_at`, `nonce`, `nonce_seed`, `statement_sha256` |
| `consent_verified` / `consent_rejected` | `similarity`, `threshold`, `embedder_id`, `enrollment_fingerprint`, `audio_sha256` (+ `reason` when rejected; score fields omitted when no score was computed) |
| `consent_revoked` | `reason` |
| `synthesis_authorized` | `context`, `scope`, `enrollment_fingerprint` |
| `synthesis_refused` | `context`, `reason` |
| `utterance_rendered` | `synth_id`, `seed`, `output_sha256`, `duration_s` |
| `delivery_*` | `target_id`, `target_kind`, `output_sha256`, `status` (+ `reason` / `error`) |
| `profile_purged` | `files_deleted`, `files_missing`, `files_failed`, `delivered_deleted`, `delivered_missing`, `delivered_failed`, `enrollment_fingerprint` |

### Committed dataset files

`data/consent_statement.txt` — template with `{owner_name}`, `{operator}`,
`{contexts}`, `{expiry_clause}`, `{nonce}`, `{date}` placeholders.
`{owner_name}` comes from `voice_profile.display_name`; `{operator}` comes
from `instance.operator_name` (FR-1); `{date}` is the caller-supplied `now`
truncated to a date.

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
| `score_scale` | float | denominator of the shipped distance similarity `s = 1 − ‖a−b‖²/score_scale` (REVIEW.md deviation 3) |
| `feature_norms` | list[{mean, scale}] | 16 per-dimension affine constants (FR-4) |
| `screening` | object | FR-2 limits: durations, clipping frac, SNR dB, voiced ratio |
| `unit_duration_ms` | int | 180; stub synthesis unit length (FR-8), also EVALS M3's duration check |
| `consent_grace_days` | int | 30; `awaiting-consent` flag window (FR-1) |
| `provenance` | str | how/when values were derived from the **dev** fixture split (EVALS.md), naming script + seed + split |

Invariants: services refuse to combine a calibration file with a mismatched
`embedder_id` (prevents scoring `spectral-v1` embeddings with ECAPA
thresholds); a committed test re-runs `evals/fixtures/calibrate.py` against
the dev split and asserts equality with this file, so editing embedder code
without bumping `embedder_id` fails the suite instead of silently leaving
stale constants (EVALS "calibration staleness").

## Relationships (summary)

```
Instance (1)     ── operator_name feeds every consent statement
VoiceProfile (1) ──▶ EnrollmentSample (N)          [centroid/fingerprint derived]
VoiceProfile (1) ──▶ ConsentRecord (N)             [≤1 draft; ≤1 effective;
                                                    greatest (drafted_at, draft_index) governs]
VoiceProfile (1) ──▶ Utterance (N) ──▶ Delivery (N) ──▶ DeviceTarget
Utterance (0..1) ──▶ ConsentRecord                 [the consent that authorized it]
AuditRecord (chain) ── references ids of all of the above; owns nothing
Authorization (FR-6): in-memory value only — never stored; its facts land in
  the utterance/delivery row and the audit record
```

## Example records

`instance`:

```json
{"id": 1, "operator_name": "Abdoul", "data_home": "/home/abdoul/.voicekin",
 "schema_version": 1, "created_at": "2026-07-31T17:50:00Z"}
```

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
  "enrolled_at": "2026-07-31T18:06:30Z",
  "next_sample_index": 3, "next_draft_index": 1, "next_attempt_index": 1,
  "created_at": "2026-07-31T18:02:11Z", "updated_at": "2026-07-31T18:12:40Z"
}
```

`enrollment_sample` (one accepted, one rejected):

```json
{
  "id": "6a8f0c2e93d14a568f0b2c7d5e1a94b3", "profile_id": "partner",
  "sample_index": 0,
  "path": "audio/enroll/partner/6a8f0c2e93d14a568f0b2c7d5e1a94b3.wav",
  "sha256": "3d7a9c...e41f", "duration_s": 8.4, "snr_db": 27.3,
  "voiced_ratio": 0.63, "embedding": [0.198, -0.101, 0.355, "..."],
  "status": "accepted", "reject_reason": null,
  "added_at": "2026-07-31T18:05:02Z"
}
```

```json
{
  "id": "b91e33f75c044d1aa2e877c6d9f01245", "profile_id": "partner",
  "sample_index": 1, "path": null, "sha256": "77b2f0...9a3c",
  "duration_s": 1.9, "snr_db": 31.0, "voiced_ratio": 0.71, "embedding": null,
  "status": "rejected", "reject_reason": "too_short",
  "added_at": "2026-07-31T18:04:41Z"
}
```

`consent_record` (verified):

```json
{
  "id": "c4d2a7e108bb4f639d153e6a80c2f974", "profile_id": "partner",
  "draft_index": 0, "status": "verified",
  "scope_contexts": ["announcement", "reminder"],
  "expires_at": "2027-06-30T00:00:00Z",
  "nonce_seed": 4815162342, "nonce": "K3TQ7WZP",
  "statement_text": "I, Amina, consent to the VoiceKin system operated by Abdoul reproducing my voice for: announcements, reminders. This consent expires on 2027-06-30 and I may revoke it at any time, effective immediately. Verification code: K3TQ7WZP. Date: 2026-07-31.",
  "audio_path": "audio/consent/partner/c4d2a7e108bb4f639d153e6a80c2f974.wav",
  "audio_sha256": "aa41cc...07de",
  "similarity": 0.83, "threshold": 0.62, "embedder_id": "spectral-v1",
  "enrollment_fingerprint": "9f2c41d8a06e5b17c3aa804d2f96e1b0d4c7358a12ef6690b8d3a45c7e01f2ab",
  "reject_reason": null,
  "drafted_at": "2026-07-31T18:08:19Z", "decided_at": "2026-07-31T18:12:40Z",
  "revoked_at": null, "revocation_reason": null
}
```

`consent_record` (rejected before scoring — note the four null score fields):

```json
{
  "id": "18ca07be5f9c4a3b8d61e0427cf5a913", "profile_id": "partner",
  "draft_index": 1, "status": "rejected",
  "scope_contexts": ["announcement"], "expires_at": null,
  "nonce_seed": 90210, "nonce": "R7M2XQ4B",
  "statement_text": "I, Amina, consent to ...",
  "audio_path": null, "audio_sha256": "aa41cc...07de",
  "similarity": null, "threshold": null, "embedder_id": null,
  "enrollment_fingerprint": null,
  "reject_reason": "reused_enrollment_audio",
  "drafted_at": "2026-08-03T09:00:00Z", "decided_at": "2026-08-03T09:01:12Z",
  "revoked_at": null, "revocation_reason": null
}
```

`utterance` (one rendered, one refused after revocation):

```json
{
  "id": "e7b19a042f6c45d8b3a190cd5e82f716", "profile_id": "partner",
  "attempt_index": 0, "consent_id": "c4d2a7e108bb4f639d153e6a80c2f974",
  "text": "dinner is ready", "text_raw": "Dinner is ready!",
  "context": "announcement", "status": "rendered", "refusal_reason": null,
  "synth_id": "stub-v1", "seed": 7, "next_delivery_index": 1,
  "output_path": "audio/out/e7b19a042f6c45d8b3a190cd5e82f716.wav",
  "output_sha256": "51f8d2...c9a0", "duration_s": 2.9,
  "requested_at": "2026-07-31T18:30:00Z"
}
```

```json
{
  "id": "f02c6d819e4a47b28c501ab3d7e94c28", "profile_id": "partner",
  "attempt_index": 1, "consent_id": null,
  "text": "the laundry is done", "text_raw": "The laundry is done",
  "context": "announcement", "status": "refused",
  "refusal_reason": "consent_revoked", "synth_id": null, "seed": null,
  "next_delivery_index": 0, "output_path": null, "output_sha256": null,
  "duration_s": null, "requested_at": "2026-08-02T09:15:00Z"
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
  "profile_id": "partner", "consent_id": "c4d2a7e108bb4f639d153e6a80c2f974",
  "utterance_id": "e7b19a042f6c45d8b3a190cd5e82f716",
  "detail": {"context": "announcement", "scope": ["announcement", "reminder"],
              "enrollment_fingerprint": "9f2c41...f2ab"},
  "prev_hash": "b0c95a...4e12",
  "record_hash": "6de1f7...a883"
}
```

```json
{
  "seq": 42, "ts": "2026-07-31T18:30:01Z", "event": "utterance_rendered",
  "profile_id": "partner", "consent_id": "c4d2a7e108bb4f639d153e6a80c2f974",
  "utterance_id": "e7b19a042f6c45d8b3a190cd5e82f716",
  "detail": {"synth_id": "stub-v1", "seed": 7,
              "output_sha256": "51f8d2...c9a0", "duration_s": 2.9},
  "prev_hash": "6de1f7...a883",
  "record_hash": "170b4c...59fe"
}
```

## Derived values (never stored, computed on demand)

- **Governing consent** for a profile — the record with the greatest
  `(drafted_at, draft_index)`; the only record FR-6 consults.
- **Effective consent** predicate at `now` (FR-6) — always evaluated fresh;
  caching it would reintroduce the invalidation problem the fingerprint
  design eliminates.
- **Authorization** — in-memory capability value; its evidence is persisted
  in utterance/delivery/audit rows, the value itself never is.
- **Provenance lookup** — `verify-output` hashes a WAV's data-chunk bytes and
  selects `utterance` by `output_sha256`, then the audit records referencing
  that utterance id (FR-10). No watermark, no extra column.
- **`awaiting-consent` flag** — `enrolled_at is not null` ∧ no `verified`
  consent has ever existed for the profile ∧
  `now - enrolled_at > consent_grace_days` (FR-1); a display-only annotation,
  never an authorization input.

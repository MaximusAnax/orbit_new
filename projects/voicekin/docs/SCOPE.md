# VoiceKin — Scope

## One-liner

A consent-gated personal voice system for the smart home: enroll your own or a
family member's voice from audio samples, verify an explicit recorded consent
artifact against that enrollment by speaker match, and only then let the house
speak in that voice — every synthesis and every delivery authorized through a
single gate, attributable by content hash, and written to a tamper-evident
audit log; revocation kills the voice instantly.

## Problem statement

The owner wants the house to make announcements, reminders, and doorbell/timer
callouts in a familiar voice — their own, or a significant person's — instead
of a stock robot voice. Voice cloning tech makes this easy; that is exactly the
problem. A person's voice is a biometric identifier (Illinois BIPA classifies
"voiceprints" as biometric data; GDPR Art. 9 treats biometrics for
identification as special-category data), and 2024 made the abuse mode vivid:
the fake-Biden New Hampshire robocall and the FCC's February 2024 declaratory
ruling that AI-cloned voices are "artificial" under the TCPA, plus Tennessee's
ELVIS Act — the first US statute protecting voice likeness from AI cloning.

So the product is deliberately *not* "a voice cloner with a checkbox." The
hard part — and the reason this tool deserves to exist — is that consent is
**implemented behavior**:

1. **Synthesis is impossible without a verified consent record bound to the
   enrolled voice.** Verification is a speaker match: the consent recording
   must be spoken by the same person as the enrollment samples, decided by the
   speaker-embedding adapter at a calibrated threshold. A housemate cannot
   "consent" on someone else's behalf, and replaying an enrollment sample as
   the consent recording is detected and rejected.
2. **The binding is to the exact enrollment.** Consent records store a
   fingerprint of the enrollment set; change the samples (swap in another
   person's audio) and every prior consent stops authorizing — checked at
   authorization time, so invalidation is immediate with no background jobs.
3. **Revocation is instant and total.** One command flips the consent record;
   the very next synthesis *or replay delivery* is refused. Purge additionally
   erases audio, embeddings, rendered outputs, and — best effort — the copies
   VoiceKin itself delivered, leaving only hashes in the audit chain.
4. **Every synthesis — and every refusal — is audited** in an append-only,
   hash-chained log, and every rendered WAV is resolvable back to its audit
   record by the hash of its PCM payload.

The technically hard part underneath all of that is **speaker verification
that actually works**: a deterministic, offline speaker-embedding pipeline
(F0, formant, and spectral-band statistics — classical source-filter features)
whose error rates are measured and gated *per feature family*, because the
consent gate is only as strong as the speaker match it rests on. Both get
first-class eval gates.

## Target user

The owner: one adult operating the tool for their own household ("operator").
Multiple *voice profiles* (self, partner, parent), but a single operator — no
accounts, no multi-tenancy. Voice owners are physically present people who can
record enrollment samples and read a consent statement aloud. Audio arrives as
WAV files (phone recording, `arecord`); no microphone capture in-tool.

### Trust and threat model (explicit, honest)

| In scope (implemented behavior) | Out of scope / residual (this pass) |
|---|---|
| Synthesis **or delivery** after revocation, expiry, or enrollment drift | A malicious operator who edits the code or the SQLite file directly (mitigated only by tamper-*evidence* via the audit chain) |
| Consent recorded by the wrong person (speaker mismatch), including a deliberate pitch mimic (EVALS M1b/M2a) | Deepfake consent audio — a cloned voice reading the statement could pass the speaker match (ASVspoof-class anti-spoofing is a named live-adapter upgrade, not MVP) |
| Enrollment-swap after consent (fingerprint binding) and mixed-speaker enrollment sets (FR-3 coherence check, EVALS M6) | Verifying the *words* of the consent statement (needs ASR; the statement nonce is stored so a live ASR adapter can check it later) |
| Replaying an enrollment sample as the consent recording | **Biometric collection precedes consent**: embeddings and `voice_params` are computed and stored at enrollment, before any consent record exists. VoiceKin flags never-consented profiles (FR-1) but cannot stop an operator enrolling someone from surreptitious recordings |
| Silent tampering with the audit log (hash chain, FR-12) | **Truncate-and-recompute** of the chain by an adversary with DB write access — an unkeyed linear chain cannot detect it. Mitigated only by the operator anchoring the head hash `audit verify` prints |
| Attribution of a **bit-exact** rendered or delivered WAV (PCM-payload hash → utterance → audit trail, FR-10) | Attribution after re-encoding, resampling, or trimming (needs robust watermarking — AudioSeal-class, named post-MVP) |
| Erasure of everything VoiceKin controls: enrollment/consent/output audio, embeddings, centroid, voice params, and delivered copies still at their recorded paths (FR-7) | Copies outside VoiceKin's reach — Home Assistant's own cache, remote/unmounted media stores, anything the operator copied by hand. **Revocation stops VoiceKin delivering; it cannot recall audio a media player already holds** |
| — | Securing the network path to Home Assistant (HA token via env; TLS is HA's job) |

## User stories & acceptance criteria

**US-1 — Enroll my own voice.** As the operator, I create a profile and add
3+ WAV samples of my voice; bad recordings are rejected with a reason.
*Accept:* samples failing quality screening (too short, clipped, too noisy,
too little voiced speech) are rejected with a specific reason; accepted
samples produce embeddings; a leave-one-out coherence check refuses an
enrollment set that mixes two different speakers; on success the profile has a
centroid, an enrollment fingerprint, and derived voice parameters.

**US-2 — Grant consent for a voice.** As a voice owner (me or my partner), I
read the generated consent statement aloud; the system verifies it is really
me and activates the voice.
*Accept:* `consent draft` requires an enrolled profile and emits the exact
text to read (owner name, operator name, scope, expiry, nonce, date);
`consent grant` screens the recording, computes speaker similarity against the
enrollment centroid, and marks the consent `verified` only when similarity ≥
the committed threshold; the record stores score, threshold, embedder id, and
enrollment fingerprint, and is immutable afterward except for revocation.

**US-3 — Reject an impostor.** As a voice owner, nobody else can activate my
voice — not a housemate reading my statement, not someone imitating my pitch,
not a replay of my enrollment audio.
*Accept:* a consent recording by a different speaker is rejected with
`speaker_mismatch`; a consent file byte-identical to any enrollment sample is
rejected with `reused_enrollment_audio`; both rejections are audited; on the
eval fixtures, impostor attempts — including single-axis siblings and
pitch-matched mimics — produce **zero** false accepts (EVALS M2a, M1b).

**US-4 — Make the house speak (the payoff).** As the operator, I type
`voicekin say partner "Dinner is ready" --context announcement` and the
utterance renders in that voice and reaches a device target.
*Accept:* rendering happens only after the authorization gate returns an
`Authorization`; output is a 16 kHz PCM16 WAV; the utterance row stores text,
consent id, PCM-payload hash, and seed; delivery to the configured target
produces a receipt and a sidecar manifest; replaying the same operation
sequence reproduces byte-identical audio (FR-15).

**US-5 — Revoke instantly.** As a voice owner, I withdraw consent with one
command and the voice is dead from that moment — including replays.
*Accept:* after `consent revoke`, the next synthesis attempt is refused with
`consent_revoked`; delivering an *already rendered* utterance is also refused
(delivery re-authorizes); revocation is one command with no confirmation
ceremony beyond `--reason` (GDPR Art. 7(3): withdrawal as easy as granting);
the refusal and revocation are both audited.

**US-6 — Consent goes stale with the enrollment.** As a voice owner, consent
covers the voice I actually enrolled, not whatever samples are swapped in
later.
*Accept:* adding or removing an enrollment sample changes the profile's
fingerprint; a synthesis attempt under the old consent is refused with
`enrollment_changed`; recording a fresh consent against the new enrollment
re-enables the voice; reverting the enrollment does **not** resurrect the
superseded consent (FR-6 governing-record rule).

**US-7 — Audit everything, tamper-evidently.** As the operator, I can list
what was said in whose voice and when, verify nothing was altered, and prove
where a WAV came from.
*Accept:* every profile/consent/synthesis/refusal/delivery event appends a
hash-chained audit record; `audit verify` walks the chain, passes on an
untouched log, and prints the head seq + head hash for out-of-band anchoring;
flipping any byte of any audited field, deleting a record, or reordering
records makes verification fail and name the first bad sequence number;
`verify-output <wav>` hashes the PCM payload and resolves it to the utterance
and its audit trail (or reports `unknown_output`).

**US-8 — Purge a voice completely.** As a voice owner, I can have my voice
removed — audio, embeddings, rendered outputs, and delivered copies — not just
disabled.
*Accept:* `profile purge` deletes enrollment/consent/output audio files,
embeddings, centroid, and voice parameters, and best-effort deletes every
delivered copy still present at its recorded path (per-file outcome audited);
DB rows remain with status `purged` and content hashes only; subsequent
synthesis attempts are refused with `profile_purged`; the purge event itself
is audited; the audit chain still verifies afterward (it stores hashes, never
audio). Copies outside VoiceKin's filesystem reach are out of scope and the
command says so.

## Functional requirements

Each FR is independently testable; test names reference FR ids.

- **FR-1 Instance & profile lifecycle.** `voicekin init` creates the data
  home, the SQLite DB, the managed audio dirs, and a **single-row `instance`
  record** (`operator_name`, `data_home`, `schema_version`, `created_at`);
  `operator_name` is what renders into the consent statement (FR-5). Then:
  create/list/show voice profiles (`id` slug, display name, relationship).
  `disable`/`enable` toggles synthesis without touching consent. `purge` is
  terminal (FR-7). `profile list` marks any profile that has been
  enrolled-complete for more than `consent_grace_days` (calibration file,
  default 30) without ever having a `verified` consent as
  `awaiting-consent` — the cheap mitigation for "biometrics collected before
  consent" (threat model). It is a flag, not an auto-purge: destroying a
  voice owner's data on a timer is its own hazard.
- **FR-2 Audio intake & quality screening.** Decode WAV (PCM16; mono-mix
  stereo; resample 22.05/44.1/48 kHz → 16 kHz via 63-tap windowed-sinc
  low-pass + linear interpolation). Screening (deterministic, thresholds in
  `data/calibration.json`): duration 3–30 s for enrollment samples, 5–60 s
  for consent recordings; clipping fraction (|x| ≥ 0.999 FS) ≤ 0.5 %; voiced
  ratio ≥ 0.40 (frame is voiced when normalized autocorrelation peak ≥ 0.5
  and energy ≥ noise floor + 6 dB); SNR proxy ≥ 15 dB, where
  `SNR = 10·log10(P_speech / P_floor)` with `P_floor` = mean energy of the
  quietest decile of frames. Rejections carry a machine-readable reason.
- **FR-3 Enrollment.** A profile becomes enrolled-complete when it has ≥ 3
  accepted samples totaling ≥ 10 s of voiced audio. Per-sample embeddings via
  the `SpeakerEmbedder` adapter; leave-one-out coherence check: every
  sample's embedding must score ≥ θ_enroll (committed) against the centroid
  of the remaining samples, else the mutation is refused with
  `incoherent_enrollment` (catches mixed-speaker sets; measured by EVALS M6).
  Centroid = L2-normalized mean of sample embeddings. Enrollment fingerprint =
  `sha256(embedder_id ‖ sorted per-sample sha256s)` — any sample add/remove
  or embedder change produces a new fingerprint. Voice parameters for the
  offline synthesizer (`f0_base`, `f0_range`, `formant_scale`, `tilt`) are
  derived deterministically from the enrollment analysis and stored.
- **FR-4 Speaker-embedding adapter.** `SpeakerEmbedder` Protocol:
  `embed(clip: AudioClip) -> Embedding` plus `embedder_id: str` (versioned,
  e.g. `spectral-v1`). Offline default `SpectralStatsEmbedder`, 16-dim,
  classical source-filter features: frame 32 ms / hop 16 ms, Hamming window;
  per-frame F0 by normalized autocorrelation (lag range 60–400 Hz); LPC order
  12 via Levinson–Durbin → F1/F2 estimates from LPC spectral peaks; 8
  mel-spaced band energy ratios; spectral tilt, centroid, rolloff. Embedding
  = [log F0 median, log F0 IQR, voiced ratio, F1 median, F2 median, tilt,
  centroid, rolloff] ⊕ 8 band ratios, each dimension affinely normalized with
  constants committed in `data/calibration.json`. Scoring is cosine
  similarity. **No feature family may be dead weight**: EVALS M1b gates each
  of the pitch, vocal-tract-length, and tilt families independently, so an
  embedder whose formant or band dimensions collapse fails the suite. Live
  adapter `EcapaEmbedder` (SpeechBrain ECAPA-TDNN, 192-dim, extra
  `voicekin[live-embed]`) is interface-specified now, implemented post-MVP.
- **FR-5 Consent artifact & verification.** Two-step:
  (a) `consent draft` — **precondition**: the profile is `active`, `enabled`,
  and enrolled-complete, and no consent is currently effective; otherwise the
  operation errors (API 409, CLI exit 2) *without* creating a record. It
  creates a draft with scope (allowed contexts), optional expiry, a
  `nonce_seed`, and a derived nonce (FR-15), and renders the statement from
  the committed template (`data/consent_statement.txt`) using the instance's
  `operator_name`, the profile's display name, contexts, expiry, nonce, and
  the caller-supplied date.
  (b) `consent grant` attaches the recording, in this exact order:
  profile no longer enrolled-complete (samples removed since drafting) →
  `rejected`/`not_enrolled`; FR-2 screening fails → `rejected`/`audio_quality`;
  normalized-payload sha256 matches any enrollment sample →
  `rejected`/`reused_enrollment_audio`; else cosine(consent embedding,
  centroid) ≥ θ_verify → `verified`, otherwise `rejected`/`speaker_mismatch`.
  The record stores similarity score, threshold used, embedder id, and the
  profile's fingerprint at verification time — the last two only when a score
  was actually computed (see DATA_MODEL invariant). **Rejected consent
  recordings are hashed and discarded** — only a verified recording is
  retained under `audio/consent/`, so a failed or impostor take never leaves
  audio behind. Verified/rejected records are immutable except revocation.
- **FR-6 Authorization gate (complete mediation).** Pure function
  `authorize(profile, consents, request, now) -> Authorization | Refusal`.

  *Governing consent* := the profile's consent record with the greatest
  `(drafted_at, draft_index)`; `None` if the profile has no consent rows.
  Only the governing record can supply a refusal reason or an authorization —
  older records are never consulted. This is well-founded because FR-5 refuses
  to draft while a consent is effective, so a newer record can only exist if
  the previous one had already stopped being effective. The one visible
  consequence: if an enrollment change is later *reverted*, the older consent
  does **not** resurrect (the newer record still governs, and reports
  `enrollment_changed`) — deliberate, monotone, and scenario-tested.

  Evaluation order (this **is** the `RefusalReason` enum order):

  1. `profile.status == purged` → **`profile_purged`**
  2. `not profile.enabled` → **`profile_disabled`**
  3. `profile.enrollment_fingerprint is null` → **`no_enrollment`**
  4. governing is `None` or `status == draft` → **`no_consent`**
  5. governing `status == rejected` → **`consent_rejected`**
  6. governing `revoked_at is not null` → **`consent_revoked`**
  7. governing `expires_at is not null and now >= expires_at` →
     **`consent_expired`** (boundary refuses)
  8. governing `enrollment_fingerprint != profile.enrollment_fingerprint` →
     **`enrollment_changed`**
  9. `request.context not in governing.scope_contexts` →
     **`scope_mismatch`**
  10. otherwise `Authorization(profile_id, consent_id, context, now,
      enrollment_fingerprint)`

  The renderer's and the deliverer's entry points *require* an
  `Authorization` value that only `authorize()` constructs — one choke point,
  per the complete-mediation and economy-of-mechanism principles (Saltzer &
  Schroeder 1975). **Delivery of an already-rendered utterance re-authorizes
  against current consent state** — revocation stops replays, not just new
  renders. Every authorization and refusal is audited; refusals are also
  persisted as utterance rows with `status = refused`.
- **FR-7 Revocation & erasure.** `consent revoke [--reason]` sets
  `revoked_at`/reason, audited; effective immediately by construction (FR-6
  reads current state). `profile purge` is terminal and performs, in one
  transaction plus best-effort filesystem work:
  (a) revoke the governing consent if it is verified and unrevoked;
  (b) delete every managed audio file for the profile (enrollment, consent,
  rendered outputs) and null the paths, keeping the hashes;
  (c) null `centroid`, `voice_params`, and per-sample `embedding`s (keep
  `enrollment_fingerprint` — it is a hash needed to interpret the audit log);
  (d) for every `delivery` row of the profile's utterances whose target is
  filesystem-reachable (`file_sink.dir`, `home_assistant.media_dir`),
  best-effort delete the written file **only if it still exists and its
  PCM-payload sha256 equals the recorded `output_sha256`** (never delete a
  file VoiceKin did not write), then replace `detail.path` with
  `detail.path_sha256`;
  (e) set `status = purged`.
  The `profile_purged` audit detail carries
  `{files_deleted, files_missing, files_failed, delivered_deleted,
  delivered_missing, delivered_failed}`. The CLI prints the residual warning
  from the threat model (copies outside VoiceKin's reach are not erased). No
  grace periods, no undo.
- **FR-8 TTS synthesis adapter.** `Synthesizer` Protocol: `synthesize(text,
  voice_params, *, sample_rate, seed) -> AudioClip` plus `synth_id: str`.
  Offline default `FormantStubSynthesizer`, a thin wrapper over the shared
  source-filter core `engine/voicebox.py`: deterministic Klatt-style
  synthesis (glottal pulse train at the profile's F0 with a declination
  contour and seeded jitter, cascaded second-order formant resonators scaled
  by `formant_scale`, noise bursts for consonant classes). Text → unit
  sequence by rule: words split to syllable-like units; each character class
  maps to one of five vowel presets (Peterson–Barney vowel space) or a
  consonant noise class; `unit_duration_ms` (calibration, 180) per unit. The
  stub is *identity-bearing, not intelligible* — its outputs must embed back
  to the right profile (EVALS M3); natural speech is explicitly the live
  adapter's job. Live adapter `XttsSynthesizer` (XTTS-class zero-shot cloning
  conditioned on enrollment WAVs, extra `voicekin[live-tts]`) is
  interface-specified now, implemented post-MVP.
- **FR-9 Rendering pipeline.** Given an `Authorization`: normalize text
  (lowercase, digits → words, punctuation → pauses; length ≤ 500 chars),
  synthesize, compute the sha256 of the WAV's PCM payload, write the WAV
  under the managed output dir, persist the utterance row (`text`,
  `text_raw`, `context`, `consent_id`, `synth_id`, `seed`, `attempt_index`,
  `duration_s`, `output_path`, `output_sha256`, `requested_at`), audit
  `utterance_rendered`. Rendered bytes are a pure function of
  `(voice_params, normalized text, seed, synth_id, sample_rate)` — nothing
  identifier-derived is mixed into the audio, so byte determinism is
  unconditional.
- **FR-10 Output provenance.** `output_sha256` is the sha256 of the WAV
  **data-chunk bytes only** (not the header), so metadata rewrites do not
  break lookup. `verify-output <wav>` hashes the payload and resolves it
  against `utterance.output_sha256`, printing the utterance, profile, consent
  id, and the audit records referencing it; no match → `unknown_output`.
  `FileSinkDeliverer` writes a sidecar `<utterance_id>.json` manifest
  (utterance id, profile id, consent id, `output_sha256`, audit seq, rendered
  timestamp) next to each delivered WAV, so a delivered copy is
  self-describing. Honest limit: attribution survives bit-exact copies only —
  identical to what an LSB watermark would give, at no subsystem cost (see
  decision 8 and the threat model).
- **FR-11 Delivery adapter.** `DeviceDeliverer` Protocol:
  `deliver(utterance_audio, target) -> DeliveryReceipt`. Offline default
  `FileSinkDeliverer`: writes WAV + JSON manifest into the target's directory
  (this is already useful — Home Assistant can watch a media dir). Live
  `HomeAssistantDeliverer`: copies the WAV into the HA-shared media path and
  POSTs `/api/services/media_player/play_media` with the target's
  `entity_id`, bearer-authenticated via `VOICEKIN_HA_URL` +
  `VOICEKIN_HA_TOKEN` (Home Assistant long-lived access token); activates
  only when both env vars are set. Each delivery re-authorizes (FR-6),
  records a receipt row (including the written path, for FR-7(d)), and audits
  success/failure/refusal.
- **FR-12 Audit hash chain.** Append-only `audit_record` table: monotonic
  `seq`, timestamp (caller-supplied), event type, subject ids, detail JSON,
  `prev_hash`, `record_hash = sha256(canonical_json(fields) ‖ prev_hash)`.
  **Canonical JSON** = `json.dumps(obj, sort_keys=True,
  separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, with every
  float in `detail` rounded to 6 decimal places before serialization (so
  chain hashes are stable across platforms); genesis
  `prev_hash = sha256("voicekin-genesis")`. Events: profile created/disabled/
  enabled/purged, sample added/removed/rejected, consent drafted/verified/
  rejected/revoked, synthesis authorized/refused, utterance rendered,
  delivery succeeded/failed/refused. `audit verify` recomputes the chain from
  genesis, reports the first divergent `seq`, and on success prints
  `head_seq` and `head_hash`. Linear hash chain in the spirit of transparency
  logs (RFC 6962); detail stores hashes, never audio, so purge never breaks
  verification. **Stated limit:** an unkeyed linear chain detects edits,
  deletions, and reorderings by an attacker who does not rewrite the chain,
  but an attacker with DB write access who truncates the tail and re-appends
  correctly chained records produces a log that verifies clean. Printing the
  head hash lets the operator anchor it out of band; that is the whole
  mitigation, and EVALS M5 encodes the limitation as an expected-undetected
  case.
- **FR-13 API.** FastAPI app per the sketch below: the **automation surface**
  — what a script or Home Assistant automation calls. Read, synthesize,
  deliver, revoke, audit, verify. No file upload (see decision 16). Thin:
  validation + service calls; gate refusals map to HTTP 403 with the refusal
  reason, precondition errors to 409.
- **FR-14 CLI.** Typer app per the sketch below: the **administrative
  surface** — what a human runs with WAV files in hand. Superset of the API;
  same services.
- **FR-15 Determinism & hermeticity.** The engine is pure: `now` and `seed`
  are always inputs; no clock reads, no filesystem, no network below
  `services.py`. **Identifiers are derived, not random.** For each row kind,
  `id = sha256(canonical_json(id_material))[:32]` where `id_material` is:
  - sample: `["sample", profile_id, payload_sha256, added_at, sample_index]`
  - consent: `["consent", profile_id, drafted_at, draft_index]`
  - utterance: `["utterance", profile_id, text, context, requested_at,
    attempt_index]`
  - delivery: `["delivery", utterance_id, target_id, requested_at,
    delivery_index]`

  The `*_index` values are per-parent monotonic counters assigned inside the
  writing transaction (`sample_index`/`draft_index`/`attempt_index` per
  profile, `delivery_index` per utterance) and are persisted. The consent
  nonce is `base32(sha256("voicekin-nonce" ‖ consent_id ‖ nonce_seed))[:8]`.

  **Determinism contract:** replaying the same ordered sequence of operations
  (same inputs, same `now` values, same seeds, same `nonce_seed`s, same audio
  bytes) against an empty data home reproduces identical row ids, identical
  rendered WAV bytes, identical `output_sha256`, and an identical audit chain
  — the same `record_hash` at every `seq`. Repeating an identical
  `synthesize` call *within* one store yields a new row (the attempt index
  advanced) whose audio is byte-identical to the first.

  The single non-deterministic input in production is the `consent draft`
  `nonce_seed`: the CLI draws `secrets.randbits(63)` when `--nonce-seed` is
  omitted, persists it on the record, and every downstream value is a pure
  function of it. Tests and evals always supply it.

## Non-goals (this pass)

1. **No web/mobile UI** — API + CLI only (workspace-wide decision).
2. **No live TTS or live embedder implementation.** `XttsSynthesizer` and
   `EcapaEmbedder` are named, interface-fixed, and extras-guarded, but land
   post-MVP (locked decision: offline stub now, XTTS-class pluggable later).
   The live Home Assistant deliverer *does* ship — it is a small REST call.
3. **No microphone capture, wake word, ASR, or assistant loop.** Input is WAV
   files; VoiceKin renders and delivers speech, it does not listen.
4. **No consent-content verification (ASR)** — the statement nonce is stored
   so a future ASR adapter can check the words; MVP verifies the speaker.
5. **No anti-spoofing of consent audio** (replay of *non-enrollment*
   recordings, synthetic playback). ASVspoof-style countermeasures are a
   named future adapter; the residual risk is documented in the threat model.
6. **No audio watermarking.** Provenance is by PCM-payload hash + delivery
   manifest (FR-10). An LSB watermark was scoped and cut: its stated
   robustness ("survives bit-exact copies") is *exactly* what a content hash
   already gives, so a ~200-line embed/extract/CRC/majority-vote subsystem
   bought one marginal case (a trimmed copy) at real budget cost.
   AudioSeal-class robust watermarking (San Roman et al. 2024) — which
   actually survives re-encoding — is the named post-MVP upgrade.
7. **No voice conversion / real-time streaming synthesis.** Batch TTS only.
8. **No multi-operator auth, remote access control, or HTTPS termination** —
   the API binds to localhost by default; HA credentials come from env vars.
9. **No Alexa/Google integrations** — generic HA-style delivery only (locked
   decision); a Wyoming-protocol server (the Rhasspy/HA voice-satellite
   protocol used by Piper) is a natural later target behind the same
   deliverer Protocol.
10. **No file upload over HTTP.** Enrollment and consent recordings are local
    WAV files handled by the CLI; multipart intake would add handling for no
    user value in a single-operator localhost deployment.
11. **No second, independent synthesizer.** The eval fixture generator and the
    product stub share `engine/voicebox.py` and differ only in parameter
    richness (see decision 17) — anti-circularity is preserved by the
    embedder being derived from neither.

## Architecture

```
projects/voicekin/
  src/voicekin/
    models.py               # Pydantic v2 domain models + StrEnums (DATA_MODEL.md)
    engine/
      audio.py              # AudioClip; WAV codec bytes<->clip; mono-mix; resample (pure)
      dsp.py                # framing, Hamming window, DFT, autocorrelation F0,
                            # LPC via Levinson-Durbin, mel band energies, IIR filters
      quality.py            # FR-2 screening: duration, clipping, SNR proxy, voiced ratio
      voicebox.py           # shared parameterized source-filter synthesis core
                            #   (product stub + eval fixture generator both call it)
      enrollment.py         # FR-3 coherence check, centroid, fingerprint, voice_params
      verification.py       # cosine scoring; consent decision at committed thresholds
      consent.py            # FR-5 statement rendering; FR-6 authorize() gate + precedence
      synthesis.py          # FR-9 text normalization, unit sequencing, payload hashing
      ids.py                # FR-15 deterministic id + nonce derivation
      audit.py              # FR-12 canonical JSON, hash chain build + verify
    adapters/
      embedder.py           # SpeakerEmbedder Protocol
      embedder_spectral.py  #   offline default: SpectralStatsEmbedder (spectral-v1)
      embedder_ecapa.py     #   live: EcapaEmbedder (extra `live-embed`; post-MVP)
      synth.py              # Synthesizer Protocol
      synth_stub.py         #   offline default: FormantStubSynthesizer (wraps voicebox)
      synth_xtts.py         #   live: XttsSynthesizer (extra `live-tts`; post-MVP)
      deliver.py            # DeviceDeliverer Protocol
      deliver_filesink.py   #   offline default: FileSinkDeliverer (+ manifest)
      deliver_hass.py       #   live: HomeAssistantDeliverer (env-gated)
    store/                  # Repository Protocol; SQLiteRepository (stdlib sqlite3).
                            # The in-memory test backend is the same class opened on
                            # ":memory:" — one implementation, not two.
    services.py             # orchestration: engine + adapters + store; all I/O lives here
    api/                    # FastAPI app
    cli/                    # Typer app
  data/                     # consent_statement.txt, calibration.json (thresholds +
                            # normalization constants + provenance note)
  evals/                    # fixtures/, metrics.py, run.py, test_gates.py
```

### Adapter interfaces

| Interface | Offline (default; evals/tests) | Live (env/extras-gated) |
|---|---|---|
| `SpeakerEmbedder.embed(clip) -> Embedding`; `.embedder_id` | `SpectralStatsEmbedder` (`spectral-v1`, 16-dim, numpy DSP) | `EcapaEmbedder` — SpeechBrain ECAPA-TDNN, 192-dim; extra `voicekin[live-embed]`; post-MVP |
| `Synthesizer.synthesize(text, voice_params, *, sample_rate, seed) -> AudioClip`; `.synth_id` | `FormantStubSynthesizer` (`stub-v1`, deterministic Klatt-style over `voicebox`) | `XttsSynthesizer` — XTTS-class zero-shot cloning from enrollment WAVs; extra `voicekin[live-tts]`; post-MVP |
| `DeviceDeliverer.deliver(audio, target) -> DeliveryReceipt` | `FileSinkDeliverer` — WAV + manifest into target dir | `HomeAssistantDeliverer` — media copy + `POST /api/services/media_player/play_media`; `VOICEKIN_HA_URL` + `VOICEKIN_HA_TOKEN` |

### API sketch (FastAPI) — automation surface, 12 routes, no multipart

```
GET  /health
GET  /profiles                          GET /profiles/{id}
GET  /profiles/{id}/consents
POST /consents/{cid}/revoke             # {reason, now}
POST /synthesize                        # {profile_id, text, context, seed, now}
                                        # -> 200 utterance | 403 {reason} (refusal persisted)
GET  /utterances/{id}                   GET /utterances/{id}/audio
POST /utterances/{id}/deliver           # {target_id, now} -> receipt | 403 (re-authorized)
POST /verify-output                     # {output_sha256} -> utterance + audit refs | 404
GET  /audit?since_seq=                  GET /audit/verify
```

### CLI sketch (Typer) — administrative surface

```
voicekin init --operator "Abdoul" [--data-home ~/.voicekin]
voicekin profile add|list|show|disable|enable|purge <id>
voicekin enroll <profile> s1.wav [s2.wav ...]        # per-file accept/reject report
voicekin sample list|remove <profile> [<sample-id>]
voicekin consent draft <profile> --scope announcement,reminder
             [--expires 2027-06-30] [--nonce-seed N] [--now ISO]
voicekin consent statement <profile>                 # print the exact text to read
voicekin consent grant <profile> consent.wav [--now ISO]
voicekin consent revoke <profile> [--reason ...]     | consent status <profile>
voicekin say <profile> "Dinner is ready" --context announcement
             [--target living-room] [--seed N] [--now ISO]
voicekin deliver <utterance-id> --target <id> [--now ISO]
voicekin target add|list <id> --kind file_sink|home_assistant [--dir ...|--entity ...]
voicekin verify-output out.wav
voicekin audit list [--since-seq N] | audit verify
```

### Implementation budget (≈ 4,000 lines of Python)

The scope above is sized, not hoped. Estimates exclude fixture JSON and
generated WAVs.

| Area | Lines |
|---|---|
| `models.py` | 220 |
| `engine/audio.py` · `dsp.py` · `quality.py` | 120 · 200 · 70 |
| `engine/voicebox.py` (shared synthesis core) | 190 |
| `engine/enrollment.py` · `verification.py` · `consent.py` | 100 · 45 · 150 |
| `engine/synthesis.py` · `ids.py` · `audit.py` | 95 · 40 · 100 |
| `adapters/` (6 files; 2 are post-MVP interface stubs) | 380 |
| `store/` (one SQLiteRepository, `:memory:` for tests) | 220 |
| `services.py` | 330 |
| `api/` · `cli/` | 150 · 210 |
| **src subtotal** | **2,620** |
| `tests/` (unit + integration, FR-referencing names) | 780 |
| `evals/metrics.py` · `run.py` · `test_gates.py` | 230 · 110 · 90 |
| `evals/fixtures/generate_voices.py` · `calibrate.py` | 150 · 80 |
| **total** | **≈ 4,060** |

Four deliberate cuts keep it there, all recorded in REVIEW.md: one shared
synthesis core instead of two synthesizers (−200); one Repository
implementation instead of two (−200); content-hash provenance instead of an
LSB watermark subsystem (−230 across engine, API, CLI, tests); no HTTP file
upload (−80). If implementation still runs over, cut in this order:
(1) `HomeAssistantDeliverer` → post-MVP, file-sink only; (2) the API's
read-only `/profiles*` routes; (3) `dsp.py`'s rolloff/centroid dimensions
(re-calibrate, re-derive EVALS gates in the same commit).

## Key design decisions & assumptions

1. **Consent is treated as biometric-grade, because voice is.** Illinois BIPA
   (740 ILCS 14) requires informed written consent before collecting a
   voiceprint, and *Rosenbach v. Six Flags* (Ill. 2019) established that a
   bare violation is actionable; GDPR Art. 9 makes biometric identification
   data special-category. VoiceKin therefore requires an explicit, specific,
   informed, revocable consent artifact per profile — scope-limited contexts,
   optional expiry, one-command withdrawal (Art. 7(3)), and purge implementing
   Art. 17-style erasure *of everything VoiceKin controls*. Two honest
   qualifications: the gate is on **synthesis**, so embeddings and voice
   params are collected at enrollment *before* consent exists (BIPA's
   collection duty is therefore not fully discharged — FR-1 flags
   never-consented profiles, and the threat model names the residual); and
   erasure cannot reach copies outside VoiceKin's filesystem.
2. **Verification is a speaker match, done the way ASV systems do it.**
   Enrollment centroid + cosine scoring against a probe embedding is the
   standard modern recipe (d-vector/GE2E, Wan et al. 2018; x-vector, Snyder
   et al. 2018; ECAPA-TDNN, Desplanques et al. 2020), with thresholds
   calibrated on a dev speaker set disjoint from the eval set, per NIST
   Speaker Recognition Evaluation practice.
3. **The operating point is deliberately asymmetric.** A false accept means
   synthesizing a voice without its owner's real consent — the catastrophic
   error; a false reject means re-recording a 15-second statement. Following
   the NIST DCF logic of unequal error costs (C_fa ≫ C_miss here), θ_verify
   is calibrated for zero false accepts with margin on the dev population,
   and EVALS gates false accepts at literally zero over a pooled impostor set
   of 330 comparisons on the eval fixtures.
4. **The offline embedder is real DSP, not a mock — and every feature family
   must earn its keep.** F0 via normalized autocorrelation (cf. YIN,
   de Cheveigné & Kawahara 2002), formant estimates via LPC (Makhoul 1975;
   Levinson–Durbin), mel-spaced band energies and spectral shape statistics —
   the classical speaker-characterizing features of the source-filter model
   (Fant 1960), MFCC-adjacent (Davis & Mermelstein 1980). Because impostors
   that differ on *one* axis at a time are in the fixture set (EVALS M1b), an
   embedder that silently reduces to pitch matching fails the gates — which
   matters because pitch is precisely the feature a housemate can consciously
   imitate.
5. **Consent binds to an enrollment fingerprint, checked at authorize time.**
   `sha256(embedder_id ‖ sorted sample hashes)` defeats the enrollment-swap
   attack (consent to my 3 samples, then swap in yours) and makes embedder
   upgrades conservative: re-embedding changes the fingerprint, so old
   consents stop authorizing until re-granted. Checking at authorization time
   means invalidation is immediate by construction — no invalidation jobs,
   no cache coherence problem. The fingerprint does not, however, protect
   against a *poisoned* enrollment set (it hashes whatever is there), which
   is why FR-3's leave-one-out coherence check exists and why EVALS M6
   measures it.
6. **One gate, capability-style, over one governing record.** `authorize()`
   is the only constructor of `Authorization`, and both rendering and
   delivery require one — complete mediation and economy of mechanism
   (Saltzer & Schroeder 1975). Gating *delivery* too is the difference
   between "revocation stops new renders" and "revocation stops the house
   speaking in your voice." Consulting only the governing consent record
   keeps refusal reasons actionable (they always describe the operator's
   latest attempt) and makes the decision a pure function of one row.
7. **Audit log is a linear hash chain.** Tamper-evident append-only logging
   in the spirit of Certificate Transparency's Merkle logs (RFC 6962),
   reduced to a linear chain (single writer, no inclusion proofs needed).
   Records carry hashes of audio, never audio, so erasure coexists with
   verification. Its ceiling is stated in FR-12 and encoded in EVALS M5.
8. **Provenance is by content hash, not watermark.** Disclosure of synthetic
   media is where regulation is heading (EU AI Act Art. 50; C2PA content
   credentials; the FCC's 2024 TCPA ruling on AI voices), and VoiceKin makes
   every rendered byte attributable — but honestly. An LSB payload and a
   sha256 of the PCM payload have the *same* robustness envelope (bit-exact
   copies), so the MVP takes the one that costs 20 lines instead of 200, and
   pairs it with a sidecar manifest so a delivered file is self-describing.
   Robust, re-encode-surviving watermarking (AudioSeal, San Roman et al.
   2024) is the named post-MVP upgrade and the only thing that would actually
   raise the ceiling.
9. **The offline TTS stub is a Klatt-style formant synthesizer** (Klatt 1980,
   cascade formant synthesis) conditioned on enrollment-derived parameters
   (F0 statistics, formant scale ≈ vocal-tract-length proxy, tilt). It is
   deterministic, seedable, dependency-light, and *identity-bearing*, which
   is exactly what the evals need: synthesized output must re-embed to its
   own profile (speaker-encoder cosine similarity, the SECS measure used in
   YourTTS, Casanova et al. 2022). Intelligibility is explicitly not its job
   — XTTS-class live synthesis (Casanova et al. 2024) is.
10. **Delivery targets Home Assistant generically** (locked decision): write
    the WAV where HA can serve it and call `media_player.play_media` over the
    REST API with a long-lived access token — works with any HA media player
    entity and degrades to the file-sink for everything else. The Wyoming
    protocol (HA's local-voice satellite protocol, used by Piper) is the
    named future path to appear *as* a TTS engine inside HA.
11. **Internal audio format is 16 kHz mono PCM16** — the standard rate for
    speaker-verification and speech pipelines (VoxCeleb, most ASV tooling);
    ample for the embedder and stub, cheap to store, and HA plays it fine.
12. **Dependencies:** numpy is the only core numeric dependency (DFT/LPC
    vectorized); everything else engine-side is stdlib (`wave`, `hashlib`,
    `sqlite3`, `secrets`). Heavy stacks (torch/speechbrain, XTTS) sit behind
    extras per workspace conventions and are never imported by tests or evals.
13. **Determinism by derivation, not by luck** (FR-15). Nothing random is
    mixed into audio, ids, or audit hashes: identifiers are sha256 prefixes
    over `(parent id, timestamp, monotonic index, content hash)` and the
    consent nonce is derived from a *persisted* seed. The only entropy drawn
    in production is that one `nonce_seed`. This is what makes "identical
    replay ⇒ identical audit chain" a claim the eval suite can actually
    check, and what makes 100 %-gates meaningful.
14. **Assumption: fixtures must be synthetic voices.** Committing real human
    voice recordings would contradict the product's own consent ethics and
    licensing hygiene; parameterized source-filter voices give exact identity
    ground truth and hermetic CI (EVALS.md details margins and the honest
    limits of what that certifies). A real-voice smoke test via the live
    embedder is a post-MVP addition, never a replacement for the gates.
15. **Assumption: single household, cooperative hardware.** One operator,
    localhost API, HA reachable over the LAN; the consent gate protects voice
    owners from misuse *through the tool*, and the audit chain makes misuse
    *around* the tool evident — it does not make a hostile operator's machine
    trustworthy (threat model).
16. **API and CLI are deliberately asymmetric.** The API is what automations
    call (say something, deliver it, revoke, read the log); the CLI is what a
    human runs with WAV files in hand (enroll, grant, manage targets, purge).
    Splitting them this way removes multipart handling entirely and keeps
    both layers thin, at the cost of "the API is not a complete
    administrative surface" — acceptable for a single-operator localhost
    tool, and reversible later.
17. **One synthesis core, two parameterizations.** `engine/voicebox.py` is
    shared by the product stub and the eval fixture generator; the generator
    drives it with richer parameters (jitter, shimmer, breathiness, 4-formant
    stacks, channel filters) that the stub never uses. Anti-circularity is
    about the *embedder*, which is derived from neither, so independence is
    preserved by disjoint parameter regimes rather than duplicated code —
    and the alternative (two hand-written Klatt synthesizers) would have cost
    ~200 lines the budget does not have.

# VoiceKin — Scope

## One-liner

A consent-gated personal voice system for the smart home: enroll your own or a
family member's voice from audio samples, verify an explicit recorded consent
artifact against that enrollment by speaker match, and only then let the house
speak in that voice — every synthesis authorized through a single gate,
watermarked, and written to a tamper-evident audit log; revocation kills the
voice instantly.

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
   erases audio, embeddings, and rendered outputs (GDPR Art. 17-style
   erasure), leaving only hashes in the audit chain.
4. **Every synthesis — and every refusal — is audited** in an append-only,
   hash-chained log, and every rendered WAV carries an embedded provenance
   watermark tying it back to its audit record.

The technically hard part underneath all of that is **speaker verification
that actually works**: a deterministic, offline speaker-embedding pipeline
(F0, formant, and spectral-band statistics — classical source-filter features)
whose error rates are measured and gated, because the consent gate is only as
strong as the speaker match it rests on. Both get first-class eval gates.

## Target user

The owner: one adult operating the tool for their own household ("operator").
Multiple *voice profiles* (self, partner, parent), but a single operator — no
accounts, no multi-tenancy. Voice owners are physically present people who can
record enrollment samples and read a consent statement aloud. Audio arrives as
WAV files (phone recording, `arecord`); no microphone capture in-tool.

### Trust and threat model (explicit, honest)

| In scope (implemented behavior) | Out of scope (this pass) |
|---|---|
| Synthesis/delivery after revocation or expiry | A malicious operator who edits the code or the SQLite file directly (mitigated only by tamper-*evidence* via the audit chain) |
| Consent recorded by the wrong person (speaker mismatch) | Deepfake consent audio — a cloned voice reading the statement could pass the speaker match (ASVspoof-class anti-spoofing is a named live-adapter upgrade, not MVP) |
| Enrollment-swap after consent (fingerprint binding) | Verifying the *words* of the consent statement (needs ASR; the statement nonce is stored so a live ASR adapter can check it later) |
| Replaying an enrollment sample as the consent recording | Adversarially robust watermarking (LSB survives bit-exact copies, not re-encoding; AudioSeal-class is the live upgrade) |
| Silent tampering with the audit log (hash chain) | Securing the network path to Home Assistant (HA token via env; TLS is HA's job) |
| Unattributed synthetic output in the wild (watermark + audit) | |

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
*Accept:* `consent statement` emits the exact text to read (name, scope,
expiry, nonce, date); `consent grant` screens the recording, computes speaker
similarity against the enrollment centroid, and marks the consent `verified`
only when similarity ≥ the committed threshold; the record stores score,
threshold, embedder id, and enrollment fingerprint, and is immutable
afterward except for revocation.

**US-3 — Reject an impostor.** As a voice owner, nobody else can activate my
voice — not a housemate reading my statement, not a replay of my enrollment
audio.
*Accept:* a consent recording by a different speaker is rejected with
`speaker_mismatch`; a consent file byte-identical to any enrollment sample is
rejected with `reused_enrollment_audio`; both rejections are audited; on the
eval fixtures, impostor consent attempts produce **zero** false accepts
(EVALS M2a).

**US-4 — Make the house speak (the payoff).** As the operator, I type
`voicekin say partner "Dinner is ready" --context announcement` and the
utterance renders in that voice and reaches a device target.
*Accept:* rendering happens only after the authorization gate returns an
`Authorization`; output is a 16 kHz PCM16 WAV with an embedded provenance
watermark; the utterance row stores text, consent id, output hash, and seed;
delivery to the configured target produces a receipt; the whole chain is
reproducible given the same seed and inputs.

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
re-enables the voice.

**US-7 — Audit everything, tamper-evidently.** As the operator, I can list
what was said in whose voice and when, verify nothing was altered, and prove
where a WAV came from.
*Accept:* every profile/consent/synthesis/refusal/delivery event appends a
hash-chained audit record; `audit verify` walks the chain and passes on an
untouched log; flipping any byte of any audited field, deleting a record, or
reordering records makes verification fail and name the first bad sequence
number; `verify-output <wav>` extracts the watermark and resolves it to the
utterance and its audit trail.

**US-8 — Purge a voice completely.** As a voice owner, I can have my voice
removed — audio, embeddings, and rendered outputs — not just disabled.
*Accept:* `profile purge` deletes enrollment/consent/output audio files,
embeddings, centroid, and voice parameters; DB rows remain with status
`purged` and content hashes only; subsequent synthesis attempts are refused
with `profile_purged`; the purge event itself is audited; the audit chain
still verifies afterward (it stores hashes, never audio).

## Functional requirements

Each FR is independently testable; test names reference FR ids.

- **FR-1 Profile lifecycle.** Create/list/show voice profiles (`id` slug,
  display name, relationship). `disable`/`enable` toggles synthesis without
  touching consent. `purge` is terminal: deletes all audio files (enrollment,
  consent, rendered outputs), embeddings, centroid, and voice params; rows
  survive with hashes; status `purged` is irreversible.
- **FR-2 Audio intake & quality screening.** Decode WAV (PCM16; mono-mix
  stereo; resample 22.05/44.1/48 kHz → 16 kHz via 63-tap windowed-sinc
  low-pass + linear interpolation). Screening (deterministic, thresholds in
  `data/calibration.json`): duration 3–30 s for enrollment samples, 5–60 s
  for consent recordings; clipping fraction (|x| ≥ 0.999 FS) ≤ 0.5 %; voiced
  ratio ≥ 0.40 (frame is voiced when normalized autocorrelation peak ≥ 0.5
  and energy ≥ noise floor + 6 dB); SNR proxy ≥ 15 dB, where
  `SNR = 10·log10(P_speech / P_floor)` with `P_floor` = mean energy of the
  quietest decile of frames. Rejections carry a machine-readable reason.
- **FR-3 Enrollment.** A profile becomes enrollable-complete when it has ≥ 3
  accepted samples totaling ≥ 10 s of voiced audio. Per-sample embeddings via
  the `SpeakerEmbedder` adapter; leave-one-out coherence check: every
  sample's embedding must score ≥ θ_enroll (committed) against the centroid
  of the remaining samples, else the mutation is refused with
  `incoherent_enrollment` (catches mixed-speaker sets). Centroid = L2-
  normalized mean of sample embeddings. Enrollment fingerprint =
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
  similarity. Live adapter `EcapaEmbedder` (SpeechBrain ECAPA-TDNN, 192-dim,
  extra `voicekin[live-embed]`) is interface-specified now, implemented
  post-MVP.
- **FR-5 Consent artifact & verification.** Two-step: (a) `consent draft`
  creates a draft record with scope (allowed contexts), optional expiry, and
  a random nonce, and renders the statement from the committed template
  (`data/consent_statement.txt`) — name, operator, contexts, expiry, nonce,
  date; (b) `consent grant` attaches the recording: FR-2 screening → reject
  `audio_quality`; byte-hash match against any enrollment sample → reject
  `reused_enrollment_audio`; else cosine(consent embedding, centroid) ≥
  θ_verify → `verified`, otherwise `rejected` with `speaker_mismatch`. The
  record stores similarity score, threshold used, embedder id, and the
  profile's fingerprint at verification time; verified/rejected records are
  immutable except revocation. At most one *effective* consent per profile
  (see FR-6); drafting while one is effective is refused.
- **FR-6 Authorization gate (complete mediation).** Pure function
  `authorize(profile, consents, request, now) -> Authorization | Refusal`.
  A consent is *effective* iff `status = verified` ∧ not revoked ∧
  (`expires_at` is null or `now < expires_at`) ∧ its stored fingerprint
  equals the profile's current fingerprint. Refusal reasons, evaluated in
  this exact precedence order (deterministic, scenario-tested):
  `profile_purged` → `profile_disabled` → `no_enrollment` → `no_consent`
  (no record, or latest is draft) → `consent_rejected` → `consent_revoked` →
  `consent_expired` → `enrollment_changed` → `scope_mismatch` (request
  context ∉ consent scope). The renderer's and the deliverer's entry points
  *require* an `Authorization` value that only `authorize()` constructs —
  one choke point, per the complete-mediation and economy-of-mechanism
  principles (Saltzer & Schroeder 1975). **Delivery of an already-rendered
  utterance re-authorizes against current consent state** — revocation stops
  replays, not just new renders. Every authorization and refusal is audited;
  refusals are also persisted as utterance rows with `status = refused`.
- **FR-7 Revocation & erasure.** `consent revoke [--reason]` sets
  `revoked_at`/reason, audited; effective immediately by construction (FR-6
  reads current state). `profile purge` = revoke (if needed) + FR-1 file/
  embedding deletion. No grace periods, no undo.
- **FR-8 TTS synthesis adapter.** `Synthesizer` Protocol: `synthesize(text,
  voice_params, *, sample_rate, seed) -> AudioClip` plus `synth_id: str`.
  Offline default `FormantStubSynthesizer`: deterministic Klatt-style
  source-filter synthesis (glottal pulse train at the profile's F0 with a
  declination contour and seeded jitter, cascaded second-order formant
  resonators scaled by `formant_scale`, noise bursts for consonant classes).
  Text → unit sequence by rule: words split to syllable-like units; each
  character class maps to one of five vowel presets (Peterson–Barney vowel
  space) or a consonant noise class; ~180 ms per unit. The stub is *identity-
  bearing, not intelligible* — its outputs must embed back to the right
  profile (EVALS M3); natural speech is explicitly the live adapter's job.
  Live adapter `XttsSynthesizer` (XTTS-class zero-shot cloning conditioned on
  enrollment WAVs, extra `voicekin[live-tts]`) is interface-specified now,
  implemented post-MVP.
- **FR-9 Rendering pipeline.** Given an `Authorization`: normalize text
  (lowercase, digits → words, punctuation → pauses; length ≤ 500 chars),
  synthesize, embed watermark (FR-10), compute output sha256, write WAV under
  the managed output dir, persist the utterance row (text, context, consent
  id, synth id, seed, duration, hash, watermark id), audit `utterance_rendered`.
  Byte-identical output for identical (profile state, text, seed, synth id).
- **FR-10 Provenance watermark.** Every rendered WAV carries a payload in the
  LSBs of every 4th PCM sample: 16-bit magic ‖ 64-bit utterance-id prefix ‖
  CRC-16, repeated for the whole file, majority-vote extraction.
  `verify-output <wav>` extracts and resolves the id against the store and
  audit chain; a WAV without a valid payload reports `no_watermark`.
  Honest limit: survives bit-exact copies only (see threat model).
- **FR-11 Delivery adapter.** `DeviceDeliverer` Protocol:
  `deliver(utterance_audio, target) -> DeliveryReceipt`. Offline default
  `FileSinkDeliverer`: writes WAV + JSON manifest into the target's directory
  (this is already useful — Home Assistant can watch a media dir). Live
  `HomeAssistantDeliverer`: copies the WAV into the HA-shared media path and
  POSTs `/api/services/media_player/play_media` with the target's
  `entity_id`, bearer-authenticated via `VOICEKIN_HA_URL` +
  `VOICEKIN_HA_TOKEN` (Home Assistant long-lived access token); activates
  only when both env vars are set. Each delivery re-authorizes (FR-6),
  records a receipt row, and audits success/failure.
- **FR-12 Audit hash chain.** Append-only `audit_record` table: monotonic
  `seq`, timestamp (caller-supplied), event type, subject ids, detail JSON,
  `prev_hash`, `record_hash = sha256(canonical_json(fields) ‖ prev_hash)`
  with canonical JSON = sorted keys, no whitespace, UTF-8; genesis
  `prev_hash = sha256("voicekin-genesis")`. Events: profile created/disabled/
  enabled/purged, sample added/removed/rejected, consent drafted/verified/
  rejected/revoked, synthesis authorized/refused, utterance rendered,
  delivery succeeded/failed/refused. `audit verify` recomputes the chain and
  reports
  the first divergent seq. Linear hash chain in the spirit of transparency
  logs (RFC 6962); detail stores hashes, never audio, so purge never breaks
  verification.
- **FR-13 API.** FastAPI app per the sketch below; thin — validation +
  service calls; gate refusals map to HTTP 403 with the refusal reason,
  consent verification rejections to 422 with the reject reason.
- **FR-14 CLI.** Typer app per the sketch below; same services as the API.
- **FR-15 Determinism & hermeticity.** Engine is pure: time (`now`) and
  `seed` are always inputs; all tests and evals run on offline adapters with
  no network and no clock reads; identical inputs ⇒ identical embeddings,
  decisions, rendered bytes, and audit hashes.

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
6. **No robust watermarking** — LSB provenance for cooperating pipelines,
   not forensics against a re-encoder; AudioSeal-class is the live upgrade.
7. **No voice conversion / real-time streaming synthesis.** Batch TTS only.
8. **No multi-operator auth, remote access control, or HTTPS termination** —
   the API binds to localhost by default; HA credentials come from env vars.
9. **No Alexa/Google integrations** — generic HA-style delivery only (locked
   decision); a Wyoming-protocol server (the Rhasspy/HA voice-satellite
   protocol used by Piper) is a natural later target behind the same
   deliverer Protocol.

## Architecture

```
projects/voicekin/
  src/voicekin/
    models.py               # Pydantic v2 domain models + StrEnums (DATA_MODEL.md)
    engine/
      audio.py              # AudioClip; WAV codec bytes<->clip; mono-mix; resample (pure)
      dsp.py                # framing, Hamming window, DFT, autocorrelation F0,
                            # LPC via Levinson-Durbin, mel band energies
      quality.py            # FR-2 screening: duration, clipping, SNR proxy, voiced ratio
      enrollment.py         # FR-3 coherence check, centroid, fingerprint, voice_params
      verification.py       # cosine scoring; consent decision at committed thresholds
      consent.py            # FR-5 statement rendering; FR-6 authorize() gate + precedence
      synthesis.py          # FR-9 text normalization, unit sequencing, output hashing
      stubsynth.py          # Klatt-style formant synthesis (pure DSP, seeded)
      watermark.py          # FR-10 LSB embed/extract + CRC-16
      audit.py              # FR-12 canonical JSON, hash chain build + verify
    adapters/
      embedder.py           # SpeakerEmbedder Protocol
      embedder_spectral.py  #   offline default: SpectralStatsEmbedder (spectral-v1)
      embedder_ecapa.py     #   live: EcapaEmbedder (extra `live-embed`; post-MVP)
      synth.py              # Synthesizer Protocol
      synth_stub.py         #   offline default: FormantStubSynthesizer (wraps stubsynth)
      synth_xtts.py         #   live: XttsSynthesizer (extra `live-tts`; post-MVP)
      deliver.py            # DeviceDeliverer Protocol
      deliver_filesink.py   #   offline default: FileSinkDeliverer
      deliver_hass.py       #   live: HomeAssistantDeliverer (env-gated)
    store/                  # Repository Protocol; SQLiteRepository (stdlib sqlite3)
                            # + InMemoryRepository (tests)
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
| `SpeakerEmbedder.embed(clip) -> Embedding`; `.embedder_id` | `SpectralStatsEmbedder` (`spectral-v1`, 16-dim, stdlib+numpy DSP) | `EcapaEmbedder` — SpeechBrain ECAPA-TDNN, 192-dim; extra `voicekin[live-embed]`; post-MVP |
| `Synthesizer.synthesize(text, voice_params, *, sample_rate, seed) -> AudioClip`; `.synth_id` | `FormantStubSynthesizer` (`stub-v1`, deterministic Klatt-style) | `XttsSynthesizer` — XTTS-class zero-shot cloning from enrollment WAVs; extra `voicekin[live-tts]`; post-MVP |
| `DeviceDeliverer.deliver(audio, target) -> DeliveryReceipt` | `FileSinkDeliverer` — WAV + manifest into target dir | `HomeAssistantDeliverer` — media copy + `POST /api/services/media_player/play_media`; `VOICEKIN_HA_URL` + `VOICEKIN_HA_TOKEN` |

### API sketch (FastAPI)

```
GET  /health
POST /profiles                          GET /profiles      GET /profiles/{id}
POST /profiles/{id}/disable|enable      POST /profiles/{id}/purge
POST /profiles/{id}/samples             # multipart WAV -> accepted/rejected + reason
GET  /profiles/{id}/samples             DELETE /profiles/{id}/samples/{sid}
POST /profiles/{id}/consents            # draft: scope, expires_at -> statement text + nonce
POST /consents/{cid}/grant              # multipart WAV -> verified | rejected(reason)
POST /consents/{cid}/revoke             GET /profiles/{id}/consents
POST /synthesize                        # {profile_id, text, context, seed, now}
                                        # -> 200 utterance | 403 {reason} (refusal persisted)
GET  /utterances?profile_id=            GET /utterances/{id}    GET /utterances/{id}/audio
POST /utterances/{id}/deliver           # {target_id, now} -> receipt | 403 (re-authorized)
POST /targets                           GET /targets
POST /verify-output                     # multipart WAV -> watermark payload + audit refs
GET  /audit?since_seq=                  GET /audit/verify
```

### CLI sketch (Typer)

```
voicekin init                                        # create DB + managed audio dirs
voicekin profile add|list|show|disable|enable|purge <id>
voicekin enroll <profile> s1.wav [s2.wav ...]        # per-file accept/reject report
voicekin consent draft <profile> --scope announcement,reminder [--expires 2027-06-30]
voicekin consent statement <profile>                 # print the exact text to read
voicekin consent grant <profile> consent.wav
voicekin consent revoke <profile> [--reason ...]     | consent status <profile>
voicekin say <profile> "Dinner is ready" --context announcement
             [--target living-room] [--seed N] [--now ISO]
voicekin deliver <utterance-id> --target <id>
voicekin target add <id> --kind file_sink|home_assistant [--dir ...|--entity ...]
voicekin verify-output out.wav
voicekin audit list [--since-seq N] | audit verify
```

## Key design decisions & assumptions

1. **Consent is treated as biometric-grade, because voice is.** Illinois BIPA
   (740 ILCS 14) requires informed written consent before collecting a
   voiceprint, and *Rosenbach v. Six Flags* (Ill. 2019) established that a
   bare violation is actionable; GDPR Art. 9 makes biometric identification
   data special-category. VoiceKin therefore requires an explicit, specific,
   informed, revocable consent artifact per profile — scope-limited contexts,
   optional expiry, one-command withdrawal (Art. 7(3): as easy to withdraw as
   to give), and purge implementing Art. 17-style erasure. This is the
   workspace quality bar ("likeness safeguards as implemented behavior")
   taken literally.
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
   and EVALS gates false accepts at literally zero on the eval fixtures.
4. **The offline embedder is real DSP, not a mock.** F0 via normalized
   autocorrelation (cf. YIN, de Cheveigné & Kawahara 2002), formant estimates
   via LPC (Makhoul 1975; Levinson–Durbin), mel-spaced band energies and
   spectral shape statistics — the classical speaker-characterizing features
   of the source-filter model of speech production (Fant 1960), MFCC-adjacent
   (Davis & Mermelstein 1980). It must genuinely separate speakers on the
   fixture population (EER-gated), so the consent gate is tested against a
   working verifier, not a canned lookup. The live ECAPA adapter swaps in
   behind the same Protocol without touching the gate.
5. **Consent binds to an enrollment fingerprint, checked at authorize time.**
   `sha256(embedder_id ‖ sorted sample hashes)` defeats the enrollment-swap
   attack (consent to my 3 samples, then swap in yours) and makes embedder
   upgrades conservative: re-embedding changes the fingerprint, so old
   consents stop authorizing until re-granted. Checking at authorization time
   means invalidation is immediate by construction — no invalidation jobs,
   no cache coherence problem.
6. **One gate, capability-style.** `authorize()` is the only constructor of
   `Authorization`, and both rendering and delivery require one — complete
   mediation and economy of mechanism (Saltzer & Schroeder 1975). Gating
   *delivery* too is the difference between "revocation stops new renders"
   and "revocation stops the house speaking in your voice," including replays
   of cached audio.
7. **Audit log is a linear hash chain.** Tamper-evident append-only logging
   in the spirit of Certificate Transparency's Merkle logs (RFC 6962),
   reduced to a linear chain (single writer, no inclusion proofs needed).
   Records carry hashes of audio, never audio, so erasure (purge) coexists
   with verification.
8. **Every output is watermarked and attributable.** Provenance disclosure
   for synthetic media is where regulation is heading (EU AI Act Art. 50
   deepfake-transparency obligations; C2PA content credentials; FCC's 2024
   TCPA ruling on AI voices). MVP embeds an LSB payload (utterance id +
   CRC) — honest about its limits (threat model); AudioSeal-class robust
   watermarking (San Roman et al. 2024) is the named live upgrade.
9. **The offline TTS stub is a Klatt-style formant synthesizer** (Klatt 1980,
   cascade formant synthesis) conditioned on enrollment-derived parameters
   (F0 statistics, formant scale ≈ vocal-tract-length proxy, tilt). It is
   deterministic, seedable, dependency-light, and *identity-bearing*, which
   is exactly what the evals need: synthesized output must re-embed to its
   own profile (speaker-encoder cosine similarity, the SECS measure used to
   evaluate cloning fidelity in YourTTS, Casanova et al. 2022).
   Intelligibility is explicitly not its job — XTTS-class live synthesis
   (Casanova et al. 2024) is.
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
    `sqlite3`). Heavy stacks (torch/speechbrain, XTTS) sit behind extras per
    workspace conventions and are never imported by tests or evals.
13. **Determinism everywhere** (FR-15): `now` and `seed` are inputs; the only
    randomness is the consent nonce (persisted, so downstream behavior is
    reproducible from stored state). Required by workspace conventions and
    what makes 100 %-gates meaningful.
14. **Assumption: fixtures must be synthetic voices.** Committing real human
    voice recordings to the repo would contradict the product's own consent
    ethics and licensing hygiene; parameterized source-filter voices give
    exact identity ground truth and hermetic CI (EVALS.md details margins).
    A real-voice smoke test via the live embedder is a post-MVP addition,
    never a replacement for the gates.
15. **Assumption: single household, cooperative hardware.** One operator,
    localhost API, HA reachable over the LAN; the consent gate protects voice
    owners from misuse *through the tool*, and the audit chain makes misuse
    *around* the tool evident — it does not make a hostile operator's machine
    trustworthy (threat model).

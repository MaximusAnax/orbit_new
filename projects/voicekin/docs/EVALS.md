# VoiceKin — Evals

## What this product lives or dies on

1. **Speaker verification** (FR-3/4/5): the consent gate is only as strong as
   the speaker match beneath it. The offline embedding pipeline must
   genuinely separate speakers — low equal-error-rate on verification trials,
   **zero** false accepts at the committed consent threshold over a large
   pooled impostor set, **no dead feature family** (an embedder that collapses
   to pitch matching must fail), a working enrollment-coherence check, and
   synthesized output that re-embeds to its own profile so the whole
   enroll → consent → synthesize loop is identity-consistent.
2. **Consent-gate enforcement** (FR-5/6/7/11/12): every path that could lead
   to unauthorized synthesis or delivery must be blocked with the exact
   documented refusal, revocation and enrollment-drift must bite immediately,
   and the audit chain must detect every tampering class it claims to detect
   (and be explicit about the one it cannot). This is safety-critical,
   deterministic behavior — gated at 100 %, like the workspace’s other safety gates and the
   workspace quality bar demand.

Everything else (WAV plumbing, CRUD, CLI wiring, HA delivery formatting,
`verify-output` hash lookup) is covered by ordinary tests, not eval gates.

### What passing does and does not certify

Passing these gates certifies that **the offline DSP pipeline separates
synthetic source-filter voices** — including impostors that differ on a
single identity axis, impostors that match the target's pitch, and probes
recorded over a mismatched channel — and that **the consent gate's policy is
exactly the documented policy**. The second claim transfers fully: it is
deterministic logic over scripted state, and the fixtures are the real
service layer's real inputs.

The first claim transfers only partly, and the docs say so rather than
implying otherwise:

- Fixture identity ground truth lives in the *same feature family* the
  embedder measures (F0, vocal-tract length / formants, spectral tilt). A
  real human population varies along axes this corpus does not model
  (nasality, phonation type, idiolect, speaking rate).
- Channel realism is three synthetic conditions (clean, telephone band-pass,
  small-room early reflections) plus gain and white noise — not real mics,
  rooms, codecs, or sessions.
- Consequently **real-voice error rates are unmeasured until the post-MVP
  ECAPA smoke test.** M1a/M1c are DSP-pipeline regression gates and
  degeneracy detectors, not predictions of field FAR/FRR.

What they *do* rule out is the failure mode that matters most here: shipping
a consent gate whose speaker match is decorative.

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with the offline adapters (`SpectralStatsEmbedder`,
`FormantStubSynthesizer`, `FileSinkDeliverer` into a temp dir,
`SQLiteRepository(":memory:")`). No network, no clock (every `now` comes from
fixture data), and every seed — synthesis seeds and `consent draft`
`nonce_seed`s alike — is supplied explicitly by the fixture, never drawn.

### M1 — Speaker-verification separability (capability 1)

Over the committed trial list (`trials.json`), each trial scores
`s = similarity(centroid(enrollment samples), embed(probe))`, where
`similarity(a, b) = 1 − ‖a − b‖² / score_scale` is the shipped scoring rule
over the calibrated embeddings and the centroid is the plain mean of the
sample embeddings (build deviation 3, REVIEW.md — raw cosine provably cannot
meet M1b over these 16 whitened dimensions). Genuine trials are split by
channel condition; impostor trials are split by impostor class.

```
FAR(θ, S) = |{impostor trials in S: s ≥ θ}| / |S|
FRR(θ, G) = |{genuine  trials in G: s < θ}| / |G|
EER(G, I) = FAR(θ*) where FAR(θ*, I) = FRR(θ*, G), linearly interpolated
            between adjacent scores on the DET sweep
```

Three reported sub-metrics (all from one score pass; DET analysis per Martin
et al. 1997 and NIST SRE threshold-sweep protocol):

```
M1a = EER(clean genuine [48], all impostor [288])          # same-channel
M1c = EER(channel-filtered genuine [48], all impostor)     # cross-channel
M1b = max over axis a ∈ {f0, vtl, tilt} of
        FAR(θ_verify, single-axis sibling trials of axis a [16 each])
```

**M1b is the anti-degeneracy gate.** Each single-axis sibling differs from
its base speaker on exactly one identity axis by ≥ 3× the within-speaker
standard deviation of that axis's features and by ~0 on the other two. An
embedder whose LPC/formant dimensions are broken sees the Δvtl-only siblings
as identical to their targets and scores FAR_vtl = 1.00; the same holds for
tilt. M1b is a *detector of a systematically dead feature family*, not a FAR
estimate — 16 trials per axis is ample for that job precisely because a dead
family fails all 16, not a marginal fraction. The pooled FAR estimate is
M2a's job.

Eval speakers are disjoint from the dev speakers used to calibrate the
committed thresholds.

### M2 — Consent decision operating point (capability 1)

Scored with the **committed** θ_verify from `data/calibration.json` (no
sweep — this is the shipped decision).

```
M2a = |{impostor comparisons with s ≥ θ_verify}|
      over the POOLED impostor set: 42 labeled impostor consent attempts
      (consent_trials.json) + all 288 M1 impostor trials = 330 comparisons
M2b_all   = |{genuine consent attempts accepted}| / 48
M2b_clean = |{clean genuine consent attempts accepted}| / 24
```

M2a pools the consent attempts with the M1 impostor trials because the scores
are already computed and the shipped operating point deserves more than 42
observations: 0/330 gives a one-sided 95 % upper bound of ≈ 0.9 % FAR, versus
≈ 7 % from the consent attempts alone. Asymmetric by design: a false accept
is a consent-forgery success (catastrophic, NIST-DCF-style C_fa ≫ C_miss per
SCOPE decision 3); a false reject costs a re-recording — which is why M2b is
split, so channel-induced rejections cannot hide a broken clean path.

### M3 — Synthesis identity attribution (capability 1, closes the loop)

For each enrolled eval profile `p` (K = 24, enrolled through the **real
service layer**) and each of 3 fixture texts, synthesize with the offline
stub (fixed seed), embed the output, and score against every profile
centroid. A trial counts as correct only if **all three** conditions hold, so
the metric cannot be satisfied by an identity beacon that ignores the text:

1. `argmax_q similarity(embed(synth_p), centroid_q) = p` (the shipped scoring
   rule, deviation 3);
2. the output passes the FR-2 quality screen (non-silent, non-clipped, voiced
   ratio ≥ 0.40) — it must be speech-like, not a tone;
3. `duration_s` equals `unit_count(text) × unit_duration_ms / 1000` within
   ± 5 %, and the three fixture texts (17, 21, and 31 units) therefore
   produce three distinct durations — it must be text-dependent.

The three texts (committed in `evals/metrics.py`, unit counts asserted) are
sized so every render is ≥ 3 s — the FR-2 floor below which the product never
embeds any clip — and vowel-balanced so a render measures speech rather than
one corner of the vowel space. The original 4/9/16-unit texts put the
shortest render at 0.72 s, far outside the embedder's specified operating
domain; the change and its measurements are REVIEW.md build deviation 5.

```
M3 = (1 / 3K) · Σ 1[conditions 1–3 hold]
```

Also reported (not gated): mean SECS = mean similarity(embed(synth_p),
centroid_p) — the speaker-encoder similarity measure of YourTTS (Casanova et
al. 2022), under the shipped scoring rule — plus the list of misattributed
trials, printed by `run.py`.

### M4 — Consent-gate scenario pass rate (capability 2)

The scenario suite (`consent_scenarios.json`, **35** scripted scenarios)
drives the real service layer (`SQLiteRepository(":memory:")`, offline
adapters). Each scenario is an ordered list of operations (init, enroll,
draft, grant, revoke, add/remove sample, disable, purge, advance `now`,
synthesize, deliver) with expected outcomes: decision
(`authorized`/`refused`/`rejected`/`error`), exact reason code, persisted row
states, and the exact sequence of audit events with their `detail` keys.

Every scenario supplies explicit `now` values, synthesis seeds, and a
`nonce_seed` per `consent draft`, so the derived nonce is fixed and the
`consent_drafted` audit `detail` — which **does** include `nonce` and
`nonce_seed` (DATA_MODEL) — is exactly predictable. Row ids are derived
(FR-15), so expected ids are computable and scenarios may assert them.

```
M4 = |{scenarios where every expectation holds}| / 35
```

A scenario fails on any mismatch — wrong decision, wrong reason, missing
utterance row for a refusal, or a missing/extra/misordered audit event.

Scenario coverage (`id` in the fixture file):

| # | Scenario | Expected |
|---|---|---|
| 1 | grant → synthesize | authorized |
| 2 | no consent record | `no_consent` |
| 3 | draft only, never granted | `no_consent` |
| 4 | rejected consent → synthesize | `consent_rejected` |
| 5 | grant → revoke → synthesize | `consent_revoked` |
| 6 | revoke twice (idempotence) | second revoke is a no-op, one audit record |
| 7 | unexpired consent | authorized |
| 8 | `now == expires_at` | `consent_expired` (boundary refuses) |
| 9 | `now > expires_at` | `consent_expired` |
| 10 | grant → add sample → synthesize | `enrollment_changed` |
| 11 | grant → remove sample → synthesize | `enrollment_changed` |
| 12 | re-grant after enrollment change | authorized |
| 13 | context outside scope | `scope_mismatch` |
| 14 | context inside scope | authorized |
| 15 | disabled profile with valid consent | `profile_disabled` |
| 16 | purge → synthesize | `profile_purged`; managed files gone, hashes retained |
| 17 | render → revoke → deliver | delivery `refused`/`consent_revoked` |
| 18 | render → deliver | `succeeded`, receipt + manifest written |
| 19 | consent audio = an enrollment sample | `reused_enrollment_audio` |
| 20 | consent audio fails screening | `audio_quality` |
| 21 | joint-sibling impostor consent | `speaker_mismatch` |
| 22 | zero-sample profile | `no_enrollment` |
| 23 | second draft while a consent is effective | operation error (409), no row created |
| 24 | refusal persistence | utterance row with `status=refused`, null `consent_id`, `synthesis_refused` audit |
| 25 | precedence: disabled + revoked | `profile_disabled` |
| 26 | history: older rejected + newer revoked | `consent_revoked` (governing record only) |
| 27 | history: older revoked + newer expired | `consent_expired` |
| 28 | enroll 2×S + 1× S's sibling | mutation refused, `incoherent_enrollment`, `sample_rejected` audit, centroid unchanged |
| 29 | enroll 2×S + 1× unrelated speaker | same |
| 30 | draft, remove a sample below the FR-3 minimum, grant | `rejected`/`not_enrolled`, score fields null |
| 31 | precedence: expired + enrollment_changed | `consent_expired` |
| 32 | precedence: purged + revoked | `profile_purged` |
| 33 | precedence: enrollment_changed + scope_mismatch | `enrollment_changed` |
| 34 | render → deliver (file_sink) → purge | delivered WAV + manifest deleted, `detail.path` → `path_sha256`, purge audit counts correct |
| 35 | grant, add sample, grant again, remove that sample (enrollment reverts) | `enrollment_changed` — the superseded consent does **not** resurrect (FR-6 governing rule) |

### M5 — Audit tamper detection (capability 2)

Run a scripted 30-event session, snapshot the DB, then apply each committed
tamper case (`tamper_cases.json`, T = 7) to a fresh copy and compare
`audit verify`'s outcome against the case's **expected** outcome:

| Case | Mutation | Expected |
|---|---|---|
| 1 | mutate a `detail` field | rejected at that `seq` |
| 2 | mutate `ts` | rejected at that `seq` |
| 3 | delete a mid-chain record | rejected at the following `seq` |
| 4 | reorder two records | rejected at the earlier `seq` |
| 5 | replace a `record_hash` with a recomputation that ignores `prev_hash` | rejected at that `seq` |
| 6 | truncate the tail, append a forged record with wrong hashes | rejected at the forged `seq` |
| 7 | truncate the tail and re-append **correctly chained** records | **verifies clean — undetectable by design** |

```
M5 = ( 1[clean log verifies] + Σ_t 1[case t matches its expected outcome] )
     / (1 + T)
```

Case 7 is executable documentation of FR-12's stated limit: an unkeyed linear
chain has no secret, so an adversary with DB write access who recomputes the
chain produces a log that verifies. `audit verify` printing `head_seq` /
`head_hash` is the whole mitigation — an operator who records the head
externally can detect the truncation out of band. SCOPE's threat model scopes
the malicious operator out; M5 makes sure the eval suite does not quietly
claim more than the design delivers.

### M6 — Enrollment coherence decision quality (capability 1, FR-3)

θ_enroll deserves the same dev-calibrated / eval-verified treatment as
θ_verify, because a mixed enrollment set is a partial voice-theft path the
fingerprint binding cannot catch (the fingerprint hashes whatever set is
there). Over sets constructed mechanically from `labels.json`
(`coherence_sets.json`):

- **24 pure sets** — each eval speaker's 3 enrollment samples;
- **24 mixed sets** — 2 samples of speaker S plus one foreign clip: 6 from
  S's joint sibling, 6 from S's single-axis sibling (2 per axis), 12 from an
  unrelated eval speaker's enrollment. The speaker assignment is the
  mechanical first-two-per-axis-group rule of `build_derived.py`; the S08
  vtl set it thereby excludes — provably unresolvable by any θ_enroll — is
  recorded honestly in REVIEW.md ("M6 mixed-set composition rule"), together
  with where that impostor *is* still gated (M1b, M2a).

```
M6_pure  = |{pure sets accepted by the FR-3 LOO check at θ_enroll}| / 24
M6_mixed = |{mixed sets rejected with incoherent_enrollment}| / 24
```

## Fixture strategy

Ground truth is always fixed by generation parameters or hand-authoring —
never by running the system under test — so the evals cannot be circular.

### Corpus materialization (deviation from "commit the fixtures")

The corpus is ≈ **486 WAVs, ≈ 53 minutes, ≈ 100 MB** of 16 kHz PCM16 — too
much binary data for the monorepo. What is committed:

- `evals/fixtures/generate_voices.py` (seeded, `--seed 20260731`);
- `evals/fixtures/labels.json` (speaker ids, sibling/mimic relations, every
  generation parameter, per-utterance role, channel condition, SNR/gain);
- `evals/fixtures/corpus_manifest.json` (relative path → sha256, plus total
  count and duration);
- every derived fixture: `trials.json`, `consent_trials.json`,
  `coherence_sets.json`, `consent_scenarios.json`, `tamper_cases.json`.

WAVs materialize into `evals/fixtures/.cache/voices/` (gitignored) on first
run and are reused thereafter. This satisfies CONVENTIONS' intent —
"generation scripts are committed and seeded", deterministic, hermetic (pure
numpy, no network) — while deviating from the literal "committed fixture
data"; the deviation is recorded in REVIEW.md.

**Reproducibility policy.** The generator is seeded and deterministic on a
given machine. Byte-exact reproduction across numpy/BLAS builds is *not* a CI
requirement: `test_fixture_corpus_manifest` compares generated sha256s
against `corpus_manifest.json` and is **skipped unless
`VOICEKIN_STRICT_FIXTURES=1`**, so ULP-level differences cannot redden CI for
the wrong reason. Metric gates never depend on exact bytes — the margins
below are 3σ-scale, orders of magnitude above float noise — and audit-chain
hashes are stable because FR-12 rounds floats to 6 dp before canonicalization.

### Synthetic voice corpus — `.cache/voices/*.wav` + `labels.json`

Committing real human voices would contradict the product's own consent
ethics; synthetic voices give exact identity ground truth and hermetic CI
(SCOPE decision 14). The generator drives the shared source-filter core
`engine/voicebox.py` (SCOPE decision 17) with a **richer parameter regime**
than the product stub ever uses — 4-formant stacks, jitter, shimmer,
breathiness, channel filters — so the embedder is evaluated on audio whose
parameter space it was not co-designed with. Anti-circularity comes from the
embedder being derived from neither generator nor stub, not from duplicated
synthesis code.

- **Speaker = parameter tuple**, sampled from realistic ranges: `f0_base`
  (male band 85–155 Hz, female band 165–255 Hz — adult speaking-F0 norms,
  Titze *Principles of Voice Production*), per-speaker F0 sd, vocal-tract
  length factor scaling a 4-formant stack anchored on the Peterson & Barney
  (1952) vowel space, spectral tilt (−6 to −15 dB/oct), jitter (0.5–2 %),
  shimmer, breathiness (harmonics-to-noise mix).
- **Utterances**: seeded pseudo-sentences — 6–14 syllables from 5 vowel
  targets + consonant noise bursts, sentence-level F0 declination and
  per-syllable prosody variation. Enrollment 6–8 s, probes 3–5 s,
  consent-style 10–14 s (statement reading).
- **Channel conditions** (recorded in `labels.json`): `clean` (gain ±6 dB,
  additive white noise SNR 20–30 dB); `phone` (300–3400 Hz band-pass,
  4th-order Butterworth, then clean's gain/noise); `room` (3-tap early
  reflections at 11/17/29 ms with gains 0.35/0.22/0.14, then clean's
  gain/noise). **Enrollment samples are always `clean`**; 2 of each speaker's
  4 probes and, for half the speakers, one extra consent take are `phone` or
  `room` — so cross-channel genuine trials (clean enrollment vs. filtered
  probe/consent) exist by construction, which is the product's own intake
  reality (phone-recorded consent vs. `arecord` enrollment).
- **Population**: 36 base speakers → **12 dev** (threshold calibration only)
  and **24 eval** (metrics), disjoint per NIST SRE practice. Impostor classes:

  | Class | Perturbation from the base speaker | Who gets one | Utterances each |
  |---|---|---|---|
  | joint sibling | Δf0 12 % **and** Δvtl 4 % **and** Δtilt 2 dB/oct | eval S01–S12, 6 dev speakers | 2 probes + 1 consent |
  | Δf0-only sibling | Δf0 12 %, others ≈ 0 | eval S01–S04, 2 dev | 4 probes + 1 consent |
  | Δvtl-only sibling | Δvtl 8 %, others ≈ 0 | eval S05–S08, 2 dev | 4 probes + 1 consent |
  | Δtilt-only sibling | Δtilt 4 dB/oct, others ≈ 0 | eval S09–S12, 2 dev | 4 probes + 1 consent |
  | pitch mimic | `f0_base` within 1× within-speaker σ of the target; vtl and tilt ≥ 3σ away | eval S13–S18 | 2 probes + 1 consent |

  Every single-axis margin is sized ≥ 3× the within-speaker standard
  deviation *of that axis's own feature dimensions* induced by prosody, gain,
  and noise (Δvtl and Δtilt are larger than in the joint sibling precisely
  because they must clear 3σ alone). Impostors are never enrolled; their
  clips are used as probes, consent attempts, and M6 foreign clips.

  The single-axis and mimic classes exist because the joint sibling alone is
  gameable: perturbing all three axes at once lets *any one* working feature
  family separate every trial, so a pitch-only embedder with dead formant and
  band dimensions would pass. Pitch is exactly the feature an in-scope
  housemate impostor can consciously imitate, so certifying a pitch matcher
  would certify the wrong product.
- **Per base speaker**: 3 enrollment + 4 probes + 1 consent-style utterance;
  12 eval and 6 dev speakers get an extra **harsh** consent take (SNR 20 dB,
  −6 dB gain); 12 eval and 6 dev speakers get an extra **channel-filtered**
  consent take.

### Trials — `evals/fixtures/trials.json` (M1)

96 genuine trials (24 eval speakers × 4 probes; 48 clean, 48
channel-filtered) and 288 impostor trials — 12 non-self probes per eval
speaker, filled in this order: all available sibling/mimic probes for that
speaker, then seeded-random other-speaker probes. Composition: 24
joint-sibling, 48 single-axis-sibling (16 per axis), 12 mimic, 204
random-other. Labels derive mechanically from `labels.json`.

### Consent attempts — `evals/fixtures/consent_trials.json` (M2)

- **48 genuine**: 24 clean, 12 harsh (SNR 20 dB, −6 dB gain), 12
  channel-filtered.
- **42 impostor**: 12 joint-sibling consents, 12 single-axis-sibling consents
  (4 per axis), 6 pitch-mimic consents, 12 random-other (each of 12 eval
  speakers' clean consent scored against a seeded non-matching enrollment).

Quality-fail and replay attempts live in M4 scenarios, not here — M2 isolates
the *speaker decision*.

### Coherence sets — `evals/fixtures/coherence_sets.json` (M6)

24 pure + 24 mixed sets as described under M6, constructed mechanically from
`labels.json` (no hand-authoring, so the set cannot drift from the corpus).

### Threshold calibration (committed, dev-only)

`evals/fixtures/calibrate.py` (committed, seeded) computes dev-split scores
and writes `data/calibration.json` (placements per REVIEW.md deviation 3 —
asymmetric rather than midpoint, matching SCOPE decision 3's unequal error
costs):

- **θ_verify** = 60 % of the way from the maximum dev **impostor** score
  (over all dev sibling, single-axis, and random-other consent attempts and
  probe trials) toward the minimum dev **clean genuine** consent score,
  asserting a gap ≥ 0.05 score units. Above the midpoint by design: a false
  accept is the catastrophic error. Clean-only on the genuine side is
  deliberate: letting harsh/channel-mismatched takes drag θ down would trade
  the catastrophic error for the benign one. Those takes are what M2b's
  0.85 tolerance is for.
- **θ_enroll** = 10 % of the way from the minimum dev *pure-set* LOO score
  down toward the maximum dev *mixed-set* LOO score, same ≥ 0.05 gap
  assertion — close to the pure side for the same safety asymmetry.
- **16 feature normalization constants** from dev statistics: `mean` is the
  dev enrollment population mean; `scale` is a robust within-speaker sd over
  dev clean takes, F-ratio-weighted so a speaker-discriminative dimension
  counts for more of the distance (formulas in `calibrate.py`).
- **provenance** names the script, seed, dev split, and both margins.

Eval metrics never touch dev speakers; calibration never touches eval
speakers.

**Calibration staleness tripwire.** An ordinary test (not a gate),
`test_calibration_matches_dev_split_fr4`, re-runs `calibrate.py` against the
dev split and asserts the result equals the committed `data/calibration.json`
within 1e-9. Editing embedder code without bumping `embedder_id` therefore
fails the suite instead of silently leaving dev-derived constants stale
behind a load-time id check that still passes.

### Scenario + tamper fixtures (M4/M5)

`consent_scenarios.json` — the 35 hand-authored scripts in the M4 table, each
op carrying explicit `now`, `seed`, and `nonce_seed` values and referencing
corpus WAVs by role (e.g. `"S03/enroll/1"`, `"S03-sib-joint/consent/0"`);
expected audit event sequences and `detail` keys are spelled out per
scenario. `tamper_cases.json` — the 7 cases as (description, JSON patch,
expected outcome). Both hand-authored and reviewed against FR-5/FR-6/FR-7/
FR-12 clause by clause.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| **M1a** same-channel EER | cosine over a 2-dim [log mean energy, log duration] "embedding" | ≈ 0.45 (near chance — gain/length variation is deliberately uninformative) | **≤ 0.05** | Generator margins put a correct F0+formant+band pipeline at ≈ 0.02–0.04 EER (single-axis siblings and mimics supply the residual errors); 0.05 fails if a feature family regresses, without demanding fixture-overfit perfection. |
| **M1b** max per-axis FAR@θ_verify | pitch-only embedder (formant + band dims zeroed after normalization) | FAR_f0 = 0, FAR_vtl = 1.00, FAR_tilt = 1.00 → max = **1.00** | **= 0** | The degeneracy detector. Any axis whose feature family is dead scores near 1.00 on its 16 single-axis trials; a working pipeline scores 0 because every margin is ≥ 3σ on that axis alone. This is the gate that makes M1a's rationale true. |
| **M2a** pooled impostor accepts @ committed θ_verify (330 comparisons) | accept-all (θ = −∞; distance scores are unbounded below) | 330/330 | **= 0** | Any accept is a consent forgery — the one unacceptable error (SCOPE decision 3). 0/330 bounds FAR ≤ 0.9 % at 95 % one-sided, versus ≈ 7 % from the 42 consent attempts alone. |
| **M2b_clean** genuine clean accept rate | reject-all (θ = +1) | 0.00 | **≥ 0.95** | Clean genuine attempts are the easy condition; ≥ 23/24 means θ is not drifting lazily high. |
| **M2b_all** genuine accept rate over 48 | reject-all | 0.00 | **≥ 0.85** | The 12 harsh and 12 channel-filtered takes make 1.0 unrealistic and are *supposed* to be hard; ≥ 41/48 tolerates ≤ 7 annoying-but-safe rejects while the clean gate keeps the easy path honest. |
| **M1c** cross-channel EER | same 2-dim baseline | ≈ 0.47 | **≤ 0.12** | Channel mismatch is the known-hard, honestly-measured condition, not a solved one; a loose but real gate makes regressions visible without pretending the offline embedder is channel-invariant. Gate loosening here can never mask a safety failure — M2a and M1b are the safety gates. |
| **M3** attribution (72 trials, 3 conditions each) | stub that ignores `voice_params` (fixed default voice) | 0.042 (measured live) | **≥ 0.93** | Measured 0.944 (68/72; mean SECS 0.46). The corpus's near-twin base speakers put 1,656 pairwise contests through an embedder whose measured same-channel EER on real recordings is 1.0 %, so zero argmax errors is statistically unattainable (REVIEW.md deviation 5 has the full derivation and the remediation record); ≥ 0.93 still sits 22× above the baseline and above every identity-dropping pipeline measured (≤ 0.55), which is the failure this metric exists to catch. |
| **M4** scenario pass rate (35) | gate = "a `verified` consent row exists for the profile" | 0.69 (24/35, measured live) under decision-only scoring; 0.00 under the real scoring (exact reason + audit sequence) | **= 1.00** | Deterministic policy over committed scripts; every scenario is a documented FR-5/FR-6/FR-7/FR-11 clause — partial credit would hide a broken revocation, precedence, or binding check (workspace rule: safety gates at 100 %). |
| **M5** tamper outcomes (1 clean + 7 cases) | plain rows, no chain (verify = "rows exist") | 2/8 = 0.25 (the clean case and case 7, which expects "verifies clean", pass trivially) | **= 1.00** | Hash-chain verification is deterministic; cases 1–6 are distinct attack classes (content, time, deletion, reorder, rehash, truncate+forge) and case 7 pins the documented limit so the suite claims exactly what the design delivers. |
| **M6** coherence: `M6_pure` / `M6_mixed` | accept every set | 1.00 / 0.00 | **both = 1.00** | θ_enroll is calibrated with a ≥ 0.05 dev margin between pure and mixed LOO scores, so both are deterministically achievable; a mixed set that enrolls is a partial voice theft the fingerprint binding cannot catch. |

If fixture composition, impostor margins, channel conditions, or trial counts
change, this table must be re-derived in the same commit (checked in review).
The M1a expectation band (0.02–0.04) followed from the committed generator
margins as a design target; the shipped pipeline measures 0.010 (M1c: 0.059)
— the gate is what is enforced.

## FR → gate mapping

| FR | Covered by |
|---|---|
| FR-3 enrollment coherence | M6, M4 #28–29 |
| FR-4 embedder | M1a, M1b, M1c |
| FR-5 consent verification | M2a, M2b, M4 #19–21, #30 |
| FR-6 authorization gate | M4 #1–17, #22–27, #31–33, #35 |
| FR-7 revocation & erasure | M4 #5, #16, #17, #34 |
| FR-8/FR-9 synthesis & rendering | M3 |
| FR-11 delivery re-authorization | M4 #17, #18, #34 |
| FR-12 audit chain | M5 |
| FR-1/2/10/13/14/15 | ordinary tests (test names reference the FR id) |

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python voicekin/evals/run.py    # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest voicekin/                # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: materializes the corpus into
  `evals/fixtures/.cache/` if absent, loads fixtures, builds
  enrollments/consents through the real service layer with offline adapters,
  computes M1–M6, and prints the table above with actual values; non-zero
  exit on any gate failure.
- `evals/test_gates.py` — one pytest per gate, names referencing FR ids for
  the auditable FR → test mapping: `test_gate_m1a_eer_fr4`,
  `test_gate_m1b_axis_degeneracy_fr4`, `test_gate_m1c_cross_channel_eer_fr4`,
  `test_gate_m2a_pooled_impostor_far_fr5`,
  `test_gate_m2b_genuine_consent_fr5`, `test_gate_m3_attribution_fr8_fr9`,
  `test_gate_m4_scenarios_fr6_fr7_fr11`, `test_gate_m5_audit_tamper_fr12`,
  `test_gate_m6_enroll_coherence_fr3`.
- `evals/metrics.py` — pure metric functions shared by both entry points.
- Hermetic: offline adapters only, no network, no wall clock (all `now`
  values come from fixture scripts), all seeds and `nonce_seed`s supplied by
  fixtures; the live ECAPA/XTTS/Home-Assistant adapters are never imported on
  the eval path.

# VoiceKin — Evals

## What this product lives or dies on

1. **Speaker verification** (FR-3/4/5): the consent gate is only as strong as
   the speaker match beneath it. The offline embedding pipeline must
   genuinely separate speakers — low equal-error-rate on verification trials,
   **zero** false accepts at the committed consent threshold on the fixture
   population (a false accept = activating a voice without its owner's real
   consent), and synthesized output must re-embed to its own profile so the
   whole enroll → consent → synthesize loop is identity-consistent.
2. **Consent-gate enforcement** (FR-5/6/7/11/12): every path that could lead
   to unauthorized synthesis or delivery must be blocked with the exact
   documented refusal, revocation and enrollment-drift must bite immediately,
   and the audit chain must actually detect tampering. This is safety-
   critical, deterministic behavior — gated at 100 %, like Orbit's safety
   gates and the workspace quality bar demand.

Everything else (WAV plumbing, CRUD, CLI wiring, HA delivery formatting) is
covered by ordinary tests, not eval gates. The watermark embed/extract round
trip is deterministic plumbing → ordinary tests (its *presence* on every
rendered output is asserted inside the M4 scenarios).

## Metrics

All metrics live in `evals/metrics.py` and run exclusively against committed
fixtures with the offline adapters (`SpectralStatsEmbedder`,
`FormantStubSynthesizer`, `FileSinkDeliverer` into a temp dir, in-memory
store). No network, no clock (every `now` comes from fixture data), fixed
seeds.

### M1 — Speaker-verification EER (capability 1)

Over the committed trial list (`trials.json`): each trial scores
`s = cosine(centroid(enrollment samples), embed(probe))`. With `G` genuine
trials (probe speaker = enrolled speaker) and `I` impostor trials:

```
FAR(θ) = |{impostor trials: s ≥ θ}| / |I|
FRR(θ) = |{genuine  trials: s < θ}| / |G|
EER    = FAR(θ*) where θ* is the point with FAR(θ*) = FRR(θ*),
         linearly interpolated between adjacent scores on the DET sweep
```

Standard ASV methodology (DET analysis, Martin et al. 1997; NIST SRE
protocol: threshold sweep over the pooled score set). Eval speakers are
disjoint from the dev speakers used to calibrate the committed thresholds.

### M2 — Consent decision operating point (capability 1)

Over the labeled consent-attempt list (`consent_trials.json`), scored with
the **committed** θ_verify from `data/calibration.json` (no sweep — this is
the shipped decision):

```
M2a = |{impostor attempts accepted}|            (count, must be 0)
M2b = |{genuine attempts accepted}| / |genuine attempts|
```

Asymmetric by design: a false accept is a consent-forgery success
(catastrophic, NIST-DCF-style C_fa ≫ C_miss reasoning per SCOPE decision 3);
a false reject costs a re-recording.

### M3 — Synthesis identity attribution (capability 1, closes the loop)

For each enrolled eval profile `p` (K = 24) and each of 3 fixture texts,
synthesize with the offline stub (fixed seed), embed the output, and score
against every profile centroid:

```
M3 = (1 / 3K) · Σ 1[ argmax_q cosine(embed(synth_p), centroid_q) = p ]
```

Also reported (not gated): mean SECS = mean cosine(embed(synth_p),
centroid_p) — the speaker-embedding cosine similarity measure used to
evaluate cloning fidelity in YourTTS (Casanova et al. 2022).

### M4 — Consent-gate scenario pass rate (capability 2)

The scenario suite (`consent_scenarios.json`, 25 scripted scenarios) drives
the real service layer (in-memory store, offline adapters). Each scenario is
an ordered list of operations (enroll, draft, grant, revoke, add/remove
sample, disable, purge, advance `now`, synthesize, deliver) with expected
outcomes: decision (`authorized`/`refused`/`rejected`/`error`), exact
reason code, persisted row states, and the exact sequence of audit events.

```
M4 = |{scenarios where every expectation holds}| / 25
```

A scenario fails on any mismatch — wrong decision, wrong reason, missing
utterance row for a refusal, or a missing/extra audit event.

Scenario coverage (ids in the fixture file): happy grant→synthesize;
no-consent; draft-only; rejected-consent then synthesize; grant→revoke→
synthesize; revoke idempotence; unexpired consent authorized; expiry
boundary `now == expires_at` refused; `now > expires_at` refused;
grant→add-sample→synthesize (`enrollment_changed`); grant→remove-sample
(`enrollment_changed`); re-grant after enrollment change re-authorizes;
scope mismatch; in-scope context authorized; disabled profile refused with
`profile_disabled` despite valid consent; purge then synthesize
(`profile_purged`, files verified gone, hashes retained); render→revoke→
deliver refused (delivery re-authorization); render→deliver success with
receipt; consent replay of an enrollment sample rejected
(`reused_enrollment_audio`); consent audio quality failure rejected
(`audio_quality`); sibling-impostor consent rejected (`speaker_mismatch`);
zero-sample profile (`no_enrollment`); second draft while one consent is
effective refused; refusals persisted as utterance rows across all
scenarios; precedence order (disabled + revoked ⇒ `profile_disabled`).

### M5 — Audit tamper detection (capability 2)

Run a scripted 30-event session, snapshot the DB, then apply each committed
tamper case (`tamper_cases.json`, T = 6: mutate a `detail` field, mutate
`ts`, delete a mid-chain record, reorder two records, replace a
`record_hash` with a self-consistent recomputation that ignores
`prev_hash`, truncate the tail and append a forged record) to a fresh copy:

```
M5 = ( 1[clean log verifies] + Σ_t 1[tampered copy t is rejected
        with the correct first-bad seq] ) / (1 + T)
```

## Fixture strategy

Everything is committed under `evals/fixtures/` and regenerable
byte-identically by the committed seeded generator
(`evals/fixtures/generate_voices.py --seed 20260731`). Ground truth is
always fixed by generation parameters or hand-authoring — never by running
the system under test — so the evals cannot be circular.

### Synthetic voice corpus — `evals/fixtures/voices/*.wav` + `labels.json`

Committing real human voices would contradict the product's own consent
ethics; synthetic voices give exact identity ground truth and hermetic CI
(SCOPE decision 14). The generator is a Klatt-style source-filter simulator
(Klatt 1980; Fant 1960) — deliberately a *different* code path with richer
parameters than the product's stub synthesizer, so the embedder is evaluated
on audio it was not co-designed with:

- **Speaker = parameter tuple**, sampled from realistic ranges: `f0_base`
  (male band 85–155 Hz, female band 165–255 Hz — adult speaking-F0 norms,
  Titze *Principles of Voice Production*), per-speaker F0 sd, vocal-tract
  length factor scaling a 4-formant stack anchored on the Peterson & Barney
  (1952) vowel space, spectral tilt (−6 to −15 dB/oct), jitter (0.5–2 %),
  shimmer, and breathiness (harmonics-to-noise mix).
- **Utterances**: seeded pseudo-sentences — 6–14 syllables drawn from 5
  vowel targets + consonant noise bursts, sentence-level F0 declination and
  per-syllable prosody variation; 4–9 s each. Consent-style utterances are
  longer (12–18 s) to match statement reading.
- **Channel realism**: per-utterance gain offsets (±6 dB) and additive white
  noise at SNR 20–30 dB (seeded, recorded in labels) — the embedder must
  survive level and noise variation, which is what the normalization
  dimensions are for.
- **Population**: 36 base speakers → **12 dev** (threshold calibration only)
  and **24 eval** (metrics), disjoint per NIST SRE practice. For 12 of the
  24 eval speakers (and 6 dev), a **sibling impostor** is generated: same
  parameters perturbed by a committed margin (Δf0_base = 12 %, Δvtl = 4 %,
  Δtilt = 2 dB/oct). Margins are sized ≥ 3× the within-speaker feature
  standard deviation induced by prosody + noise, so a correct embedder
  separates siblings and a sloppy one (e.g. energy-dominated features)
  fails — the honest-difficulty knob, same philosophy as formcoach's
  near-threshold reps.
- **Per speaker**: 3 enrollment + 4 probe + 1 consent-style utterance;
  siblings get 2 probes + 1 consent-style utterance (siblings are never
  enrolled).
- **`labels.json`**: speaker id, sibling-of, all generation parameters,
  per-utterance role, SNR/gain applied — identity ground truth by
  construction.

### Trials — `evals/fixtures/trials.json` (M1)

96 genuine trials (24 eval speakers × 4 probes) and 288 impostor trials:
for each enrolled eval speaker, 12 non-self probes — every available
sibling probe (the hard trials) plus seeded-random other-speaker probes.
Labels derive mechanically from `labels.json`.

### Consent attempts — `evals/fixtures/consent_trials.json` (M2)

24 genuine attempts (each eval speaker's consent-style utterance; 6 of them
at the harsh end: SNR 20 dB and −6 dB gain) and 24 impostor attempts (12
sibling consent utterances against their original's enrollment + 12
random-other). Quality-fail and replay attempts are covered in M4
scenarios, not here — M2 isolates the *speaker decision*.

### Threshold calibration (committed, dev-only)

`evals/fixtures/calibrate.py` (committed, seeded) computes dev-split scores
and writes `data/calibration.json`: θ_verify = midpoint between the maximum
dev impostor score and the minimum dev genuine consent score, asserting a
margin ≥ 0.05 cosine between them; θ_enroll and the 16 feature
normalization constants come from dev enrollment statistics. The provenance
string in the file names the script, seed, and dev split. Eval metrics
never touch dev speakers; calibration never touches eval speakers.

### Scenario + tamper fixtures (M4/M5)

`consent_scenarios.json` — the 25 hand-authored scenario scripts listed
under M4, each op carrying explicit `now` timestamps and referencing corpus
WAVs by role (e.g. "speaker S03 enrollment 1", "sibling of S03 consent");
expected audit event sequences are spelled out per scenario.
`tamper_cases.json` — the 6 mutations as (description, SQL/JSON patch,
expected first-bad seq). Hand-authored, reviewed against FR-6/FR-12 clause
by clause.

## Naive baselines and gates

| Metric | Naive baseline | Baseline score | Gate | Rationale |
|---|---|---|---|---|
| M1 EER | cosine over a 2-dim [log mean energy, log duration] "embedding" | ≈ 0.45 (near chance — gain/length variation is deliberately uninformative) | **≤ 0.05** | Generator margins put a correct F0+formant+band pipeline at ≈ 0.01–0.03 EER (siblings supply the residual errors); 0.05 fails if any feature family (F0 extraction, LPC formants, normalization) regresses, while not demanding fixture-overfit perfection. |
| M2a impostor consent accepts | accept-all (θ = −1) | 24/24 accepted | **= 0** | Sibling margins are ≥ 3× within-speaker σ by construction and θ_verify is calibrated with explicit dev margin, so zero is achievable deterministically; any accept is a consent forgery — the one unacceptable error (SCOPE decision 3). |
| M2b genuine consent accept rate | reject-all (θ = +1) | 0.00 | **≥ 0.90** | The 6 harsh-condition genuine attempts make 1.0 non-trivial; ≥ 0.90 (≥ 22/24) tolerates ≤ 2 harsh-condition rejects — annoying-but-safe failures — without letting θ drift lazily high. |
| M3 attribution | stub that ignores `voice_params` (fixed default voice) | ≈ 1/24 ≈ 0.04 | **= 1.00** | Deterministic stub conditioned on well-separated enrolled params; anything below 1.0 means the render pipeline drops or distorts identity — exactly the regression this metric exists to catch. |
| M4 scenario pass rate | gate = "a `verified` consent row exists for the profile" | ≈ 0.32 (8/25: passes only scenarios where existence happens to equal effectiveness) | **= 1.00** | Deterministic policy over committed scripts; every scenario is a documented FR-6/FR-5/FR-7/FR-11 clause — partial credit would hide a broken revocation or binding check (workspace rule: safety gates at 100 %). |
| M5 tamper detection | plain rows, no chain (verify = "rows exist") | 1/7 ≈ 0.14 (passes only the clean-log case) | **= 1.00** | Hash-chain verification is deterministic; each tamper case is a distinct attack class (content, time, deletion, reorder, rehash, truncate+forge) — missing any one is a real hole in tamper evidence. |

If fixture composition, sibling margins, or trial counts change, this table
must be re-derived in the same commit (checked in review). The M1
expectation band (0.01–0.03) follows from the committed generator margins;
it is a design target, not a measured promise — the gate is what is
enforced.

## How the suite runs

Per workspace conventions:

```bash
cd projects
uv run python voicekin/evals/run.py    # scorecard: metric | value | gate | PASS/FAIL; exit 1 on any FAIL
uv run pytest voicekin/                # unit/integration tests + evals/test_gates.py
```

- `evals/run.py` — zero-config: loads fixtures, builds enrollments/consents
  through the real service layer with offline adapters, computes M1–M5, and
  prints the table above with actual values; non-zero exit on any gate
  failure.
- `evals/test_gates.py` — one pytest per gate, names referencing FR ids for
  the auditable FR → test mapping: `test_gate_m1_eer_fr4`,
  `test_gate_m2a_impostor_consent_fr5`, `test_gate_m2b_genuine_consent_fr5`,
  `test_gate_m3_attribution_fr8_fr9`, `test_gate_m4_scenarios_fr6_fr7_fr11`,
  `test_gate_m5_audit_tamper_fr12`.
- `evals/metrics.py` — pure metric functions shared by both entry points.
- Hermetic: offline adapters only, no network, no wall clock (all `now`
  values come from fixture scripts), seeded randomness only; the live
  ECAPA/XTTS/Home-Assistant adapters are never imported on the eval path.

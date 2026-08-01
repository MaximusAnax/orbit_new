# VoiceKin

A consent-gated personal voice system for the smart home. Enroll your own or a
family member's voice from WAV samples, verify an explicit recorded consent
artifact against that enrollment **by speaker match**, and only then let the
house speak in that voice. Every synthesis and every delivery goes through one
authorization gate, every rendered WAV is attributable by the hash of its PCM
payload, and everything lands in a tamper-evident, hash-chained audit log.
Revocation kills the voice instantly — including replays of audio already
rendered.

See `docs/SCOPE.md` for the problem statement, threat model and numbered
functional requirements; `docs/DATA_MODEL.md` for entities and invariants;
`docs/EVALS.md` for the metrics and gates; `docs/REVIEW.md` for the scoping
critique log and the recorded build-phase deviations.

## Quickstart

```bash
cd projects
uv sync --all-packages

export VOICEKIN_HOME=~/.voicekin        # or pass --data-home everywhere

uv run voicekin init --operator "Abdoul"
uv run voicekin profile add partner --name "Amina" --relationship partner
uv run voicekin enroll partner sample1.wav sample2.wav sample3.wav
uv run voicekin consent draft partner --scope announcement,reminder
# ...the voice owner reads the printed statement aloud, you record it...
uv run voicekin consent grant partner consent.wav
uv run voicekin target add living-room --kind file_sink --dir /srv/ha-media
uv run voicekin say partner "Dinner is ready" --context announcement --target living-room
```

Real output of that flow (WAVs from the synthetic eval corpus):

```text
$ voicekin enroll partner S03/enroll-0.wav S03/enroll-1.wav S03/enroll-2.wav
accepted .../S03/enroll-0.wav -> sample 685f902d8907e8bd2390167e271ccca9 (8.4s, snr 30.3 dB, voiced 0.82)
accepted .../S03/enroll-1.wav -> sample bba9ab8fde535c2c249a00a4a2e56b11 (7.3s, snr 21.3 dB, voiced 0.80)
accepted .../S03/enroll-2.wav -> sample e45667051879672854caf87c2ef9529f (7.4s, snr 27.3 dB, voiced 0.82)
profile partner is enrolled (fingerprint 0a769ef1fd8c...804ef83b)

$ voicekin consent draft partner --scope announcement,reminder --nonce-seed 4815162342
drafted consent 17d6a854fee941b5dd9837dad3d98d2a (nonce 7CCHATZY)
have the voice owner read this aloud, then run `voicekin consent grant`:

I, Amina, consent to the VoiceKin system operated by Abdoul reproducing my
voice for: announcements, reminders. This consent does not expire and I may
revoke it at any time, effective immediately. Verification code: 7CCHATZY.
Date: 2026-08-01.

$ voicekin consent grant partner S03/consent-0.wav
consent 17d6a854fee941b5dd9837dad3d98d2a verified (similarity 0.969 >= threshold 0.800)

$ voicekin say partner "Dinner is ready" --context announcement --target living-room --seed 7
rendered utterance e812cb40c92bd63673e2bc1a265d88cd
  output:   audio/out/e812cb40c92bd63673e2bc1a265d88cd.wav (0.90s)
  sha256:   e0b4272a949eef3a99fb726075c8b788970716ccb8464826447e1c3ec5f18bdb
  consent:  17d6a854fee941b5dd9837dad3d98d2a
delivered to living-room: succeeded {'path': '.../e812cb40....wav', 'manifest_path': '.../e812cb40....json'}

$ voicekin consent revoke partner --reason "testing revocation"
consent 17d6a854fee941b5dd9837dad3d98d2a revoked at 2026-08-01T05:40:45Z

$ voicekin say partner "The laundry is done" --context announcement
refused: consent_revoked (utterance 09657aabee5388fcf340320d0d5624cf)   # exit code 3

$ voicekin audit verify
audit chain OK: head_seq=11 head_hash=beca04f4813a366f4d626f61e03b84dd2532...
record the head hash somewhere VoiceKin cannot touch — that anchor is
the only defence against a truncate-and-recompute of the whole chain.
```

Exit codes: `0` success · `1` not found / unknown output / broken audit chain ·
`2` fixable precondition (e.g. drafting while a consent is effective) ·
`3` the authorization gate refused (the refusal is persisted and audited
before the CLI exits).

An impostor cannot activate the voice: a consent recording by anyone else is
rejected with `speaker_mismatch` (and the recording is hashed and discarded,
never stored), a replayed enrollment sample with `reused_enrollment_audio`,
and changing the enrollment after consent invalidates it (`enrollment_changed`)
until the owner re-grants. `voicekin profile purge` erases audio, embeddings,
rendered outputs and delivered copies VoiceKin itself wrote — and says plainly
that copies outside its reach are not recalled.

## The API (automation surface)

The REST API is what scripts and Home Assistant automations call — read,
synthesize, deliver, revoke, audit, verify. Enrollment and consent recordings
stay on the CLI (no file upload by design; `docs/SCOPE.md` decision 16).

```bash
uv run uvicorn --factory voicekin.api:create_app   # binds localhost:8000
```

| Route | Purpose |
|---|---|
| `GET /health` | liveness + operator |
| `GET /profiles` · `GET /profiles/{id}` | profile state (never ships centroids/embeddings) |
| `GET /profiles/{id}/consents` | consent history |
| `POST /consents/{cid}/revoke` | instant revocation (governing record only) |
| `POST /synthesize` | `{profile_id, text, context, seed, now}` → utterance, or **403** `{reason}` (refusal persisted) |
| `GET /utterances/{id}` · `GET /utterances/{id}/audio` | row + WAV |
| `POST /utterances/{id}/deliver` | re-authorizes, then delivers → receipt or **403** |
| `POST /verify-output` | `{output_sha256}` → provenance, or **404** `unknown_output` |
| `GET /audit?since_seq=` · `GET /audit/verify` | chain read + verification |

Error catalog: gate refusals → `403 {"error":"refused","reason":...}`;
precondition failures → `409`; unknown ids → `404`; malformed bodies → `422`.
`now` may be supplied in any body for deterministic replay; omitted, the
current UTC time is stamped at this thin layer (the engine itself never reads
the clock — FR-15).

## Running the tests and evals

```bash
cd projects
uv run pytest voicekin/ -q             # 278 tests: unit, integration, API, CLI + eval gates
uv run python voicekin/evals/run.py    # eval scorecard (below)
uv run ruff check voicekin/            # lint
uv run python verify_all.py voicekin   # everything CI runs
```

The eval suite is hermetic: offline adapters only, synthetic fixture voices
(committing real human recordings would contradict the product's own consent
ethics), no network, no wall clock, every seed supplied by fixtures. On first
run it materializes the ~490-WAV corpus into `evals/fixtures/.cache/` from the
committed seeded generator. Scorecard as of this commit:

```text
metric        value  gate           naive baseline (computed live)                detail
----------------------------------------------------------------------------------------
M1a          0.0104  <= 0.05  PASS  2-dim energy/duration EER 0.497               same-channel EER, 48 genuine / 288 impostor
M1b          0.0000  = 0      PASS  pitch-only max FAR 1.00 (f0 0.00 vtl 1.00 ...)  max per-axis FAR@theta f0 0.00, vtl 0.00, tilt 0.00
M1c          0.0590  <= 0.12  PASS  2-dim baseline EER 0.479                      cross-channel EER
M2a               0  = 0      PASS  accept-all: 330/330                           impostor accepts over the pooled 330
M2b_clean    0.9583  >= 0.95  PASS  reject-all: 0.00                              clean genuine accepts / 24
M2b_all      0.8958  >= 0.85  PASS  reject-all: 0.00                              all genuine accepts / 48
M3           0.9444  >= 0.93  PASS  fixed-voice stub 0.042                        68/72 attributions, mean SECS 0.461
M4           1.0000  = 1.00   PASS  naive verified-row gate 0.69 (decision-only)  35/35 scenarios
M5           1.0000  = 1.00   PASS  no-chain verify 0.25                          clean chain ok + 7/7 tamper cases
M6_pure      1.0000  = 1.00   PASS  accept-all 1.00                               pure sets accepted / 24
M6_mixed     1.0000  = 1.00   PASS  accept-all 0.00                               mixed sets rejected / 24
```

What passing does and does not certify — including why real-voice error rates
remain unmeasured until the live-embedder smoke test — is spelled out in
`docs/EVALS.md`; gate changes made during the build are argued in
`docs/REVIEW.md`.

## Layout

```
src/voicekin/
  models.py               Pydantic v2 entities + enums (DATA_MODEL.md)
  engine/                 pure domain logic — no network, no filesystem, no clock
    audio.py              WAV codec, mono-mix, 16 kHz resampling, payload hashing
    dsp.py                framing, F0, LPC/formants, mel bands, spectral shape, IIR
    quality.py            FR-2 screening
    voicebox.py           shared Klatt-style source-filter synthesis core
    enrollment.py         centroid, leave-one-out coherence, fingerprint, voice params
    verification.py       similarity scoring + the FR-5(b) consent decision
    consent.py            statement rendering + the FR-6 authorization gate
    synthesis.py          text normalization, unit sequencing, band EQ, packaging
    ids.py                FR-15 derived identifiers and consent nonces
    audit.py              FR-12 canonical JSON, hash chain, verification
  adapters/               one Protocol per external capability (offline + live)
  store/                  Repository interface + SQLiteRepository (file or :memory:)
  services.py             orchestration; every filesystem touch lives here
  api/                    FastAPI automation surface (FR-13)
  cli/                    Typer administrative surface (FR-14)
data/                     consent_statement.txt, calibration.json (committed constants)
evals/                    corpus access, metrics, scorecard, gate tests, fixtures
```

## Committed datasets

`data/calibration.json` holds every tunable decision constant: the consent
threshold θ_verify, the enrollment-coherence threshold θ_enroll, the score
scale of the similarity rule, the 16 embedding normalization constants, the
FR-2 screening limits, and a `provenance` string recording exactly how each
value was derived from the dev fixture split. Services refuse to combine a
calibration file with a mismatched `embedder_id`, and an ordinary test re-runs
the calibration script against the dev split so editing embedder code without
re-deriving the constants fails the suite instead of silently drifting.

## Live adapters

Live implementations activate only when their credentials or optional extras
are present, and are never imported on the offline/eval path:

| Adapter | Activates when |
|---|---|
| `HomeAssistantDeliverer` — copies the WAV into HA's media dir and POSTs `media_player.play_media` | `VOICEKIN_HA_URL` + `VOICEKIN_HA_TOKEN` (long-lived access token) are set |
| `EcapaEmbedder` (SpeechBrain ECAPA-TDNN, interface-fixed, post-MVP) | extra `voicekin[live-embed]` |
| `XttsSynthesizer` (XTTS-class cloning, interface-fixed, post-MVP) | extra `voicekin[live-tts]` |

No secrets are stored in the database: Home Assistant credentials come from
the environment, and `device_target.config` rejects credential-shaped keys.

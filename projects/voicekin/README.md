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
`docs/EVALS.md` for the metrics and gates.

## Status

This tree currently ships the **core**: domain models, the pure engine, the
adapter interfaces with their offline implementations, the SQLite repository, the
service layer, and the engine/integration test suite. The REST API, the CLI and
the eval suite land in the next pass.

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
    verification.py       cosine scoring + the FR-5(b) consent decision
    consent.py            statement rendering + the FR-6 authorization gate
    synthesis.py          text normalization, unit sequencing, output packaging
    ids.py                FR-15 derived identifiers and consent nonces
    audit.py              FR-12 canonical JSON, hash chain, verification
  adapters/               one Protocol per external capability
  store/                  Repository interface + SQLiteRepository
  services.py             orchestration; every filesystem touch lives here
data/                     consent_statement.txt, calibration.json
```

## Running

```bash
cd projects
uv run pytest voicekin/ -q      # engine + integration tests
uv run ruff check voicekin/     # lint
```

## Committed datasets

`data/calibration.json` holds every tunable decision constant: the consent
threshold θ_verify, the enrollment-coherence threshold θ_enroll, the 16 embedding
normalization constants, the FR-2 screening limits, and a `provenance` string
recording exactly how each value was derived and from which dev split. Services
refuse to combine a calibration file with a mismatched `embedder_id`, so
`spectral-v1` embeddings can never be scored against thresholds calibrated for a
different embedder.

## Live adapters

Live implementations activate only when their credentials or optional extras are
present, and are never imported on the offline path:

| Adapter | Gate |
|---|---|
| `EcapaEmbedder` (SpeechBrain ECAPA-TDNN) | extra `voicekin[live-embed]` |
| `XttsSynthesizer` (XTTS-class cloning) | extra `voicekin[live-tts]` |
| `HomeAssistantDeliverer` | `VOICEKIN_HA_URL` + `VOICEKIN_HA_TOKEN` |

No secrets are stored in the database: Home Assistant credentials come from the
environment, and `device_target.config` rejects credential-shaped keys.

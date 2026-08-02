"""Shared fixtures: synthetic voices, an in-memory store, and a wired service.

Test voices are rendered by ``engine/voicebox.py`` in the same richer regime the
eval fixture generator uses (4 formants, jitter, shimmer, breathiness, gain and
white noise), so the embedder is exercised on audio it was not co-designed with.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest
from voicekin.adapters.embedder_spectral import SpectralStatsEmbedder
from voicekin.adapters.synth_stub import FormantStubSynthesizer
from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip, clip_from_pcm16, encode_wav
from voicekin.engine.voicebox import Unit, VoiceboxParams, synthesize_units
from voicekin.models import Context
from voicekin.services import VoiceKinService, load_calibration, load_statement_template
from voicekin.store import SQLiteRepository

VOWELS = ("a", "e", "i", "o", "u")
ENROLL_SECONDS = 7.5
CONSENT_SECONDS = 9.0
CONSONANTS = ("fricative", "plosive", "nasal", "approximant")


@dataclass(frozen=True)
class Speaker:
    """A synthetic voice identity: pitch, vocal-tract length, spectral tilt."""

    f0_base_hz: float = 120.0
    formant_scale: float = 1.0
    tilt_db_oct: float = -10.0
    jitter: float = 0.012
    shimmer: float = 0.05
    breathiness: float = 0.3
    bandwidth_scale: float = 1.0

    def params(self) -> VoiceboxParams:
        return VoiceboxParams(
            f0_base_hz=self.f0_base_hz,
            f0_range_hz=self.f0_base_hz * 0.16,
            formant_scale=self.formant_scale,
            tilt_db_oct=self.tilt_db_oct,
            n_formants=4,
            jitter=self.jitter,
            shimmer=self.shimmer,
            breathiness=self.breathiness,
            bandwidth_scale=self.bandwidth_scale,
        )

    def shifted(self, *, f0: float = 0.0, vtl: float = 0.0, tilt: float = 0.0) -> Speaker:
        """A sibling voice differing on the named identity axes."""
        return replace(
            self,
            f0_base_hz=self.f0_base_hz * (1.0 + f0),
            formant_scale=self.formant_scale * (1.0 + vtl),
            tilt_db_oct=self.tilt_db_oct + tilt,
        )


#: Well-separated identities used across the suite.
ALICE = Speaker(f0_base_hz=215.0, formant_scale=0.88, tilt_db_oct=-9.0)
BOB = Speaker(f0_base_hz=105.0, formant_scale=1.14, tilt_db_oct=-11.0)
CARLA = Speaker(f0_base_hz=160.0, formant_scale=1.05, tilt_db_oct=-10.5)

#: A housemate deliberately matching Alice's pitch but nobody else's tract or
#: source — the impostor class SCOPE decision 4 says a pitch matcher would pass.
MIMIC = Speaker(f0_base_hz=ALICE.f0_base_hz * 1.01, formant_scale=1.12, tilt_db_oct=-14.0)


def make_units(rng: np.random.Generator, target_s: float) -> list[Unit]:
    """A phonetically balanced pseudo-sentence: the five vowel targets rotate."""
    units: list[Unit] = []
    elapsed = 0.0
    index = int(rng.integers(0, len(VOWELS)))
    while elapsed < target_s:
        duration = int(rng.integers(150, 230))
        if rng.random() < 0.12 and units:
            units.append(Unit(duration_ms=duration))
        else:
            units.append(
                Unit(
                    duration_ms=duration,
                    vowel=VOWELS[index % len(VOWELS)],
                    onset=str(rng.choice(CONSONANTS)) if rng.random() < 0.7 else None,
                    f0_scale=float(np.clip(1.0 + rng.normal(0.0, 0.05), 0.8, 1.25)),
                    amplitude=float(0.75 + 0.35 * rng.random()),
                )
            )
            index += 1
        elapsed += duration / 1000.0
    return units


def render_voice(
    speaker: Speaker, duration_s: float, seed: int, *, snr_db: float = 26.0
) -> AudioClip:
    """Render a clip of ``speaker`` with gain and additive noise, quantized to PCM16."""
    rng = np.random.default_rng(seed)
    units = make_units(rng, duration_s)
    samples = synthesize_units(units, speaker.params(), sample_rate=TARGET_SAMPLE_RATE, seed=seed)
    samples = samples * 10.0 ** (rng.uniform(-4.0, 4.0) / 20.0)
    power = float(np.mean(samples**2))
    noise_power = power / (10.0 ** (snr_db / 10.0))
    samples = samples + rng.normal(0.0, np.sqrt(noise_power), samples.shape[0])
    samples = np.clip(samples, -0.999, 0.999)
    pcm = np.clip(np.round(samples * 32768.0), -32768, 32767).astype(np.int16)
    return clip_from_pcm16(pcm)


def write_voice(
    directory: Path, name: str, speaker: Speaker, duration_s: float, seed: int, **kwargs
) -> Path:
    """Render a voice to a WAV file and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.wav"
    path.write_bytes(encode_wav(render_voice(speaker, duration_s, seed, **kwargs)))
    return path


@pytest.fixture(scope="session")
def calibration():
    return load_calibration()


@pytest.fixture(scope="session")
def statement_template():
    return load_statement_template()


@pytest.fixture(scope="session")
def embedder(calibration):
    return SpectralStatsEmbedder(calibration)


@pytest.fixture(scope="session")
def synthesizer(calibration):
    return FormantStubSynthesizer(calibration.unit_duration_ms)


@pytest.fixture
def repository():
    repo = SQLiteRepository.in_memory()
    yield repo
    repo.close()


@pytest.fixture
def service(tmp_path, repository, calibration, statement_template):
    return VoiceKinService(
        repository=repository,
        data_home=tmp_path / "home",
        calibration=calibration,
        statement_template=statement_template,
    )


@pytest.fixture
def initialized(service):
    service.initialize("Abdoul", "2026-07-31T10:00:00Z")
    return service


@pytest.fixture(scope="session")
def enrollment_clips():
    """Three clean enrollment takes of ALICE, reused across tests."""
    return [render_voice(ALICE, ENROLL_SECONDS, seed=4100 + k) for k in range(3)]


def enroll(
    service,
    profile_id: str,
    speaker: Speaker,
    now: str,
    tmp_path: Path,
    *,
    seed: int = 4100,
    display_name: str | None = None,
):
    """Create the profile if needed and enroll three clean takes of ``speaker``."""
    if service.repository.get_profile(profile_id) is None:
        service.create_profile(profile_id, display_name or profile_id.title(), "self", now)
    paths = [
        write_voice(tmp_path / "wav" / profile_id, f"e{k}", speaker, ENROLL_SECONDS, seed + k)
        for k in range(3)
    ]
    return service.add_samples(profile_id, paths, now)


def grant(
    service,
    profile_id: str,
    speaker: Speaker,
    now: str,
    tmp_path: Path,
    *,
    contexts=(Context.ANNOUNCEMENT, Context.REMINDER),
    expires_at: str | None = None,
    nonce_seed: int = 4815162342,
    seed: int = 7700,
):
    """Draft and grant consent using a fresh recording of ``speaker``."""
    service.draft_consent(profile_id, list(contexts), expires_at, nonce_seed, now)
    path = write_voice(
        tmp_path / "wav" / profile_id, f"consent{seed}", speaker, CONSENT_SECONDS, seed
    )
    return service.grant_consent(profile_id, path, now)

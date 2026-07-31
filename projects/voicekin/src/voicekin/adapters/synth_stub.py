"""``stub-v1`` — the offline default synthesizer (FR-8).

A thin, deterministic wrapper over ``engine/voicebox.py``: a Klatt-style cascade
driven by the profile's enrollment-derived parameters, with a declination contour
and seeded jitter.

The cascade carries four formants. Three is not enough: without an F4 resonance
the voiced spectrum has no energy above ~3 kHz, the consonant bursts dominate the
top of the band, and the measured spectral tilt stops responding to the profile's
tilt parameter entirely — the stub would silently drop one of the three identity
axes it exists to carry (FR-8: "identity-bearing"). The richer fixture regime
(shimmer, breathiness, per-formant offsets, channel filters) stays exclusive to
the eval generator, and anti-circularity is unaffected either way because the
embedder is derived from neither (SCOPE decision 17).

It is *identity-bearing, not intelligible*, and that is the whole design intent:
its output must re-embed to its own profile (EVALS M3, the SECS measure of
YourTTS, Casanova et al. 2022). Natural speech is the live adapter's job.
"""

from __future__ import annotations

import numpy as np

from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.engine.synthesis import build_units
from voicekin.engine.voicebox import VoiceboxParams, synthesize_units
from voicekin.models import VoiceParams

STUB_SYNTH_ID = "stub-v1"
STUB_FORMANT_COUNT = 4
STUB_JITTER = 0.02
"""Cycle-to-cycle F0 perturbation. Seeded, so it never breaks byte determinism."""

STUB_NOISE_FLOOR_DB = 35.0
"""Seeded dither mixed into the render, at this SNR.

Every recording VoiceKin ever screens or embeds has a noise floor; a
mathematically noiseless render does not, and its spectrum falls away to -100 dB
where a microphone's would flatten out. Measured spectral tilt then describes the
synthesizer's filter rolloff instead of the profile's voice, and the rendered
utterance stops embedding near its own enrollment (EVALS M3). The dither is drawn
from the same seed as the synthesis, so byte determinism is untouched."""


class FormantStubSynthesizer:
    """Cascade formant synthesis over the shared voicebox core."""

    def __init__(self, unit_duration_ms: int) -> None:
        if unit_duration_ms <= 0:
            raise ValueError("unit_duration_ms must be positive")
        self.unit_duration_ms = unit_duration_ms

    @property
    def synth_id(self) -> str:
        return STUB_SYNTH_ID

    def voicebox_params(self, voice_params: VoiceParams) -> VoiceboxParams:
        """Map the four stored voice parameters onto the synthesis core."""
        return VoiceboxParams(
            f0_base_hz=voice_params.f0_base_hz,
            f0_range_hz=voice_params.f0_range_hz,
            formant_scale=voice_params.formant_scale,
            tilt_db_oct=voice_params.tilt_db_oct,
            n_formants=STUB_FORMANT_COUNT,
            jitter=STUB_JITTER,
        )

    def synthesize(
        self,
        text: str,
        voice_params: VoiceParams,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        seed: int = 0,
    ) -> AudioClip:
        """Render normalized ``text``; duration is exactly ``units * unit_duration_ms``."""
        units = build_units(text, self.unit_duration_ms)
        if not units:
            raise ValueError("nothing to synthesize: the text produced no units")
        samples = synthesize_units(
            units, self.voicebox_params(voice_params), sample_rate=sample_rate, seed=seed
        )
        return AudioClip(samples=self._dither(samples, seed), sample_rate=sample_rate)

    @staticmethod
    def _dither(samples: np.ndarray, seed: int) -> np.ndarray:
        """Mix seeded broadband noise in at :data:`STUB_NOISE_FLOOR_DB`."""
        power = float(np.mean(samples**2)) if samples.size else 0.0
        if power <= 0.0:
            return samples
        amplitude = np.sqrt(power / (10.0 ** (STUB_NOISE_FLOOR_DB / 10.0)))
        noise = np.random.default_rng(seed + 1).normal(0.0, amplitude, samples.shape[0])
        return np.clip(samples + noise, -0.999, 0.999)


__all__ = [
    "STUB_FORMANT_COUNT",
    "STUB_JITTER",
    "STUB_NOISE_FLOOR_DB",
    "STUB_SYNTH_ID",
    "FormantStubSynthesizer",
]

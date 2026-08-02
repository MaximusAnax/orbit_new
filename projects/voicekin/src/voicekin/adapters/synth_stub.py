"""``stub-v1`` — the offline default synthesizer (FR-8).

A thin, deterministic wrapper over the shared stub render in
``engine/synthesis.py`` (itself a parameterization of ``engine/voicebox.py``): a
Klatt-style cascade driven by the profile's enrollment-derived parameters, with
a declination contour, seeded jitter, and a seeded noise floor.

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

from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.engine.synthesis import (
    STUB_FORMANT_COUNT,
    STUB_JITTER,
    STUB_NOISE_FLOOR_DB,
    stub_render,
    stub_voicebox_params,
)
from voicekin.models import VoiceParams

STUB_SYNTH_ID = "stub-v1"


class FormantStubSynthesizer:
    """Cascade formant synthesis over the shared voicebox core."""

    def __init__(self, unit_duration_ms: int) -> None:
        if unit_duration_ms <= 0:
            raise ValueError("unit_duration_ms must be positive")
        self.unit_duration_ms = unit_duration_ms

    @property
    def synth_id(self) -> str:
        return STUB_SYNTH_ID

    def voicebox_params(self, voice_params: VoiceParams):
        """Map the four stored voice parameters onto the synthesis core."""
        return stub_voicebox_params(voice_params)

    def synthesize(
        self,
        text: str,
        voice_params: VoiceParams,
        *,
        sample_rate: int = TARGET_SAMPLE_RATE,
        seed: int = 0,
    ) -> AudioClip:
        """Render normalized ``text``; duration is exactly ``units * unit_duration_ms``."""
        return stub_render(
            text,
            voice_params,
            sample_rate=sample_rate,
            seed=seed,
            unit_duration_ms=self.unit_duration_ms,
        )


__all__ = [
    "STUB_FORMANT_COUNT",
    "STUB_JITTER",
    "STUB_NOISE_FLOOR_DB",
    "STUB_SYNTH_ID",
    "FormantStubSynthesizer",
]

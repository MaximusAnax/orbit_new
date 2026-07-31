"""Shared parameterized source-filter synthesis core (FR-8, SCOPE decision 17).

One implementation, two parameter regimes: the product's ``FormantStubSynthesizer``
drives it with the four enrollment-derived voice parameters and a 3-formant
stack; the eval fixture generator drives the same code with a richer regime
(4-formant stacks, jitter, shimmer, breathiness, channel filters). Anti-circularity
is preserved by the *embedder* being derived from neither.

The model is a cascade formant synthesizer in the spirit of Klatt (1980): a
glottal pulse train with a declination contour and seeded jitter, spectrally
tilted, driven through cascaded second-order resonators whose centre frequencies
come from the Peterson & Barney (1952) vowel space scaled by a vocal-tract-length
factor, with band-shaped noise for consonant classes.

Deterministic: identical ``(units, params, sample_rate, seed)`` give identical
samples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from voicekin.engine.dsp import convolve_fft, resonator_impulse_response

#: Peterson & Barney (1952) adult-male F1-F4 for the five vowel presets (Hz).
VOWEL_PRESETS: dict[str, tuple[float, float, float, float]] = {
    "i": (270.0, 2290.0, 3010.0, 3700.0),
    "e": (530.0, 1840.0, 2480.0, 3600.0),
    "a": (730.0, 1090.0, 2440.0, 3400.0),
    "o": (570.0, 840.0, 2410.0, 3300.0),
    "u": (300.0, 870.0, 2240.0, 3300.0),
}

#: Neutral (schwa-like) tract used for consonant-only units.
NEUTRAL_FORMANTS: tuple[float, float, float, float] = (500.0, 1500.0, 2500.0, 3500.0)

#: Nominal resonator bandwidths for F1-F4 (Hz).
FORMANT_BANDWIDTHS: tuple[float, float, float, float] = (60.0, 90.0, 120.0, 180.0)

#: Mean F1/F2 across the vowel presets — the reference vocal tract that
#: ``formant_scale = 1.0`` denotes (used both here and by FR-3 derivation).
REFERENCE_F1_HZ = float(np.mean([v[0] for v in VOWEL_PRESETS.values()]))
REFERENCE_F2_HZ = float(np.mean([v[1] for v in VOWEL_PRESETS.values()]))

CONSONANT_CLASSES = ("fricative", "plosive", "nasal", "approximant")

# Amplitude envelope of one unit, in milliseconds.
ATTACK_MS = 10.0
RELEASE_MS = 20.0
GAP_MS = 35.0
"""Silence at the tail of every unit. Speech that never goes quiet has no noise
floor, and the FR-2 SNR proxy would (correctly) call it unusable."""

RESONATOR_TAPS = 1024
TAIL_TAPS = 512
TILT_ANCHOR_HZ = 100.0
PEAK_LEVEL = 0.85

RADIATION_DB_OCT = 6.0
"""Lip radiation: the radiated pressure is the derivative of the volume velocity,
so the spectrum measured at a microphone sits ``+6 dB/octave`` above the glottal
source. Omitting it makes every voice far darker than a real one — the formant
region drops below the noise floor and the vocal-tract features stop being
measurable at all. ``VoiceboxParams.tilt_db_oct`` is the *source* slope;
``tilt_db_oct + RADIATION_DB_OCT`` is what a microphone (and the embedder) sees."""

# Excitation mix, all relative to the voiced source's RMS.
ONSET_BURST_MS = 30.0
VOICED_ONSET_LEVEL = 0.30
UNVOICED_UNIT_LEVEL = 0.70
ASPIRATION_LEVEL = 0.30


@dataclass(frozen=True)
class VoiceboxParams:
    """Everything the core needs to sound like one particular voice."""

    f0_base_hz: float
    f0_range_hz: float
    formant_scale: float
    tilt_db_oct: float
    n_formants: int = 3
    jitter: float = 0.0
    shimmer: float = 0.0
    breathiness: float = 0.0
    bandwidth_scale: float = 1.0
    formant_offsets_hz: tuple[float, ...] = ()
    """Optional per-formant additive offsets — the fixture regime's extra knob."""

    def __post_init__(self) -> None:
        if self.f0_base_hz <= 0.0:
            raise ValueError("f0_base_hz must be positive")
        if not 1 <= self.n_formants <= 4:
            raise ValueError("n_formants must be between 1 and 4")
        if self.formant_scale <= 0.0:
            raise ValueError("formant_scale must be positive")


@dataclass(frozen=True)
class Unit:
    """One syllable-like synthesis unit."""

    duration_ms: int
    vowel: str | None = None
    onset: str | None = None
    f0_scale: float = 1.0
    amplitude: float = 1.0

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            raise ValueError("unit duration must be positive")
        if self.vowel is not None and self.vowel not in VOWEL_PRESETS:
            raise ValueError(f"unknown vowel preset {self.vowel!r}")
        if self.onset is not None and self.onset not in CONSONANT_CLASSES:
            raise ValueError(f"unknown consonant class {self.onset!r}")

    @property
    def voiced(self) -> bool:
        return self.vowel is not None


@dataclass
class _KernelCache:
    sample_rate: int
    params: VoiceboxParams
    cache: dict[str | None, np.ndarray] = field(default_factory=dict)

    def get(self, vowel: str | None) -> np.ndarray:
        if vowel not in self.cache:
            self.cache[vowel] = cascade_kernel(vowel, self.params, self.sample_rate)
        return self.cache[vowel]


def formant_stack(vowel: str | None, params: VoiceboxParams) -> list[tuple[float, float]]:
    """``(centre, bandwidth)`` pairs for a unit's resonator cascade."""
    base = VOWEL_PRESETS[vowel] if vowel is not None else NEUTRAL_FORMANTS
    stack: list[tuple[float, float]] = []
    for index in range(params.n_formants):
        offset = params.formant_offsets_hz[index] if index < len(params.formant_offsets_hz) else 0.0
        freq = base[index] * params.formant_scale + offset
        bandwidth = FORMANT_BANDWIDTHS[index] * params.bandwidth_scale
        stack.append((freq, bandwidth))
    return stack


def cascade_kernel(vowel: str | None, params: VoiceboxParams, sample_rate: int) -> np.ndarray:
    """Impulse response of the cascaded formant resonators for one unit."""
    kernel = np.zeros(RESONATOR_TAPS, dtype=np.float64)
    kernel[0] = 1.0
    nyquist = sample_rate / 2.0
    for freq, bandwidth in formant_stack(vowel, params):
        centre = float(np.clip(freq, 60.0, nyquist * 0.98))
        response = resonator_impulse_response(centre, bandwidth, sample_rate, RESONATOR_TAPS)
        kernel = convolve_fft(kernel, response)
    return kernel


def unit_boundaries(units: list[Unit], sample_rate: int) -> list[tuple[int, int]]:
    """Sample spans per unit; the total is exact for the summed durations."""
    bounds: list[tuple[int, int]] = []
    elapsed_ms = 0.0
    start = 0
    for unit in units:
        elapsed_ms += unit.duration_ms
        end = round(elapsed_ms * sample_rate / 1000.0)
        bounds.append((start, end))
        start = end
    return bounds


def unit_envelope(length: int, sample_rate: int) -> np.ndarray:
    """Raised-cosine attack, sustain, release, then a silent gap."""
    envelope = np.ones(length, dtype=np.float64)
    attack = min(int(ATTACK_MS * sample_rate / 1000.0), length)
    gap = min(int(GAP_MS * sample_rate / 1000.0), length)
    release = min(int(RELEASE_MS * sample_rate / 1000.0), max(length - gap, 0))
    if attack > 1:
        envelope[:attack] = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, attack))
    if gap > 0:
        envelope[length - gap :] = 0.0
    if release > 1:
        stop = length - gap
        envelope[stop - release : stop] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, release))
    return envelope


def f0_track(
    units: list[Unit], bounds: list[tuple[int, int]], params: VoiceboxParams, total: int
) -> np.ndarray:
    """Per-sample fundamental frequency: declination and per-unit prosody."""
    track = np.full(total, params.f0_base_hz, dtype=np.float64)
    if total == 0:
        return track
    position = np.arange(total, dtype=np.float64) / max(total - 1, 1)
    declination = params.f0_range_hz * (0.5 - position)
    track = track + declination
    for unit, (start, end) in zip(units, bounds, strict=True):
        track[start:end] *= unit.f0_scale
    return np.maximum(track, 20.0)


def _pulse_train(
    track: np.ndarray,
    voiced: np.ndarray,
    envelope: np.ndarray,
    params: VoiceboxParams,
    sample_rate: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Glottal pulse train with seeded jitter and shimmer."""
    total = track.shape[0]
    excitation = np.zeros(total, dtype=np.float64)
    position = 0.0
    while position < total:
        index = int(position)
        if voiced[index]:
            amplitude = 1.0 + params.shimmer * float(rng.uniform(-1.0, 1.0))
            excitation[index] += amplitude * envelope[index]
        period = sample_rate / track[index]
        if params.jitter > 0.0:
            period *= 1.0 + params.jitter * float(rng.uniform(-1.0, 1.0))
        position += max(period, 2.0)
    return excitation


def apply_tilt(signal: np.ndarray, tilt_db_oct: float, sample_rate: int) -> np.ndarray:
    """Impose a spectral tilt in dB/octave, anchored at 100 Hz.

    Anchoring low and only ever attenuating keeps the source spectrum bounded —
    a tilt anchored mid-band would apply enormous gain near DC.
    """
    if signal.size == 0 or tilt_db_oct == 0.0:
        return signal
    n_fft = 1 << int(np.ceil(np.log2(max(signal.size, 2))))
    spectrum = np.fft.rfft(signal, n_fft)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    octaves = np.log2(np.maximum(freqs, TILT_ANCHOR_HZ) / TILT_ANCHOR_HZ)
    gain = 10.0 ** ((tilt_db_oct * octaves) / 20.0)
    return np.fft.irfft(spectrum * gain, n_fft)[: signal.size]


def _shaped_noise(length: int, consonant: str, rng: np.random.Generator) -> np.ndarray:
    """Band-shaped, unit-RMS noise for one consonant class."""
    if length <= 0:
        return np.zeros(0, dtype=np.float64)
    noise = rng.normal(0.0, 1.0, length)
    if consonant in ("fricative", "plosive"):
        # Two-fold first difference: a +12 dB/octave high emphasis.
        noise = np.diff(noise, n=2, prepend=noise[:1], append=noise[-1:])[:length]
    elif consonant == "nasal":
        kernel = np.ones(8) / 8.0
        noise = np.convolve(noise, kernel, mode="same")
    else:  # approximant: mild low emphasis
        kernel = np.ones(3) / 3.0
        noise = np.convolve(noise, kernel, mode="same")
    rms = float(np.sqrt(np.mean(noise**2))) if noise.size else 0.0
    return noise / rms if rms > 0.0 else noise


def _consonant_excitation(
    units: list[Unit],
    bounds: list[tuple[int, int]],
    total: int,
    sample_rate: int,
    rng: np.random.Generator,
    reference_rms: float,
) -> np.ndarray:
    """Onset bursts and consonant-only units (FR-8 "noise bursts").

    Levels are relative to the voiced source's RMS. Fixing them in absolute terms
    would make the consonants swamp the voice whenever the spectral tilt is steep
    — and a signal dominated by broadband noise has no measurable pitch.
    """
    signal = np.zeros(total, dtype=np.float64)
    for unit, (start, end) in zip(units, bounds, strict=True):
        if unit.onset is None:
            continue
        span = end - start
        if unit.voiced:
            burst = min(span, int(ONSET_BURST_MS * sample_rate / 1000.0))
            level = VOICED_ONSET_LEVEL
        else:
            burst = max(span - int(GAP_MS * sample_rate / 1000.0), 1)
            level = UNVOICED_UNIT_LEVEL
        noise = _shaped_noise(burst, unit.onset, rng)
        decay = np.exp(-np.linspace(0.0, 4.0, burst)) if unit.onset == "plosive" else np.ones(burst)
        signal[start : start + burst] += level * reference_rms * unit.amplitude * noise * decay
    return signal


def synthesize_units(
    units: list[Unit],
    params: VoiceboxParams,
    *,
    sample_rate: int,
    seed: int,
) -> np.ndarray:
    """Render a unit sequence to mono float samples in ``[-1, 1]``.

    The output length is exactly the summed unit durations — no filter tail is
    appended — so duration is a pure function of the unit sequence (EVALS M3).
    """
    if not units:
        return np.zeros(0, dtype=np.float64)
    rng = np.random.default_rng(seed)
    bounds = unit_boundaries(units, sample_rate)
    total = bounds[-1][1]
    if total <= 0:
        return np.zeros(0, dtype=np.float64)

    envelope = np.zeros(total, dtype=np.float64)
    voiced = np.zeros(total, dtype=bool)
    for unit, (start, end) in zip(units, bounds, strict=True):
        envelope[start:end] = unit_envelope(end - start, sample_rate) * unit.amplitude
        voiced[start:end] = unit.voiced

    track = f0_track(units, bounds, params, total)
    glottal = _pulse_train(track, voiced, envelope, params, sample_rate, rng)
    source = apply_tilt(glottal, params.tilt_db_oct + RADIATION_DB_OCT, sample_rate)

    active = source[voiced] if bool(voiced.any()) else source
    reference_rms = float(np.sqrt(np.mean(active**2))) if active.size else 0.0
    if reference_rms <= 0.0:
        reference_rms = 1.0
    if params.breathiness > 0.0:
        aspiration = rng.normal(0.0, 1.0, total) * envelope * voiced
        source = source + params.breathiness * ASPIRATION_LEVEL * reference_rms * aspiration
    source = source + _consonant_excitation(units, bounds, total, sample_rate, rng, reference_rms)

    cache = _KernelCache(sample_rate=sample_rate, params=params)
    out = np.zeros(total + TAIL_TAPS, dtype=np.float64)
    for unit, (start, end) in zip(units, bounds, strict=True):
        segment = source[start:end]
        if not np.any(segment):
            continue
        padded = np.concatenate([segment, np.zeros(TAIL_TAPS, dtype=np.float64)])
        out[start : start + padded.shape[0]] += convolve_fft(padded, cache.get(unit.vowel))

    rendered = out[:total]
    peak = float(np.max(np.abs(rendered)))
    if peak > 0.0:
        rendered = rendered * (PEAK_LEVEL / peak)
    return rendered


__all__ = [
    "ATTACK_MS",
    "CONSONANT_CLASSES",
    "FORMANT_BANDWIDTHS",
    "GAP_MS",
    "NEUTRAL_FORMANTS",
    "PEAK_LEVEL",
    "RADIATION_DB_OCT",
    "REFERENCE_F1_HZ",
    "REFERENCE_F2_HZ",
    "RELEASE_MS",
    "VOWEL_PRESETS",
    "Unit",
    "VoiceboxParams",
    "apply_tilt",
    "cascade_kernel",
    "f0_track",
    "formant_stack",
    "synthesize_units",
    "unit_boundaries",
    "unit_envelope",
]

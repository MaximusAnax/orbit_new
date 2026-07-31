"""Classical source-filter DSP (FR-4). Deterministic, numpy-only, no I/O.

This module is the measurement half of the product: F0 by normalized
autocorrelation (cf. YIN, de Cheveigné & Kawahara 2002), formants from an LPC
spectral envelope (Makhoul 1975; Levinson-Durbin), mel-spaced band energies and
spectral shape statistics (Fant 1960; Davis & Mermelstein 1980). It also carries
the IIR helpers the synthesis core and the fixture channel conditions need.

Every function here is a pure function of its arguments.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from voicekin.engine.audio import AudioClip

FRAME_MS = 32.0
HOP_MS = 16.0
F0_MIN_HZ = 60.0
F0_MAX_HZ = 400.0
LPC_ORDER = 12
N_MEL_BANDS = 8
LPC_FFT_SIZE = 1024
PRE_EMPHASIS = 0.97

#: Formant search bands (Hz). F1 and F2 for adult speech across the vowel space.
F1_BAND_HZ = (180.0, 1200.0)
F2_BAND_HZ = (600.0, 3400.0)
MIN_FORMANT_SEPARATION_HZ = 120.0
FORMANT_MIN_HZ = 120.0
FORMANT_MAX_HZ = 4200.0
MAX_FORMANT_BANDWIDTH_HZ = 700.0
"""Poles broader than this are not formants — the standard resonance criterion."""

#: Spectral-tilt regression band (Hz). Both edges are chosen against known
#: contaminants, measured on the dev fixtures. Above ~1.6 kHz a realistic
#: recording's 20-30 dB SNR noise floor dominates a -8 dB/oct voice, so a wider
#: regression measures where the noise sits and slope differences between voices
#: compress toward zero. Below ~450 Hz two other things intrude: the speaker's
#: own harmonics move through the lowest octave with prosody (large take-to-take
#: swings), and a telephone band-pass (300-3400 Hz, the product's own consent
#: intake path) removes the region entirely, biasing every phone take. 450-1600
#: keeps ~1.8 octaves that survive both.
TILT_BAND_HZ = (450.0, 1600.0)
ROLLOFF_FRACTION = 0.85

SPEECH_BAND_HZ = (300.0, 3400.0)
"""Band the spectral-shape features are measured over.

The classical telephone band, and not by accident: VoiceKin's own intake mixes a
phone-recorded consent statement with an ``arecord`` enrollment, so every
dimension measured outside 300-3400 Hz is a dimension where a band-limited take
has *no* signal and the embedding ends up describing the channel instead of the
speaker. Restricting the mel bands, the centroid and the rolloff to the band the
narrowest realistic channel preserves is what makes cross-channel scoring
possible at all (EVALS M1c); the cost is the fricative energy above 3.4 kHz,
which is dominated by the recording's noise floor anyway."""

_EPS = 1e-12


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


def frame_lengths(sample_rate: int) -> tuple[int, int]:
    """``(frame_len, hop_len)`` in samples for the committed 32 ms / 16 ms grid."""
    return round(FRAME_MS * sample_rate / 1000.0), round(HOP_MS * sample_rate / 1000.0)


def frame_signal(x: np.ndarray, frame_len: int, hop: int) -> np.ndarray:
    """Split ``x`` into overlapping frames, shape ``(n_frames, frame_len)``."""
    if x.shape[0] < frame_len:
        return np.zeros((0, frame_len), dtype=np.float64)
    windows = np.lib.stride_tricks.sliding_window_view(x, frame_len)
    return np.ascontiguousarray(windows[::hop])


# --------------------------------------------------------------------------- #
# Frame-level analysis: energy, pitch, voicing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, eq=False)
class FrameAnalysis:
    """Per-frame measurements shared by quality screening and the embedder."""

    sample_rate: int
    frames: np.ndarray
    energy: np.ndarray
    nac_peak: np.ndarray
    f0_hz: np.ndarray
    voiced: np.ndarray
    noise_floor: float
    speech_power: float

    @property
    def n_frames(self) -> int:
        return int(self.frames.shape[0])

    @property
    def voiced_ratio(self) -> float:
        if self.n_frames == 0:
            return 0.0
        return float(np.count_nonzero(self.voiced) / self.n_frames)

    @property
    def snr_db(self) -> float:
        """FR-2 SNR proxy: ``10·log10(P_speech / P_floor)``."""
        return float(10.0 * np.log10(max(self.speech_power, _EPS) / max(self.noise_floor, _EPS)))


def normalized_autocorrelation(frames: np.ndarray) -> np.ndarray:
    """Per-frame normalized autocorrelation, energy-corrected per lag.

    ``nac[τ] = Σₙ x[n]x[n+τ] / sqrt(Σ x[n]² · Σ x[n+τ]²)`` over the overlapping
    span only. Dividing by ``r[0]`` instead would taper the score toward zero as
    the lag grows and make low-pitched voices read as unvoiced — the correlation
    coefficient of the two overlapping halves is the quantity that actually
    means "this frame repeats itself at this lag".
    """
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros((0, frame_len), dtype=np.float64)
    centred = frames - frames.mean(axis=1, keepdims=True)
    n_fft = 1 << int(np.ceil(np.log2(2 * frame_len)))
    spectrum = np.fft.rfft(centred, n=n_fft, axis=1)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum), n=n_fft, axis=1)[:, :frame_len]

    squared = centred**2
    prefix = np.concatenate(
        [np.zeros((n_frames, 1)), np.cumsum(squared, axis=1)], axis=1
    )  # prefix[:, k] = sum of x[n]^2 for n < k
    total = prefix[:, -1:]
    lags = np.arange(frame_len)
    head = prefix[:, frame_len - lags]  # Σ_{n < N-τ} x[n]²
    tail = total - prefix[:, lags]  # Σ_{n ≥ τ} x[n]²
    return acf / np.sqrt(np.maximum(head * tail, _EPS))


def _parabolic_peak(y_left: np.ndarray, y_mid: np.ndarray, y_right: np.ndarray) -> np.ndarray:
    """Sub-sample offset of a parabola through three samples, clamped to ±0.5."""
    denom = y_left - 2.0 * y_mid + y_right
    safe = np.where(np.abs(denom) < _EPS, _EPS, denom)
    offset = 0.5 * (y_left - y_right) / safe
    return np.clip(offset, -0.5, 0.5)


#: A candidate lag this close to the best one wins if it is shorter. Octave-down
#: errors (locking onto twice the period) are the dominant failure of peak picking.
OCTAVE_PREFERENCE = 0.85


def estimate_f0(frames: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame ``(f0_hz, nac_peak)`` by normalized-autocorrelation peak picking.

    Among local maxima of the normalized autocorrelation, the *shortest* lag
    scoring within :data:`OCTAVE_PREFERENCE` of the best one is chosen, then
    parabolically refined. Taking the global maximum instead halves the reported
    pitch whenever the two-period peak edges out the one-period peak.
    """
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros(0), np.zeros(0)
    nac = normalized_autocorrelation(frames)
    min_lag = max(2, int(np.floor(sample_rate / F0_MAX_HZ)))
    max_lag = min(frame_len - 2, int(np.ceil(sample_rate / F0_MIN_HZ)))
    if max_lag <= min_lag:
        return np.zeros(n_frames), np.zeros(n_frames)

    band = nac[:, min_lag : max_lag + 1]
    best = np.max(band, axis=1)
    is_local_max = np.ones_like(band, dtype=bool)
    is_local_max[:, 1:] &= band[:, 1:] > band[:, :-1]
    is_local_max[:, :-1] &= band[:, :-1] >= band[:, 1:]
    acceptable = is_local_max & (band >= OCTAVE_PREFERENCE * best[:, None])
    has_candidate = acceptable.any(axis=1)
    rel = np.where(has_candidate, np.argmax(acceptable, axis=1), np.argmax(band, axis=1))

    rows = np.arange(n_frames)
    lag = rel + min_lag
    peak = band[rows, rel]
    offset = _parabolic_peak(nac[rows, lag - 1], nac[rows, lag], nac[rows, lag + 1])
    refined = np.maximum(lag + offset, 1.0)
    return sample_rate / refined, peak


def analyze_frames(
    clip: AudioClip,
    *,
    nac_threshold: float = 0.5,
    energy_margin_db: float = 6.0,
) -> FrameAnalysis:
    """Frame the clip and measure energy, pitch and voicing (FR-2/FR-4).

    A frame is *voiced* when its normalized autocorrelation peak clears
    ``nac_threshold`` **and** its energy clears the noise floor by
    ``energy_margin_db``. The floor is the mean energy of the quietest decile of
    frames, which is also ``P_floor`` in the FR-2 SNR proxy.
    """
    frame_len, hop = frame_lengths(clip.sample_rate)
    frames = frame_signal(np.asarray(clip.samples, dtype=np.float64), frame_len, hop)
    if frames.shape[0] == 0:
        return FrameAnalysis(
            sample_rate=clip.sample_rate,
            frames=frames,
            energy=np.zeros(0),
            nac_peak=np.zeros(0),
            f0_hz=np.zeros(0),
            voiced=np.zeros(0, dtype=bool),
            noise_floor=0.0,
            speech_power=0.0,
        )
    energy = np.mean(frames**2, axis=1)
    f0_hz, nac_peak = estimate_f0(frames, clip.sample_rate)

    order = np.sort(energy)
    decile = max(1, int(np.ceil(0.1 * order.shape[0])))
    noise_floor = float(np.mean(order[:decile]))
    margin = 10.0 ** (energy_margin_db / 10.0)
    voiced = (nac_peak >= nac_threshold) & (energy >= noise_floor * margin)
    speech_power = float(np.mean(energy[voiced])) if bool(voiced.any()) else float(np.mean(energy))
    return FrameAnalysis(
        sample_rate=clip.sample_rate,
        frames=frames,
        energy=energy,
        nac_peak=nac_peak,
        f0_hz=f0_hz,
        voiced=voiced,
        noise_floor=noise_floor,
        speech_power=speech_power,
    )


# --------------------------------------------------------------------------- #
# Linear prediction
# --------------------------------------------------------------------------- #


def levinson_durbin(autocorr: np.ndarray, order: int) -> np.ndarray:
    """Solve the Yule-Walker equations for every frame at once.

    ``autocorr`` is ``(n_frames, order + 1)``; the result is the prediction-error
    filter ``A(z) = 1 + a₁z⁻¹ + … + a_pz⁻ᵖ`` as ``(n_frames, order + 1)``.
    """
    n_frames = autocorr.shape[0]
    coeffs = np.zeros((n_frames, order + 1), dtype=np.float64)
    coeffs[:, 0] = 1.0
    if n_frames == 0:
        return coeffs
    # Ridge the zero lag slightly: guarantees a positive-definite system for
    # near-silent frames without perceptibly moving the envelope.
    error = np.maximum(autocorr[:, 0] * 1.0001, _EPS)
    for i in range(1, order + 1):
        acc = autocorr[:, i].copy()
        if i > 1:
            acc += np.sum(coeffs[:, 1:i] * autocorr[:, i - 1 : 0 : -1], axis=1)
        reflection = -acc / error
        reflection = np.clip(reflection, -0.999999, 0.999999)
        previous = coeffs[:, 1:i].copy()
        if i > 1:
            coeffs[:, 1:i] = previous + reflection[:, None] * previous[:, ::-1]
        coeffs[:, i] = reflection
        error = np.maximum(error * (1.0 - reflection**2), _EPS)
    return coeffs


def lpc_envelope(coeffs: np.ndarray, n_fft: int = LPC_FFT_SIZE) -> np.ndarray:
    """All-pole spectral envelope magnitude ``1/|A(e^{jω})|`` per frame."""
    response = np.fft.rfft(coeffs, n=n_fft, axis=1)
    return 1.0 / np.maximum(np.abs(response), _EPS)


def lpc_roots(coeffs: np.ndarray) -> np.ndarray:
    """Roots of every frame's prediction-error polynomial, in one batched solve.

    ``A(z) = 1 + a₁z⁻¹ + … + a_pz⁻ᵖ`` has the same roots as the monic polynomial
    ``zᵖ + a₁zᵖ⁻¹ + … + a_p``, whose companion matrix eigenvalues LAPACK computes
    for all frames at once.
    """
    n_frames, width = coeffs.shape
    order = width - 1
    companion = np.zeros((n_frames, order, order), dtype=np.float64)
    companion[:, 0, :] = -coeffs[:, 1:]
    diagonal = np.arange(order - 1)
    companion[:, diagonal + 1, diagonal] = 1.0
    return np.linalg.eigvals(companion)


def formants_from_roots(roots: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """The two lowest resonances per frame, from LPC pole angles.

    Each conjugate pole pair *is* a spectral peak of the all-pole envelope: its
    angle gives the centre frequency and its radius the bandwidth. Reading the
    poles rather than sampling the envelope keeps F1 and F2 apart for back vowels
    (``/o/``, ``/u/``), where they sit close enough to merge into one visible
    peak and a peak-picker reports F3 as F2 — a bimodal estimator whose median
    swings with the vowel content of the recording.

    Selection is **per band**, not by pole rank. Ranking is what breaks in
    practice: order 12 routinely spends a pole just above the fundamental of a
    low-pitched voice or on a noise resonance, and then "the lowest two usable
    poles" are F0's shadow and F1 — both fall outside their bands, the frame
    reports two NaNs, and it drops out of the median. Which frames drop out
    depends on the vowel, so the surviving distribution is vowel-biased and the
    F1/F2 medians swing with content instead of with the speaker's vocal tract.
    Taking the lowest pole *within* each formant's band, with F2 required to sit
    at least :data:`MIN_FORMANT_SEPARATION_HZ` above F1, keeps the estimate on
    the tract.
    """
    n_frames = roots.shape[0]
    if n_frames == 0:
        return np.zeros(0), np.zeros(0)
    freq = np.angle(roots) * sample_rate / (2.0 * np.pi)
    bandwidth = -np.log(np.maximum(np.abs(roots), _EPS)) * sample_rate / np.pi
    usable = (
        (freq >= FORMANT_MIN_HZ)
        & (freq <= FORMANT_MAX_HZ)
        & (bandwidth <= MAX_FORMANT_BANDWIDTH_HZ)
    )
    in_f1 = usable & (freq >= F1_BAND_HZ[0]) & (freq <= F1_BAND_HZ[1])
    first = np.min(np.where(in_f1, freq, np.inf), axis=1)
    floor = np.where(np.isfinite(first), first, -np.inf) + MIN_FORMANT_SEPARATION_HZ
    in_f2 = (
        usable
        & (freq >= F2_BAND_HZ[0])
        & (freq <= F2_BAND_HZ[1])
        & (freq >= floor[:, None])
    )
    second = np.min(np.where(in_f2, freq, np.inf), axis=1)
    f1 = np.where(np.isfinite(first), first, np.nan)
    f2 = np.where(np.isfinite(second), second, np.nan)
    return f1, f2


def pre_emphasize(frames: np.ndarray, coefficient: float = PRE_EMPHASIS) -> np.ndarray:
    """First-order high-pass ``x[n] - a·x[n-1]`` applied per frame.

    Standard practice before LPC analysis (Makhoul 1975): it flattens the glottal
    source tilt so the all-pole fit spends its poles on formants. Without it, a
    steeply tilted voice's F1 is a shoulder on a falling slope rather than a
    local maximum, and peak picking simply loses it.
    """
    emphasized = np.empty_like(frames)
    emphasized[:, 0] = frames[:, 0]
    emphasized[:, 1:] = frames[:, 1:] - coefficient * frames[:, :-1]
    return emphasized


FORMANT_ANALYSIS_RATE = 8_000
"""LPC analysis rate for formant estimation.

Order 12 at 16 kHz must spend its six pole pairs across the full 0-8 kHz band —
on four formants, the source tilt *and* whatever the noise floor does above
4 kHz — and in practice F2 wanders by ~80 Hz between takes of the same voice.
Halving the rate puts all six pairs on the 0-4 kHz band that actually contains
F1-F4, which is the classical practice (formant analysis at 8-10 kHz) and cuts
the within-speaker spread of the F1/F2 medians roughly fourfold. The order-12
Levinson-Durbin recursion of FR-4 is unchanged."""

_FORMANT_DECIMATION_TAPS = 31
_FORMANT_ANTIALIAS_HZ = 3_600.0


def estimate_formants(
    frames: np.ndarray, sample_rate: int, order: int = LPC_ORDER
) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame ``(F1, F2)`` in Hz from the LPC spectral envelope's first peaks.

    Frames are anti-alias filtered and decimated to
    :data:`FORMANT_ANALYSIS_RATE` before the LPC fit (see its docstring).
    Frames whose envelope has no qualifying peak in a band report ``NaN`` there.
    """
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros(0), np.zeros(0)
    analysis_rate = sample_rate
    if sample_rate >= 2 * FORMANT_ANALYSIS_RATE:
        factor = sample_rate // FORMANT_ANALYSIS_RATE
        from voicekin.engine.audio import sinc_lowpass  # local: avoids an import cycle

        kernel = sinc_lowpass(_FORMANT_DECIMATION_TAPS, _FORMANT_ANTIALIAS_HZ, sample_rate)
        n_fft = 1 << int(np.ceil(np.log2(frame_len + kernel.shape[0])))
        spectra = np.fft.rfft(frames, n=n_fft, axis=1) * np.fft.rfft(kernel, n=n_fft)
        filtered = np.fft.irfft(spectra, n=n_fft, axis=1)
        # Compensate the kernel's group delay so the frame stays centred.
        delay = (kernel.shape[0] - 1) // 2
        frames = filtered[:, delay : delay + frame_len : factor]
        analysis_rate = sample_rate // factor
    window = np.hamming(frames.shape[1])
    centred = frames - frames.mean(axis=1, keepdims=True)
    windowed = pre_emphasize(centred) * window
    n_fft = 1 << int(np.ceil(np.log2(2 * frames.shape[1])))
    spectrum = np.fft.rfft(windowed, n=n_fft, axis=1)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum), n=n_fft, axis=1)[:, : order + 1]
    coeffs = levinson_durbin(acf, order)
    return formants_from_roots(lpc_roots(coeffs), analysis_rate)


# --------------------------------------------------------------------------- #
# Spectral shape
# --------------------------------------------------------------------------- #


def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def mel_filterbank(
    n_bands: int, n_fft: int, sample_rate: int, band: tuple[float, float] | None = None
) -> np.ndarray:
    """Triangular mel filterbank over ``band``, shape ``(n_bands, n_fft // 2 + 1)``."""
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    lo, hi = (band or SPEECH_BAND_HZ)[0], min((band or SPEECH_BAND_HZ)[1], sample_rate / 2.0)
    edges = mel_to_hz(np.linspace(hz_to_mel(lo), hz_to_mel(hi), n_bands + 2))
    bank = np.zeros((n_bands, freqs.shape[0]), dtype=np.float64)
    for b in range(n_bands):
        lo, mid, hi = edges[b], edges[b + 1], edges[b + 2]
        rising = (freqs - lo) / max(mid - lo, _EPS)
        falling = (hi - freqs) / max(hi - mid, _EPS)
        bank[b] = np.clip(np.minimum(rising, falling), 0.0, None)
    return bank


def power_spectrum(frames: np.ndarray) -> tuple[np.ndarray, int]:
    """Hamming-windowed power spectrum per frame plus the FFT size used."""
    frame_len = frames.shape[1]
    window = np.hamming(frame_len)
    spectrum = np.fft.rfft(frames * window, n=frame_len, axis=1)
    return np.abs(spectrum) ** 2, frame_len


def band_ratios(power: np.ndarray, sample_rate: int, n_bands: int = N_MEL_BANDS) -> np.ndarray:
    """Mel-band energy ratios (each band's share of total band energy)."""
    n_fft = 2 * (power.shape[1] - 1)
    bank = mel_filterbank(n_bands, n_fft, sample_rate)
    energies = power @ bank.T
    total = np.maximum(energies.sum(axis=1, keepdims=True), _EPS)
    return energies / total


TILT_SMOOTHING_BINS = 9
TILT_DYNAMIC_RANGE_DB = 60.0

SHAPE_NAC_THRESHOLD = 0.7
"""Spectral-shape features are measured on frames at least this periodic.

Voiced frames that straddle a consonant onset carry the burst's high-frequency
energy; how many of them a take has depends on its consonant draw, not on the
speaker. Requiring a strong autocorrelation peak keeps the shape statistics on
the vowel nuclei."""

FORMANT_TRIM_FRACTION = 0.1
"""F1/F2 location statistic: mean of the per-frame estimates after trimming this
fraction at each end. The per-frame distribution is multi-modal (one mode per
vowel), so the raw median sits on a flat valley between modes and jumps between
takes; the trimmed mean averages across the balanced vowel content while still
discarding outright estimation errors."""


def smooth_spectrum(power: np.ndarray, width: int = TILT_SMOOTHING_BINS) -> np.ndarray:
    """Moving-average the power spectrum across frequency.

    A voiced periodogram is a comb: harmonics separated by deep nulls. Regressing
    dB against frequency without smoothing measures the *nulls* — their depth is
    set by window leakage and the recording's noise floor, not by the speaker —
    and the resulting "tilt" stops responding to the source slope entirely.
    """
    if width <= 1 or power.shape[1] <= width:
        return power
    kernel = np.ones(width) / width
    padded = np.pad(power, ((0, 0), (width // 2, width // 2)), mode="edge")
    return np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded)


def spectral_tilt(power: np.ndarray, sample_rate: int) -> np.ndarray:
    """Per-frame spectral tilt in dB/octave by least squares over log2 frequency.

    Measured on the smoothed envelope with a floor ``TILT_DYNAMIC_RANGE_DB``
    below each frame's peak, so neither harmonic nulls nor an arbitrarily quiet
    noise floor can dominate the fit.
    """
    n_fft = 2 * (power.shape[1] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mask = (freqs >= TILT_BAND_HZ[0]) & (freqs <= TILT_BAND_HZ[1])
    if not mask.any() or power.shape[0] == 0:  # pragma: no cover - impossible at 16 kHz
        return np.zeros(power.shape[0])
    envelope = smooth_spectrum(power)[:, mask]
    floor = np.max(envelope, axis=1, keepdims=True) * 10.0 ** (-TILT_DYNAMIC_RANGE_DB / 10.0)
    db = 10.0 * np.log10(np.maximum(envelope, np.maximum(floor, _EPS)))
    octaves = np.log2(freqs[mask])
    # Weight each bin by 1/f, i.e. equally per *octave*. An unweighted fit over a
    # linear frequency grid spends ~90 % of its leverage on the top octave, so the
    # "slope per octave" it returns is really the shape of the 2-3 kHz region and
    # barely moves when the speaker's source slope does.
    weights = 1.0 / np.maximum(freqs[mask], _EPS)
    weights = weights / weights.sum()
    centre = float(weights @ octaves)
    x = octaves - centre
    denom = float(weights @ (x**2))
    residual = db - (db @ weights)[:, None]
    return (residual * weights) @ x / max(denom, _EPS)


def noise_psd(all_frame_power: np.ndarray, energy: np.ndarray) -> np.ndarray:
    """Noise power spectrum estimated from the quietest decile of frames.

    Every unit the synthesis or a recording produces ends in silence, so the
    quiet frames of any usable clip sample the additive noise floor. The median
    across them is robust to the odd quiet-but-voiced frame.
    """
    if all_frame_power.shape[0] == 0:
        return np.zeros(all_frame_power.shape[1])
    order = np.argsort(energy)
    quietest = max(1, int(np.ceil(0.1 * all_frame_power.shape[0])))
    return np.median(all_frame_power[order[:quietest]], axis=0)


def speech_spectrum(shape_power: np.ndarray, noise: np.ndarray) -> np.ndarray:
    """Noise-subtracted mean spectrum of the shape-feature frames.

    Without the subtraction, every spectral-shape feature above ~1.5 kHz (where
    speech energy has fallen to the recording's noise floor) measures the take's
    SNR draw instead of the voice.
    """
    if shape_power.shape[0] == 0:
        return np.zeros(shape_power.shape[1])
    return np.maximum(shape_power.mean(axis=0) - noise, 0.0)


def _speech_band_mask(power: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """``(freqs, mask)`` restricting a spectrum to :data:`SPEECH_BAND_HZ`."""
    n_fft = 2 * (power.shape[1] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mask = (freqs >= SPEECH_BAND_HZ[0]) & (freqs <= SPEECH_BAND_HZ[1])
    if not mask.any():  # pragma: no cover - impossible at 16 kHz
        mask = np.ones_like(freqs, dtype=bool)
    return freqs, mask


def spectral_centroid(power: np.ndarray, sample_rate: int) -> np.ndarray:
    """Energy-weighted mean frequency inside the speech band."""
    freqs, mask = _speech_band_mask(power, sample_rate)
    banded = power[:, mask]
    total = np.maximum(banded.sum(axis=1), _EPS)
    return (banded @ freqs[mask]) / total


def spectral_rolloff(
    power: np.ndarray, sample_rate: int, fraction: float = ROLLOFF_FRACTION
) -> np.ndarray:
    """Frequency below which ``fraction`` of the speech band's energy lies."""
    freqs, mask = _speech_band_mask(power, sample_rate)
    banded = power[:, mask]
    cumulative = np.cumsum(banded, axis=1)
    total = np.maximum(cumulative[:, -1:], _EPS)
    index = np.argmax(cumulative >= fraction * total, axis=1)
    return freqs[mask][index]


# --------------------------------------------------------------------------- #
# The 16 raw features (FR-4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VoiceFeatures:
    """Raw, un-normalized source-filter statistics for one clip.

    ``to_vector`` lays them out in the FR-4 order:
    ``[log F0 median, log F0 IQR, voiced ratio, F1 median, F2 median, tilt,
    centroid, rolloff] ⊕ 8 band ratios``.

    Location statistics (build-stage deviation, recorded in REVIEW.md): the
    F1/F2 fields hold a 10 %-trimmed mean of the per-frame estimates rather than
    the raw median (the per-frame distribution is one mode per vowel, and a
    median sitting on the flat valley between modes swings with content); the
    band "ratios" are the square roots of the mel-band energy shares (the
    variance-stabilizing transform for proportions, without which the tiny
    high-band shares measure the recording's SNR draw, not the voice).
    """

    log_f0_median: float
    log_f0_iqr: float
    voiced_ratio: float
    f1_median: float
    f2_median: float
    tilt_db_oct: float
    centroid_hz: float
    rolloff_hz: float
    band_ratios: tuple[float, ...]

    def to_vector(self) -> np.ndarray:
        return np.array(
            [
                self.log_f0_median,
                self.log_f0_iqr,
                self.voiced_ratio,
                self.f1_median,
                self.f2_median,
                self.tilt_db_oct,
                self.centroid_hz,
                self.rolloff_hz,
                *self.band_ratios,
            ],
            dtype=np.float64,
        )


def trimmed_location(values: np.ndarray, fallback: float, trim: float = FORMANT_TRIM_FRACTION) -> float:
    """Trimmed mean over the finite entries; ``fallback`` when there are none."""
    finite = values[np.isfinite(values)] if values.size else values
    if finite.size == 0:
        return fallback
    lo, hi = np.percentile(finite, [100.0 * trim, 100.0 * (1.0 - trim)])
    core = finite[(finite >= lo) & (finite <= hi)]
    return float(core.mean()) if core.size else float(finite.mean())


def _mean_envelope_tilt(mean_power: np.ndarray, sample_rate: int) -> float:
    """Clip-level tilt: 1/f-weighted regression on the smoothed mean spectrum."""
    n_fft = 2 * (mean_power.shape[0] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mask = (freqs >= TILT_BAND_HZ[0]) & (freqs <= TILT_BAND_HZ[1])
    if not mask.any():  # pragma: no cover - impossible at 16 kHz
        return 0.0
    envelope = smooth_spectrum(mean_power[None, :])[0][mask]
    peak = float(np.max(envelope))
    if peak <= 0.0:
        return 0.0
    floor = peak * 10.0 ** (-TILT_DYNAMIC_RANGE_DB / 10.0)
    db = 10.0 * np.log10(np.maximum(envelope, max(floor, _EPS)))
    octaves = np.log2(freqs[mask])
    weights = 1.0 / np.maximum(freqs[mask], _EPS)
    weights = weights / weights.sum()
    centre = float(weights @ octaves)
    x = octaves - centre
    denom = float(weights @ (x**2))
    return float((weights * (db - float(weights @ db))) @ x / max(denom, _EPS))


def _interp_rolloff(banded: np.ndarray, freqs: np.ndarray, fraction: float) -> float:
    """Sub-bin interpolated rolloff of a single spectrum (quantization-free)."""
    cumulative = np.cumsum(banded)
    total = float(cumulative[-1]) if cumulative.size else 0.0
    if total <= 0.0:
        return 0.0
    target = fraction * total
    index = int(np.argmax(cumulative >= target))
    if index == 0:
        return float(freqs[0])
    span = float(cumulative[index] - cumulative[index - 1])
    step = (target - float(cumulative[index - 1])) / max(span, _EPS)
    return float(freqs[index - 1] + step * (freqs[index] - freqs[index - 1]))


def analyze_voice(clip: AudioClip, frames: FrameAnalysis | None = None) -> VoiceFeatures:
    """Measure the 16 raw source-filter features of a clip (FR-4).

    Pitch and formants are measured over *voiced* frames; the spectral-shape
    block (tilt, centroid, rolloff, band shares) is measured on the
    noise-subtracted mean spectrum of the strongly periodic frames
    (:data:`SHAPE_NAC_THRESHOLD`), so it describes the vowel nuclei rather than
    the take's consonant draw or its noise floor. An unvoiced clip degrades to
    neutral constants rather than to noise.
    """
    analysis = frames if frames is not None else analyze_frames(clip)
    if analysis.n_frames == 0:
        return VoiceFeatures(
            log_f0_median=np.log(120.0),
            log_f0_iqr=0.0,
            voiced_ratio=0.0,
            f1_median=float(np.mean(F1_BAND_HZ)),
            f2_median=float(np.mean(F2_BAND_HZ)),
            tilt_db_oct=0.0,
            centroid_hz=0.0,
            rolloff_hz=0.0,
            band_ratios=tuple([np.sqrt(1.0 / N_MEL_BANDS)] * N_MEL_BANDS),
        )

    voiced = analysis.voiced
    selection = voiced if bool(voiced.any()) else np.ones(analysis.n_frames, dtype=bool)
    voiced_frames = analysis.frames[selection]

    f0 = analysis.f0_hz[selection]
    f0 = f0[(f0 >= F0_MIN_HZ) & (f0 <= F0_MAX_HZ)]
    log_f0 = np.log(f0) if f0.size else np.array([np.log(120.0)])
    q75, q25 = np.percentile(log_f0, [75.0, 25.0]) if log_f0.size > 1 else (0.0, 0.0)

    f1, f2 = estimate_formants(voiced_frames, analysis.sample_rate)

    shape_selection = selection & (analysis.nac_peak >= SHAPE_NAC_THRESHOLD)
    if not bool(shape_selection.any()):
        shape_selection = selection
    all_power, _ = power_spectrum(analysis.frames)
    noise = noise_psd(all_power, analysis.energy)
    mean_power = speech_spectrum(all_power[shape_selection], noise)

    n_fft = 2 * (mean_power.shape[0] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / analysis.sample_rate)
    bank = mel_filterbank(N_MEL_BANDS, n_fft, analysis.sample_rate)
    energies = bank @ mean_power
    shares = np.sqrt(energies / max(float(energies.sum()), _EPS))

    speech_mask = (freqs >= SPEECH_BAND_HZ[0]) & (
        freqs <= min(SPEECH_BAND_HZ[1], analysis.sample_rate / 2.0)
    )
    banded = mean_power[speech_mask]
    band_freqs = freqs[speech_mask]
    total = float(banded.sum())
    centroid = float(banded @ band_freqs / total) if total > 0.0 else 0.0

    return VoiceFeatures(
        log_f0_median=float(np.median(log_f0)),
        log_f0_iqr=float(q75 - q25),
        voiced_ratio=analysis.voiced_ratio,
        f1_median=trimmed_location(f1, float(np.mean(F1_BAND_HZ))),
        f2_median=trimmed_location(f2, float(np.mean(F2_BAND_HZ))),
        tilt_db_oct=_mean_envelope_tilt(mean_power, analysis.sample_rate),
        centroid_hz=centroid,
        rolloff_hz=_interp_rolloff(banded, band_freqs, ROLLOFF_FRACTION),
        band_ratios=tuple(float(v) for v in shares),
    )


# --------------------------------------------------------------------------- #
# IIR helpers (synthesis resonators, fixture channel conditions)
# --------------------------------------------------------------------------- #


def resonator_coefficients(
    freq_hz: float, bandwidth_hz: float, sample_rate: int
) -> tuple[float, float, float]:
    """Klatt second-order resonator ``y[n] = A·x[n] + B·y[n-1] + C·y[n-2]``.

    ``A`` is chosen for unit gain at DC (Klatt 1980).
    """
    radius = float(np.exp(-np.pi * bandwidth_hz / sample_rate))
    theta = 2.0 * np.pi * freq_hz / sample_rate
    b = 2.0 * radius * np.cos(theta)
    c = -(radius**2)
    return 1.0 - b - c, b, c


def resonator_impulse_response(
    freq_hz: float, bandwidth_hz: float, sample_rate: int, n_taps: int
) -> np.ndarray:
    """Closed-form impulse response of :func:`resonator_coefficients`.

    ``h[n] = A·r^n·sin((n+1)θ)/sin θ``. Using the analytic form lets the whole
    formant cascade run as vectorized FFT convolution instead of a Python loop,
    with no change to the filter it realizes (only truncation at ``n_taps``).
    """
    a, _, _ = resonator_coefficients(freq_hz, bandwidth_hz, sample_rate)
    radius = float(np.exp(-np.pi * bandwidth_hz / sample_rate))
    theta = 2.0 * np.pi * freq_hz / sample_rate
    n = np.arange(n_taps, dtype=np.float64)
    sin_theta = np.sin(theta)
    if abs(sin_theta) < 1e-9:  # pragma: no cover - degenerate 0 Hz/Nyquist resonator
        return a * (radius**n) * (n + 1.0)
    return a * (radius**n) * np.sin((n + 1.0) * theta) / sin_theta


def convolve_fft(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Linear convolution via FFT, truncated to ``len(x)`` (causal filtering)."""
    if x.size == 0:
        return x
    n = x.size + kernel.size - 1
    n_fft = 1 << (n - 1).bit_length()
    spectrum = np.fft.rfft(x, n_fft) * np.fft.rfft(kernel, n_fft)
    return np.fft.irfft(spectrum, n_fft)[: x.size]


def _bilinear_section(poles: np.ndarray, sample_rate: int, highpass: bool) -> np.ndarray:
    """Bilinear-transform one analog conjugate pole pair into a biquad."""
    k = 2.0 * sample_rate
    p1, p2 = poles[0], poles[1]
    # Denominator of (1 - p1/s')(1 - p2/s') under s' = k(1-z^-1)/(1+z^-1).
    d0 = (k - p1) * (k - p2)
    a0 = 1.0
    a1 = (2.0 * (-(k**2) + p1 * p2) / d0).real
    a2 = ((k + p1) * (k + p2) / d0).real
    if highpass:
        b = np.array([1.0, -2.0, 1.0]) * (k**2 / d0).real
        gain = float(np.sum(b * np.array([1.0, -1.0, 1.0])) / (a0 - a1 + a2))
    else:
        b = np.array([1.0, 2.0, 1.0]) * ((p1 * p2) / d0).real
        gain = float(np.sum(b) / (a0 + a1 + a2))
    b = b / gain if abs(gain) > _EPS else b
    return np.array([b[0], b[1], b[2], a0, a1, a2], dtype=np.float64)


def _butter_poles(order: int) -> np.ndarray:
    """Left-half-plane poles of the analog Butterworth low-pass prototype."""
    k = np.arange(order, dtype=np.float64)
    return np.exp(1j * np.pi * (2.0 * k + order + 1.0) / (2.0 * order))


def butter_sos(cutoff_hz: float, sample_rate: int, order: int, highpass: bool) -> np.ndarray:
    """Butterworth low/high-pass as second-order sections, via bilinear transform."""
    if order % 2 != 0:
        raise ValueError("butter_sos supports even orders (conjugate pole pairs)")
    if not 0.0 < cutoff_hz < sample_rate / 2.0:
        raise ValueError("cutoff must lie strictly inside the Nyquist band")
    warped = 2.0 * sample_rate * np.tan(np.pi * cutoff_hz / sample_rate)
    prototype = _butter_poles(order)
    poles = warped / prototype if highpass else warped * prototype
    sections = [
        _bilinear_section(np.array([poles[i], poles[order - 1 - i]]), sample_rate, highpass)
        for i in range(order // 2)
    ]
    return np.array(sections, dtype=np.float64)


def butter_bandpass_sos(
    low_hz: float, high_hz: float, sample_rate: int, order: int = 4
) -> np.ndarray:
    """Band-pass realized as a Butterworth high-pass cascaded with a low-pass."""
    if low_hz >= high_hz:
        raise ValueError("band-pass requires low_hz < high_hz")
    return np.vstack(
        [
            butter_sos(low_hz, sample_rate, order, highpass=True),
            butter_sos(high_hz, sample_rate, order, highpass=False),
        ]
    )


def sos_impulse_response(sos: np.ndarray, n_taps: int) -> np.ndarray:
    """Impulse response of a second-order-section cascade (direct form I)."""
    signal = np.zeros(n_taps, dtype=np.float64)
    signal[0] = 1.0
    for b0, b1, b2, a0, a1, a2 in sos:
        out = np.zeros_like(signal)
        x1 = x2 = y1 = y2 = 0.0
        for i, x0 in enumerate(signal):
            y0 = (b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2) / a0
            out[i] = y0
            x2, x1 = x1, x0
            y2, y1 = y1, y0
        signal = out
    return signal


def apply_sos(x: np.ndarray, sos: np.ndarray, n_taps: int = 2048) -> np.ndarray:
    """Filter ``x`` through an SOS cascade using its truncated impulse response.

    Truncation at ``n_taps`` is exact to well below PCM16 resolution for the
    stable filters used here, and keeps filtering vectorized.
    """
    return convolve_fft(x, sos_impulse_response(sos, n_taps))


__all__ = [
    "F0_MAX_HZ",
    "F0_MIN_HZ",
    "FORMANT_ANALYSIS_RATE",
    "FRAME_MS",
    "HOP_MS",
    "LPC_ORDER",
    "N_MEL_BANDS",
    "SHAPE_NAC_THRESHOLD",
    "FrameAnalysis",
    "VoiceFeatures",
    "analyze_frames",
    "analyze_voice",
    "apply_sos",
    "band_ratios",
    "butter_bandpass_sos",
    "butter_sos",
    "convolve_fft",
    "estimate_f0",
    "estimate_formants",
    "frame_lengths",
    "frame_signal",
    "levinson_durbin",
    "lpc_envelope",
    "mel_filterbank",
    "noise_psd",
    "normalized_autocorrelation",
    "power_spectrum",
    "resonator_coefficients",
    "resonator_impulse_response",
    "sos_impulse_response",
    "spectral_centroid",
    "spectral_rolloff",
    "spectral_tilt",
    "speech_spectrum",
    "trimmed_location",
]

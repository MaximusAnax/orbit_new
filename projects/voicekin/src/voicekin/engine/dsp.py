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

#: Formant search bands (Hz). F1 and F2 for adult speech across the vowel space.
F1_BAND_HZ = (180.0, 1200.0)
F2_BAND_HZ = (700.0, 3400.0)
MIN_FORMANT_SEPARATION_HZ = 180.0

#: Spectral-tilt regression band (Hz).
TILT_BAND_HZ = (150.0, 7000.0)
ROLLOFF_FRACTION = 0.85

_EPS = 1e-12


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


def frame_lengths(sample_rate: int) -> tuple[int, int]:
    """``(frame_len, hop_len)`` in samples for the committed 32 ms / 16 ms grid."""
    return int(round(FRAME_MS * sample_rate / 1000.0)), int(round(HOP_MS * sample_rate / 1000.0))


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
    """Per-frame normalized autocorrelation ``r[τ]/r[0]`` of Hamming-windowed frames."""
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros((0, frame_len), dtype=np.float64)
    window = np.hamming(frame_len)
    centred = (frames - frames.mean(axis=1, keepdims=True)) * window
    n_fft = 1 << int(np.ceil(np.log2(2 * frame_len)))
    spectrum = np.fft.rfft(centred, n=n_fft, axis=1)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum), n=n_fft, axis=1)[:, :frame_len]
    zero_lag = np.maximum(acf[:, :1], _EPS)
    return acf / zero_lag


def _parabolic_peak(y_left: np.ndarray, y_mid: np.ndarray, y_right: np.ndarray) -> np.ndarray:
    """Sub-sample offset of a parabola through three samples, clamped to ±0.5."""
    denom = y_left - 2.0 * y_mid + y_right
    safe = np.where(np.abs(denom) < _EPS, _EPS, denom)
    offset = 0.5 * (y_left - y_right) / safe
    return np.clip(offset, -0.5, 0.5)


def estimate_f0(frames: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame ``(f0_hz, nac_peak)`` by normalized-autocorrelation peak picking."""
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros(0), np.zeros(0)
    nac = normalized_autocorrelation(frames)
    min_lag = max(2, int(np.floor(sample_rate / F0_MAX_HZ)))
    max_lag = min(frame_len - 2, int(np.ceil(sample_rate / F0_MIN_HZ)))
    if max_lag <= min_lag:
        return np.zeros(n_frames), np.zeros(n_frames)
    band = nac[:, min_lag : max_lag + 1]
    rel = np.argmax(band, axis=1)
    lag = rel + min_lag
    peak = band[np.arange(n_frames), rel]
    rows = np.arange(n_frames)
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


def _first_peaks(envelope: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """First two spectral-envelope peaks per frame, parabolically refined."""
    n_frames, n_bins = envelope.shape
    log_env = np.log(np.maximum(envelope, _EPS))
    interior = log_env[:, 1:-1]
    is_peak = (interior > log_env[:, :-2]) & (interior >= log_env[:, 2:])
    bins = np.arange(1, n_bins - 1)
    bin_hz = freqs[bins]

    def pick(mask: np.ndarray, lo: float, hi: float) -> np.ndarray:
        allowed = mask & (bin_hz >= lo)[None, :] & (bin_hz <= hi)[None, :]
        found = allowed.any(axis=1)
        index = np.where(found, bins[np.argmax(allowed, axis=1)], -1)
        return index

    f1_bin = pick(is_peak, *F1_BAND_HZ)
    sep_bins = MIN_FORMANT_SEPARATION_HZ / (freqs[1] - freqs[0])
    after_f1 = is_peak & (bins[None, :] > (f1_bin[:, None] + sep_bins))
    f2_bin = pick(after_f1, *F2_BAND_HZ)

    def refine(index: np.ndarray, lo: float, hi: float) -> np.ndarray:
        rows = np.arange(n_frames)
        safe = np.clip(index, 1, n_bins - 2)
        offset = _parabolic_peak(
            log_env[rows, safe - 1], log_env[rows, safe], log_env[rows, safe + 1]
        )
        hz = (safe + offset) * (freqs[1] - freqs[0])
        # Frames with no qualifying peak fall back to the band midpoint, which is
        # the least informative choice available rather than an invented one.
        return np.where(index >= 0, hz, 0.5 * (lo + hi))

    return refine(f1_bin, *F1_BAND_HZ), refine(f2_bin, *F2_BAND_HZ)


def estimate_formants(
    frames: np.ndarray, sample_rate: int, order: int = LPC_ORDER
) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame ``(F1, F2)`` in Hz from the LPC spectral envelope's first peaks."""
    n_frames, frame_len = frames.shape
    if n_frames == 0:
        return np.zeros(0), np.zeros(0)
    window = np.hamming(frame_len)
    windowed = (frames - frames.mean(axis=1, keepdims=True)) * window
    n_fft = 1 << int(np.ceil(np.log2(2 * frame_len)))
    spectrum = np.fft.rfft(windowed, n=n_fft, axis=1)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum), n=n_fft, axis=1)[:, : order + 1]
    coeffs = levinson_durbin(acf, order)
    envelope = lpc_envelope(coeffs)
    freqs = np.fft.rfftfreq(LPC_FFT_SIZE, d=1.0 / sample_rate)
    return _first_peaks(envelope, freqs)


# --------------------------------------------------------------------------- #
# Spectral shape
# --------------------------------------------------------------------------- #


def hz_to_mel(hz: np.ndarray | float) -> np.ndarray | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz, dtype=np.float64) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel, dtype=np.float64) / 2595.0) - 1.0)


def mel_filterbank(n_bands: int, n_fft: int, sample_rate: int) -> np.ndarray:
    """Triangular mel filterbank, shape ``(n_bands, n_fft // 2 + 1)``."""
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    edges = mel_to_hz(np.linspace(hz_to_mel(0.0), hz_to_mel(sample_rate / 2.0), n_bands + 2))
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


def spectral_tilt(power: np.ndarray, sample_rate: int) -> np.ndarray:
    """Per-frame spectral tilt in dB/octave by least squares over log2 frequency."""
    n_fft = 2 * (power.shape[1] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mask = (freqs >= TILT_BAND_HZ[0]) & (freqs <= TILT_BAND_HZ[1])
    if not mask.any():  # pragma: no cover - impossible at 16 kHz
        return np.zeros(power.shape[0])
    octaves = np.log2(freqs[mask])
    db = 10.0 * np.log10(np.maximum(power[:, mask], _EPS))
    x = octaves - octaves.mean()
    denom = float(np.sum(x**2))
    return (db - db.mean(axis=1, keepdims=True)) @ x / max(denom, _EPS)


def spectral_centroid(power: np.ndarray, sample_rate: int) -> np.ndarray:
    n_fft = 2 * (power.shape[1] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    total = np.maximum(power.sum(axis=1), _EPS)
    return (power @ freqs) / total


def spectral_rolloff(
    power: np.ndarray, sample_rate: int, fraction: float = ROLLOFF_FRACTION
) -> np.ndarray:
    n_fft = 2 * (power.shape[1] - 1)
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    cumulative = np.cumsum(power, axis=1)
    total = np.maximum(cumulative[:, -1:], _EPS)
    index = np.argmax(cumulative >= fraction * total, axis=1)
    return freqs[index]


# --------------------------------------------------------------------------- #
# The 16 raw features (FR-4)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VoiceFeatures:
    """Raw, un-normalized source-filter statistics for one clip.

    ``to_vector`` lays them out in the FR-4 order:
    ``[log F0 median, log F0 IQR, voiced ratio, F1 median, F2 median, tilt,
    centroid, rolloff] ⊕ 8 band ratios``.
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


def _median(values: np.ndarray, fallback: float) -> float:
    return float(np.median(values)) if values.size else fallback


def analyze_voice(clip: AudioClip, frames: FrameAnalysis | None = None) -> VoiceFeatures:
    """Measure the 16 raw source-filter features of a clip (FR-4).

    Statistics are taken over *voiced* frames only where voicing is meaningful
    (pitch, formants, tilt); an unvoiced clip degrades to neutral constants
    rather than to noise.
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
            band_ratios=tuple([1.0 / N_MEL_BANDS] * N_MEL_BANDS),
        )

    voiced = analysis.voiced
    selection = voiced if bool(voiced.any()) else np.ones(analysis.n_frames, dtype=bool)
    voiced_frames = analysis.frames[selection]

    f0 = analysis.f0_hz[selection]
    f0 = f0[(f0 >= F0_MIN_HZ) & (f0 <= F0_MAX_HZ)]
    log_f0 = np.log(f0) if f0.size else np.array([np.log(120.0)])
    q75, q25 = np.percentile(log_f0, [75.0, 25.0]) if log_f0.size > 1 else (0.0, 0.0)

    f1, f2 = estimate_formants(voiced_frames, analysis.sample_rate)
    power, _ = power_spectrum(voiced_frames)
    ratios = band_ratios(power, analysis.sample_rate).mean(axis=0)

    return VoiceFeatures(
        log_f0_median=float(np.median(log_f0)),
        log_f0_iqr=float(q75 - q25),
        voiced_ratio=analysis.voiced_ratio,
        f1_median=_median(f1, float(np.mean(F1_BAND_HZ))),
        f2_median=_median(f2, float(np.mean(F2_BAND_HZ))),
        tilt_db_oct=_median(spectral_tilt(power, analysis.sample_rate), 0.0),
        centroid_hz=_median(spectral_centroid(power, analysis.sample_rate), 0.0),
        rolloff_hz=_median(spectral_rolloff(power, analysis.sample_rate), 0.0),
        band_ratios=tuple(float(v) for v in ratios),
    )


# --------------------------------------------------------------------------- #
# IIR helpers (synthesis resonators, fixture channel conditions)
# --------------------------------------------------------------------------- #


def resonator_coefficients(freq_hz: float, bandwidth_hz: float, sample_rate: int) -> tuple[float, float, float]:
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
    "FRAME_MS",
    "HOP_MS",
    "LPC_ORDER",
    "N_MEL_BANDS",
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
    "normalized_autocorrelation",
    "power_spectrum",
    "resonator_coefficients",
    "resonator_impulse_response",
    "sos_impulse_response",
    "spectral_centroid",
    "spectral_rolloff",
    "spectral_tilt",
]

"""FR-4: the speaker-embedding DSP pipeline, family by family.

EVALS M1b's premise is that no feature family may be dead weight. These tests
assert the same property directly on the estimators: pitch tracking must follow
F0, the LPC formants must follow vocal-tract length, and the tilt regression must
follow the source slope — each while the other two axes are held fixed.
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import ALICE, BOB, CARLA, MIMIC, Speaker, render_voice
from voicekin.adapters.embedder_spectral import (
    FEATURE_FAMILIES,
    CalibrationMismatch,
    SpectralStatsEmbedder,
)
from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.engine.dsp import (
    LPC_ORDER,
    analyze_frames,
    analyze_voice,
    apply_sos,
    band_ratios,
    butter_bandpass_sos,
    estimate_f0,
    estimate_formants,
    frame_lengths,
    frame_signal,
    levinson_durbin,
    lpc_envelope,
    lpc_roots,
    mel_filterbank,
    power_spectrum,
    resonator_coefficients,
    resonator_impulse_response,
    spectral_centroid,
    spectral_rolloff,
    spectral_tilt,
)
from voicekin.engine.enrollment import centroid
from voicekin.engine.verification import distance_similarity
from voicekin.models import EMBEDDING_DIM, Calibration, FeatureNorm


def _sine(freq: float, duration_s: float = 1.0, sample_rate: int = TARGET_SAMPLE_RATE) -> AudioClip:
    t = np.arange(int(duration_s * sample_rate)) / sample_rate
    return AudioClip(samples=0.5 * np.sin(2 * np.pi * freq * t), sample_rate=sample_rate)


def _pulse_train(f0: float, duration_s: float = 1.0) -> np.ndarray:
    signal = np.zeros(int(duration_s * TARGET_SAMPLE_RATE))
    signal[:: round(TARGET_SAMPLE_RATE / f0)] = 1.0
    return signal


# --------------------------------------------------------------------------- #
# Framing and pitch
# --------------------------------------------------------------------------- #


def test_fr4_frames_use_the_committed_32ms_16ms_grid():
    frame_len, hop = frame_lengths(TARGET_SAMPLE_RATE)
    assert (frame_len, hop) == (512, 256)
    frames = frame_signal(np.zeros(2000), frame_len, hop)
    assert frames.shape == (6, 512)


def test_fr4_frame_signal_handles_clips_shorter_than_a_frame():
    assert frame_signal(np.zeros(100), 512, 256).shape == (0, 512)


@pytest.mark.parametrize("f0", [80.0, 120.0, 200.0, 330.0])
def test_fr4_f0_tracks_a_pulse_train(f0):
    frame_len, hop = frame_lengths(TARGET_SAMPLE_RATE)
    frames = frame_signal(_pulse_train(f0), frame_len, hop)
    estimates, peaks = estimate_f0(frames, TARGET_SAMPLE_RATE)
    assert np.median(estimates) == pytest.approx(f0, rel=0.02)
    assert np.median(peaks) > 0.9


def test_fr4_f0_does_not_halve_on_a_low_pitched_voice():
    """The octave-down error a plain argmax makes; the estimator must not."""
    clip = render_voice(Speaker(f0_base_hz=92.0, formant_scale=1.1, tilt_db_oct=-9.0), 4.0, seed=11)
    features = analyze_voice(clip)
    assert float(np.exp(features.log_f0_median)) == pytest.approx(92.0, rel=0.08)


def test_fr4_voicing_separates_speech_from_silence():
    clip = render_voice(ALICE, 4.0, seed=12)
    silence = AudioClip(samples=np.zeros(TARGET_SAMPLE_RATE * 2), sample_rate=TARGET_SAMPLE_RATE)
    assert analyze_frames(clip).voiced_ratio > 0.4
    assert analyze_frames(silence).voiced_ratio == 0.0


# --------------------------------------------------------------------------- #
# Linear prediction and formants
# --------------------------------------------------------------------------- #


def test_fr4_levinson_durbin_solves_the_yule_walker_system():
    """Recover the coefficients of a known AR(2) process from its autocorrelation."""
    rng = np.random.default_rng(3)
    a1, a2 = -1.2, 0.5
    x = np.zeros(20000)
    noise = rng.normal(0.0, 1.0, x.shape[0])
    for n in range(2, x.shape[0]):
        x[n] = -a1 * x[n - 1] - a2 * x[n - 2] + noise[n]
    lags = np.array([[float(np.dot(x[: x.shape[0] - k], x[k:])) for k in range(3)]])
    coeffs = levinson_durbin(lags, 2)
    assert coeffs[0, 0] == 1.0
    assert coeffs[0, 1] == pytest.approx(a1, abs=0.05)
    assert coeffs[0, 2] == pytest.approx(a2, abs=0.05)


def test_fr4_lpc_roots_agree_with_the_envelope_peaks():
    """Root angles and envelope maxima are two views of the same resonances."""
    clip = render_voice(CARLA, 3.0, seed=13)
    frame_len, hop = frame_lengths(TARGET_SAMPLE_RATE)
    frames = frame_signal(clip.samples, frame_len, hop)[40:41]
    window = np.hamming(frame_len)
    windowed = (frames - frames.mean(axis=1, keepdims=True)) * window
    spectrum = np.fft.rfft(windowed, n=2048, axis=1)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum), n=2048, axis=1)[:, : LPC_ORDER + 1]
    coeffs = levinson_durbin(acf, LPC_ORDER)
    envelope = lpc_envelope(coeffs)[0]
    freqs = np.fft.rfftfreq(envelope.shape[0] * 2 - 2, 1.0 / TARGET_SAMPLE_RATE)

    roots = lpc_roots(coeffs)[0]
    angles = np.angle(roots) * TARGET_SAMPLE_RATE / (2 * np.pi)
    strongest = float(freqs[int(np.argmax(envelope))])
    assert np.min(np.abs(angles[angles > 0] - strongest)) < 120.0


def test_fr4_formants_follow_a_synthetic_two_resonance_tract():
    rng = np.random.default_rng(5)
    excitation = _pulse_train(110.0, 2.0) + 0.001 * rng.normal(0.0, 1.0, 2 * TARGET_SAMPLE_RATE)
    signal = excitation
    for freq in (520.0, 1500.0):
        signal = np.convolve(
            signal, resonator_impulse_response(freq, 70.0, TARGET_SAMPLE_RATE, 1024), mode="same"
        )
    frame_len, hop = frame_lengths(TARGET_SAMPLE_RATE)
    f1, f2 = estimate_formants(frame_signal(signal, frame_len, hop), TARGET_SAMPLE_RATE)
    assert np.nanmedian(f1) == pytest.approx(520.0, rel=0.12)
    assert np.nanmedian(f2) == pytest.approx(1500.0, rel=0.12)


# --------------------------------------------------------------------------- #
# Spectral shape
# --------------------------------------------------------------------------- #


def test_fr4_mel_filterbank_partitions_the_band():
    bank = mel_filterbank(8, 512, TARGET_SAMPLE_RATE)
    assert bank.shape == (8, 257)
    assert np.all(bank >= 0.0)
    peaks = [float(np.argmax(row)) for row in bank]
    assert peaks == sorted(peaks)


def test_fr4_band_ratios_sum_to_one():
    power, _ = power_spectrum(frame_signal(render_voice(ALICE, 2.0, seed=14).samples, 512, 256))
    ratios = band_ratios(power, TARGET_SAMPLE_RATE)
    assert np.allclose(ratios.sum(axis=1), 1.0)


def test_fr4_spectral_tilt_measures_a_known_slope():
    """Pink-ish noise shaped to -6 dB/oct must read back near -6 dB/oct."""
    rng = np.random.default_rng(7)
    n = 1 << 15
    spectrum = np.fft.rfft(rng.normal(0.0, 1.0, n))
    freqs = np.fft.rfftfreq(n, 1.0 / TARGET_SAMPLE_RATE)
    gain = 10.0 ** ((-6.0 * np.log2(np.maximum(freqs, 100.0) / 100.0)) / 20.0)
    shaped = np.fft.irfft(spectrum * gain, n)
    power, _ = power_spectrum(frame_signal(shaped, 512, 256))
    assert float(np.median(spectral_tilt(power, TARGET_SAMPLE_RATE))) == pytest.approx(
        -6.0, abs=1.5
    )


def test_fr4_centroid_and_rolloff_follow_a_tone():
    power, _ = power_spectrum(frame_signal(_sine(2000.0).samples, 512, 256))
    assert float(np.median(spectral_centroid(power, TARGET_SAMPLE_RATE))) == pytest.approx(
        2000.0, abs=100.0
    )
    assert float(np.median(spectral_rolloff(power, TARGET_SAMPLE_RATE))) == pytest.approx(
        2000.0, abs=100.0
    )


# --------------------------------------------------------------------------- #
# Resonators and channel filters
# --------------------------------------------------------------------------- #


def test_fr4_resonator_impulse_response_matches_its_difference_equation():
    a, b, c = resonator_coefficients(700.0, 80.0, TARGET_SAMPLE_RATE)
    analytic = resonator_impulse_response(700.0, 80.0, TARGET_SAMPLE_RATE, 256)
    recursive = np.zeros(256)
    for n in range(256):
        x = a if n == 0 else 0.0
        y1 = recursive[n - 1] if n >= 1 else 0.0
        y2 = recursive[n - 2] if n >= 2 else 0.0
        recursive[n] = x + b * y1 + c * y2
    assert np.allclose(analytic, recursive, atol=1e-10)


def test_fr4_butterworth_bandpass_passes_in_band_and_stops_out_of_band():
    sos = butter_bandpass_sos(300.0, 3400.0, TARGET_SAMPLE_RATE, order=4)
    for freq, expected in ((100.0, 0.15), (1000.0, 1.0), (6000.0, 0.15)):
        out = apply_sos(_sine(freq, 0.5).samples, sos)
        gain = float(
            np.sqrt(np.mean(out[2000:] ** 2) / np.mean(_sine(freq, 0.5).samples[2000:] ** 2))
        )
        if expected == 1.0:
            assert gain == pytest.approx(1.0, abs=0.1)
        else:
            assert gain < expected


# --------------------------------------------------------------------------- #
# The embedding
# --------------------------------------------------------------------------- #


def test_fr4_embedding_is_sixteen_dimensional_and_deterministic(embedder):
    clip = render_voice(ALICE, 4.0, seed=15)
    first = embedder.embed(clip)
    assert len(first) == EMBEDDING_DIM
    assert first == embedder.embed(clip)
    # The embedding is the whitened feature vector itself (REVIEW.md build
    # deviation 3): finite in every dimension, deliberately not unit-norm.
    assert np.all(np.isfinite(first))


def test_fr4_embedder_refuses_foreign_calibration_constants(calibration):
    foreign = calibration.model_copy(update={"embedder_id": "ecapa-voxceleb-v1"})
    with pytest.raises(CalibrationMismatch):
        SpectralStatsEmbedder(foreign)


def _score(embedding, reference, calibration) -> float:
    return distance_similarity(embedding, reference, score_scale=calibration.score_scale)


def test_fr4_same_speaker_scores_above_different_speakers(embedder, calibration):
    alice = centroid([embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(3)])
    genuine = _score(embedder.embed(render_voice(ALICE, 9.0, seed=7700)), alice, calibration)
    for impostor in (BOB, CARLA):
        score = _score(embedder.embed(render_voice(impostor, 9.0, seed=7700)), alice, calibration)
        assert score < genuine
        assert score < calibration.theta_verify
    assert genuine >= calibration.theta_verify


@pytest.mark.parametrize(
    ("axis", "shift"),
    [("f0", {"f0": 0.20}), ("vtl", {"vtl": 0.18}), ("tilt", {"tilt": 8.0})],
)
def test_fr4_single_axis_siblings_are_rejected_no_family_is_dead(
    embedder, calibration, axis, shift
):
    """EVALS M1b in miniature: an embedder blind to one axis fails exactly here."""
    alice = centroid([embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(3)])
    sibling = ALICE.shifted(**shift)
    score = _score(embedder.embed(render_voice(sibling, 9.0, seed=7710)), alice, calibration)
    assert score < calibration.theta_verify, f"{axis} sibling accepted at {score}"


def test_fr4_pitch_mimic_is_rejected(embedder, calibration):
    """A housemate can imitate pitch; the tract and source give them away."""
    alice = centroid([embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(3)])
    score = _score(embedder.embed(render_voice(MIMIC, 9.0, seed=7705)), alice, calibration)
    assert score < calibration.theta_verify


def test_fr4_feature_families_cover_every_dimension():
    covered = sorted(index for indices in FEATURE_FAMILIES.values() for index in indices)
    assert covered == [i for i in range(EMBEDDING_DIM) if i != 2]


def test_fr4_embedding_rejects_a_wrong_width_norm_table(calibration):
    with pytest.raises(ValueError, match="exactly 16"):
        Calibration(
            **{
                **calibration.model_dump(),
                "feature_norms": [FeatureNorm(mean=0.0, scale=1.0)] * 15,
            }
        )

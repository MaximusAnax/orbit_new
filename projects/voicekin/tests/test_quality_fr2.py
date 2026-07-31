"""FR-2 quality screening: every rejection reason, and the boundaries."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import ALICE, render_voice
from voicekin.engine.audio import TARGET_SAMPLE_RATE, AudioClip
from voicekin.engine.quality import ClipKind, clipping_fraction, duration_window, screen_clip
from voicekin.models import SampleRejectReason


def _trim(clip: AudioClip, seconds: float) -> AudioClip:
    return AudioClip(
        samples=clip.samples[: int(seconds * clip.sample_rate)], sample_rate=clip.sample_rate
    )


def test_fr2_a_clean_take_passes(calibration):
    report = screen_clip(
        render_voice(ALICE, 6.0, seed=200), ClipKind.ENROLLMENT, calibration.screening
    )
    assert report.ok and report.reason is None
    assert report.snr_db >= calibration.screening.min_snr_db
    assert report.voiced_ratio >= calibration.screening.min_voiced_ratio


def test_fr2_too_short_is_rejected(calibration):
    clip = _trim(render_voice(ALICE, 6.0, seed=201), 2.0)
    report = screen_clip(clip, ClipKind.ENROLLMENT, calibration.screening)
    assert report.reason is SampleRejectReason.TOO_SHORT


def test_fr2_too_long_is_rejected(calibration):
    clip = render_voice(ALICE, 31.0, seed=202)
    report = screen_clip(clip, ClipKind.ENROLLMENT, calibration.screening)
    assert report.reason is SampleRejectReason.TOO_LONG


def test_fr2_duration_boundaries_are_inclusive(calibration):
    limits = calibration.screening
    clip = render_voice(ALICE, 8.0, seed=203)
    exact = _trim(clip, limits.enroll_min_duration_s)
    assert (
        screen_clip(exact, ClipKind.ENROLLMENT, limits).reason is not SampleRejectReason.TOO_SHORT
    )
    just_under = AudioClip(samples=exact.samples[:-1], sample_rate=exact.sample_rate)
    assert (
        screen_clip(just_under, ClipKind.ENROLLMENT, limits).reason is SampleRejectReason.TOO_SHORT
    )


def test_fr2_clipping_is_rejected(calibration):
    clip = render_voice(ALICE, 6.0, seed=204)
    samples = clip.samples * 6.0
    clipped = AudioClip(samples=np.clip(samples, -0.9999, 0.9999), sample_rate=clip.sample_rate)
    report = screen_clip(clipped, ClipKind.ENROLLMENT, calibration.screening)
    assert report.reason is SampleRejectReason.CLIPPED
    assert report.clipping_fraction > calibration.screening.max_clipping_fraction


def test_fr2_low_snr_is_rejected(calibration):
    clip = render_voice(ALICE, 6.0, seed=205)
    rng = np.random.default_rng(9)
    power = float(np.mean(clip.samples**2))
    noisy = clip.samples + rng.normal(0.0, np.sqrt(power * 0.6), clip.n_samples)
    report = screen_clip(
        AudioClip(samples=np.clip(noisy, -0.99, 0.99), sample_rate=clip.sample_rate),
        ClipKind.ENROLLMENT,
        calibration.screening,
    )
    assert report.reason in (SampleRejectReason.LOW_SNR, SampleRejectReason.LOW_VOICED_RATIO)
    assert report.snr_db < 15.0 or report.voiced_ratio < 0.4


def test_fr2_unvoiced_noise_is_rejected(calibration):
    rng = np.random.default_rng(11)
    noise = rng.normal(0.0, 0.2, TARGET_SAMPLE_RATE * 6)
    report = screen_clip(
        AudioClip(samples=noise, sample_rate=TARGET_SAMPLE_RATE),
        ClipKind.ENROLLMENT,
        calibration.screening,
    )
    assert not report.ok
    assert report.voiced_ratio < calibration.screening.min_voiced_ratio


def test_fr2_digital_silence_is_rejected(calibration):
    silence = AudioClip(samples=np.zeros(TARGET_SAMPLE_RATE * 6), sample_rate=TARGET_SAMPLE_RATE)
    report = screen_clip(silence, ClipKind.ENROLLMENT, calibration.screening)
    assert not report.ok
    assert report.voiced_ratio == 0.0


def test_fr2_consent_uses_a_wider_duration_window(calibration):
    limits = calibration.screening
    assert duration_window(ClipKind.ENROLLMENT, limits) == (3.0, 30.0)
    assert duration_window(ClipKind.CONSENT, limits) == (5.0, 60.0)
    four_seconds = _trim(render_voice(ALICE, 6.0, seed=206), 4.0)
    assert screen_clip(four_seconds, ClipKind.ENROLLMENT, limits).ok
    assert (
        screen_clip(four_seconds, ClipKind.CONSENT, limits).reason is SampleRejectReason.TOO_SHORT
    )


def test_fr2_clipping_fraction_counts_full_scale_samples():
    clip = AudioClip(
        samples=np.array([0.0, 0.5, 0.9995, -1.0, 0.2]), sample_rate=TARGET_SAMPLE_RATE
    )
    assert clipping_fraction(clip, 0.999) == pytest.approx(0.4)
    assert clipping_fraction(AudioClip(samples=np.zeros(0), sample_rate=16000), 0.999) == 0.0

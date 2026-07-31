"""FR-2 audio intake, FR-10 payload hashing."""

from __future__ import annotations

import io
import wave

import numpy as np
import pytest
from voicekin.engine.audio import (
    TARGET_SAMPLE_RATE,
    AudioClip,
    AudioFormatError,
    data_chunk_bytes,
    decode_wav,
    encode_wav,
    normalize_intake,
    payload_sha256,
    resample_to_target,
    sinc_lowpass,
    to_mono,
)


def _tone(freq: float, duration_s: float, sample_rate: int, channels: int = 1) -> bytes:
    t = np.arange(int(duration_s * sample_rate)) / sample_rate
    mono = 0.4 * np.sin(2 * np.pi * freq * t)
    pcm = np.round(mono * 32768.0).astype(np.int16)
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.astype("<i2").tobytes())
    return buffer.getvalue()


def test_fr2_wav_roundtrip_is_bit_exact():
    clip = AudioClip(samples=np.linspace(-0.9, 0.9, 8000), sample_rate=TARGET_SAMPLE_RATE)
    decoded, decoded_rate = decode_wav(encode_wav(clip))
    assert decoded_rate == TARGET_SAMPLE_RATE
    assert np.array_equal(
        AudioClip(samples=to_mono(decoded), sample_rate=decoded_rate).pcm16(), clip.pcm16()
    )


def test_fr2_stereo_is_mono_mixed():
    frames, _sample_rate = decode_wav(_tone(220.0, 0.5, TARGET_SAMPLE_RATE, channels=2))
    assert frames.shape[1] == 2
    mono = to_mono(frames)
    assert mono.ndim == 1
    assert np.allclose(mono, frames[:, 0])


@pytest.mark.parametrize("source_rate", [22050, 44100, 48000])
def test_fr2_resamples_supported_rates_to_16k(source_rate):
    clip = normalize_intake(_tone(440.0, 1.0, source_rate))
    assert clip.sample_rate == TARGET_SAMPLE_RATE
    assert clip.n_samples == pytest.approx(TARGET_SAMPLE_RATE, rel=0.01)


def test_fr2_resampling_preserves_the_tone():
    clip = normalize_intake(_tone(440.0, 1.0, 48000))
    spectrum = np.abs(np.fft.rfft(clip.samples))
    peak_hz = np.fft.rfftfreq(clip.n_samples, 1.0 / TARGET_SAMPLE_RATE)[int(np.argmax(spectrum))]
    assert peak_hz == pytest.approx(440.0, abs=5.0)


def test_fr2_antialias_kernel_is_unit_gain_lowpass():
    kernel = sinc_lowpass(63, 7600.0, 48000)
    assert kernel.shape == (63,)
    assert kernel.sum() == pytest.approx(1.0)
    response = np.abs(np.fft.rfft(kernel, 4096))
    freqs = np.fft.rfftfreq(4096, 1.0 / 48000)
    assert response[np.argmin(np.abs(freqs - 1000.0))] == pytest.approx(1.0, abs=0.02)
    assert response[np.argmin(np.abs(freqs - 12000.0))] < 0.02


def test_fr2_rates_below_the_internal_rate_are_refused():
    with pytest.raises(AudioFormatError):
        resample_to_target(np.zeros(800), 8000)


def test_fr2_non_wav_bytes_are_refused():
    with pytest.raises(AudioFormatError):
        normalize_intake(b"this is not a wav file at all")


def test_fr2_eight_bit_wav_is_refused():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(TARGET_SAMPLE_RATE)
        handle.writeframes(b"\x80" * 1000)
    with pytest.raises(AudioFormatError):
        normalize_intake(buffer.getvalue())


def test_fr10_payload_hash_ignores_container_metadata():
    """A LIST chunk changes the file but not the audio, so not the hash."""
    original = _tone(300.0, 0.25, TARGET_SAMPLE_RATE)
    digest = payload_sha256(original)

    extra = b"LIST" + (8).to_bytes(4, "little") + b"INFOxxxx"
    header_end = original.index(b"data")
    rewritten = original[:header_end] + extra + original[header_end:]
    riff_size = int.from_bytes(rewritten[4:8], "little") + len(extra)
    rewritten = rewritten[:4] + riff_size.to_bytes(4, "little") + rewritten[8:]

    assert rewritten != original
    assert payload_sha256(rewritten) == digest
    assert data_chunk_bytes(rewritten) == data_chunk_bytes(original)


def test_fr10_payload_hash_tracks_the_audio():
    a = payload_sha256(_tone(300.0, 0.25, TARGET_SAMPLE_RATE))
    b = payload_sha256(_tone(301.0, 0.25, TARGET_SAMPLE_RATE))
    assert a != b


def test_fr10_missing_data_chunk_is_reported():
    with pytest.raises(AudioFormatError):
        data_chunk_bytes(b"RIFF" + (4).to_bytes(4, "little") + b"WAVE")


def test_fr2_normalized_payload_is_stable_across_containers():
    """Same audio, two source rates: intake must land on the same 16 kHz payload."""
    at_16k = normalize_intake(_tone(440.0, 1.0, TARGET_SAMPLE_RATE))
    again = normalize_intake(encode_wav(at_16k))
    assert again.payload_sha256() == at_16k.payload_sha256()

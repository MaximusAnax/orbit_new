"""Audio intake and WAV codec (FR-2, FR-10). Pure: bytes in, bytes/arrays out.

The internal format is 16 kHz mono PCM16 (SCOPE decision 11). Everything that
enters VoiceKin is normalized to it before it is hashed, screened, or embedded,
so a sample's ``sha256`` identifies the *audio*, not the container it arrived in.
"""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass

import numpy as np

TARGET_SAMPLE_RATE = 16_000
"""Internal sample rate for every clip after intake."""

_FULL_SCALE = 32768.0

# FR-2 resampler: 63-tap windowed-sinc low-pass followed by linear interpolation.
RESAMPLE_TAPS = 63
RESAMPLE_CUTOFF_HZ = 7_600.0
"""Anti-alias cutoff, 400 Hz below the 8 kHz target Nyquist (transition band)."""


class AudioFormatError(ValueError):
    """Raised when bytes cannot be decoded as 16-bit PCM WAV."""


@dataclass(frozen=True, eq=False)
class AudioClip:
    """Mono float samples in ``[-1, 1)`` plus their sample rate."""

    samples: np.ndarray
    sample_rate: int

    def __post_init__(self) -> None:
        if self.samples.ndim != 1:
            raise ValueError("AudioClip samples must be a 1-D mono array")
        if self.sample_rate <= 0:
            raise ValueError("AudioClip sample_rate must be positive")

    @property
    def n_samples(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.sample_rate

    def pcm16(self) -> np.ndarray:
        """Quantize to int16 with symmetric clipping (deterministic)."""
        scaled = np.round(np.asarray(self.samples, dtype=np.float64) * _FULL_SCALE)
        return np.clip(scaled, -_FULL_SCALE, _FULL_SCALE - 1).astype(np.int16)

    def payload_bytes(self) -> bytes:
        """The canonical little-endian PCM16 payload — what gets hashed."""
        return self.pcm16().astype("<i2").tobytes()

    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# WAV container
# --------------------------------------------------------------------------- #


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode PCM16 WAV bytes into ``(samples[n_frames, n_channels], sample_rate)``.

    Samples are float64 in ``[-1, 1)``.
    """
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            n_channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            sample_rate = handle.getframerate()
            n_frames = handle.getnframes()
            raw = handle.readframes(n_frames)
    except (wave.Error, EOFError) as exc:  # pragma: no cover - message varies
        raise AudioFormatError(f"not a readable WAV stream: {exc}") from exc

    if sample_width != 2:
        raise AudioFormatError(f"only 16-bit PCM is supported (got {sample_width * 8}-bit)")
    if n_channels not in (1, 2):
        raise AudioFormatError(f"only mono or stereo is supported (got {n_channels} channels)")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float64) / _FULL_SCALE
    usable = (pcm.shape[0] // n_channels) * n_channels
    return pcm[:usable].reshape(-1, n_channels), sample_rate


def encode_wav(clip: AudioClip) -> bytes:
    """Encode a clip as a canonical 44-byte-header mono PCM16 WAV."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(clip.sample_rate)
        handle.writeframes(clip.payload_bytes())
    return buffer.getvalue()


def data_chunk_bytes(wav_bytes: bytes) -> bytes:
    """Return the WAV ``data`` chunk payload only — no header (FR-10).

    Hashing the payload rather than the file means metadata rewrites (a LIST
    chunk, a different header layout) do not break provenance lookup.
    """
    if len(wav_bytes) < 12 or wav_bytes[0:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise AudioFormatError("not a RIFF/WAVE stream")
    pos = 12
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos : pos + 4]
        size = int.from_bytes(wav_bytes[pos + 4 : pos + 8], "little")
        body = wav_bytes[pos + 8 : pos + 8 + size]
        if chunk_id == b"data":
            if len(body) != size:
                raise AudioFormatError("truncated data chunk")
            return body
        pos += 8 + size + (size & 1)
    raise AudioFormatError("WAV stream has no data chunk")


def payload_sha256(wav_bytes: bytes) -> str:
    """sha256 of a WAV's data-chunk bytes (FR-10)."""
    return hashlib.sha256(data_chunk_bytes(wav_bytes)).hexdigest()


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def to_mono(samples: np.ndarray) -> np.ndarray:
    """Mono-mix a ``[n_frames, n_channels]`` block by averaging channels."""
    if samples.ndim == 1:
        return samples
    return samples.mean(axis=1)


def sinc_lowpass(taps: int, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """A Hamming-windowed sinc low-pass with unit DC gain."""
    if taps % 2 == 0:
        raise ValueError("windowed-sinc low-pass needs an odd tap count")
    cutoff = cutoff_hz / sample_rate
    if not 0.0 < cutoff < 0.5:
        raise ValueError("cutoff must lie strictly between 0 and the Nyquist frequency")
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    kernel = 2.0 * cutoff * np.sinc(2.0 * cutoff * n)
    kernel *= np.hamming(taps)
    return kernel / kernel.sum()


def resample_to_target(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Resample mono float samples to :data:`TARGET_SAMPLE_RATE` (FR-2).

    63-tap windowed-sinc anti-alias low-pass, then linear interpolation onto the
    target grid. Rates below the target are refused: upsampling invents detail
    the speaker embedder would then measure.
    """
    if sample_rate == TARGET_SAMPLE_RATE:
        return samples
    if sample_rate < TARGET_SAMPLE_RATE:
        raise AudioFormatError(
            f"sample rate {sample_rate} Hz is below the {TARGET_SAMPLE_RATE} Hz internal rate"
        )
    if samples.size == 0:
        return samples
    kernel = sinc_lowpass(RESAMPLE_TAPS, RESAMPLE_CUTOFF_HZ, sample_rate)
    filtered = np.convolve(samples, kernel, mode="same")
    ratio = sample_rate / TARGET_SAMPLE_RATE
    n_out = int(np.floor(samples.shape[0] / ratio))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float64)
    positions = np.arange(n_out, dtype=np.float64) * ratio
    return np.interp(positions, np.arange(filtered.shape[0], dtype=np.float64), filtered)


def normalize_intake(wav_bytes: bytes) -> AudioClip:
    """Decode → mono-mix → resample → quantize: the FR-2 intake pipeline.

    The returned clip is bit-exactly what gets hashed and analysed, so
    ``clip.payload_sha256()`` is stable regardless of the source container.
    """
    frames, sample_rate = decode_wav(wav_bytes)
    mono = to_mono(frames)
    resampled = resample_to_target(mono, sample_rate)
    clip = AudioClip(samples=resampled, sample_rate=TARGET_SAMPLE_RATE)
    # Re-materialize from the quantized payload so analysis sees exactly the
    # samples the hash covers.
    return AudioClip(
        samples=clip.pcm16().astype(np.float64) / _FULL_SCALE,
        sample_rate=TARGET_SAMPLE_RATE,
    )


def clip_from_pcm16(pcm: np.ndarray, sample_rate: int = TARGET_SAMPLE_RATE) -> AudioClip:
    """Build a clip from int16 samples."""
    return AudioClip(samples=np.asarray(pcm, dtype=np.float64) / _FULL_SCALE, sample_rate=sample_rate)


__all__ = [
    "RESAMPLE_CUTOFF_HZ",
    "RESAMPLE_TAPS",
    "TARGET_SAMPLE_RATE",
    "AudioClip",
    "AudioFormatError",
    "clip_from_pcm16",
    "data_chunk_bytes",
    "decode_wav",
    "encode_wav",
    "normalize_intake",
    "payload_sha256",
    "resample_to_target",
    "sinc_lowpass",
    "to_mono",
]

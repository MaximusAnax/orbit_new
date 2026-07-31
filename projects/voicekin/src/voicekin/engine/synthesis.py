"""FR-9 text normalization, unit sequencing, and output packaging. Pure.

The rendered bytes are a function of ``(voice_params, normalized text, seed,
synth_id, sample_rate)`` only — nothing identifier-derived is mixed into audio,
which is what makes FR-15's replay determinism unconditional.
"""

from __future__ import annotations

import re

from voicekin.engine.audio import AudioClip, encode_wav, payload_sha256
from voicekin.engine.voicebox import Unit

MAX_TEXT_CHARS = 500

_VOWEL_CHARS = frozenset("aeiouy")
_VOWEL_PRESET_FOR_CHAR = {"a": "a", "e": "e", "i": "i", "o": "o", "u": "u", "y": "i"}
_CONSONANT_CLASS = {
    **dict.fromkeys("fvszh", "fricative"),
    **dict.fromkeys("pbtdkgcqx", "plosive"),
    **dict.fromkeys("mn", "nasal"),
    **dict.fromkeys("lrwj", "approximant"),
}

_DIGIT_RUN = re.compile(r"\d+")
_PAUSE_PUNCT = re.compile(r"[.,;:!?]+")
_NON_TEXT = re.compile(r"[^a-z, ]+")
_APOSTROPHE = re.compile("['\u2018\u2019]")

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_SCALES = ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"))

#: Stress pattern applied deterministically: first unit of a word is prominent.
_STRESSED_F0_SCALE = 1.06
_UNSTRESSED_F0_SCALE = 0.97


class TextNormalizationError(ValueError):
    """The submitted text cannot be rendered."""


class TextTooLongError(TextNormalizationError):
    """Normalized text exceeds the FR-9 length limit."""


class EmptyTextError(TextNormalizationError):
    """Nothing speakable survived normalization."""


# --------------------------------------------------------------------------- #
# Numbers to words
# --------------------------------------------------------------------------- #


def _under_hundred(value: int) -> list[str]:
    if value < 20:
        return [_ONES[value]]
    tens, ones = divmod(value, 10)
    return [_TENS[tens]] if ones == 0 else [_TENS[tens], _ONES[ones]]


def _under_thousand(value: int) -> list[str]:
    hundreds, rest = divmod(value, 100)
    words: list[str] = []
    if hundreds:
        words += [_ONES[hundreds], "hundred"]
    if rest or not hundreds:
        words += _under_hundred(rest)
    return words


def number_to_words(value: int) -> str:
    """Spell out a non-negative integer below one trillion."""
    if value < 0:
        return "minus " + number_to_words(-value)
    if value >= 1_000_000_000_000:
        return " ".join(_ONES[int(digit)] for digit in str(value))
    if value == 0:
        return "zero"
    words: list[str] = []
    remainder = value
    for scale, name in _SCALES:
        count, remainder = divmod(remainder, scale)
        if count:
            words += [*_under_thousand(count), name]
    if remainder:
        words += _under_thousand(remainder)
    return " ".join(words)


def _spell_digits(token: str) -> str:
    return " ".join(_ONES[int(digit)] for digit in token)


def _expand_number(token: str) -> str:
    """Leading zeros and very long runs are read digit by digit."""
    if len(token) > 1 and token[0] == "0":
        return _spell_digits(token)
    if len(token) > 12:
        return _spell_digits(token)
    return number_to_words(int(token))


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def normalize_text(raw: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Lowercase, spell out digits, and reduce punctuation to pauses (FR-9).

    Pause-inducing punctuation collapses to a single comma attached to the
    preceding word; apostrophes are elided so contractions stay one word; every
    other symbol becomes a separator. Leading, trailing
    and repeated pauses are dropped, so the result is a stable canonical form.
    """
    lowered = _APOSTROPHE.sub("", raw.lower())
    expanded = _DIGIT_RUN.sub(lambda m: f" {_expand_number(m.group())} ", lowered)
    paused = _PAUSE_PUNCT.sub(" , ", expanded)
    tokens = _NON_TEXT.sub(" ", paused).split()

    kept: list[str] = []
    for token in tokens:
        if token == ",":
            if kept and kept[-1] != ",":
                kept.append(token)
        else:
            kept.append(token)
    while kept and kept[-1] == ",":
        kept.pop()

    parts: list[str] = []
    for token in kept:
        if token == "," and parts:
            parts[-1] += ","
        else:
            parts.append(token)
    text = " ".join(parts)

    if not text:
        raise EmptyTextError("text contains nothing speakable after normalization")
    if len(text) > max_chars:
        raise TextTooLongError(f"normalized text is {len(text)} chars (limit {max_chars})")
    return text


# --------------------------------------------------------------------------- #
# Unit sequencing
# --------------------------------------------------------------------------- #


def _consonant_class(cluster: str) -> str | None:
    for char in cluster:
        cls = _CONSONANT_CLASS.get(char)
        if cls is not None:
            return cls
    return None


def _split_word(word: str) -> list[tuple[str | None, str | None]]:
    """Split a word into ``(vowel_preset, onset_class)`` syllable-like units."""
    units: list[tuple[str | None, str | None]] = []
    index = 0
    length = len(word)
    while index < length:
        onset_start = index
        while index < length and word[index] not in _VOWEL_CHARS:
            index += 1
        onset = word[onset_start:index]
        if index >= length:
            if not units:
                units.append((None, _consonant_class(onset) or "fricative"))
            break
        vowel_start = index
        while index < length and word[index] in _VOWEL_CHARS:
            index += 1
        preset = _VOWEL_PRESET_FOR_CHAR[word[vowel_start]]
        units.append((preset, _consonant_class(onset)))
    return units


def build_units(text: str, unit_duration_ms: int) -> list[Unit]:
    """Turn normalized text into the FR-8 unit sequence.

    Words split into onset+nucleus units; a comma becomes a silent unit. Every
    unit lasts ``unit_duration_ms``, so duration is a pure function of the text.
    """
    units: list[Unit] = []
    for token in text.split():
        word = token.rstrip(",")
        pause = token.endswith(",")
        for position, (vowel, onset) in enumerate(_split_word(word)):
            units.append(
                Unit(
                    duration_ms=unit_duration_ms,
                    vowel=vowel,
                    onset=onset,
                    f0_scale=_STRESSED_F0_SCALE if position == 0 else _UNSTRESSED_F0_SCALE,
                )
            )
        if pause:
            units.append(Unit(duration_ms=unit_duration_ms))
    return units


def unit_count(text: str, unit_duration_ms: int = 1) -> int:
    """How many units the normalized ``text`` renders to (EVALS M3 condition 3)."""
    return len(build_units(text, unit_duration_ms))


def expected_duration_s(text: str, unit_duration_ms: int) -> float:
    return unit_count(text) * unit_duration_ms / 1000.0


# --------------------------------------------------------------------------- #
# The stub render (FR-8) — shared by the adapter and the FR-3 derivation
# --------------------------------------------------------------------------- #

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


def stub_voicebox_params(voice_params: "VoiceParamsLike") -> "VoiceboxParams":
    """Map the four stored voice parameters onto the synthesis core (FR-8)."""
    from voicekin.engine.voicebox import VoiceboxParams

    return VoiceboxParams(
        f0_base_hz=voice_params.f0_base_hz,
        f0_range_hz=voice_params.f0_range_hz,
        formant_scale=voice_params.formant_scale,
        tilt_db_oct=voice_params.tilt_db_oct,
        n_formants=STUB_FORMANT_COUNT,
        jitter=STUB_JITTER,
    )


def _stub_dither(samples, seed: int):
    """Mix seeded broadband noise in at :data:`STUB_NOISE_FLOOR_DB`."""
    import numpy as np

    power = float(np.mean(samples**2)) if samples.size else 0.0
    if power <= 0.0:
        return samples
    amplitude = np.sqrt(power / (10.0 ** (STUB_NOISE_FLOOR_DB / 10.0)))
    noise = np.random.default_rng(seed + 1).normal(0.0, amplitude, samples.shape[0])
    return np.clip(samples + noise, -0.999, 0.999)


def stub_render(
    text: str,
    voice_params: "VoiceParamsLike",
    *,
    sample_rate: int,
    seed: int,
    unit_duration_ms: int,
) -> AudioClip:
    """Render normalized ``text`` in the stub voice (FR-8).

    One implementation serves both the product adapter
    (:class:`~voicekin.adapters.synth_stub.FormantStubSynthesizer`) and the
    FR-3 analysis-by-synthesis parameter derivation, so the derivation
    calibrates against exactly the renderer the profile will speak through.
    """
    from voicekin.engine.voicebox import synthesize_units

    units = build_units(text, unit_duration_ms)
    if not units:
        raise ValueError("nothing to synthesize: the text produced no units")
    samples = synthesize_units(
        units, stub_voicebox_params(voice_params), sample_rate=sample_rate, seed=seed
    )
    return AudioClip(samples=_stub_dither(samples, seed), sample_rate=sample_rate)


class VoiceParamsLike:  # pragma: no cover - typing helper only
    """Structural type: anything with the four FR-3 voice parameters."""

    f0_base_hz: float
    f0_range_hz: float
    formant_scale: float
    tilt_db_oct: float


# --------------------------------------------------------------------------- #
# Output packaging
# --------------------------------------------------------------------------- #


def package_output(clip: AudioClip) -> tuple[bytes, str, float]:
    """``(wav_bytes, output_sha256, duration_s)`` for a rendered clip (FR-9/FR-10)."""
    wav_bytes = encode_wav(clip)
    return wav_bytes, payload_sha256(wav_bytes), clip.duration_s


__all__ = [
    "MAX_TEXT_CHARS",
    "STUB_FORMANT_COUNT",
    "STUB_JITTER",
    "STUB_NOISE_FLOOR_DB",
    "EmptyTextError",
    "TextNormalizationError",
    "TextTooLongError",
    "build_units",
    "expected_duration_s",
    "normalize_text",
    "number_to_words",
    "package_output",
    "stub_render",
    "stub_voicebox_params",
    "unit_count",
]

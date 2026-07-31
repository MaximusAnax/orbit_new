"""FR-8 stub synthesis and FR-9 the rendering pipeline."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import ALICE, BOB, CARLA, render_voice
from voicekin.engine.audio import TARGET_SAMPLE_RATE
from voicekin.engine.dsp import analyze_voice
from voicekin.engine.enrollment import centroid, derive_voice_params
from voicekin.engine.quality import ClipKind, screen_clip
from voicekin.engine.synthesis import (
    EmptyTextError,
    TextTooLongError,
    build_units,
    expected_duration_s,
    normalize_text,
    number_to_words,
    package_output,
    unit_count,
)
from voicekin.engine.verification import cosine_similarity
from voicekin.engine.voicebox import (
    VOWEL_PRESETS,
    Unit,
    VoiceboxParams,
    apply_tilt,
    formant_stack,
    synthesize_units,
    unit_boundaries,
    unit_envelope,
)
from voicekin.models import VoiceParams

PARAMS = VoiceParams(f0_base_hz=180.0, f0_range_hz=30.0, formant_scale=1.0, tilt_db_oct=-10.0)


# --------------------------------------------------------------------------- #
# Text normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dinner is ready!", "dinner is ready"),
        ("The laundry is done", "the laundry is done"),
        ("Hello, world.", "hello, world"),
        ("Wait...   what?", "wait, what"),
        ("It's 7 o'clock", "its seven oclock"),
        ("Room 101 - go", "room one hundred one go"),
        ("...leading punctuation", "leading punctuation"),
        ("MIXED Case TEXT", "mixed case text"),
    ],
)
def test_fr9_text_normalization(raw, expected):
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("value", "words"),
    [
        (0, "zero"),
        (7, "seven"),
        (13, "thirteen"),
        (20, "twenty"),
        (42, "forty two"),
        (100, "one hundred"),
        (365, "three hundred sixty five"),
        (1000, "one thousand"),
        (1984, "one thousand nine hundred eighty four"),
        (1_000_000, "one million"),
        (2_034_567, "two million thirty four thousand five hundred sixty seven"),
    ],
)
def test_fr9_number_to_words(value, words):
    assert number_to_words(value) == words


def test_fr9_leading_zero_runs_are_spelled_digit_by_digit():
    assert normalize_text("call 007 now") == "call zero zero seven now"


def test_fr9_empty_and_overlong_text_are_refused():
    with pytest.raises(EmptyTextError):
        normalize_text("!!! ,,, ...")
    with pytest.raises(TextTooLongError):
        normalize_text("word " * 200)


def test_fr9_normalization_is_idempotent():
    once = normalize_text("Hello, World! It is 3 o'clock.")
    assert normalize_text(once) == once


# --------------------------------------------------------------------------- #
# Unit sequencing
# --------------------------------------------------------------------------- #


def test_fr8_units_are_syllable_like():
    units = build_units("dinner is ready", 180)
    assert [u.vowel for u in units] == ["i", "e", "i", "e", "i"]
    assert units[0].onset == "plosive"
    assert all(u.duration_ms == 180 for u in units)


def test_fr8_a_comma_becomes_a_silent_unit():
    units = build_units("hello, world", 180)
    silent = [u for u in units if u.vowel is None and u.onset is None]
    assert len(silent) == 1


def test_fr8_a_vowelless_word_becomes_a_noise_unit():
    units = build_units("hmm", 180)
    assert len(units) == 1
    assert units[0].vowel is None and units[0].onset == "fricative"


def test_fr8_stress_pattern_is_deterministic_and_word_initial():
    units = build_units("dinner ready", 180)
    assert units[0].f0_scale > units[1].f0_scale
    assert build_units("dinner ready", 180) == units


def test_fr8_unit_count_drives_expected_duration():
    text = normalize_text("Dinner is ready")
    assert unit_count(text) == 5
    assert expected_duration_s(text, 180) == pytest.approx(0.9)


# --------------------------------------------------------------------------- #
# The voicebox core
# --------------------------------------------------------------------------- #


def test_fr8_unit_boundaries_are_exact_and_contiguous():
    units = [Unit(duration_ms=180, vowel="a") for _ in range(4)]
    bounds = unit_boundaries(units, TARGET_SAMPLE_RATE)
    assert bounds[0][0] == 0
    assert all(bounds[i][1] == bounds[i + 1][0] for i in range(3))
    assert bounds[-1][1] == round(4 * 180 * TARGET_SAMPLE_RATE / 1000)


def test_fr8_unit_envelope_ends_in_silence():
    envelope = unit_envelope(2880, TARGET_SAMPLE_RATE)
    assert envelope[0] == pytest.approx(0.0)
    assert envelope[1440] == pytest.approx(1.0)
    assert envelope[-1] == 0.0
    assert np.count_nonzero(envelope == 0.0) >= int(0.035 * TARGET_SAMPLE_RATE)


def test_fr8_formant_stack_scales_with_vocal_tract_length():
    short = formant_stack("a", VoiceboxParams(120.0, 20.0, 0.9, -10.0))
    long = formant_stack("a", VoiceboxParams(120.0, 20.0, 1.2, -10.0))
    assert short[0][0] == pytest.approx(VOWEL_PRESETS["a"][0] * 0.9)
    assert all(s[0] < lo[0] for s, lo in zip(short, long, strict=True))


def test_fr8_apply_tilt_imposes_the_requested_slope():
    rng = np.random.default_rng(3)
    signal = rng.normal(0.0, 1.0, 1 << 17)
    tilted = apply_tilt(signal, -12.0, TARGET_SAMPLE_RATE)
    freqs = np.fft.rfftfreq(tilted.shape[0], 1.0 / TARGET_SAMPLE_RATE)
    spectrum = np.abs(np.fft.rfft(tilted)) ** 2
    low = float(np.mean(spectrum[(freqs > 180) & (freqs < 220)]))
    high = float(np.mean(spectrum[(freqs > 360) & (freqs < 440)]))
    assert 10.0 * np.log10(high / low) == pytest.approx(-12.0, abs=1.5)


def test_fr8_voicebox_rejects_impossible_parameters():
    with pytest.raises(ValueError, match="f0_base_hz"):
        VoiceboxParams(0.0, 10.0, 1.0, -10.0)
    with pytest.raises(ValueError, match="n_formants"):
        VoiceboxParams(120.0, 10.0, 1.0, -10.0, n_formants=9)
    with pytest.raises(ValueError, match="unknown vowel"):
        Unit(duration_ms=180, vowel="q")
    with pytest.raises(ValueError, match="unknown consonant"):
        Unit(duration_ms=180, onset="click")
    with pytest.raises(ValueError, match="duration"):
        Unit(duration_ms=0)


def test_fr8_empty_unit_list_renders_nothing():
    assert (
        synthesize_units(
            [], VoiceboxParams(120.0, 10.0, 1.0, -10.0), sample_rate=16000, seed=1
        ).size
        == 0
    )


# --------------------------------------------------------------------------- #
# The stub synthesizer
# --------------------------------------------------------------------------- #


def test_fr8_stub_is_byte_deterministic(synthesizer):
    a = synthesizer.synthesize("dinner is ready", PARAMS, sample_rate=TARGET_SAMPLE_RATE, seed=7)
    b = synthesizer.synthesize("dinner is ready", PARAMS, sample_rate=TARGET_SAMPLE_RATE, seed=7)
    assert a.payload_bytes() == b.payload_bytes()


def test_fr8_stub_output_changes_with_seed_and_text(synthesizer):
    base = synthesizer.synthesize("dinner is ready", PARAMS, sample_rate=16000, seed=7)
    assert (
        synthesizer.synthesize("dinner is ready", PARAMS, sample_rate=16000, seed=8).payload_bytes()
        != base.payload_bytes()
    )
    assert (
        synthesizer.synthesize(
            "the laundry is done", PARAMS, sample_rate=16000, seed=7
        ).payload_bytes()
        != base.payload_bytes()
    )


def test_fr8_stub_output_changes_with_voice_params(synthesizer):
    other = PARAMS.model_copy(update={"f0_base_hz": 110.0, "formant_scale": 1.2})
    assert (
        synthesizer.synthesize("dinner is ready", other, sample_rate=16000, seed=7).payload_bytes()
        != synthesizer.synthesize(
            "dinner is ready", PARAMS, sample_rate=16000, seed=7
        ).payload_bytes()
    )


@pytest.mark.parametrize(
    ("text", "units"), [("dinner is ready", 5), ("the laundry is done now", 7)]
)
def test_fr9_duration_is_exactly_unit_count_times_unit_duration(
    synthesizer, calibration, text, units
):
    """EVALS M3 condition 3: the render must be text-dependent, not a fixed beacon."""
    normalized = normalize_text(text)
    assert unit_count(normalized) == units
    clip = synthesizer.synthesize(normalized, PARAMS, sample_rate=TARGET_SAMPLE_RATE, seed=3)
    assert clip.duration_s == pytest.approx(
        units * calibration.unit_duration_ms / 1000.0, rel=0.001
    )


def test_fr9_stub_output_passes_the_quality_screen(synthesizer, calibration):
    """EVALS M3 condition 2: the output must be speech-like, not a tone."""
    clip = synthesizer.synthesize(
        normalize_text("the dinner in the kitchen is ready for everyone in the house tonight"),
        PARAMS,
        sample_rate=TARGET_SAMPLE_RATE,
        seed=5,
    )
    report = screen_clip(clip, ClipKind.ENROLLMENT, calibration.screening)
    assert report.voiced_ratio >= calibration.screening.min_voiced_ratio
    assert report.clipping_fraction <= calibration.screening.max_clipping_fraction
    assert report.ok, report.reason


def test_fr8_stub_refuses_text_that_produces_no_units(synthesizer):
    with pytest.raises(ValueError, match="no units"):
        synthesizer.synthesize("", PARAMS, sample_rate=16000, seed=1)


@pytest.mark.parametrize("speaker", [ALICE, BOB])
@pytest.mark.parametrize("text", ["dinner is ready in the kitchen", "hello there everyone"])
def test_fr8_synthesis_re_embeds_to_its_own_profile(embedder, synthesizer, speaker, text):
    """FR-8/EVALS M3: rendered speech must attribute back to the voice it came from.

    Scored as an argmax over all three profile centroids, so an identity beacon
    that ignores ``voice_params`` cannot pass.

    Known limitation, measured not assumed: the round trip
    (enrollment -> voice_params -> stub -> embedding) preserves the pitch and
    source-tilt axes but compresses the vocal-tract-length axis, because the
    F1/F2 medians an order-12 LPC estimator reports vary less between speakers
    than their tracts do. A third voice sitting *between* two enrolled voices on
    the surviving axes is therefore not reliably attributable. ALICE and BOB are
    well separated; CARLA is deliberately in between and is scored against, not
    rendered from, here.
    """
    profiles = {}
    for name, spec in (("alice", ALICE), ("bob", BOB), ("carla", CARLA)):
        clips = [render_voice(spec, 7.5, seed=4100 + k) for k in range(3)]
        profiles[name] = (
            centroid([embedder.embed(c) for c in clips]),
            derive_voice_params([analyze_voice(c) for c in clips]),
        )
    target = {ALICE: "alice", BOB: "bob"}[speaker]
    rendered = synthesizer.synthesize(
        normalize_text(text), profiles[target][1], sample_rate=TARGET_SAMPLE_RATE, seed=11
    )
    probe = embedder.embed(rendered)
    scores = {name: cosine_similarity(probe, cent) for name, (cent, _) in profiles.items()}
    assert max(scores, key=scores.get) == target, scores


def test_fr8_synthesis_carries_the_pitch_of_its_profile(embedder, synthesizer):
    """The identity axis the round trip does preserve, asserted directly."""
    for spec in (ALICE, BOB, CARLA):
        clips = [render_voice(spec, 7.5, seed=4100 + k) for k in range(3)]
        params = derive_voice_params([analyze_voice(c) for c in clips])
        rendered = synthesizer.synthesize(
            normalize_text("dinner is ready in the kitchen"),
            params,
            sample_rate=TARGET_SAMPLE_RATE,
            seed=11,
        )
        measured = float(np.exp(analyze_voice(rendered).log_f0_median))
        assert measured == pytest.approx(spec.f0_base_hz, rel=0.12)


def test_fr9_package_output_hashes_the_payload(synthesizer):
    clip = synthesizer.synthesize("dinner is ready", PARAMS, sample_rate=16000, seed=7)
    wav_bytes, digest, duration = package_output(clip)
    assert wav_bytes.startswith(b"RIFF")
    assert len(digest) == 64
    assert duration == pytest.approx(clip.duration_s)
    assert package_output(clip)[1] == digest

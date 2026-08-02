"""US-4, FR-11: the rendering vocabulary for transitions and reports."""

from __future__ import annotations

from flowlist.engine.explain import (
    FLAG_LABELS,
    compare_reports,
    component_phrase,
    explain_report,
    explain_transition,
    flag_label,
    format_signed,
    key_phrase,
    summarize_coverage,
    summarize_report,
)
from flowlist.engine.models import ArcProfile, CoverageReport, FeatureSnapshot
from flowlist.engine.optimizer import reorder
from flowlist.engine.scoring import build_matrix, score_order, transition_score
from flowlist_testkit import make_features


def test_us4_transition_line_carries_every_documented_field() -> None:
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.71, loudness_db=-7.2)
    b = FeatureSnapshot(bpm=126.5, key_pc=4, mode=0, energy=0.76, loudness_db=-6.4)
    line = explain_transition(transition_score(a, b), position=0)
    assert "8A -> 9A" in line
    assert "adjacent fifth/fourth" in line
    assert "tempo +2.0%" in line
    assert "energy +0.05" in line
    assert "loudness +0.8 dB" in line
    assert "key=" in line and "bpm=" in line
    assert "total 0.9" in line
    assert line.startswith("  0 -> 1")


def test_us4_flags_are_rendered_with_labels() -> None:
    a = FeatureSnapshot(bpm=86.0, key_pc=None, mode=None, energy=0.3, loudness_db=-20.0)
    b = FeatureSnapshot(bpm=172.0, key_pc=None, mode=None, energy=0.9, loudness_db=-4.0)
    line = explain_transition(transition_score(a, b))
    assert "no key on one side" in line
    assert "half/double-time blend" in line


def test_us4_missing_values_read_as_na_not_zero() -> None:
    blank = FeatureSnapshot()
    line = explain_transition(transition_score(blank, blank))
    assert "key unknown" in line
    assert "tempo n/a" in line
    assert "energy n/a" in line
    assert "loudness n/a" in line


def test_us4_key_phrase_and_components() -> None:
    a = FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.5, loudness_db=-8.0)
    transition = transition_score(a, a)
    assert key_phrase(transition) == "8A -> 8A (same key)"
    phrase = component_phrase(transition)
    assert phrase.startswith("key=1.00 bpm=1.00")
    assert "danceability" not in phrase  # zero-weight components are omitted


def test_us4_every_flag_has_a_label() -> None:
    for flag in (
        "missing_key",
        "missing_bpm",
        "missing_energy",
        "missing_loudness",
        "missing_danceability",
        "half_time",
        "cliff",
        "anchored",
    ):
        assert flag in FLAG_LABELS
        assert flag_label(flag) != flag
    assert flag_label("unknown_flag") == "unknown_flag"


def test_us3_scorecard_shows_before_and_after() -> None:
    features = make_features(20, seed=12)
    matrix = build_matrix(features)
    before = score_order(list(range(20)), features)
    after = score_order(reorder(matrix, seed=7).order, features)
    lines = compare_reports(before, after)
    assert len(lines) == 3
    assert lines[0].startswith("before: n=20")
    assert lines[1].startswith("after ")
    assert lines[2].startswith("delta:")
    assert "seamless >= 0.70" in lines[2]
    assert "cliff < 0.40" in lines[2]


def test_fr7_report_summary_and_lines() -> None:
    features = make_features(8, seed=21)
    report = score_order(list(range(8)), features, profile=ArcProfile.BUILD)
    lines = explain_report(report)
    assert len(lines) == 7
    assert all(line.strip() for line in lines)
    summary = summarize_report(report)
    assert "n=8" in summary
    assert f"mean={report.mean:.3f}" in summary
    assert f"cliffs={report.cliffs}" in summary


def test_us2_coverage_summary() -> None:
    coverage = CoverageReport(tracks=44, full=41, fields=["bpm", "key_pc"], missing={"key_pc": 3})
    assert summarize_coverage(coverage) == "41/44 tracks fully featured; 3 missing key_pc"


def test_format_signed() -> None:
    assert format_signed(None) == "n/a"
    assert format_signed(0.05) == "+0.05"
    assert format_signed(-0.05) == "-0.05"
    assert format_signed(2.016, 1, "%") == "+2.0%"

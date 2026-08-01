"""Pytest-enforced eval gates (EVALS.md "Naive baselines and gates").

One test per gate, names carrying the FR ids they certify, so `uv run pytest
voicekin/` fails whenever a safety property regresses. Thresholds are asserted
here *and* encoded in ``metrics.GATES`` — the scorecard and the gates cannot
drift apart because both read the same table.

Plus two ordinary (non-gate) tripwires from EVALS.md: the calibration
staleness check and the strict-fixtures corpus manifest check.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from evals import corpus, metrics

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module", autouse=True)
def _corpus():
    corpus.ensure_corpus()


# --------------------------------------------------------------------------- #
# Capability 1 — speaker verification
# --------------------------------------------------------------------------- #


def test_gate_m1a_eer_fr4():
    result = metrics.compute_m1()
    assert metrics.GATES["M1a"](result.m1a), f"same-channel EER {result.m1a:.4f} > 0.05"
    # The gate must sit meaningfully above its naive baseline (CONVENTIONS).
    baseline_m1a, _ = metrics.compute_m1_baseline()
    assert baseline_m1a > 0.25, f"2-dim baseline unexpectedly strong: {baseline_m1a:.3f}"


def test_gate_m1b_axis_degeneracy_fr4():
    result = metrics.compute_m1()
    assert metrics.GATES["M1b"](result.m1b), (
        f"single-axis FAR@theta must be zero, got {result.m1b_by_axis}"
    )
    # The pitch-only degenerate embedder must fail exactly here (EVALS M1b).
    baseline = metrics.compute_m1b_baseline()
    assert max(baseline.values()) == 1.0, f"pitch-only baseline should collapse: {baseline}"


def test_gate_m1c_cross_channel_eer_fr4():
    result = metrics.compute_m1()
    assert metrics.GATES["M1c"](result.m1c), f"cross-channel EER {result.m1c:.4f} > 0.12"


def test_gate_m2a_pooled_impostor_far_fr5():
    result = metrics.compute_m2()
    assert result.pooled_size == 330
    assert metrics.GATES["M2a"](float(result.m2a_accepts)), (
        f"{result.m2a_accepts} impostor accepts at the committed theta — any accept "
        "is a consent forgery (SCOPE decision 3)"
    )


def test_gate_m2b_genuine_consent_fr5():
    result = metrics.compute_m2()
    assert metrics.GATES["M2b_clean"](result.m2b_clean), (
        f"clean genuine accept rate {result.m2b_clean:.3f} < 0.95"
    )
    assert metrics.GATES["M2b_all"](result.m2b_all), (
        f"overall genuine accept rate {result.m2b_all:.3f} < 0.85"
    )


def test_gate_m3_attribution_fr8_fr9():
    result = metrics.compute_m3()
    assert metrics.GATES["M3"](result.m3), (
        f"attribution {result.correct}/{result.trials} below the 0.93 gate; "
        f"failures: {result.failures}"
    )
    # A pipeline that drops identity scores near the fixed-voice baseline;
    # the gate must clear it by an order of magnitude.
    assert metrics.compute_m3_baseline() <= 0.20


def test_gate_m6_enroll_coherence_fr3():
    result = metrics.compute_m6()
    assert metrics.GATES["M6_pure"](result.m6_pure), (
        f"pure sets accepted {result.m6_pure:.3f} != 1.00"
    )
    assert metrics.GATES["M6_mixed"](result.m6_mixed), (
        f"mixed sets rejected {result.m6_mixed:.3f} != 1.00 "
        f"(min mixed LOO {min(result.mixed_scores):.4f}, "
        f"theta {metrics.calibration().theta_enroll})"
    )


# --------------------------------------------------------------------------- #
# Capability 2 — consent-gate enforcement
# --------------------------------------------------------------------------- #


def test_gate_m4_scenarios_fr6_fr7_fr11():
    outcomes = metrics.compute_m4()
    assert len(outcomes) == 35
    failures = [
        f"#{o.scenario_id} {o.name}: {o.mismatches}" for o in outcomes if not o.passed
    ]
    assert not failures, "scenario failures:\n" + "\n".join(failures)


def test_gate_m5_audit_tamper_fr12():
    result = metrics.compute_m5()
    assert result.clean_ok, "the untampered session must verify"
    assert metrics.GATES["M5"](result.m5), f"tamper outcomes mismatched: {result.outcomes}"


# --------------------------------------------------------------------------- #
# Tripwires (ordinary tests, not gates)
# --------------------------------------------------------------------------- #


def test_calibration_matches_dev_split_fr4():
    """EVALS 'calibration staleness': re-run calibrate.py against the dev split
    and require equality with the committed data/calibration.json — editing
    embedder code without re-deriving the constants fails here, not silently."""
    from evals.fixtures import calibrate

    payload, _, _ = calibrate.build_calibration()
    committed = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "calibration.json").read_text()
    )
    assert payload["embedder_id"] == committed["embedder_id"]
    assert payload["theta_verify"] == pytest.approx(committed["theta_verify"], abs=1e-9)
    assert payload["theta_enroll"] == pytest.approx(committed["theta_enroll"], abs=1e-9)
    for derived, stored in zip(
        payload["feature_norms"], committed["feature_norms"], strict=True
    ):
        assert derived["mean"] == pytest.approx(stored["mean"], abs=1e-9)
        assert derived["scale"] == pytest.approx(stored["scale"], abs=1e-9)


def test_corpus_scoring_path_matches_the_shipped_embedder_fr4():
    """M1/M2/M6 score cached raw features through ``corpus.normalize_vector`` and
    ``corpus.similarity`` — small re-implementations of the shipped affine
    normalization and distance rule (the cache exists so calibrate.py and the
    metrics share one feature pass). This tripwire pins them to the real
    ``SpectralStatsEmbedder`` and ``distance_similarity``: edit either shipped
    function without updating the corpus path and the eval suite fails here
    instead of silently measuring stale math (hardening pass, 2026-08-01)."""
    from voicekin.adapters.embedder_spectral import SpectralStatsEmbedder
    from voicekin.engine.verification import distance_similarity

    cal = metrics.calibration()
    embedder = SpectralStatsEmbedder(cal)
    roles = ("S01/enroll/0", "S05/probe/2", "D03/consent/0")
    for role in roles:
        via_corpus = np.asarray(corpus.embedding(role, cal))
        via_embedder = np.asarray(embedder.embed(corpus.clip(role)))
        assert np.allclose(via_corpus, via_embedder, atol=1e-12), role
    a = corpus.embedding(roles[0], cal)
    b = corpus.embedding(roles[1], cal)
    assert corpus.similarity(a, b, cal) == pytest.approx(
        distance_similarity(a, b, score_scale=cal.score_scale), abs=1e-12
    )


def test_derived_fixtures_match_their_builder():
    """The committed trial fixtures must be exactly what build_derived.py emits."""
    from evals.fixtures import build_derived

    trials = build_derived.build_trials()
    consent, coherence = build_derived.build_consent_and_coherence()
    build_derived.validate(trials, consent, coherence)
    assert corpus.load_fixture("trials.json") == json.loads(
        json.dumps(trials, sort_keys=True)
    )
    assert corpus.load_fixture("consent_trials.json") == json.loads(
        json.dumps(consent, sort_keys=True)
    )
    assert corpus.load_fixture("coherence_sets.json") == json.loads(
        json.dumps(coherence, sort_keys=True)
    )


@pytest.mark.skipif(
    os.environ.get("VOICEKIN_STRICT_FIXTURES") != "1",
    reason="byte-exact corpus reproduction is not a CI requirement (EVALS.md); "
    "set VOICEKIN_STRICT_FIXTURES=1 to enforce",
)
def test_fixture_corpus_manifest():
    manifest = corpus.load_manifest()
    assert len(manifest["sha256"]) == manifest["count"]
    for relative, expected in manifest["sha256"].items():
        digest = hashlib.sha256((corpus.CACHE_DIR / relative).read_bytes()).hexdigest()
        assert digest == expected, f"{relative} diverges from the committed manifest"

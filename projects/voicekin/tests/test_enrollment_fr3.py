"""FR-3 enrollment: centroid, leave-one-out coherence, fingerprint, voice params."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import ALICE, BOB, CARLA, render_voice
from voicekin.engine.dsp import analyze_voice
from voicekin.engine.enrollment import (
    MIN_ACCEPTED_SAMPLES,
    MIN_VOICED_SECONDS,
    accepted_samples,
    centroid,
    check_coherence,
    derive_voice_params,
    enrollment_fingerprint,
    is_enrolled_complete,
    l2_normalize,
    leave_one_out_scores,
    voiced_seconds,
)
from voicekin.engine.verification import DEFAULT_SCORE_SCALE, distance_similarity
from voicekin.models import EnrollmentSample, SampleRejectReason, SampleStatus

TS = "2026-07-31T12:00:00Z"


def _sample(index: int, *, duration=6.0, voiced=0.75, accepted=True) -> EnrollmentSample:
    return EnrollmentSample(
        id=f"{index:032x}",
        profile_id="alice",
        sample_index=index,
        path=None if not accepted else f"audio/enroll/alice/{index}.wav",
        sha256=f"{index:064x}",
        duration_s=duration,
        snr_db=25.0,
        voiced_ratio=voiced,
        embedding=[1.0] + [0.0] * 15 if accepted else None,
        status=SampleStatus.ACCEPTED if accepted else SampleStatus.REJECTED,
        reject_reason=None if accepted else SampleRejectReason.TOO_SHORT,
        added_at=TS,
    )


# --------------------------------------------------------------------------- #
# Centroid / normalization
# --------------------------------------------------------------------------- #


def test_fr3_centroid_is_the_plain_mean():
    """REVIEW.md build deviation 3: scoring is calibrated distance, so the
    centroid is the samples' true mean — L2-normalizing it would move it off
    the center the distance scorer measures against."""
    vectors = [[3.0, 0.0], [0.0, 4.0]]
    result = centroid(vectors)
    assert result == pytest.approx([1.5, 2.0])
    assert float(np.linalg.norm(l2_normalize(result))) == pytest.approx(1.0)


def test_fr3_centroid_needs_at_least_one_embedding():
    with pytest.raises(ValueError, match="at least one"):
        centroid([])


def test_fr3_l2_normalize_leaves_the_zero_vector_alone():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


# --------------------------------------------------------------------------- #
# Coherence
# --------------------------------------------------------------------------- #


def test_fr3_leave_one_out_scores_each_sample_against_the_others():
    vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    scores = leave_one_out_scores(vectors)
    assert len(scores) == 3
    # Sample 2 vs centroid([1,0],[1,0]) = [1,0]: squared distance 2.
    assert scores[2] == pytest.approx(1.0 - 2.0 / DEFAULT_SCORE_SCALE)
    # Sample 0 vs centroid([1,0],[0,1]) = [.5,.5]: squared distance 0.5.
    assert scores[0] == pytest.approx(1.0 - 0.5 / DEFAULT_SCORE_SCALE)
    assert scores[0] > scores[2]


def test_fr3_leave_one_out_needs_two_samples():
    with pytest.raises(ValueError, match="at least two"):
        leave_one_out_scores([[1.0, 0.0]])


def test_fr3_a_single_speaker_set_is_coherent(embedder, calibration):
    embeddings = [embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(3)]
    result = check_coherence(embeddings, calibration.theta_enroll)
    assert result.coherent
    assert result.min_score >= calibration.theta_enroll


@pytest.mark.parametrize("intruder", [BOB, CARLA])
def test_fr3_mixed_speaker_set_is_incoherent(embedder, calibration, intruder):
    """The poisoned-enrollment attack the fingerprint binding cannot catch."""
    embeddings = [embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(2)]
    embeddings.append(embedder.embed(render_voice(intruder, 6.5, seed=4102)))
    result = check_coherence(embeddings, calibration.theta_enroll)
    assert not result.coherent
    assert result.worst_index == 2
    assert result.min_score < calibration.theta_enroll


def test_fr3_sibling_intruder_is_incoherent(embedder, calibration):
    embeddings = [embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(2)]
    embeddings.append(embedder.embed(render_voice(ALICE.shifted(vtl=0.18), 6.5, seed=4102)))
    assert not check_coherence(embeddings, calibration.theta_enroll).coherent


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #


def test_fr3_fingerprint_is_order_independent():
    hashes = [f"{i:064x}" for i in range(3)]
    assert enrollment_fingerprint("spectral-v1", hashes) == enrollment_fingerprint(
        "spectral-v1", list(reversed(hashes))
    )


def test_fr3_fingerprint_changes_on_add_remove_and_embedder_swap():
    hashes = [f"{i:064x}" for i in range(3)]
    base = enrollment_fingerprint("spectral-v1", hashes)
    assert base != enrollment_fingerprint("spectral-v1", [*hashes, f"{9:064x}"])
    assert base != enrollment_fingerprint("spectral-v1", hashes[:2])
    assert base != enrollment_fingerprint("ecapa-voxceleb-v1", hashes)
    assert len(base) == 64


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #


def test_fr3_enrollment_needs_three_samples_and_ten_voiced_seconds():
    assert MIN_ACCEPTED_SAMPLES == 3
    assert MIN_VOICED_SECONDS == 10.0
    plenty = [_sample(i) for i in range(3)]
    assert is_enrolled_complete(plenty)
    assert voiced_seconds(plenty) == pytest.approx(13.5)
    assert not is_enrolled_complete(plenty[:2])
    thin = [_sample(i, duration=4.0, voiced=0.5) for i in range(3)]
    assert not is_enrolled_complete(thin)


def test_fr3_rejected_samples_do_not_count_toward_completeness():
    samples = [_sample(0), _sample(1), _sample(2, accepted=False), _sample(3)]
    assert len(accepted_samples(samples)) == 3
    assert is_enrolled_complete(samples)
    assert not is_enrolled_complete(samples[:3])


# --------------------------------------------------------------------------- #
# Derived voice parameters
# --------------------------------------------------------------------------- #


def test_fr3_voice_params_track_the_enrolled_identity(embedder, calibration):
    """Pitch and vocal-tract scale are physically identified; the derived tilt is
    an analysis-by-synthesis proxy (whatever source slope makes the stub's
    *measured* tilt match the enrollment's), so the identity property asserted
    for it is the one FR-8 needs: the render lands on its own enrollment."""
    from voicekin.engine.synthesis import stub_render

    profiles = {}
    for name, speaker in (("alice", ALICE), ("bob", BOB)):
        embeddings = [embedder.embed(render_voice(speaker, 7.5, seed=4100 + k)) for k in range(3)]
        params = derive_voice_params(
            [analyze_voice(render_voice(speaker, 7.5, seed=4100 + k)) for k in range(3)]
        )
        profiles[name] = (centroid(embeddings), params)

    (alice_centroid, alice), (bob_centroid, bob) = profiles["alice"], profiles["bob"]
    assert alice.f0_base_hz == pytest.approx(ALICE.f0_base_hz, rel=0.10)
    assert bob.f0_base_hz == pytest.approx(BOB.f0_base_hz, rel=0.10)
    assert alice.formant_scale < bob.formant_scale
    assert alice.f0_range_hz > 0.0

    text = "the house is ready and dinner is on the table now"
    for params, own, other in ((alice, alice_centroid, bob_centroid),
                               (bob, bob_centroid, alice_centroid)):
        rendered = embedder.embed(
            stub_render(text, params, sample_rate=16_000, seed=7, unit_duration_ms=180)
        )
        own_score = distance_similarity(rendered, own, score_scale=calibration.score_scale)
        other_score = distance_similarity(rendered, other, score_scale=calibration.score_scale)
        assert own_score > other_score


def test_fr3_voice_params_are_deterministic():
    features = [analyze_voice(render_voice(CARLA, 7.5, seed=4100 + k)) for k in range(3)]
    assert derive_voice_params(features) == derive_voice_params(features)


def test_fr3_voice_params_need_analysis():
    with pytest.raises(ValueError, match="at least one"):
        derive_voice_params([])


def test_fr3_derived_params_stay_inside_their_committed_ranges():
    extreme = derive_voice_params(
        [analyze_voice(render_voice(ALICE.shifted(vtl=0.5), 6.0, seed=42))]
    )
    assert 0.60 <= extreme.formant_scale <= 1.60
    assert -24.0 <= extreme.tilt_db_oct <= 0.0


def test_fr3_centroid_is_closer_to_its_own_speaker(embedder, calibration):
    alice = centroid([embedder.embed(render_voice(ALICE, 7.5, seed=4100 + k)) for k in range(3)])
    bob = centroid([embedder.embed(render_voice(BOB, 7.5, seed=4100 + k)) for k in range(3)])
    probe = embedder.embed(render_voice(ALICE, 5.0, seed=9001))
    scale = calibration.score_scale
    assert distance_similarity(probe, alice, score_scale=scale) > distance_similarity(
        probe, bob, score_scale=scale
    )

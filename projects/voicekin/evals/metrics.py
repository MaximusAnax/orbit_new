"""Metric implementations for the VoiceKin eval suite (EVALS.md).

Every metric runs against the committed fixtures with the offline adapters
only: ``SpectralStatsEmbedder``, ``FormantStubSynthesizer``,
``FileSinkDeliverer`` into a temp dir, ``SQLiteRepository(":memory:")``. No
network, no wall clock — every ``now`` comes from fixture data — and every
seed (synthesis seeds and ``nonce_seed``s alike) is supplied explicitly.

Similarity is the shipped scoring rule ``s = 1 - ||a-b||^2 / score_scale``
over calibrated embeddings (REVIEW.md build deviation 3); M3's attribution
argmax uses it too, and additionally reports the cosine-form SECS diagnostic.

Baselines are computed live by the naive implementations at the bottom of each
section — never hardcoded.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from voicekin.adapters.embedder_spectral import FEATURE_FAMILIES, SpectralStatsEmbedder
from voicekin.adapters.synth_stub import FormantStubSynthesizer
from voicekin.engine import audit as audit_engine
from voicekin.engine.enrollment import leave_one_out_scores
from voicekin.engine.quality import ClipKind, screen_clip
from voicekin.engine.synthesis import normalize_text, unit_count
from voicekin.models import (
    Calibration,
    ConsentStatus,
    Context,
    DeliveryStatus,
    TargetKind,
    UtteranceStatus,
)
from voicekin.services import ServiceError, VoiceKinService, load_statement_template
from voicekin.store import SQLiteRepository

from . import corpus

# --------------------------------------------------------------------------- #
# Shared plumbing
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def calibration() -> Calibration:
    from voicekin.services import load_calibration

    return load_calibration()


def _similarity(a: Sequence[float], b: Sequence[float]) -> float:
    return corpus.similarity(a, b, calibration())


@lru_cache(maxsize=1)
def eval_speakers() -> tuple[str, ...]:
    return tuple(s["id"] for s in corpus.speakers(split="eval", role="base"))


@lru_cache(maxsize=1)
def eval_centroids() -> dict[str, np.ndarray]:
    cal = calibration()
    return {
        sid: np.asarray(corpus.centroid_of(corpus.roles(sid, "enroll"), cal))
        for sid in eval_speakers()
    }


def far(scores: Sequence[float], threshold: float) -> float:
    """``FAR(theta, S) = |{impostor trials: s >= theta}| / |S|`` (EVALS M1)."""
    if not scores:
        return 0.0
    return sum(1 for s in scores if s >= threshold) / len(scores)


def frr(scores: Sequence[float], threshold: float) -> float:
    """``FRR(theta, G) = |{genuine trials: s < theta}| / |G|`` (EVALS M1)."""
    if not scores:
        return 0.0
    return sum(1 for s in scores if s < threshold) / len(scores)


def eer(genuine: Sequence[float], impostor: Sequence[float]) -> float:
    """Equal error rate: FAR at the FAR=FRR crossing, linearly interpolated
    between adjacent scores on the DET sweep (Martin et al. 1997)."""
    thresholds = sorted(set(genuine) | set(impostor))
    previous: tuple[float, float] | None = None
    for threshold in thresholds:
        fa, fr = far(impostor, threshold), frr(genuine, threshold)
        if previous is not None:
            fa0, fr0 = previous
            d0, d1 = fa0 - fr0, fa - fr
            if d0 >= 0 >= d1 or d0 <= 0 <= d1:
                weight = 0.0 if d0 == d1 else d0 / (d0 - d1)
                return fa0 + weight * (fa - fa0)
        previous = (fa, fr)
    return 0.5  # pragma: no cover - degenerate score sets never cross


# --------------------------------------------------------------------------- #
# M1 — speaker-verification separability
# --------------------------------------------------------------------------- #

SINGLE_AXIS_CLASSES = ("single_f0", "single_vtl", "single_tilt")


@dataclass(frozen=True)
class M1Result:
    m1a: float
    m1c: float
    m1b_by_axis: dict[str, float]
    genuine_clean: list[float]
    genuine_channel: list[float]
    impostor: list[tuple[float, str]]

    @property
    def m1b(self) -> float:
        return max(self.m1b_by_axis.values())


def _trial_score(target: str, probe_role: str, embed=None) -> float:
    cal = calibration()
    probe = corpus.embedding(probe_role, cal) if embed is None else embed(probe_role)
    return _similarity(eval_centroids()[target], probe)


@lru_cache(maxsize=1)
def compute_m1() -> M1Result:
    trials = corpus.load_fixture("trials.json")
    cal = calibration()
    genuine_clean: list[float] = []
    genuine_channel: list[float] = []
    for trial in trials["genuine"]:
        score = _trial_score(trial["target"], trial["probe"])
        (genuine_clean if trial["condition"] == "clean" else genuine_channel).append(score)
    impostor = [
        (_trial_score(trial["target"], trial["probe"]), trial["class"])
        for trial in trials["impostor"]
    ]
    impostor_scores = [s for s, _ in impostor]
    by_axis = {
        axis: far([s for s, k in impostor if k == axis], cal.theta_verify)
        for axis in SINGLE_AXIS_CLASSES
    }
    return M1Result(
        m1a=eer(genuine_clean, impostor_scores),
        m1c=eer(genuine_channel, impostor_scores),
        m1b_by_axis=by_axis,
        genuine_clean=genuine_clean,
        genuine_channel=genuine_channel,
        impostor=impostor,
    )


def _naive_embedding(role: str) -> np.ndarray:
    """The M1 baseline 'embedding': [log mean energy, log duration]."""
    clip = corpus.clip(role)
    energy = float(np.mean(np.asarray(clip.samples) ** 2))
    return np.array([np.log(max(energy, 1e-12)), np.log(max(clip.duration_s, 1e-6))])


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 1e-12 else 0.0


@lru_cache(maxsize=1)
def compute_m1_baseline() -> tuple[float, float]:
    """Live baseline: EER of the 2-dim energy/duration embedding (M1a, M1c)."""
    trials = corpus.load_fixture("trials.json")
    centroids = {
        sid: np.mean([_naive_embedding(r) for r in corpus.roles(sid, "enroll")], axis=0)
        for sid in eval_speakers()
    }

    def score(target: str, probe: str) -> float:
        return _cosine(centroids[target], _naive_embedding(probe))

    genuine_clean = [
        score(t["target"], t["probe"]) for t in trials["genuine"] if t["condition"] == "clean"
    ]
    genuine_channel = [
        score(t["target"], t["probe"]) for t in trials["genuine"] if t["condition"] != "clean"
    ]
    impostor = [score(t["target"], t["probe"]) for t in trials["impostor"]]
    return eer(genuine_clean, impostor), eer(genuine_channel, impostor)


@lru_cache(maxsize=1)
def compute_m1b_baseline() -> dict[str, float]:
    """Live baseline: a pitch-only embedder (formant and band dims zeroed after
    normalization) scored at the committed theta — the degeneracy M1b exists to
    catch. Its FAR on the f0 axis is ~0 while vtl and tilt collapse."""
    cal = calibration()
    pitch_dims = FEATURE_FAMILIES["f0"]

    def pitch_only(role: str) -> np.ndarray:
        embedding = np.asarray(corpus.embedding(role, cal), dtype=np.float64)
        masked = np.zeros_like(embedding)
        for dim in pitch_dims:
            masked[dim] = embedding[dim]
        return masked

    centroids = {
        sid: np.mean([pitch_only(r) for r in corpus.roles(sid, "enroll")], axis=0)
        for sid in eval_speakers()
    }
    trials = corpus.load_fixture("trials.json")
    by_axis: dict[str, list[float]] = {axis: [] for axis in SINGLE_AXIS_CLASSES}
    for trial in trials["impostor"]:
        if trial["class"] in by_axis:
            score = _similarity(centroids[trial["target"]], pitch_only(trial["probe"]))
            by_axis[trial["class"]].append(score)
    return {axis: far(scores, cal.theta_verify) for axis, scores in by_axis.items()}


# --------------------------------------------------------------------------- #
# M2 — consent decision operating point
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class M2Result:
    m2a_accepts: int
    pooled_size: int
    m2b_clean: float
    m2b_all: float
    genuine: list[tuple[float, str]]
    impostor_consent: list[float]


@lru_cache(maxsize=1)
def compute_m2() -> M2Result:
    cal = calibration()
    fixture = corpus.load_fixture("consent_trials.json")
    genuine = [
        (_trial_score(t["target"], t["recording"]), t["condition"]) for t in fixture["genuine"]
    ]
    impostor_consent = [_trial_score(t["target"], t["recording"]) for t in fixture["impostor"]]
    pooled = impostor_consent + [s for s, _ in compute_m1().impostor]
    clean = [s for s, condition in genuine if condition == "clean"]
    return M2Result(
        m2a_accepts=sum(1 for s in pooled if s >= cal.theta_verify),
        pooled_size=len(pooled),
        m2b_clean=sum(1 for s in clean if s >= cal.theta_verify) / len(clean),
        m2b_all=sum(1 for s, _ in genuine if s >= cal.theta_verify) / len(genuine),
        genuine=genuine,
        impostor_consent=impostor_consent,
    )


def compute_m2_baselines() -> dict[str, float]:
    """Live: accept-all (theta = -1) impostor accepts; reject-all genuine rate."""
    result = compute_m2()
    pooled = result.impostor_consent + [s for s, _ in compute_m1().impostor]
    accept_all = sum(1 for s in pooled if s >= -1.0)
    reject_all = sum(1 for s, _ in result.genuine if s >= 1.0) / len(result.genuine)
    return {"m2a_accept_all": float(accept_all), "m2b_reject_all": reject_all}


# --------------------------------------------------------------------------- #
# M3 — synthesis identity attribution
# --------------------------------------------------------------------------- #

#: The three fixture texts. Unit counts 17 / 21 / 31 (three distinct
#: text-dependent durations, EVALS M3 condition 3), every render >= 3 s — the
#: FR-2 floor below which the product never embeds any clip — and vowel-class
#: shares bounded so the render measures speech, not one corner of the vowel
#: space (REVIEW.md deviation 5 records the recalibration of this metric).
M3_TEXTS = (
    "the washing machine has finished and the front door is open",
    "your bath is warm the calm dog sat by the door and the hot food is on the table now",
    "good morning the coffee pot is on the kitchen table and your first meeting "
    "will begin in half an hour have a good day",
)
M3_UNIT_COUNTS = (17, 21, 31)
M3_SEED = 20260731
M3_NOW = "2026-07-01T10:00:00Z"


@dataclass(frozen=True)
class M3Result:
    m3: float
    trials: int
    correct: int
    mean_secs: float
    failures: list[tuple[str, str, str]]  # (speaker, text, winner)


def _service_profiles() -> tuple[VoiceKinService, Path]:
    """Enroll every eval speaker through the real service layer."""
    home = Path(tempfile.mkdtemp(prefix="voicekin-m3-"))
    service = VoiceKinService(
        repository=SQLiteRepository.in_memory(),
        data_home=home,
        calibration=calibration(),
        statement_template=load_statement_template(),
    )
    service.initialize("Eval Operator", M3_NOW)
    for sid in eval_speakers():
        service.create_profile(sid.lower(), sid, "eval", M3_NOW)
        paths = [corpus.wav_path(role) for role in corpus.roles(sid, "enroll")]
        results = service.add_samples(sid.lower(), paths, M3_NOW)
        assert all(r.accepted for r in results), f"{sid}: enrollment take rejected"
    return service, home


@lru_cache(maxsize=1)
def compute_m3() -> M3Result:
    for text, expected_units in zip(M3_TEXTS, M3_UNIT_COUNTS, strict=True):
        assert unit_count(normalize_text(text)) == expected_units, text
    cal = calibration()
    embedder = SpectralStatsEmbedder(cal)
    service, home = _service_profiles()
    try:
        centroids = {}
        params = {}
        for sid in eval_speakers():
            profile = service.require_profile(sid.lower())
            assert profile.centroid is not None and profile.voice_params is not None
            centroids[sid] = np.asarray(profile.centroid)
            params[sid] = profile.voice_params
        synthesizer = service.synthesizer
        correct = 0
        secs: list[float] = []
        failures: list[tuple[str, str, str]] = []
        for sid in eval_speakers():
            for text in M3_TEXTS:
                normalized = normalize_text(text)
                clip = synthesizer.synthesize(
                    normalized, params[sid], sample_rate=16_000, seed=M3_SEED
                )
                embedding = np.asarray(embedder.embed(clip))
                scores = {
                    q: _similarity(embedding, centroids[q]) for q in eval_speakers()
                }
                winner = max(scores, key=scores.get)  # type: ignore[arg-type]
                secs.append(scores[sid])
                screening = screen_clip(clip, ClipKind.ENROLLMENT, cal.screening)
                quality_ok = (
                    screening.clipping_fraction <= cal.screening.max_clipping_fraction
                    and screening.snr_db >= cal.screening.min_snr_db
                    and screening.voiced_ratio >= cal.screening.min_voiced_ratio
                )
                expected = unit_count(normalized) * cal.unit_duration_ms / 1000.0
                duration_ok = abs(clip.duration_s - expected) <= 0.05 * expected
                if winner == sid and quality_ok and duration_ok:
                    correct += 1
                else:
                    failures.append((sid, text[:24], winner))
        trials = len(eval_speakers()) * len(M3_TEXTS)
        return M3Result(
            m3=correct / trials,
            trials=trials,
            correct=correct,
            mean_secs=float(np.mean(secs)),
            failures=failures,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)


@lru_cache(maxsize=1)
def compute_m3_baseline() -> float:
    """Live baseline: a stub that ignores ``voice_params`` (fixed default voice)."""
    from voicekin.models import VoiceParams

    cal = calibration()
    embedder = SpectralStatsEmbedder(cal)
    synthesizer = FormantStubSynthesizer(cal.unit_duration_ms)
    default_voice = VoiceParams(
        f0_base_hz=120.0, f0_range_hz=18.0, formant_scale=1.0, tilt_db_oct=-10.0
    )
    centroids = eval_centroids()
    correct = 0
    for sid in eval_speakers():
        for text in M3_TEXTS:
            clip = synthesizer.synthesize(
                normalize_text(text), default_voice, sample_rate=16_000, seed=M3_SEED
            )
            embedding = np.asarray(embedder.embed(clip))
            scores = {q: _similarity(embedding, centroids[q]) for q in eval_speakers()}
            if max(scores, key=scores.get) == sid:  # type: ignore[arg-type]
                correct += 1
    return correct / (len(eval_speakers()) * len(M3_TEXTS))


# --------------------------------------------------------------------------- #
# M4 — consent-gate scenario suite
# --------------------------------------------------------------------------- #


@dataclass
class ScenarioOutcome:
    scenario_id: int
    name: str
    mismatches: list[str] = field(default_factory=list)
    naive_decision_errors: int = 0
    decision_ops: int = 0

    @property
    def passed(self) -> bool:
        return not self.mismatches

    @property
    def naive_passed(self) -> bool:
        """Decision-only scoring for the naive baseline gate (EVALS M4 table)."""
        return self.naive_decision_errors == 0


class _ScenarioRunner:
    """Drives one scripted scenario against the real service layer."""

    def __init__(self, script: dict[str, Any]) -> None:
        self.script = script
        self.outcome = ScenarioOutcome(script["id"], script["name"])
        self.home = Path(tempfile.mkdtemp(prefix=f"voicekin-m4-{script['id']}-"))
        self.service = VoiceKinService(
            repository=SQLiteRepository.in_memory(),
            data_home=self.home,
            calibration=calibration(),
            statement_template=load_statement_template(),
        )
        self.saved: dict[str, str] = {}
        self.sink_dir = self.home / "sink-out"

    def close(self) -> None:
        self.service.repository.close()
        shutil.rmtree(self.home, ignore_errors=True)

    # -- helpers ---------------------------------------------------------- #

    def fail(self, message: str) -> None:
        self.outcome.mismatches.append(message)

    def expect(self, condition: bool, message: str) -> None:
        if not condition:
            self.fail(message)

    def _resolve(self, token: str) -> str:
        if token.startswith("$"):
            return self.saved[token[1:]]
        return token

    def _naive_decision(self, profile_id: str) -> str:
        """The naive gate: 'a verified consent row exists for the profile'."""
        consents = self.service.repository.list_consents(profile_id)
        verified = any(c.status is ConsentStatus.VERIFIED for c in consents)
        return "authorized" if verified else "refused"

    def _score_decision(self, op: dict[str, Any], profile_id: str) -> None:
        expected = op["expect"]["decision"] if "decision" in op["expect"] else (
            "authorized" if op["expect"].get("status") == "succeeded" else "refused"
        )
        self.outcome.decision_ops += 1
        if self._naive_decision(profile_id) != expected:
            self.outcome.naive_decision_errors += 1

    # -- ops --------------------------------------------------------------- #

    def run(self) -> ScenarioOutcome:
        try:
            for op in self.script["ops"]:
                handler = getattr(self, f"_op_{op['op']}", None)
                if handler is None:
                    self.fail(f"unknown op {op['op']!r}")
                    break
                handler(op)
            self._check_audit()
        finally:
            self.close()
        return self.outcome

    def _op_init(self, op: dict[str, Any]) -> None:
        self.service.initialize(op["operator"], op["now"])

    def _op_create_profile(self, op: dict[str, Any]) -> None:
        self.service.create_profile(
            op["profile"], op["display_name"], op["relationship"], op["now"]
        )

    def _op_enroll(self, op: dict[str, Any]) -> None:
        before = self.service.require_profile(op["profile"])
        paths = [corpus.wav_path(role) for role in op["takes"]]
        results = self.service.add_samples(op["profile"], paths, op["now"])
        expect = op.get("expect", {})
        accepted = sum(1 for r in results if r.accepted)
        rejected = len(results) - accepted
        if "accepted" in expect:
            self.expect(accepted == expect["accepted"],
                        f"enroll accepted {accepted} != {expect['accepted']}")
        if "rejected" in expect:
            self.expect(rejected == expect["rejected"],
                        f"enroll rejected {rejected} != {expect['rejected']}")
        if "reject_reasons" in expect:
            reasons = [str(r.sample.reject_reason) for r in results if not r.accepted]
            self.expect(reasons == expect["reject_reasons"],
                        f"reject reasons {reasons} != {expect['reject_reasons']}")
        after = self.service.require_profile(op["profile"])
        if "enrolled" in expect:
            self.expect(after.is_enrolled == expect["enrolled"],
                        f"enrolled {after.is_enrolled} != {expect['enrolled']}")
        if expect.get("centroid_unchanged"):
            self.expect(after.centroid == before.centroid,
                        "centroid moved despite a rejected mutation")
        if expect.get("fingerprint_changed"):
            self.expect(after.enrollment_fingerprint != before.enrollment_fingerprint,
                        "fingerprint did not change")

    def _op_remove_sample(self, op: dict[str, Any]) -> None:
        before = self.service.require_profile(op["profile"])
        samples = self.service.repository.list_samples(op["profile"])
        match = next(s for s in samples if s.sample_index == op["sample_index"])
        after = self.service.remove_sample(op["profile"], match.id, op["now"])
        expect = op.get("expect", {})
        if "enrolled" in expect:
            self.expect(after.is_enrolled == expect["enrolled"],
                        f"enrolled {after.is_enrolled} != {expect['enrolled']}")
        if expect.get("fingerprint_changed"):
            self.expect(after.enrollment_fingerprint != before.enrollment_fingerprint,
                        "fingerprint did not change on removal")

    def _op_draft(self, op: dict[str, Any]) -> None:
        expect = op.get("expect", {})
        contexts = [Context(c) for c in op["scope"]]
        rows_before = len(self.service.repository.list_consents(op["profile"]))
        try:
            record = self.service.draft_consent(
                op["profile"], contexts, op["expires_at"], op["nonce_seed"], op["now"]
            )
        except ServiceError:
            if expect.get("error") != "precondition":
                self.fail("draft raised unexpectedly")
            rows_after = len(self.service.repository.list_consents(op["profile"]))
            if "consent_rows" in expect:
                self.expect(rows_after == expect["consent_rows"],
                            f"consent rows {rows_after} != {expect['consent_rows']}")
            else:
                self.expect(rows_after == rows_before, "a failed draft created a row")
            return
        if expect.get("error"):
            self.fail("draft succeeded where a precondition error was expected")
            return
        self.expect(str(record.status) == expect.get("status", "draft"), "draft status wrong")

    def _op_grant(self, op: dict[str, Any]) -> None:
        record = self.service.grant_consent(
            op["profile"], corpus.wav_path(op["take"]), op["now"]
        )
        expect = op.get("expect", {})
        self.expect(str(record.status) == expect.get("status"),
                    f"grant status {record.status} != {expect.get('status')}")
        if "reject_reason" in expect:
            self.expect(str(record.reject_reason) == expect["reject_reason"],
                        f"reject reason {record.reject_reason} != {expect['reject_reason']}")
        if expect.get("score_fields_null") is True:
            self.expect(
                record.similarity is None and record.threshold is None
                and record.embedder_id is None and record.enrollment_fingerprint is None,
                "score fields should be null for a pre-scoring rejection",
            )
        if expect.get("score_fields_null") is False:
            self.expect(
                record.similarity is not None and record.threshold is not None,
                "score fields should be recorded when a score was computed",
            )
        if expect.get("audio_sha256_null"):
            self.expect(record.audio_sha256 is None, "audio_sha256 should be null")
        if str(record.status) == "rejected":
            self.expect(record.audio_path is None, "rejected consent audio must not be retained")

    def _op_revoke(self, op: dict[str, Any]) -> None:
        before = self.service.repository.list_consents(op["profile"])
        already = any(c.status is ConsentStatus.REVOKED for c in before)
        record = self.service.revoke_consent(op["profile"], op["reason"], op["now"])
        expect = op.get("expect", {})
        self.expect(str(record.status) == expect.get("status", "revoked"), "revoke status wrong")
        if expect.get("noop"):
            self.expect(already, "second revoke should find the record already revoked")

    def _op_set_enabled(self, op: dict[str, Any]) -> None:
        self.service.set_enabled(op["profile"], op["enabled"], op["now"])

    def _op_add_target(self, op: dict[str, Any]) -> None:
        self.sink_dir.mkdir(parents=True, exist_ok=True)
        self.service.add_target(
            op["target"], TargetKind(op["kind"]), {"dir": str(self.sink_dir)}, op["now"]
        )

    def _op_synthesize(self, op: dict[str, Any]) -> None:
        self._score_decision(op, op["profile"])
        result = self.service.synthesize(
            op["profile"], op["text"], Context(op["context"]), op["seed"], op["now"]
        )
        expect = op["expect"]
        decision = "refused" if result.refused else "authorized"
        self.expect(decision == expect["decision"],
                    f"decision {decision} != {expect['decision']}")
        if expect["decision"] == "refused":
            self.expect(
                result.refusal is not None and str(result.refusal.reason) == expect["reason"],
                f"refusal reason {result.refusal and result.refusal.reason} != {expect['reason']}",
            )
            self.expect(result.utterance.status is UtteranceStatus.REFUSED,
                        "refusal must persist an utterance row")
        else:
            utterance = result.utterance
            self.expect(utterance.status is UtteranceStatus.RENDERED, "utterance not rendered")
            self.expect(utterance.output_sha256 is not None, "rendered without output hash")
            path = utterance.output_path and self.service.absolute(utterance.output_path)
            self.expect(bool(path and path.exists()), "rendered WAV missing on disk")
        if "save_as" in op:
            self.saved[op["save_as"]] = result.utterance.id

    def _op_deliver(self, op: dict[str, Any]) -> None:
        utterance_id = self._resolve(op["utterance"])
        utterance = self.service.repository.get_utterance(utterance_id)
        assert utterance is not None
        self._score_decision(op, utterance.profile_id)
        result = self.service.deliver(utterance_id, op["target"], op["now"])
        expect = op["expect"]
        self.expect(str(result.delivery.status) == expect["status"],
                    f"delivery {result.delivery.status} != {expect['status']}")
        if expect["status"] == "refused":
            self.expect(
                result.refusal is not None and str(result.refusal.reason) == expect["reason"],
                "delivery refusal reason mismatch",
            )
        if expect.get("files_exist"):
            detail = result.delivery.detail
            self.expect(
                Path(detail["path"]).exists() and Path(detail["manifest_path"]).exists(),
                "delivered WAV + manifest should exist",
            )
        if "save_as" in op:
            self.saved[op["save_as"]] = result.delivery.id

    def _op_purge(self, op: dict[str, Any]) -> None:
        report = self.service.purge_profile(op["profile"], op["now"])
        expect = op.get("expect", {})
        for key, value in expect.items():
            actual = getattr(report, key)
            self.expect(actual == value, f"purge {key} {actual} != {value}")

    def _op_check_purged(self, op: dict[str, Any]) -> None:
        profile = self.service.require_profile(op["profile"])
        self.expect(str(profile.status) == "purged", "profile should be purged")
        self.expect(profile.centroid is None and profile.voice_params is None,
                    "purge must null biometric derivatives")
        for sample in self.service.repository.list_samples(op["profile"]):
            self.expect(sample.path is None, "purged sample still has a path")
            self.expect(sample.embedding is None, "purged sample still has an embedding")
            self.expect(bool(sample.sha256), "purge must keep the content hash")
        for name in ("enroll", "consent"):
            directory = self.home / "audio" / name / op["profile"]
            leftovers = list(directory.glob("*")) if directory.exists() else []
            self.expect(not leftovers, f"purged {name} audio left behind: {leftovers}")

    def _op_check_utterance(self, op: dict[str, Any]) -> None:
        utterance = self.service.repository.get_utterance(self._resolve(op["utterance"]))
        assert utterance is not None
        expect = op["expect"]
        self.expect(str(utterance.status) == expect["status"], "utterance status mismatch")
        if "refusal_reason" in expect:
            self.expect(str(utterance.refusal_reason) == expect["refusal_reason"],
                        "utterance refusal_reason mismatch")
        if "consent_id" in expect:
            self.expect(utterance.consent_id == expect["consent_id"],
                        "refused utterance must have a null consent_id")

    def _op_check_delivery(self, op: dict[str, Any]) -> None:
        delivery_id = self._resolve(op["delivery"])
        deliveries = [
            d
            for profile in self.service.repository.list_profiles()
            for d in self.service.repository.list_profile_deliveries(profile.id)
        ]
        match = next(d for d in deliveries if d.id == delivery_id)
        expect = op["expect"]
        if expect.get("path_gone"):
            self.expect("path" not in match.detail and "manifest_path" not in match.detail,
                        "purge must drop delivered paths from the receipt")
        if expect.get("path_sha256_present"):
            self.expect(
                "path_sha256" in match.detail and "manifest_path_sha256" in match.detail,
                "purge must keep path hashes in the receipt",
            )
        if expect.get("delivered_files_exist") is False:
            leftovers = list(self.sink_dir.glob("*")) if self.sink_dir.exists() else []
            self.expect(not leftovers, f"delivered copies still exist: {leftovers}")

    # -- audit ------------------------------------------------------------- #

    def _check_audit(self) -> None:
        records = self.service.repository.list_audit()
        expected = self.script["audit"]
        actual = [(str(r.event), sorted(r.detail.keys())) for r in records]
        wanted = [(entry["event"], entry["keys"]) for entry in expected]
        if actual != wanted:
            self.fail(
                "audit sequence mismatch:\n  expected "
                f"{[e for e, _ in wanted]}\n  actual   {[e for e, _ in actual]}\n"
                f"  expected keys {wanted}\n  actual keys   {actual}"
            )
            return
        for record, entry in zip(records, expected, strict=True):
            for key, value in entry.get("match", {}).items():
                actual_value = record.detail.get(key)
                if actual_value != value:
                    self.fail(
                        f"audit seq {record.seq} {record.event}: detail[{key!r}] "
                        f"{actual_value!r} != {value!r}"
                    )
        verification = audit_engine.verify_chain(records)
        self.expect(verification.ok, "the scenario's own audit chain must verify")


@lru_cache(maxsize=1)
def compute_m4() -> list[ScenarioOutcome]:
    corpus.ensure_corpus()
    scripts = corpus.load_fixture("consent_scenarios.json")
    return [_ScenarioRunner(script).run() for script in scripts]


def m4_pass_rate(outcomes: Sequence[ScenarioOutcome]) -> float:
    return sum(1 for o in outcomes if o.passed) / len(outcomes)


def m4_naive_pass_rate(outcomes: Sequence[ScenarioOutcome]) -> float:
    """Decision-only scoring of the naive 'a verified consent row exists' gate."""
    return sum(1 for o in outcomes if o.naive_passed) / len(outcomes)


# --------------------------------------------------------------------------- #
# M5 — audit tamper detection
# --------------------------------------------------------------------------- #


def _m5_now(step: int) -> str:
    hour, minute = divmod(step, 60)
    return f"2026-07-02T{9 + hour:02d}:{minute:02d}:00Z"


@lru_cache(maxsize=1)
def m5_session() -> list:
    """Run the scripted 30-event session and return its audit records."""
    corpus.ensure_corpus()
    home = Path(tempfile.mkdtemp(prefix="voicekin-m5-"))
    service = VoiceKinService(
        repository=SQLiteRepository.in_memory(),
        data_home=home,
        calibration=calibration(),
        statement_template=load_statement_template(),
    )
    try:
        step = iter(range(200))
        now = lambda: _m5_now(next(step))  # noqa: E731

        service.initialize("Eval Operator", now())
        service.create_profile("p1", "Sam", "self", now())                      # 1
        service.add_samples(
            "p1", [corpus.wav_path(r) for r in corpus.roles("S03", "enroll")], now()
        )                                                                       # 2-4
        service.draft_consent(
            "p1", [Context.ANNOUNCEMENT, Context.REMINDER], None, 4815162342, now()
        )                                                                       # 5
        service.grant_consent("p1", corpus.wav_path("S03/consent/0"), now())    # 6
        (home / "sink").mkdir(parents=True, exist_ok=True)
        service.add_target("sink", TargetKind.FILE_SINK, {"dir": str(home / "sink")}, now())
        first = service.synthesize(
            "p1", "dinner is ready", Context.ANNOUNCEMENT, 7, now()
        )                                                                       # 7-8
        service.deliver(first.utterance.id, "sink", now())                      # 9
        service.revoke_consent("p1", "session revocation", now())               # 10
        service.synthesize("p1", "the laundry is done", Context.ANNOUNCEMENT, 7, now())  # 11
        service.deliver(first.utterance.id, "sink", now())                      # 12 (refused)

        service.create_profile("p2", "Ana", "partner", now())                   # 13
        service.add_samples("p2", [corpus.wav_path("S01/support/0")], now())    # 14 (too_short)
        service.add_samples(
            "p2", [corpus.wav_path(r) for r in corpus.roles("S05", "enroll")], now()
        )                                                                       # 15-17
        service.draft_consent("p2", [Context.ANNOUNCEMENT], None, 90210, now()) # 18
        service.grant_consent("p2", corpus.wav_path("S05-sib-vtl/consent/0"), now())  # 19
        service.set_enabled("p2", False, now())                                 # 20
        service.set_enabled("p2", True, now())                                  # 21
        service.draft_consent("p2", [Context.ANNOUNCEMENT], None, 31337, now()) # 22
        service.grant_consent("p2", corpus.wav_path("S05/consent/0"), now())    # 23
        service.synthesize("p2", "hello from ana", Context.ANNOUNCEMENT, 3, now())  # 24-25
        service.add_samples("p2", [corpus.wav_path("S05/probe/0")], now())      # 26
        service.synthesize("p2", "hello again", Context.ANNOUNCEMENT, 3, now()) # 27
        drift = [
            s for s in service.repository.list_samples("p2") if s.sample_index == 4
        ][0]
        service.remove_sample("p2", drift.id, now())                            # 28
        service.purge_profile("p2", now())                                      # 29-30
        records = service.repository.list_audit()
        return records
    finally:
        service.repository.close()
        shutil.rmtree(home, ignore_errors=True)


def _apply_mutation(records: list, mutation: dict[str, Any]) -> list:
    """Apply one committed tamper case to a copy of the session's records."""
    from voicekin.models import AuditRecord

    kind = mutation["kind"]
    rows = [r.model_copy() for r in records]
    if kind == "edit_detail":
        index = mutation["seq"] - 1
        detail = dict(rows[index].detail)
        detail[mutation["key"]] = mutation["value"]
        rows[index] = rows[index].model_copy(update={"detail": detail})
        return rows
    if kind == "edit_ts":
        index = mutation["seq"] - 1
        rows[index] = rows[index].model_copy(update={"ts": mutation["value"]})
        return rows
    if kind == "delete":
        return [r for r in rows if r.seq != mutation["seq"]]
    if kind == "swap_contents":
        a, b = mutation["seq_a"] - 1, mutation["seq_b"] - 1
        fields = ("ts", "event", "profile_id", "consent_id", "utterance_id",
                  "detail", "prev_hash", "record_hash")
        swapped_a = rows[a].model_copy(update={f: getattr(rows[b], f) for f in fields})
        swapped_b = rows[b].model_copy(update={f: getattr(rows[a], f) for f in fields})
        rows[a], rows[b] = swapped_a, swapped_b
        return rows
    if kind == "rehash_without_prev":
        import hashlib

        index = mutation["seq"] - 1
        row = rows[index]
        payload = audit_engine.canonical_json(
            {
                "seq": row.seq, "ts": row.ts, "event": str(row.event),
                "profile_id": row.profile_id, "consent_id": row.consent_id,
                "utterance_id": row.utterance_id, "detail": dict(row.detail),
            }
        )
        rows[index] = row.model_copy(
            update={"record_hash": hashlib.sha256(payload).hexdigest()}
        )
        return rows
    if kind == "truncate_and_forge":
        keep = [r for r in rows if r.seq < mutation["from_seq"]]
        forged = AuditRecord(
            seq=mutation["from_seq"],
            ts="2026-07-02T12:00:00Z",
            event="synthesis_authorized",
            profile_id="p2",
            detail={"context": "announcement", "scope": ["announcement"],
                    "enrollment_fingerprint": "0" * 64},
            prev_hash="0" * 64,
            record_hash="f" * 64,
        )
        return [*keep, forged]
    if kind == "truncate_and_recompute":
        keep = [r for r in rows if r.seq < mutation["from_seq"]]
        tail = [r for r in rows if r.seq >= mutation["from_seq"]]
        head = keep[-1] if keep else None
        rebuilt = list(keep)
        for position, row in enumerate(tail):
            detail = dict(row.detail)
            if position == 0 and mutation["edit_key"] in detail:
                detail[mutation["edit_key"]] = mutation["edit_value"]
            head = audit_engine.build_record(
                head,
                ts=row.ts,
                event=row.event,
                profile_id=row.profile_id,
                consent_id=row.consent_id,
                utterance_id=row.utterance_id,
                detail=detail,
            )
            rebuilt.append(head)
        return rebuilt
    raise ValueError(f"unknown mutation kind {kind!r}")


@dataclass(frozen=True)
class M5Result:
    m5: float
    outcomes: list[tuple[str, bool]]  # (case name, matched expectation)
    clean_ok: bool


@lru_cache(maxsize=1)
def compute_m5() -> M5Result:
    fixture = corpus.load_fixture("tamper_cases.json")
    records = m5_session()
    assert len(records) == fixture["session_events"], (
        f"session produced {len(records)} events, fixture expects {fixture['session_events']}"
    )
    clean_ok = audit_engine.verify_chain(records).ok
    outcomes: list[tuple[str, bool]] = []
    for case in fixture["cases"]:
        mutated = _apply_mutation(records, case["mutation"])
        verification = audit_engine.verify_chain(mutated)
        expected = case["expected"]
        matched = verification.ok == expected["ok"]
        if not expected["ok"]:
            matched = matched and verification.bad_seq == expected["bad_seq"]
        outcomes.append((case["name"], matched))
    score = (int(clean_ok) + sum(1 for _, ok in outcomes if ok)) / (1 + len(outcomes))
    return M5Result(m5=score, outcomes=outcomes, clean_ok=clean_ok)


def compute_m5_baseline() -> float:
    """Live baseline: no chain — 'verification' only checks that rows exist."""
    fixture = corpus.load_fixture("tamper_cases.json")
    records = m5_session()

    def naive_verify(rows: list) -> bool:
        return len(rows) > 0

    clean_ok = naive_verify(records)  # trivially matches the clean expectation
    matched = int(clean_ok)
    for case in fixture["cases"]:
        mutated = _apply_mutation(records, case["mutation"])
        matched += int(naive_verify(mutated) == case["expected"]["ok"])
    return matched / (1 + len(fixture["cases"]))


# --------------------------------------------------------------------------- #
# M6 — enrollment coherence decision quality
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class M6Result:
    m6_pure: float
    m6_mixed: float
    pure_scores: list[float]
    mixed_scores: list[float]


def _loo_min(roles: Sequence[str]) -> float:
    cal = calibration()
    embeddings = [corpus.embedding(role, cal) for role in roles]
    return min(leave_one_out_scores(embeddings, score_scale=cal.score_scale))


@lru_cache(maxsize=1)
def compute_m6() -> M6Result:
    cal = calibration()
    fixture = corpus.load_fixture("coherence_sets.json")
    pure = [_loo_min(entry["roles"]) for entry in fixture["pure"]]
    mixed = [
        _loo_min([*entry["own_roles"], entry["foreign_role"]]) for entry in fixture["mixed"]
    ]
    return M6Result(
        m6_pure=sum(1 for s in pure if s >= cal.theta_enroll) / len(pure),
        m6_mixed=sum(1 for s in mixed if s < cal.theta_enroll) / len(mixed),
        pure_scores=pure,
        mixed_scores=mixed,
    )


def compute_m6_baseline() -> tuple[float, float]:
    """Live baseline: accept every set — the no-coherence-check gate.

    Evaluated by scoring every committed set against a threshold of -inf, so
    the numbers come from the same score pass as the metric."""
    result = compute_m6()
    pure = sum(1 for s in result.pure_scores if s >= float("-inf")) / len(result.pure_scores)
    mixed = sum(1 for s in result.mixed_scores if not s >= float("-inf")) / len(
        result.mixed_scores
    )
    return pure, mixed


# --------------------------------------------------------------------------- #
# Scorecard
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateRow:
    metric: str
    value: float
    gate: str
    passed: bool
    baseline: str
    note: str = ""


def _gate(metric: str, value: float, passed: bool, gate: str, baseline: str,
          note: str = "") -> GateRow:
    return GateRow(metric=metric, value=value, gate=gate, passed=passed,
                   baseline=baseline, note=note)


GATES: dict[str, Callable[[float], bool]] = {
    "M1a": lambda v: v <= 0.05,
    "M1b": lambda v: v == 0.0,
    "M1c": lambda v: v <= 0.12,
    "M2a": lambda v: v == 0.0,
    "M2b_clean": lambda v: v >= 0.95,
    "M2b_all": lambda v: v >= 0.85,
    "M3": lambda v: v >= 0.93,
    "M4": lambda v: v == 1.0,
    "M5": lambda v: v == 1.0,
    "M6_pure": lambda v: v == 1.0,
    "M6_mixed": lambda v: v == 1.0,
}


def compute_scorecard() -> list[GateRow]:
    corpus.ensure_corpus()
    m1 = compute_m1()
    base_m1a, base_m1c = compute_m1_baseline()
    base_m1b = compute_m1b_baseline()
    m2 = compute_m2()
    m2_base = compute_m2_baselines()
    m3 = compute_m3()
    m3_base = compute_m3_baseline()
    m4 = compute_m4()
    m5 = compute_m5()
    m5_base = compute_m5_baseline()
    m6 = compute_m6()
    base_pure, base_mixed = compute_m6_baseline()

    m4_value = m4_pass_rate(m4)
    rows = [
        _gate("M1a", m1.m1a, GATES["M1a"](m1.m1a), "<= 0.05",
              f"2-dim energy/duration EER {base_m1a:.3f}",
              f"same-channel EER, {len(m1.genuine_clean)} genuine / {len(m1.impostor)} impostor"),
        _gate("M1b", m1.m1b, GATES["M1b"](m1.m1b), "= 0",
              "pitch-only max FAR "
              f"{max(base_m1b.values()):.2f} "
              f"(f0 {base_m1b['single_f0']:.2f} vtl {base_m1b['single_vtl']:.2f} "
              f"tilt {base_m1b['single_tilt']:.2f})",
              "max per-axis FAR@theta over "
              + ", ".join(f"{k.split('_')[1]} {v:.2f}" for k, v in m1.m1b_by_axis.items())),
        _gate("M1c", m1.m1c, GATES["M1c"](m1.m1c), "<= 0.12",
              f"2-dim baseline EER {base_m1c:.3f}", "cross-channel EER"),
        _gate("M2a", float(m2.m2a_accepts), GATES["M2a"](float(m2.m2a_accepts)), "= 0",
              f"accept-all: {m2_base['m2a_accept_all']:.0f}/{m2.pooled_size}",
              f"impostor accepts over the pooled {m2.pooled_size}"),
        _gate("M2b_clean", m2.m2b_clean, GATES["M2b_clean"](m2.m2b_clean), ">= 0.95",
              f"reject-all: {m2_base['m2b_reject_all']:.2f}", "clean genuine accepts / 24"),
        _gate("M2b_all", m2.m2b_all, GATES["M2b_all"](m2.m2b_all), ">= 0.85",
              f"reject-all: {m2_base['m2b_reject_all']:.2f}", "all genuine accepts / 48"),
        _gate("M3", m3.m3, GATES["M3"](m3.m3), ">= 0.93",
              f"fixed-voice stub {m3_base:.3f}",
              f"{m3.correct}/{m3.trials} attributions, mean SECS {m3.mean_secs:.3f}"),
        _gate("M4", m4_value, GATES["M4"](m4_value), "= 1.00",
              f"naive verified-row gate {m4_naive_pass_rate(m4):.2f} (decision-only)",
              f"{sum(1 for o in m4 if o.passed)}/{len(m4)} scenarios"),
        _gate("M5", m5.m5, GATES["M5"](m5.m5), "= 1.00",
              f"no-chain verify {m5_base:.2f}",
              f"clean chain {'ok' if m5.clean_ok else 'BAD'} + "
              f"{sum(1 for _, ok in m5.outcomes if ok)}/{len(m5.outcomes)} cases"),
        _gate("M6_pure", m6.m6_pure, GATES["M6_pure"](m6.m6_pure), "= 1.00",
              f"accept-all {base_pure:.2f}", "pure sets accepted / 24"),
        _gate("M6_mixed", m6.m6_mixed, GATES["M6_mixed"](m6.m6_mixed), "= 1.00",
              f"accept-all {base_mixed:.2f}", "mixed sets rejected / 24"),
    ]
    return rows


__all__ = [
    "GATES",
    "GateRow",
    "M1Result",
    "M2Result",
    "M3Result",
    "M5Result",
    "M6Result",
    "M3_TEXTS",
    "ScenarioOutcome",
    "calibration",
    "compute_m1",
    "compute_m1_baseline",
    "compute_m1b_baseline",
    "compute_m2",
    "compute_m2_baselines",
    "compute_m3",
    "compute_m3_baseline",
    "compute_m4",
    "compute_m5",
    "compute_m5_baseline",
    "compute_m6",
    "compute_m6_baseline",
    "compute_scorecard",
    "eer",
    "far",
    "frr",
    "m4_naive_pass_rate",
    "m4_pass_rate",
    "m5_session",
]

"""Metric implementations for the flowlist eval suite (EVALS.md §3).

Every formula here is the one written down in EVALS.md.  The suite is
hermetic: committed fixtures, offline adapters, seeded randomness, and a
literal timestamp — nothing reads the network or the clock.

Two metric families, per EVALS §2:

* **spec conformance** (M1, M3) pins the engine to externally documented DJ
  practice, encoded as hand-authored *data*;
* **behavioral** (M2, M4-M7) measures emergent behaviour the rulebook does not
  encode — ranking labelled pairs, and recovering near-optimal orderings
  against independent ground truth.
"""

from __future__ import annotations

import itertools
import json
import random
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from flowlist.adapters.metadata import FixtureMetadataProvider
from flowlist.engine.keys import camelot, camelot_to_pitch_class, key_name, key_score
from flowlist.engine.models import (
    SEAMLESS_THRESHOLD,
    ArcProfile,
    FeatureSnapshot,
    FeatureSource,
    PlaylistSource,
    ReorderParams,
    Track,
    TransitionWeights,
)
from flowlist.engine.optimizer import exact_optimal, reorder
from flowlist.engine.scoring import (
    bpm_component,
    build_matrix,
    energy_component,
    loudness_component,
    pair_score,
    score_order,
    transition_score,
)
from flowlist.store.memory import InMemoryRepository

from flowlist import services

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PLAYLIST_DIR = FIXTURES / "playlists"

#: A literal timestamp: the suite never reads a clock (CONVENTIONS 3).
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: Gates from EVALS.md §5.  ``None`` marks a report-only row.
GATES: dict[str, float | None] = {
    "M1_key_relation_accuracy": 1.00,
    "M2_pair_ranking_auc": 0.90,
    "M2b_anti_gaming_margin": 0.10,
    "M3_component_monotonicity": 1.00,
    "M4_exact_optimality_mean": 0.97,
    "M4_exact_optimality_min": 0.90,
    "M5_planted_chain_recovery": 0.92,
    "M5_planted_chain_recovery_min": 0.85,
    "M6_baseline_margin": 0.08,
    "M6_baseline_margin_min": 0.0,
    "M7_determinism": 1.00,
}

#: Seeds the ``random`` baseline averages over (EVALS §3-M6).
RANDOM_SEEDS = tuple(range(20))
#: Seed every gated reorder uses, and the one the golden orderings pin.
EVAL_SEED = 7


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class MetricResult:
    name: str
    value: float
    gate: float | None
    passed: bool
    detail: str = ""

    @property
    def report_only(self) -> bool:
        return self.gate is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": round(self.value, 6),
            "gate": self.gate,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class EvalReport:
    metrics: list[MetricResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(m.passed for m in self.metrics if not m.report_only)

    def add(self, result: MetricResult) -> MetricResult:
        self.metrics.append(result)
        return result

    def get(self, name: str) -> MetricResult:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.all_passed,
            "metrics": [m.to_dict() for m in self.metrics],
        }


def gated(name: str, value: float, detail: str = "", *, minimum: bool = True) -> MetricResult:
    """Build a :class:`MetricResult` against the EVALS §5 gate for ``name``."""
    gate = GATES[name]
    if gate is None:
        return MetricResult(name, value, None, True, detail)
    passed = value >= gate if minimum else value <= gate
    # M6_min's gate is a strict "> 0"; everything else is ">=".
    if name == "M6_baseline_margin_min":
        passed = value > gate
    return MetricResult(name, value, gate, passed, detail)


def report_only(name: str, value: float, detail: str = "") -> MetricResult:
    return MetricResult(name, value, None, True, detail)


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fixture:
    """One committed playlist fixture with its features resolved."""

    id: str
    suite: str
    track_ids: tuple[str, ...]
    features: tuple[FeatureSnapshot, ...]
    start: int | None
    end: int | None
    planted_order: tuple[int, ...] | None

    @property
    def n(self) -> int:
        return len(self.track_ids)


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    """The committed synthetic catalog, keyed by track id."""
    data = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["tracks"]}


@lru_cache(maxsize=1)
def _catalog_provider() -> FixtureMetadataProvider:
    """The offline adapter the evals resolve features through (CONVENTIONS 3)."""
    return FixtureMetadataProvider.from_path(FIXTURES / "catalog.json", analyzed_at=EPOCH)


@lru_cache(maxsize=1)
def fixtures() -> tuple[Fixture, ...]:
    """Every committed playlist fixture, in file-name order.

    Features are read through :class:`FixtureMetadataProvider` — the offline
    adapter CONVENTIONS 3 makes the default — rather than straight out of the
    JSON, so the evals exercise the same provider path the product uses.
    """
    rows = catalog()
    provider = _catalog_provider()
    out: list[Fixture] = []
    for path in sorted(PLAYLIST_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        track_ids = tuple(payload["tracks"])
        tracks = [
            Track(
                id=track_id,
                title=rows[track_id]["title"],
                artist=rows[track_id]["artist"],
                created_at=EPOCH,
            )
            for track_id in track_ids
        ]
        served = provider.get_features(tracks)
        features = tuple(served[track_id].snapshot() for track_id in track_ids)
        planted = payload.get("planted_order")
        out.append(
            Fixture(
                id=payload["id"],
                suite=payload["suite"],
                track_ids=track_ids,
                features=features,
                start=payload["anchors"]["start"],
                end=payload["anchors"]["end"],
                planted_order=None if planted is None else tuple(planted),
            )
        )
    return tuple(out)


def suite(name: str) -> list[Fixture]:
    return [f for f in fixtures() if f.suite == name]


def by_id(fixture_id: str) -> Fixture:
    for fixture in fixtures():
        if fixture.id == fixture_id:
            return fixture
    raise KeyError(fixture_id)


@cache
def matrix_of(fixture_id: str) -> tuple[tuple[float, ...], ...]:
    """The O(n^2) score matrix under default weights and the neutral profile."""
    fixture = by_id(fixture_id)
    rows = build_matrix(list(fixture.features))
    return tuple(tuple(row) for row in rows)


@lru_cache(maxsize=1)
def expected() -> dict[str, Any]:
    """Generator-measured stats. Informational only — never a gate input."""
    return json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def golden_orderings() -> dict[str, list[str]]:
    payload = json.loads((FIXTURES / "golden_orderings.json").read_text(encoding="utf-8"))
    return payload["orderings"]


@lru_cache(maxsize=1)
def key_relation_table() -> dict[str, Any]:
    return json.loads((FIXTURES / "key_relations.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def transition_pairs() -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / "transition_pairs.json").read_text(encoding="utf-8"))
    return payload["cases"]


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def order_mean(order: Sequence[int], matrix: Sequence[Sequence[float]]) -> float:
    """``total(pi) / (n - 1)`` — EVALS §3's ordering score."""
    if len(order) < 2:
        return 0.0
    return sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1)) / (len(order) - 1)


def order_total(order: Sequence[int], matrix: Sequence[Sequence[float]]) -> float:
    return sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1))


def auc(positive: Sequence[float], negative: Sequence[float]) -> float:
    """EVALS §3-M2: ties count half, so the metric is threshold-free."""
    if not positive or not negative:
        return 0.0
    hits = 0.0
    for x in positive:
        for y in negative:
            if x > y:
                hits += 1.0
            elif x == y:
                hits += 0.5
    return hits / (len(positive) * len(negative))


# ------------------------------- baselines (EVALS §3-M6, computed live) ----- #


def identity_order(fixture: Fixture) -> list[int]:
    """The stored order — a seeded shuffle for the generated suites."""
    return list(range(fixture.n))


def random_orders(fixture: Fixture, seeds: Iterable[int] = RANDOM_SEEDS) -> list[list[int]]:
    orders = []
    for seed in seeds:
        working = list(range(fixture.n))
        random.Random(seed).shuffle(working)
        orders.append(working)
    return orders


def bpm_sort_order(fixture: Fixture) -> list[int]:
    """Ascending BPM; tracks with no tempo sort last, ties by index.

    The strongest naive strategy: it buys tempo continuity outright and, given
    the generator's BPM-energy correlation, much of energy continuity too.
    """

    def key(index: int) -> tuple[int, float, int]:
        bpm = fixture.features[index].bpm
        return (1, 0.0, index) if bpm is None else (0, bpm, index)

    return sorted(range(fixture.n), key=key)


def heuristic_order(fixture: Fixture, seed: int = EVAL_SEED) -> list[int]:
    """What the system under test proposes for this fixture."""
    return reorder(matrix_of(fixture.id), seed=seed, start=fixture.start, end=fixture.end).order


# --------------------------------------------------------------------------- #
# M1 — key_relation_accuracy (H1)
# --------------------------------------------------------------------------- #


def m1_key_relation_accuracy() -> MetricResult:
    """Engine relation and score versus the hand-authored Camelot chart."""
    table = key_relation_table()
    total = 0
    correct = 0
    failures: list[str] = []

    for case in table["relations"]:
        total += 1
        from_pc, from_mode = camelot_to_pitch_class(case["from"])
        to_pc, to_mode = camelot_to_pitch_class(case["to"])
        result = key_score(from_pc, from_mode, to_pc, to_mode)
        ok = (
            result.relation.value == case["expected_relation"]
            and result.score is not None
            and abs(result.score - case["expected_score"]) <= 1e-9
        )
        correct += int(ok)
        if not ok:
            failures.append(
                f"{case['from']}->{case['to']}: expected "
                f"{case['expected_relation']}/{case['expected_score']}, got "
                f"{result.relation.value}/{result.score}"
            )

    for case in table["conversions"]:
        total += 1
        forward = camelot(case["pitch_class"], case["mode"]) == case["camelot"]
        backward = camelot_to_pitch_class(case["camelot"]) == (
            case["pitch_class"],
            case["mode"],
        )
        named = key_name(case["pitch_class"], case["mode"]) == case["key_name"]
        ok = forward and backward and named
        correct += int(ok)
        if not ok:
            failures.append(f"conversion {case['camelot']} <-> {case['key_name']} round-trip")

    value = correct / total if total else 0.0
    detail = f"{correct}/{total} cases"
    if failures:
        detail += "; " + "; ".join(failures[:3])
    return gated("M1_key_relation_accuracy", value, detail)


# --------------------------------------------------------------------------- #
# M2 — pair_ranking_auc (H1)
# --------------------------------------------------------------------------- #

_LABEL_PAIRS = (("seamless", "workable"), ("workable", "clash"), ("seamless", "clash"))


def _pair_features(side: dict[str, Any]) -> FeatureSnapshot:
    return FeatureSnapshot(
        bpm=side["bpm"],
        key_pc=side["key_pc"],
        mode=side["mode"],
        energy=side["energy"],
        loudness_db=side["loudness_db"],
    )


def pair_ranking_auc(weights: TransitionWeights | None = None) -> tuple[float, dict[str, float]]:
    """Mean of the three ordered-label AUCs, plus the individual values."""
    normalized = (weights or TransitionWeights()).normalized()
    scores: dict[str, list[float]] = {"seamless": [], "workable": [], "clash": []}
    for case in transition_pairs():
        value = pair_score(
            _pair_features(case["from"]),
            _pair_features(case["to"]),
            normalized,
            ArcProfile.NEUTRAL,
        )
        scores[case["label"]].append(value)
    per_pair = {f"{hi}>{lo}": auc(scores[hi], scores[lo]) for hi, lo in _LABEL_PAIRS}
    return sum(per_pair.values()) / len(per_pair), per_pair


def bpm_only_auc() -> float:
    """M2 recomputed with ``{bpm: 1}`` — the naive tempo-only scorer."""
    value, _ = pair_ranking_auc(
        TransitionWeights(key=0.0, bpm=1.0, energy=0.0, loudness=0.0, danceability=0.0)
    )
    return value


def m2_pair_ranking_auc() -> tuple[MetricResult, MetricResult, float]:
    value, per_pair = pair_ranking_auc()
    naive = bpm_only_auc()
    counts = {label: 0 for label in ("seamless", "workable", "clash")}
    for case in transition_pairs():
        counts[case["label"]] += 1
    detail = (
        ", ".join(f"{k}={v:.3f}" for k, v in per_pair.items())
        + f"; n={sum(counts.values())} ({counts['seamless']}/{counts['workable']}/{counts['clash']})"
        + f"; bpm_only_auc={naive:.3f}"
    )
    m2 = gated("M2_pair_ranking_auc", value, detail)
    m2b = gated(
        "M2b_anti_gaming_margin",
        value - naive,
        f"M2 {value:.3f} - bpm_only {naive:.3f}",
    )
    return m2, m2b, naive


# --------------------------------------------------------------------------- #
# M3 — component_monotonicity (H1)
# --------------------------------------------------------------------------- #


def _bpm_at(pct: float) -> float:
    """Score of a transition whose tempo deviates by ``pct`` percent."""
    outcome = bpm_component(120.0, 120.0 * (1.0 + pct / 100.0))
    assert outcome.score is not None
    return outcome.score


def m3_checks() -> list[tuple[str, bool]]:
    """Every property sweep EVALS §3-M3 names, as ``(name, passed)`` pairs."""
    checks: list[tuple[str, bool]] = []
    weights = TransitionWeights()

    # -- D3: BPM proximity ---------------------------------------------------
    sweep = [round(0.5 * step, 1) for step in range(31)]  # 0 .. 15%
    values = [_bpm_at(pct) for pct in sweep]
    checks.append(
        (
            "bpm_non_increasing_over_0_to_15pct",
            all(a >= b - 1e-12 for a, b in itertools.pairwise(values)),
        )
    )
    for pct in (0.0, 1.0, 2.0):
        checks.append((f"bpm_is_1.0_at_{pct:g}pct", abs(_bpm_at(pct) - 1.0) <= 1e-12))
    for pct in (12.0, 13.0, 15.0):
        checks.append((f"bpm_is_0_at_{pct:g}pct", abs(_bpm_at(pct)) <= 1e-12))
    checks.append(("bpm_is_0.75_at_4pct", abs(_bpm_at(4.0) - 0.75) <= 1e-9))
    checks.append(("bpm_is_0.5_at_6pct", abs(_bpm_at(6.0) - 0.5) <= 1e-9))

    # -- D3: octave folding --------------------------------------------------
    same = bpm_component(86.0, 86.0)
    folded = bpm_component(86.0, 172.0)
    near = bpm_component(86.0, 150.0)
    assert same.score is not None and folded.score is not None and near.score is not None
    checks.append(
        ("fold_86_to_172_pays_only_the_0.9_penalty", abs(folded.score - 0.9 * same.score) <= 1e-12)
    )
    checks.append(("fold_86_to_172_beats_86_to_150", folded.score > near.score))
    half_time = transition_score(
        FeatureSnapshot(bpm=86.0, key_pc=9, mode=0, energy=0.6, loudness_db=-8.0),
        FeatureSnapshot(bpm=172.0, key_pc=9, mode=0, energy=0.6, loudness_db=-8.0),
        weights,
    )
    checks.append(("half_time_flag_is_set", "half_time" in half_time.flags))

    # -- D4: energy continuity ----------------------------------------------
    deltas = [round(0.02 * step, 2) for step in range(31)]  # 0 .. 0.60
    energies = [energy_component(0.5, min(1.0, 0.5 + d)) for d in deltas]
    checks.append(
        (
            "energy_non_increasing_in_abs_delta",
            all(a >= b - 1e-12 for a, b in itertools.pairwise(energies)),
        )
    )
    checks.append(("energy_is_0_at_delta_0.5", energy_component(0.4, 0.9) == 0.0))
    up = energy_component(0.5, 0.7, ArcProfile.BUILD)
    down = energy_component(0.5, 0.3, ArcProfile.BUILD)
    assert up is not None and down is not None
    checks.append(("build_penalises_a_drop_more_than_a_rise", down < up))
    cool_up = energy_component(0.5, 0.7, ArcProfile.COOL)
    cool_down = energy_component(0.5, 0.3, ArcProfile.COOL)
    assert cool_up is not None and cool_down is not None
    checks.append(("cool_penalises_a_rise_more_than_a_drop", cool_up < cool_down))
    # Compared at D6's 12-decimal precision: 0.7 - 0.5 and 0.5 - 0.3 differ in
    # the last bit as floats, which is a property of IEEE arithmetic, not of
    # the formula.
    rise = energy_component(0.5, 0.7)
    drop = energy_component(0.5, 0.3)
    assert rise is not None and drop is not None
    checks.append(("neutral_is_symmetric", abs(rise - drop) <= 1e-12))
    neutral_pair = transition_score(
        FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.5, loudness_db=-8.0),
        FeatureSnapshot(bpm=124.0, key_pc=4, mode=0, energy=0.3, loudness_db=-8.0),
        weights,
    )
    build_pair = transition_score(
        FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.5, loudness_db=-8.0),
        FeatureSnapshot(bpm=124.0, key_pc=4, mode=0, energy=0.3, loudness_db=-8.0),
        weights,
        ArcProfile.BUILD,
    )
    checks.append(
        (
            "profile_only_touches_the_energy_component",
            all(
                neutral_pair.components[name] == build_pair.components[name]
                for name in ("key", "bpm", "loudness")
            )
            and build_pair.components["energy"] != neutral_pair.components["energy"],
        )
    )

    # -- D5: loudness --------------------------------------------------------
    for db in (0.0, 1.0, 2.0):
        checks.append((f"loudness_is_1.0_at_{db:g}dB", loudness_component(-8.0, -8.0 + db) == 1.0))
    for db in (10.0, 12.0):
        checks.append((f"loudness_is_0_at_{db:g}dB", loudness_component(-16.0, -16.0 + db) == 0.0))
    checks.append(
        ("loudness_is_0.5_at_6dB", abs((loudness_component(-14.0, -8.0) or 0.0) - 0.5) <= 1e-12)
    )
    ramp = [loudness_component(-20.0, -20.0 + d) for d in range(0, 13)]
    checks.append(
        (
            "loudness_non_increasing",
            all(a >= b - 1e-12 for a, b in itertools.pairwise(ramp)),
        )
    )

    # -- D10: missing data ---------------------------------------------------
    no_key = transition_score(
        FeatureSnapshot(bpm=124.0, energy=0.6, loudness_db=-8.0),
        FeatureSnapshot(bpm=124.0, key_pc=9, mode=0, energy=0.6, loudness_db=-8.0),
        weights,
    )
    checks.append(("missing_key_scores_exactly_0.5", no_key.components["key"] == 0.5))
    checks.append(("missing_key_is_flagged", "missing_key" in no_key.flags))
    no_bpm = transition_score(
        FeatureSnapshot(key_pc=9, mode=0, energy=0.6, loudness_db=-8.0),
        FeatureSnapshot(key_pc=9, mode=0, energy=0.6, loudness_db=-8.0),
        weights,
    )
    checks.append(("missing_bpm_scores_exactly_0.5", no_bpm.components["bpm"] == 0.5))
    checks.append(("missing_bpm_is_flagged", "missing_bpm" in no_bpm.flags))
    checks.append(
        (
            "missing_data_keeps_the_total_in_unit_range",
            all(0.0 <= t.score <= 1.0 for t in (no_key, no_bpm, half_time)),
        )
    )

    # -- FR-6: weight algebra ------------------------------------------------
    a = FeatureSnapshot(bpm=120.0, key_pc=9, mode=0, energy=0.30, loudness_db=-14.0)
    b = FeatureSnapshot(bpm=127.0, key_pc=2, mode=1, energy=0.75, loudness_db=-4.0)
    bpm_only = TransitionWeights(key=0.0, bpm=1.0, energy=0.0, loudness=0.0, danceability=0.0)
    outcome = bpm_component(a.bpm, b.bpm)
    assert outcome.score is not None
    checks.append(
        (
            "bpm_only_weights_reproduce_the_bpm_component",
            abs(pair_score(a, b, bpm_only.normalized(), ArcProfile.NEUTRAL) - outcome.score)
            <= 1e-12,
        )
    )
    scaled = TransitionWeights(key=3.5, bpm=3.5, energy=2.0, loudness=1.0, danceability=0.0)
    checks.append(
        (
            "scaling_every_weight_is_a_no_op",
            pair_score(a, b, scaled.normalized(), ArcProfile.NEUTRAL)
            == pair_score(a, b, weights.normalized(), ArcProfile.NEUTRAL),
        )
    )
    zero_weighted = transition_score(a, b, weights)
    checks.append(
        (
            "zero_weight_component_is_reported_null_without_a_flag",
            zero_weighted.components["danceability"] is None
            and "missing_danceability" not in zero_weighted.flags,
        )
    )
    return checks


def m3_component_monotonicity() -> MetricResult:
    checks = m3_checks()
    passed = sum(1 for _, ok in checks if ok)
    failures = [name for name, ok in checks if not ok]
    detail = f"{passed}/{len(checks)} property checks"
    if failures:
        detail += "; failed: " + ", ".join(failures[:4])
    return gated("M3_component_monotonicity", passed / len(checks), detail)


# --------------------------------------------------------------------------- #
# M4 — exact_optimality_ratio (H2)
# --------------------------------------------------------------------------- #


class ExactSolverBroken(AssertionError):
    """Raised when the heuristic 'beats' Held-Karp — proof the DP is wrong."""


def m4_exact_optimality() -> tuple[MetricResult, MetricResult]:
    """Ratio of the heuristic's total to fixed-endpoint Held-Karp ground truth.

    Recomputed at eval time, never stored, and hard-failed if any ratio exceeds
    1 — a heuristic that beats the exact optimum proves the DP is broken, which
    is exactly the degenerate case EVALS §3-M4 guards against.
    """
    ratios: dict[str, float] = {}
    for fixture in suite("exact"):
        matrix = matrix_of(fixture.id)
        optimum = exact_optimal(matrix, fixture.start, fixture.end).total
        found = round(order_total(heuristic_order(fixture), matrix), 12)
        ratio = found / optimum if optimum else 1.0
        if ratio > 1.0 + 1e-9:
            raise ExactSolverBroken(
                f"{fixture.id}: heuristic total {found!r} exceeds the exact optimum "
                f"{optimum!r} (ratio {ratio:.9f}); exact_optimal is broken"
            )
        ratios[fixture.id] = ratio

    values = list(ratios.values())
    worst = min(ratios, key=lambda k: ratios[k])
    detail = f"{len(values)} instances, n=8-14; worst {worst}={ratios[worst]:.4f}"
    return (
        gated("M4_exact_optimality_mean", sum(values) / len(values), detail),
        gated("M4_exact_optimality_min", min(values), f"worst instance {worst}"),
    )


def greedy_reference_m4() -> tuple[float, float]:
    """What M4 would score for the committed reference greedy (informational).

    Read from ``expected.json``: this is a *fixture* property (EVALS §4's
    discrimination invariant), not a measurement of the system under test.
    """
    rows = expected()["playlists"]
    ratios = [row["greedy_only_ratio"] for pid, row in rows.items() if pid.startswith("exact_")]
    return sum(ratios) / len(ratios), min(ratios)


# --------------------------------------------------------------------------- #
# M5 — planted_chain_recovery (H2)
# --------------------------------------------------------------------------- #


def m5_planted_recovery() -> tuple[MetricResult, MetricResult, float]:
    """Heuristic mean over planted-chain mean, clamped at 1.0 before averaging."""
    ratios: dict[str, float] = {}
    for fixture in suite("planted"):
        assert fixture.planted_order is not None
        matrix = matrix_of(fixture.id)
        planted = order_mean(fixture.planted_order, matrix)
        found = order_mean(heuristic_order(fixture), matrix)
        ratios[fixture.id] = found / planted if planted else 1.0

    clamped = [min(1.0, r) for r in ratios.values()]
    worst = min(ratios, key=lambda k: ratios[k])
    detail = f"{len(ratios)} chains, n=50-150; unclamped " + ", ".join(
        f"{k}={v:.3f}" for k, v in ratios.items()
    )
    greedy_recovery = sum(
        expected()["playlists"][fid]["greedy_only_recovery"] for fid in ratios
    ) / len(ratios)
    return (
        gated("M5_planted_chain_recovery", sum(clamped) / len(clamped), detail),
        gated("M5_planted_chain_recovery_min", min(ratios.values()), f"worst instance {worst}"),
        greedy_recovery,
    )


# --------------------------------------------------------------------------- #
# M6 — baseline_margin (H2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MessyRow:
    fixture_id: str
    identity: float
    random_mean: float
    bpm_sort: float
    heuristic: float
    min_before: float
    min_after: float
    seamless_after: int
    transitions: int

    @property
    def margin(self) -> float:
        return self.heuristic - self.bpm_sort


def messy_rows() -> list[MessyRow]:
    """Live baselines for the messy suite — nothing is read from expected.json."""
    rows: list[MessyRow] = []
    for fixture in suite("messy"):
        matrix = matrix_of(fixture.id)
        stored = identity_order(fixture)
        proposed = heuristic_order(fixture)
        before = score_order(stored, list(fixture.features))
        after = score_order(proposed, list(fixture.features))
        rows.append(
            MessyRow(
                fixture_id=fixture.id,
                identity=order_mean(stored, matrix),
                random_mean=sum(order_mean(o, matrix) for o in random_orders(fixture))
                / len(RANDOM_SEEDS),
                bpm_sort=order_mean(bpm_sort_order(fixture), matrix),
                heuristic=order_mean(proposed, matrix),
                min_before=before.min_score,
                min_after=after.min_score,
                seamless_after=after.seamless,
                transitions=len(after.transitions),
            )
        )
    return rows


def m6_baseline_margin(rows: Sequence[MessyRow]) -> tuple[MetricResult, MetricResult]:
    margins = [row.margin for row in rows]
    detail = ", ".join(f"{row.fixture_id}={row.margin:+.3f}" for row in rows)
    return (
        gated("M6_baseline_margin", sum(margins) / len(margins), detail),
        gated(
            "M6_baseline_margin_min",
            min(margins),
            f"worst {min(rows, key=lambda r: r.margin).fixture_id}",
        ),
    )


# --------------------------------------------------------------------------- #
# M7 — determinism (FR-15)
# --------------------------------------------------------------------------- #


def _persist(fixture: Fixture) -> tuple[InMemoryRepository, str]:
    """Import a fixture through the real store so runs can be checked (FR-11)."""
    repo = InMemoryRepository()
    rows = catalog()
    tracks = [
        {
            "title": rows[tid]["title"],
            "artist": rows[tid]["artist"],
            "album": rows[tid]["album"],
            "duration_ms": rows[tid]["duration_ms"],
            "features": rows[tid]["features"],
        }
        for tid in fixture.track_ids
    ]
    imported = services.read_playlist_content(
        json.dumps({"name": fixture.id, "tracks": tracks}),
        name=fixture.id,
        fmt=PlaylistSource.JSON,
    )
    result = services.import_playlist(
        repo,
        imported,
        now=EPOCH,
        id_factory=_counter_ids(),
    )
    return repo, result.playlist.id


def _counter_ids() -> Any:
    """Deterministic id factory: the eval must not depend on uuid4."""
    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"fx-{counter['n']:05d}"

    return next_id


def m7_determinism() -> tuple[MetricResult, list[str]]:
    """Repeatability, run-aggregate consistency, and the committed goldens."""
    notes: list[str] = []
    ok = True
    goldens = golden_orderings()

    for fixture_id in sorted(goldens):
        fixture = by_id(fixture_id)
        matrix = matrix_of(fixture_id)

        first = reorder(matrix, seed=EVAL_SEED, start=fixture.start, end=fixture.end).order
        second = reorder(matrix, seed=EVAL_SEED, start=fixture.start, end=fixture.end).order
        if first != second:
            ok = False
            notes.append(f"{fixture_id}: seed {EVAL_SEED} is not repeatable")

        # A different seed may differ; it must still be a valid permutation.
        other = reorder(matrix, seed=EVAL_SEED + 1, start=fixture.start, end=fixture.end).order
        if sorted(other) != list(range(fixture.n)):
            ok = False
            notes.append(f"{fixture_id}: seed {EVAL_SEED + 1} did not return a permutation")

        if [fixture.track_ids[i] for i in first] != goldens[fixture_id]:
            ok = False
            notes.append(f"{fixture_id}: ordering differs from golden_orderings.json")

        # FR-11: a run's stored aggregates must be recomputable from the
        # per-transition breakdowns it persisted.
        repo, playlist_id = _persist(fixture)
        outcome = services.reorder_playlist(
            repo,
            playlist_id,
            ReorderParams(seed=EVAL_SEED, start_entry=None, end_entry=None),
            now=EPOCH,
            precedence=(FeatureSource.IMPORT,),
            id_factory=_counter_ids(),
        )
        rebuilt = services.rebuild_after_report(services.load_run(repo, outcome.run.id))
        stored = outcome.run
        consistent = (
            rebuilt.total == stored.score_total_after
            and rebuilt.mean == stored.score_mean_after
            and rebuilt.min_score == stored.score_min_after
            and rebuilt.seamless == stored.seamless_after
            and rebuilt.cliffs == stored.cliff_after
        )
        if not consistent:
            ok = False
            notes.append(f"{fixture_id}: run aggregates disagree with their stored breakdowns")
        entry_order = [e.entry_id for e in outcome.run_entries]
        # import_playlist mints the entry ids first, then the playlist id.
        expected_order = [f"fx-{i + 1:05d}" for i in first]
        if entry_order != expected_order:
            ok = False
            notes.append(f"{fixture_id}: the persisted run disagrees with the pure-engine order")

    detail = f"{len(goldens)} fixtures: repeatability, run-aggregate consistency, goldens"
    if notes:
        detail += "; " + "; ".join(notes[:3])
    return gated("M7_determinism", 1.0 if ok else 0.0, detail), notes


# --------------------------------------------------------------------------- #
# Report-only rows (EVALS §3)
# --------------------------------------------------------------------------- #


def report_rows(rows: Sequence[MessyRow], greedy_recovery: float) -> list[MetricResult]:
    worst_improvement = sum(row.min_after - row.min_before for row in rows) / len(rows)
    seamless_fraction = sum(row.seamless_after for row in rows) / sum(
        row.transitions for row in rows
    )
    greedy_mean, greedy_min = greedy_reference_m4()

    largest = max(suite("planted"), key=lambda f: f.n)
    matrix = matrix_of(largest.id)  # built outside the timed section
    started = time.perf_counter()
    reorder(matrix, seed=EVAL_SEED)
    elapsed = time.perf_counter() - started

    return [
        report_only(
            "worst_transition_improvement",
            worst_improvement,
            "mean (min after - min before) on the messy suite",
        ),
        report_only(
            "seamless_fraction_after",
            seamless_fraction,
            f"transitions >= {SEAMLESS_THRESHOLD:.2f} on the messy suite",
        ),
        report_only(
            "greedy_only_recovery",
            greedy_recovery,
            "reference construction-only greedy on the planted suite (from expected.json)",
        ),
        report_only(
            "greedy_only_M4_mean",
            greedy_mean,
            f"reference greedy would score M4_mean={greedy_mean:.4f} / "
            f"M4_min={greedy_min:.4f} - it fails both gates, which is what makes M4 bite",
        ),
        report_only(
            "reorder_wall_time_s",
            elapsed,
            f"{largest.id} (n={largest.n}), informational; the n=500 NFR is a slow-marked test",
        ),
    ]


def baseline_rows(rows: Sequence[MessyRow]) -> list[MetricResult]:
    """The naive strategies EVALS §5 names, computed live on the messy suite."""
    return [
        report_only(
            "baseline_identity",
            sum(row.identity for row in rows) / len(rows),
            "stored (shuffled) order",
        ),
        report_only(
            "baseline_random",
            sum(row.random_mean for row in rows) / len(rows),
            f"mean over {len(RANDOM_SEEDS)} seeded shuffles",
        ),
        report_only(
            "baseline_bpm_sort",
            sum(row.bpm_sort for row in rows) / len(rows),
            "ascending BPM - the strongest naive strategy",
        ),
        report_only(
            "heuristic_messy_mean",
            sum(row.heuristic for row in rows) / len(rows),
            "the system under test on the same playlists",
        ),
    ]


# --------------------------------------------------------------------------- #
# Full evaluation
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def evaluate() -> EvalReport:
    """Run every metric once; cached so gates and the scorecard agree and are cheap."""
    report = EvalReport()
    report.add(m1_key_relation_accuracy())
    m2, m2b, _ = m2_pair_ranking_auc()
    report.add(m2)
    report.add(m2b)
    report.add(m3_component_monotonicity())
    m4_mean, m4_min = m4_exact_optimality()
    report.add(m4_mean)
    report.add(m4_min)
    m5, m5_min, greedy_recovery = m5_planted_recovery()
    report.add(m5)
    report.add(m5_min)
    rows = messy_rows()
    m6, m6_min = m6_baseline_margin(rows)
    report.add(m6)
    report.add(m6_min)
    report.add(m7_determinism()[0])
    for extra in baseline_rows(rows) + report_rows(rows, greedy_recovery):
        report.add(extra)
    return report


def fixture_invariants() -> dict[str, Any]:
    """The fixture properties EVALS §4 requires CI to re-assert."""
    rows = expected()["playlists"]
    ratios = {
        pid: row["greedy_only_ratio"] for pid, row in rows.items() if pid.startswith("exact_")
    }
    greedy_mean, greedy_min = greedy_reference_m4()
    return {
        "bpm_only_auc": bpm_only_auc(),
        "greedy_only_ratios": ratios,
        "instances_below_0.97": sum(1 for r in ratios.values() if r < 0.97),
        "greedy_M4_mean": greedy_mean,
        "greedy_M4_min": greedy_min,
        "planted": {
            pid: (row["planted_mean"], row["planted_min"])
            for pid, row in rows.items()
            if "planted_mean" in row
        },
    }


__all__ = [
    "EPOCH",
    "GATES",
    "EvalReport",
    "ExactSolverBroken",
    "Fixture",
    "MessyRow",
    "MetricResult",
    "auc",
    "bpm_only_auc",
    "bpm_sort_order",
    "by_id",
    "catalog",
    "evaluate",
    "expected",
    "fixture_invariants",
    "fixtures",
    "golden_orderings",
    "greedy_reference_m4",
    "heuristic_order",
    "identity_order",
    "key_relation_table",
    "m1_key_relation_accuracy",
    "m2_pair_ranking_auc",
    "m3_checks",
    "m3_component_monotonicity",
    "m4_exact_optimality",
    "m5_planted_recovery",
    "m6_baseline_margin",
    "m7_determinism",
    "matrix_of",
    "messy_rows",
    "order_mean",
    "order_total",
    "pair_ranking_auc",
    "random_orders",
    "suite",
    "transition_pairs",
]

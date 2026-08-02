"""The Almanac eval scorecard.

    uv run python almanac/evals/run.py

Runs S1-S4 across seeds 7/8/9, computes M1-M9 plus the report-only diagnostics,
prints one line per metric with its value, gate and PASS/FAIL, then the pool
histogram, the M8 confusion matrix and a JSON summary.  Exit code 0 iff every
gate passes.

Hermetic by construction: offline adapters, in-memory store, committed
fixtures, hash-keyed determinism.  No network, no wall clock.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct `python evals/run.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import metrics as M
from evals.faulty_personalizer import apply_case, load_cases, load_clean
from evals.simulate import load_scenarios, simulate

#: EVALS.md section 5: statistical gates take the worst value across these seeds.
SEEDS = (7, 8, 9)
PRIMARY_SEED = 7
#: EVALS.md M3: each gated scenario must supply at least this many windows.
MIN_WINDOWS = 60
#: EVALS.md M6, re-derived at the fixtures' realized stretch factor (REVIEW.md
#: B1, corrected in H1).  Empirical sensitivity across seeds 7/8/9 on S1+S2:
#: correct implementation 0.382-0.516, flat-streak *tracking* removed (which
#: silences both the demotion and the archive-candidate signal) 0.668-0.718,
#: whole grade table flattened 0.797-0.883.  The gate sits between the correct
#: worst case and the half-broken best case, so it fails both of those
#: degradations at every seed.  The narrower "demotion branch alone removed"
#: variant measures 0.413-0.563 and does NOT fail this gate — the persona
#: archives the duds the still-live archive-candidate signal flags — but it is
#: caught deterministically by M7c's independent re-fold (REVIEW.md H1).
M6_GATE = 0.60
RULE = "=" * 96


def _suggester():
    """The FR-3 suggester, injected so `metrics.py` stays engine-free."""
    from almanac.datasets import default_datasets
    from almanac.engine.themes import suggest_themes

    themes = default_datasets().themes

    def suggest(text: str) -> list[str]:
        return [s.theme_id for s in suggest_themes(themes, text, (), None)]

    return suggest


def _validator():
    from almanac.engine.validate import validate_prompt
    from almanac.models import PromptKind

    def validate(text: str, kind: str, excerpt: str | None):
        verdict = validate_prompt(text, PromptKind(kind), excerpt)
        return verdict.ok, verdict.check

    return validate


def run_all(scenarios: dict, seeds=SEEDS) -> dict:
    """Every timeline the scorecard needs: {(scenario, seed, policy): Timeline}."""
    runs: dict[tuple[str, int, str], object] = {}
    for name, scenario in scenarios.items():
        for seed in seeds:
            runs[(name, seed, "almanac")] = simulate(scenario, seed)
    for name, scenario in scenarios.items():
        for policy in ("random_eligible", "fifo_rotation"):
            runs[(name, PRIMARY_SEED, policy)] = simulate(scenario, PRIMARY_SEED, policy=policy)
    return runs


def _policy_runs(runs: dict, policy: str, seed: int, names=None) -> list:
    return [
        timeline
        for (name, run_seed, run_policy), timeline in runs.items()
        if run_policy == policy and run_seed == seed and (names is None or name in names)
    ]


def score_policy(runs: dict, policy: str, params: dict) -> dict:
    """The gated numbers for one selection policy, worst-of-seeds where EVALS says so."""
    seeds = SEEDS if policy == "almanac" else (PRIMARY_SEED,)
    out: dict[str, object] = {}

    per_seed_a = [
        M.m2a_stream_debut_latency(_policy_runs(runs, policy, s, {"S1", "S2"})) for s in seeds
    ]
    out["m2a_p50"] = max(row[0] for row in per_seed_a)
    out["m2a_p95"] = max(row[1] for row in per_seed_a)
    out["m2a_n"] = per_seed_a[0][2]

    drains = [M.m2b_backlog_drain(_policy_runs(runs, policy, s)) for s in seeds]
    out["m2b"] = min(row[0] for row in drains)
    out["m2b_detail"] = drains[0][1]
    out["m2_hard"] = max(M.m2_hard_bound(_policy_runs(runs, policy, s))[0] for s in seeds)
    out["m2_hard_detail"] = M.m2_hard_bound(_policy_runs(runs, policy, PRIMARY_SEED))[1][:3]

    mix = []
    windows = []
    for seed in seeds:
        for name in ("S1", "S2"):
            value, count = M.m3_mix_balance(runs[(name, seed, policy)], params)
            mix.append(value)
            windows.append((name, seed, count))
    out["m3"] = max(mix)
    out["m3_windows"] = min(count for _, _, count in windows)
    out["m3_window_detail"] = {f"{n}/s{s}": c for n, s, c in windows if s == PRIMARY_SEED}

    out["m4a"] = max(
        M.m4a_early_violations(_policy_runs(runs, policy, s, {"S1", "S4"}), params)[0]
        for s in seeds
    )
    out["m4a_detail"] = M.m4a_early_violations(
        _policy_runs(runs, policy, PRIMARY_SEED, {"S1", "S4"}), params
    )[1][:3]
    b_values = [
        M.m4b_proportional_fidelity(_policy_runs(runs, policy, s, {"S1", "S4"}), params)
        for s in seeds
    ]
    out["m4b"] = max(row[0] for row in b_values)
    out["m4b_n"] = b_values[0][1]
    out["m4b_median"] = b_values[0][2]

    m5 = [M.m5_pinned_recurrence(_policy_runs(runs, policy, s, {"S1", "S2", "S4"})) for s in seeds]
    out["m5"] = min(row[0] for row in m5)
    out["m5_intervals"] = m5[0][1]
    out["m5_detail"] = m5[0][2][:3]

    m6 = [M.m6_feedback_responsiveness(_policy_runs(runs, policy, s, {"S1", "S2"})) for s in seeds]
    out["m6"] = max(row[0] for row in m6)
    out["m6_detail"] = m6[0][1]
    sub, offenders = M.m6_archive_subcheck(_policy_runs(runs, policy, PRIMARY_SEED), params)
    out["m6_subcheck"] = sub
    out["m6_subcheck_detail"] = offenders[:3]
    return out


def build_report(runs: dict, params: dict) -> M.EvalReport:
    report = M.EvalReport()
    almanac = score_policy(runs, "almanac", params)
    baselines = {
        policy: score_policy(runs, policy, params)
        for policy in ("random_eligible", "fifo_rotation")
    }

    def versus(key: str, fmt: str = "{:.3g}") -> str:
        return "baselines " + ", ".join(
            f"{policy}={fmt.format(values[key]) if isinstance(values[key], float) else values[key]}"
            for policy, values in baselines.items()
        )

    m1 = M.m1_constraint_compliance(_policy_runs(runs, "almanac", PRIMARY_SEED), params)
    report.add(m1)

    report.add(
        M.MetricResult(
            "M2a stream_debut p50",
            almanac["m2a_p50"],
            "<= 7 d",
            almanac["m2a_p50"] <= 7,
            f"n={almanac['m2a_n']} worst of seeds {SEEDS}; " + versus("m2a_p50"),
        )
    )
    report.add(
        M.MetricResult(
            "M2a stream_debut p95",
            almanac["m2a_p95"],
            "<= 21 d",
            almanac["m2a_p95"] <= 21,
            versus("m2a_p95"),
        )
    )
    report.add(
        M.MetricResult(
            "M2b backlog_drain",
            almanac["m2b"],
            "== 1.0",
            almanac["m2b"] >= 1.0,
            f"{almanac['m2b_detail']}; " + versus("m2b"),
        )
    )
    report.add(
        M.MetricResult(
            "M2 hard bound >180d",
            almanac["m2_hard"],
            "== 0",
            almanac["m2_hard"] == 0,
            f"{almanac['m2_hard_detail']}; " + versus("m2_hard"),
        )
    )
    report.add(
        M.MetricResult(
            "M3 mix_balance",
            almanac["m3"],
            "<= 0.12",
            almanac["m3"] <= 0.12,
            f"rho={params['rho']}; " + versus("m3"),
        )
    )
    report.add(
        M.MetricResult(
            "M3 qualifying windows",
            almanac["m3_windows"],
            ">= 60",
            almanac["m3_windows"] >= MIN_WINDOWS,
            f"per scenario at seed {PRIMARY_SEED}: {almanac['m3_window_detail']}",
        )
    )
    report.add(
        M.MetricResult(
            "M4a early_violations",
            almanac["m4a"],
            "== 0",
            almanac["m4a"] == 0,
            f"{almanac['m4a_detail']}; " + versus("m4a"),
        )
    )
    report.add(
        M.MetricResult(
            "M4b proportional_fidelity",
            almanac["m4b"],
            "<= 3.0",
            almanac["m4b"] <= 3.0,
            f"n={almanac['m4b_n']} median O={almanac['m4b_median']:.2f}; " + versus("m4b"),
        )
    )
    report.add(
        M.MetricResult(
            "M5 pinned_recurrence",
            almanac["m5"],
            "== 1.0",
            almanac["m5"] >= 1.0,
            f"{almanac['m5_intervals']} intervals; {almanac['m5_detail']}; " + versus("m5"),
        )
    )
    report.add(
        M.MetricResult(
            "M6 feedback_responsiveness",
            almanac["m6"],
            f"<= {M6_GATE}",
            almanac["m6"] <= M6_GATE,
            f"{almanac['m6_detail']}; " + versus("m6"),
        )
    )
    report.add(
        M.MetricResult(
            "M6 archive sub-check",
            almanac["m6_subcheck"],
            "== 1.0",
            almanac["m6_subcheck"] >= 1.0,
            str(almanac["m6_subcheck_detail"]),
        )
    )

    # --- M7 determinism ---------------------------------------------------
    scenarios = load_scenarios()
    repeat = simulate(scenarios["S1"], PRIMARY_SEED)
    same = repeat.fingerprint() == runs[("S1", PRIMARY_SEED, "almanac")].fingerprint()
    differs = (
        runs[("S1", 8, "almanac")].fingerprint()
        != runs[("S1", PRIMARY_SEED, "almanac")].fingerprint()
    )
    replay = [
        offender
        for name in ("S1", "S2", "S3", "S4")
        for offender in M.m7_replay_equality(runs[(name, PRIMARY_SEED, "almanac")], params)
    ]
    checkpoints = M.m7_checkpoint_replay(runs[("S1", PRIMARY_SEED, "almanac")], params)
    with tempfile.TemporaryDirectory() as tmp:
        roundtrip = simulate(
            scenarios["S1"],
            PRIMARY_SEED,
            roundtrip_path=Path(tmp) / "roundtrip.db",
            roundtrip_day=180,
        )
    survives = roundtrip.fingerprint() == runs[("S1", PRIMARY_SEED, "almanac")].fingerprint()
    m7 = 1.0 if (same and differs and not replay and not checkpoints and survives) else 0.0
    report.add(
        M.MetricResult(
            "M7 determinism_replay",
            m7,
            "== 1.0",
            m7 >= 1.0,
            f"a(repeat)={same} b(seed 8 differs)={differs} "
            f"c(fold equality)={not replay} d(sqlite round-trip)={survives}"
            + (f" offenders={replay[:2]}" if replay else ""),
        )
    )

    # --- M8 theme suggestion ---------------------------------------------
    labeled = M.load_labeled()
    suggest = _suggester()
    held = M.m8_theme_accuracy([r for r in labeled if r["split"] == "held"], suggest)
    dev = M.m8_theme_accuracy([r for r in labeled if r["split"] == "dev"], suggest)
    uniform = 1 / len(M.load_theme_ids())
    report.add(
        M.MetricResult(
            "M8 top1 (held)",
            held["top1"],
            ">= 0.55",
            held["top1"] >= 0.55,
            f"n={held['n']}  uniform/majority baseline={uniform:.3f}  dev={dev['top1']:.3f}",
        )
    )
    report.add(
        M.MetricResult(
            "M8 hit3 (held)",
            held["hit3"],
            ">= 0.80",
            held["hit3"] >= 0.80,
            f"dev={dev['hit3']:.3f}",
        )
    )
    report.add(
        M.MetricResult(
            "M8 worst_theme_hit3",
            held["worst_theme_hit3"],
            ">= 0.50",
            held["worst_theme_hit3"] >= 0.50,
            "min over the 16 themes of hit3 within that theme's 6 held quotes",
        )
    )

    # --- M9 personalizer validator ---------------------------------------
    m9 = M.m9_validator(load_cases(), load_clean(), apply_case, _validator())
    report.add(
        M.MetricResult(
            "M9 recall",
            m9["recall"],
            "== 1.0",
            m9["recall"] >= 1.0,
            f"40 scripted mutations; escaped={m9['escaped']} wrong_check={m9['wrong_check']}",
        )
    )
    report.add(
        M.MetricResult(
            "M9 fpr",
            m9["fpr"],
            "== 0.0",
            m9["fpr"] <= 0.0,
            f"20 clean personalizations; rejected={m9['false_positives']}",
        )
    )

    report.diagnostics = {
        "m8_confusion": held["confusion"],
        "m8_per_theme_held": held["per_theme"],
        "m8_dev": {"top1": dev["top1"], "hit3": dev["hit3"]},
        "m9_boundary": f"{m9['off_spec_accepted']}/{m9['off_spec_total']} accepted",
        "baselines": baselines,
        "almanac": almanac,
    }
    return report


def print_scorecard(runs: dict, report: M.EvalReport, params: dict) -> None:
    print(RULE)
    print("ALMANAC — eval scorecard")
    print(
        f"scenarios S1-S4 x seeds {SEEDS} + 2 naive baselines   params {params['params_version']}"
    )
    print(RULE)
    for result in report.results:
        print(result.line())

    print()
    print("report-only diagnostics")
    print("-" * 96)
    for name in ("S1", "S2", "S3", "S4"):
        timeline = runs[(name, PRIMARY_SEED, "almanac")]
        histogram = M.pool_histogram(timeline)
        total = sum(histogram.values()) or 1
        shares = "  ".join(f"{pool}={count / total:.2f}" for pool, count in histogram.items())
        print(f"  {name} pool_histogram   {shares}")
        print(
            f"  {name} kind_coverage={M.kind_coverage(timeline):.2f}"
            f"  stretch_lambda={timeline.stats_lambda:.2f}"
            f"  entries={len(timeline.entries)}  surfacings={len(timeline.surfacings)}"
        )
    print(
        f"  novelty_share_curve (S1, sigma in [{params['rho'] - 0.15:.2f}, {params['rho'] + 0.15:.2f}])"
        f" |{M.novelty_sparkline(runs[('S1', PRIMARY_SEED, 'almanac')], params)}|"
    )
    print(
        "  seed_sensitivity (S1, seeds 7 vs 8) = "
        f"{M.seed_sensitivity(runs[('S1', 7, 'almanac')], runs[('S1', 8, 'almanac')]):.2f}"
    )
    print(f"  validator_boundary: {report.diagnostics['m9_boundary']} off-spec cases accepted")
    print(f"  M8 dev split: {report.diagnostics['m8_dev']}")

    print()
    print("M8 confusion (label -> rank-1 suggestion, held split; . = correct)")
    print("-" * 96)
    themes = sorted(M.load_theme_ids())
    confusion = report.diagnostics["m8_confusion"]
    header = "".join(f"{t[:3]:>4}" for t in themes)
    print(f"  {'':<26}{header}")
    for label in themes:
        cells = "".join(
            f"{('.' if label == predicted else confusion.get((label, predicted), 0)) if (label == predicted or confusion.get((label, predicted))) else '':>4}"
            for predicted in themes
        )
        missing = confusion.get((label, "(none)"), 0)
        print(f"  {label:<26}{cells}   {'none=' + str(missing) if missing else ''}")

    print()
    print(RULE)
    verdict = "ALL GATES PASS" if report.passed else "GATES FAILED"
    print(f"{verdict}: {sum(1 for r in report.results if r.passed)}/{len(report.results)}")
    print(RULE)
    summary = {
        result.name: {"value": result.value, "gate": result.gate, "passed": result.passed}
        for result in report.results
    }
    print(json.dumps({"passed": report.passed, "metrics": summary}, indent=1, default=str))


def main() -> int:
    params = M.load_params()
    runs = run_all(load_scenarios())
    report = build_report(runs, params)
    print_scorecard(runs, report, params)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

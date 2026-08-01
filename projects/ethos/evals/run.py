"""Ethos eval scorecard — zero configuration.

    uv run python ethos/evals/run.py                  # scorecard
    uv run python ethos/evals/run.py --sweep          # (tau, kappa) tradeoff curve
    uv run python ethos/evals/run.py --write-baselines # regenerate evals/baselines.json

Validates the corpus (C-gates), builds the index, runs M1-M5 and D0 with offline
components only, prints every metric with its recorded baseline and gate, and
exits non-zero if anything fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ethos.corpus import default_data_dir, load_corpus  # noqa: E402
from ethos.engine.router import build_index, index_to_json  # noqa: E402

from evals import corpus_gates as gates  # noqa: E402
from evals import metrics as M  # noqa: E402

BASELINES = ROOT / "evals" / "baselines.json"

GATES: list[tuple[str, str, float]] = [
    ("M1-direct", ">=", 0.97),
    ("M1-coll", ">=", 0.85),
    ("M1b", ">=", 0.70),
    ("M1b'", ">=", 0.55),
    ("M1gap", "<=", 0.15),
    ("M1c", ">=", 0.95),
    ("M1d", ">=", 0.85),
    ("M2a", ">=", 0.80),
    ("M2a_near", ">=", 0.68),
    ("M2b", "<=", 0.05),
    ("M3", "==", 1.0),
    ("M4a", "==", 1.0),
    ("M4b", "==", 0.0),
    ("M5", "==", 1.0),
]

BASELINE_OF = {
    "M1-direct": "baseline_title_overlap",
    "M1-coll": "baseline_title_overlap",
    "M1b": "baseline_title_overlap",
    "M1b'": "baseline_title_overlap",
    "M1a": "baseline_title_overlap",
    "M1c": "baseline_title_overlap",
    "M1d": "baseline_title_overlap",
    "M2a": "baseline_s1_zero_abstain",
    "M2a_near": "baseline_s1_zero_abstain",
    "M2b": "baseline_s1_zero_abstain",
    "M4a": "baseline_no_verifier",
    "M4b": "baseline_reject_all",
    "M5": "baseline_first_tradition_only",
}

FIXTURE_SETS = (
    "routing_questions",
    "oblique_holdout",
    "ambiguous_questions",
    "oos_questions",
)


def load_fixtures() -> dict[str, list]:
    return {name: gates.load_fixture(f"{name}.json") for name in FIXTURE_SETS}


def measure(verbose: bool = True) -> dict[str, object]:
    """Everything the scorecard, the gate tests and the baselines file need."""
    corpus = load_corpus(default_data_dir())
    fixtures = load_fixtures()
    harness = M.RouterHarness(corpus)
    values: dict[str, float] = {}
    values.update(M.m1_suite(harness, fixtures))
    values.update(M.m2_suite(harness, fixtures))

    m3, m3_failures = M.m3_citation_integrity(corpus, harness, fixtures, default_data_dir())
    values["M3"] = m3
    polish_cases = gates.load_fixture("polish_cases.json")
    m4 = M.m4_tamper(corpus, polish_cases)
    values["M4a"] = m4["M4a"]
    values["M4b"] = m4["M4b"]
    m5, m5_problems = M.m5_completeness(corpus)
    values["M5"] = m5

    title = M.BaselineTitleOverlap(corpus)
    null_rule = M.BaselineS1ZeroAbstain(harness)
    baselines: dict[str, float] = {}
    for key, value in M.m1_suite(title, fixtures).items():
        baselines[f"baseline_title_overlap:{key}"] = value
    for key, value in M.m2_suite(null_rule, fixtures).items():
        baselines[f"baseline_s1_zero_abstain:{key}"] = value
    baselines.update(
        {f"{k}:M4": v for k, v in M.m4_baselines(corpus, polish_cases).items()}
    )
    first_only, _ = M.m5_completeness(corpus, M.BaselineFirstTraditionOnly(corpus))
    baselines["baseline_first_tradition_only:M5"] = first_only

    record = dict(gates.freeze_record(corpus))
    record.update(baselines)
    record.update({f"measured:{key}": value for key, value in values.items()})

    oos_s1, direct_s1 = M.c20_scores(harness, fixtures)
    gate_results = gates.run_corpus_gates(corpus)
    gate_results["C20"] = gates.check_c20(corpus, oos_s1, direct_s1)
    gate_results["C17"] = safeguard_matrix(corpus)
    gate_results["D0"] = determinism_checks(corpus)
    return {
        "corpus": corpus,
        "values": values,
        "baselines": baselines,
        "gates": gate_results,
        "problems": {"M3": m3_failures, "M4": m4["problems"], "M5": m5_problems},
        "freeze": record,
        "harness": harness,
        "fixtures": fixtures,
        "verbose": verbose,
    }


def safeguard_matrix(corpus) -> list[str]:
    """C17 — the safeguard block in every cell of the matrix (EVALS § C17)."""
    from evals.safeguard_matrix import build_cells

    return gates.check_c17_safeguard_cells(build_cells(corpus))


def determinism_checks(corpus) -> list[str]:
    """D0(c) index byte-stability and D0(d) stored-render reproduction."""
    from ethos.engine.compose import render_text

    errors: list[str] = []
    first = index_to_json(build_index(corpus.topics, corpus.router_config, corpus.stopwords))
    second = index_to_json(build_index(corpus.topics, corpus.router_config, corpus.stopwords))
    if first != second:
        errors.append("D0(c): rebuilding the index produced a different serialization")
    titles = {t.id: t.title for t in corpus.topics}
    names = {t.id: t.name for t in corpus.traditions}
    for topic in corpus.topics[:6]:
        body, text = M.compose_unverified(corpus, topic.id, None)
        if render_text(body, titles, names) != text:
            errors.append(f"D0(d): re-rendering {topic.id} is not byte-identical")
    return errors


def scorecard(result: dict[str, object]) -> int:
    values: dict[str, float] = result["values"]  # type: ignore[assignment]
    baselines: dict[str, float] = result["baselines"]  # type: ignore[assignment]
    recorded = json.loads(BASELINES.read_text(encoding="utf-8")) if BASELINES.exists() else {}
    failures = 0
    print("ethos — eval scorecard\n")
    print(f"{'metric':<12}{'baseline':>10}{'value':>9}{'gate':>14}  result")
    print("-" * 60)
    for name, op, threshold in GATES:
        value = values[name]
        baseline_key = BASELINE_OF.get(name)
        baseline = recorded.get(f"{baseline_key}:{name}") if baseline_key else None
        if baseline is None and baseline_key:
            baseline = baselines.get(f"{baseline_key}:{name}")
        ok = {
            ">=": value >= threshold - 1e-9,
            "<=": value <= threshold + 1e-9,
            "==": abs(value - threshold) < 1e-9,
        }[op]
        failures += not ok
        shown = f"{baseline:.3f}" if isinstance(baseline, (int, float)) else "—"
        print(
            f"{name:<12}{shown:>10}{value:>9.3f}{op + ' ' + format(threshold, '.2f'):>14}"
            f"  {'PASS' if ok else 'FAIL'}"
        )
    print(f"{'M1a':<12}{'—':>10}{values['M1a']:>9.3f}{'report only':>14}  —")
    print("-" * 60)
    for gate, errors in sorted(result["gates"].items(), key=lambda kv: (len(kv[0]), kv[0])):  # type: ignore[union-attr]
        status = "PASS" if not errors else "FAIL"
        failures += bool(errors)
        print(f"{gate:<12}{'':>10}{'':>9}{'all pass':>14}  {status}")
        for error in errors[:5]:
            print(f"    {error}")
    for label, problems in result["problems"].items():  # type: ignore[union-attr]
        for problem in problems[:5]:
            print(f"    {label}: {problem}")
    c19 = gates.check_c19(result["freeze"])  # type: ignore[arg-type]
    failures += bool(c19)
    print(f"{'C19':<12}{'':>10}{'':>9}{'all pass':>14}  {'PASS' if not c19 else 'FAIL'}")
    for error in c19[:5]:
        print(f"    {error}")
    print()
    print("all gates pass" if not failures else f"{failures} gate(s) FAILED")
    return 0 if not failures else 1


def write_baselines(result: dict[str, object]) -> int:
    payload = dict(result["freeze"])  # type: ignore[arg-type]
    BASELINES.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {BASELINES} ({len(payload)} entries)")
    return 0


def print_sweep(result: dict[str, object]) -> int:
    rows = M.sweep(
        result["harness"],  # type: ignore[arg-type]
        result["fixtures"],  # type: ignore[arg-type]
        taus=(0.0, 2.0, 4.0, 6.0),
        kappas=(0.10, 0.20, 0.25, 0.30, 0.35, 0.40),
    )
    holdout_key = "M1b'"
    print(f"{'tau':>5}{'kappa':>7}{'tune':>8}{'M1b':>8}{'M1b-hd':>8}{'M2a':>8}{'M2b':>8}")
    for row in rows:
        print(
            f"{row['tau']:>5.1f}{row['kappa']:>7.2f}{row['tune_acc']:>8.3f}"
            f"{row['M1b']:>8.3f}{row[holdout_key]:>8.3f}"
            f"{row['M2a']:>8.3f}{row['M2b']:>8.3f}"
        )
    return 0


def main(argv: list[str]) -> int:
    result = measure()
    if "--write-baselines" in argv:
        return write_baselines(result)
    if "--sweep" in argv:
        return print_sweep(result)
    return scorecard(result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

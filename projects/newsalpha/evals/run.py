#!/usr/bin/env python3
"""NewsAlpha eval scorecard (EVALS.md "How the suite runs").

Zero configuration: loads the committed fixture corpus, runs the full pipeline
with offline adapters into an in-memory store, backtests over the five committed
market seeds plus fifteen placebo realizations, and prints every metric with its
gate and PASS/FAIL.  Exits non-zero on any gate failure.

Naive baselines are **computed live** from the same fixtures, never hardcoded, so
the scorecard always shows how far the implementation is above the shortcut each
gate exists to rule out.

    uv run python newsalpha/evals/run.py
    uv run python newsalpha/evals/run.py --regen-check   # re-derive + diff the fixtures
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics as M  # noqa: E402  (path shim above must run first)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    value: float
    gate: float
    comparison: str
    baseline: str
    note: str = ""

    @property
    def passing(self) -> bool:
        if self.comparison == ">=":
            return self.value >= self.gate
        if self.comparison == "<=":
            return self.value <= self.gate
        return abs(self.value - self.gate) < 1e-12

    def gate_text(self) -> str:
        return f"{self.comparison} {self.gate:g}"


def _fmt(value: float) -> str:
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def build_gates() -> tuple[list[Gate], dict[str, Any]]:
    val = M.m1a("val")
    dev = M.m1a("dev")
    m1b, m1c, m1_failures = M.m1b_m1c("val")
    naive_m1a_macro, naive_m1a_floor = M.naive_m1a("val")
    naive_m1b, naive_m1c = M.naive_m1b_m1c("val")
    m2a_score, m2a_counts = M.m2a("val")
    m2b_score, m2b_wrong = M.m2b()
    m3_score, m3_wrong = M.m3()
    m4_score, m4_per_seed = M.m4()
    m4_gap, m4_dev, m4_val = M.m4_transfer()
    m5_score, m5_per_seed = M.m5()
    m6_score, m6_per_seed, m6_occ = M.m6()
    m7a, m7b, placebo_hits, placebo_ics = M.m7()
    m8_score, m8_outcomes = M.m8()
    g1_value = M.g1()
    g2_value, g2_reasons = M.g2()
    g2p_value, g2p_reasons = M.g2_placebo()
    capture_rate, captured, capture_n = M.announcement_capture_rate()
    naive_m6_score, naive_m6_occ = M.naive_m6()
    naive_m7a, naive_m7b = M.naive_m7()
    expected_abstentions, other_abstentions, abstention_notes = M.abstentions()

    gates = [
        Gate("M1a  event macro-F1 (VAL)", val.macro_f1, 0.80, ">=", _fmt(naive_m1a_macro)),
        Gate("M1a-floor  min per-type F1", val.floor, 0.65, ">=", _fmt(naive_m1a_floor)),
        Gate(
            "M1a-transfer  DEV - VAL",
            dev.macro_f1 - val.macro_f1,
            0.10,
            "<=",
            ">= 0.40",
            f"DEV {dev.macro_f1:.4f} / VAL {val.macro_f1:.4f}",
        ),
        Gate("M1b  stage+attrs, conditional", m1b, 0.85, ">=", _fmt(naive_m1b)),
        Gate("M1c  stage+attrs, unconditional", m1c, 0.70, ">=", _fmt(naive_m1c)),
        Gate(
            "M2a  link F1 (VAL)",
            m2a_score,
            0.85,
            ">=",
            _fmt(M.naive_m2a("val")),
            f"tp {m2a_counts['tp']} fp {m2a_counts['fp']} fn {m2a_counts['fn']}",
        ),
        Gate(
            "M2b  trap accuracy (50)",
            m2b_score,
            0.90,
            ">=",
            _fmt(M.naive_m2b()),
            f"{len(m2b_wrong)} wrong",
        ),
        Gate(
            "M3   role accuracy (40)",
            m3_score,
            0.85,
            ">=",
            _fmt(M.naive_m3()),
            f"{len(m3_wrong)} wrong",
        ),
        Gate(
            "M4   direction hit rate",
            m4_score,
            0.72,
            ">=",
            _fmt(M.naive_m4()),
            "per seed " + ", ".join(f"{v:.3f}" for v in m4_per_seed),
        ),
        Gate(
            "M4-transfer  DEV - VAL",
            m4_gap,
            0.10,
            "<=",
            ">= 0.25",
            f"DEV {m4_dev:.4f} / VAL {m4_val:.4f}",
        ),
        Gate(
            "M5   information coefficient",
            m5_score,
            0.35,
            ">=",
            _fmt(M.naive_m5()),
            "per seed " + ", ".join(f"{v:.3f}" for v in m5_per_seed),
        ),
        Gate(
            "M6   calibration separation",
            m6_score,
            0.12,
            ">=",
            _fmt(naive_m6_score),
            "per seed " + ", ".join(f"{v:.3f}" for v in m6_per_seed),
        ),
        Gate(
            "M6-occupancy  min bucket n",
            float(min(m6_occ.values())),
            20,
            ">=",
            f"lo {naive_m6_occ['lo']} / mid {naive_m6_occ['mid']} / hi {naive_m6_occ['hi']}",
            f"lo {m6_occ['lo']} / mid {m6_occ['mid']} / hi {m6_occ['hi']}",
        ),
        Gate(
            "M7a  placebo |mean hit - 0.5|",
            m7a,
            0.035,
            "<=",
            _fmt(naive_m7a),
            f"{len(placebo_hits)} realizations",
        ),
        Gate(
            "M7b  placebo |mean IC|",
            m7b,
            0.06,
            "<=",
            _fmt(naive_m7b),
            f"{len(placebo_ics)} realizations",
        ),
        Gate(
            "M8   framing verdict accuracy",
            m8_score,
            1.0,
            "==",
            _fmt(M.naive_m8()),
            f"{len(m8_outcomes)} verdicts",
        ),
        Gate(
            "M8-lexicon  shipped is superset",
            1.0 if M.lexicon_is_superset() else 0.0,
            1.0,
            "==",
            "fails",
            f"{len(M.REFERENCE_FORBIDDEN_LEXICON)} reference terms",
        ),
        Gate("G1   N_directional", float(g1_value), 100, ">=", str(M.naive_g1())),
        Gate(
            "G2   exclusion rate (real)",
            g2_value,
            0.05,
            "<=",
            _fmt(M.naive_g2()),
            f"by reason {g2_reasons or '{}'}",
        ),
        Gate(
            "Leak canary  announcement capture",
            capture_rate,
            0.0,
            "==",
            ">= 0.9 for a one-bar-early harness",
            f"{captured}/{capture_n} evaluated windows",
        ),
    ]

    context = {
        "articles": len(M.prediction().articles),
        "clusters": len(M.prediction().clusters),
        "events": len(M.prediction().events),
        "signals": len(M.scored_signals()),
        "briefs": len(M.prediction().briefs),
        "seeds": M.market_seeds(),
        "placebo_seeds": M.PLACEBO_SEEDS,
        "m1_failures": m1_failures,
        "m2b_wrong": m2b_wrong,
        "m3_wrong": m3_wrong,
        "per_type_f1": val.per_type,
        "g2p": (g2p_value, g2p_reasons),
        "placebo_overlap": M.placebo_window_overlap(),
        "abstentions": (expected_abstentions, other_abstentions, abstention_notes),
        "m8_failures": [o for o in m8_outcomes if not o.correct],
    }
    return gates, context


def print_scorecard(gates: list[Gate], context: dict[str, Any]) -> None:
    print("=" * 100)
    print("NewsAlpha eval scorecard")
    print("=" * 100)
    print(
        f"corpus: {context['articles']} articles -> {context['clusters']} clusters -> "
        f"{context['events']} events -> {context['signals']} signals -> "
        f"{context['briefs']} briefs"
    )
    print(
        f"market: seeds {list(context['seeds'])} x placebo seeds "
        f"{list(context['placebo_seeds'])} = "
        f"{len(context['seeds']) * len(context['placebo_seeds'])} placebo realizations"
    )
    print("")
    header = f"{'metric':<36}{'value':>10}  {'gate':>10}  {'baseline':>12}  {'verdict':<8}notes"
    print(header)
    print("-" * 100)
    for gate in gates:
        verdict = "PASS" if gate.passing else "FAIL"
        print(
            f"{gate.name:<36}{gate.value:>10.4f}  {gate.gate_text():>10}  "
            f"{gate.baseline:>12}  {verdict:<8}{gate.note}"
        )
    print("-" * 100)

    print("\nper-type F1 (VAL):")
    for name, score in sorted(context["per_type_f1"].items()):
        print(f"  {name:<22}{score:.4f}")

    expected, other, notes = context["abstentions"]
    print(
        f"\nabstentions: {expected} expected (4 symmetric M&A + 3 denied acquirers), "
        f"{other} other  {notes}"
    )
    g2p_value, g2p_reasons = context["g2p"]
    print(
        f"placebo exclusion rate (reported only): {g2p_value:.4f}  by reason "
        f"{g2p_reasons or '{}'}"
    )
    print(f"placebo windows overlapping a planted window: {context['placebo_overlap']}")

    for label, rows in (
        ("M1b/M1c failures", context["m1_failures"]),
        ("M2b wrong decisions", [f"{w['id']} {w['surface']!r} -> {w['predicted']}" for w in context["m2b_wrong"]]),
        ("M3 wrong decisions", [f"{w['id']} {w['predicted_roles']}" for w in context["m3_wrong"]]),
        ("M8 wrong verdicts", [f"{o.subject} expected {o.expected} got {o.engine}" for o in context["m8_failures"]]),
    ):
        if rows:
            print(f"\n{label}:")
            for row in rows[:10]:
                print(f"  {row}")


def regen_check() -> int:
    """Re-run both generators and diff their output against the committed fixtures."""
    status = 0
    for command in (
        [sys.executable, str(FIXTURES / "generate_articles.py"), "--check-disjoint"],
        [sys.executable, str(FIXTURES / "generate_articles.py"), "--regen-check"],
        [sys.executable, str(FIXTURES / "generate_market.py"), "--regen-check"],
    ):
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        label = " ".join(Path(part).name for part in command[1:])
        print(f"$ {label}\n{completed.stdout.strip()}")
        if completed.returncode != 0:
            print(completed.stderr.strip(), file=sys.stderr)
            status = 1
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regen-check",
        action="store_true",
        help="re-run the seeded generators and diff against the committed fixtures",
    )
    args = parser.parse_args(argv)
    if args.regen_check:
        return regen_check()

    gates, context = build_gates()
    print_scorecard(gates, context)
    failed = [gate for gate in gates if not gate.passing]
    print("")
    if failed:
        print(f"FAILED {len(failed)} of {len(gates)} gates: " + ", ".join(g.name.split()[0] for g in failed))
        return 1
    print(f"ALL {len(gates)} GATES PASS")
    return 0


_ = Callable  # re-exported for type checkers that read the annotations above

if __name__ == "__main__":
    raise SystemExit(main())

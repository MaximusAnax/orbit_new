#!/usr/bin/env python3
"""Workspace-wide verification: run every project's tests, eval scorecard, lint, and CLI.

Usage (from projects/):
    uv run python verify_all.py            # everything
    uv run python verify_all.py flowlist   # one or more projects

Exit code is non-zero if any project fails any check, so this doubles as CI.
"""
from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent
SLUGS = [
    "almanac", "chessmentor", "datasweep", "dresscast", "ethos", "flowlist",
    "formcoach", "grailtrader", "newsalpha", "pointsmax", "tickerpress", "voicekin",
]
TIMEOUT = 1800


@dataclass
class Result:
    slug: str
    tests: str = "—"
    tests_ok: bool = False
    evals: str = "—"
    evals_ok: bool = False
    lint_ok: bool = False
    cli_ok: bool = False
    loc: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.tests_ok and self.evals_ok and self.lint_ok and self.cli_ok


def run(cmd: list[str], timeout: int = TIMEOUT) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"
    except OSError as exc:  # pragma: no cover - environment failure
        return 125, str(exc)


def count_loc(slug: str) -> int:
    total = 0
    for sub in ("src", "tests", "evals"):
        for path in (ROOT / slug / sub).rglob("*.py"):
            with contextlib.suppress(OSError):
                total += sum(1 for _ in path.open(errors="replace"))
    return total


def check(slug: str) -> Result:
    res = Result(slug=slug)
    started = time.time()
    res.loc = count_loc(slug)

    code, out = run(["uv", "run", "pytest", slug, "-q", "--no-header", "-p", "no:cacheprovider"])
    res.tests_ok = code == 0
    m = re.search(r"(\d+) passed", out)
    failed = re.search(r"(\d+) failed", out)
    errored = re.search(r"(\d+) error", out)
    parts = []
    if m:
        parts.append(f"{m.group(1)} passed")
    if failed:
        parts.append(f"{failed.group(1)} FAILED")
    if errored:
        parts.append(f"{errored.group(1)} ERROR")
    res.tests = ", ".join(parts) if parts else ("no tests" if code == 5 else f"exit {code}")
    if not res.tests_ok:
        res.errors.append(f"pytest:\n{out[-2500:]}")

    runner = ROOT / slug / "evals" / "run.py"
    if runner.exists():
        code, out = run(["uv", "run", "python", str(runner)])
        res.evals_ok = code == 0
        gates = re.findall(r"\b(PASS|FAIL)\b", out)
        if gates:
            res.evals = f"{gates.count('PASS')} pass, {gates.count('FAIL')} fail"
        else:
            res.evals = "ran" if code == 0 else f"exit {code}"
        if not res.evals_ok:
            res.errors.append(f"evals/run.py:\n{out[-2500:]}")
    else:
        res.errors.append("evals/run.py missing")

    code, out = run(["uv", "run", "ruff", "check", slug])
    res.lint_ok = code == 0
    if not res.lint_ok:
        res.errors.append(f"ruff:\n{out[-1500:]}")

    code, out = run(["uv", "run", slug, "--help"], timeout=120)
    res.cli_ok = code == 0
    if not res.cli_ok:
        res.errors.append(f"cli --help:\n{out[-1200:]}")

    res.seconds = time.time() - started
    return res


def main(argv: list[str]) -> int:
    slugs = [s for s in argv[1:] if not s.startswith("-")] or SLUGS
    unknown = [s for s in slugs if s not in SLUGS]
    if unknown:
        print(f"unknown project(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    results = []
    for slug in slugs:
        print(f"checking {slug} ...", flush=True)
        results.append(check(slug))

    width = max(len(s) for s in slugs)
    print("\n" + "=" * (width + 62))
    print(f"{'project':<{width}}  {'tests':<20} {'evals':<16} {'lint':<5} {'cli':<4} {'loc':>6}")
    print("-" * (width + 62))
    for r in results:
        print(
            f"{r.slug:<{width}}  {r.tests:<20} {r.evals:<16} "
            f"{'ok' if r.lint_ok else 'FAIL':<5} {'ok' if r.cli_ok else 'FAIL':<4} {r.loc:>6}"
        )
    print("=" * (width + 62))

    failing = [r for r in results if not r.ok]
    total_loc = sum(r.loc for r in results)
    print(f"\n{len(results) - len(failing)}/{len(results)} projects fully green   ({total_loc:,} lines)")

    for r in failing:
        print(f"\n{'-' * 70}\n{r.slug} FAILURES\n{'-' * 70}")
        for err in r.errors:
            print(err[:3000])

    (ROOT / "verify_report.json").write_text(json.dumps(
        [{"slug": r.slug, "tests": r.tests, "evals": r.evals, "lint_ok": r.lint_ok,
          "cli_ok": r.cli_ok, "loc": r.loc, "ok": r.ok, "seconds": round(r.seconds, 1)}
         for r in results], indent=2) + "\n")

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

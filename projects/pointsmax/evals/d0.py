"""D0 — determinism and state integrity (EVALS "D0", no score).

(a) recomputing five designated scenarios is byte-identical and plan signatures
    are stable;
(b) executing a fixture plan's steps and replaying the ledger from zero
    reproduces every balance and every ``post_balance``;
(c) executing against a world whose ``content_hash`` differs — including when the
    semver string is unchanged — fails with the structured version-mismatch
    error.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import metrics
from pointsmax.adapters.world_provider import build_world
from pointsmax.engine.advisor import compute_plan_set, plan_set_fingerprint
from pointsmax.engine.execution import WorldPinMismatch
from pointsmax.models import GoalSpec, Goal, Wallet
from pointsmax.service import PointsMaxService
from pointsmax.store import InMemoryRepository

#: The five scenarios D0(a) recomputes.
DETERMINISM_CASES = ("sd_01", "rt_01", "mh_01", "cash_01", "st_03")


def _case(case_id: str) -> dict[str, Any]:
    for case in metrics.search_cases():
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


def check_determinism() -> tuple[bool, str]:
    for case_id in DETERMINISM_CASES:
        case = _case(case_id)
        world = metrics.eval_world(case["world"])
        kwargs = {
            "world": world,
            "wallet": Wallet(cards=list(case["cards"]), balances=dict(case["balances"])),
            "goal": GoalSpec.model_validate(case["goal"]),
            "today": date.fromisoformat(case["today"]),
            "params": metrics.params_of(case),
        }
        first = compute_plan_set(**kwargs)
        second = compute_plan_set(**kwargs)
        if plan_set_fingerprint(first) != plan_set_fingerprint(second):
            return False, f"{case_id}: recompute is not byte-identical"
        if [p.signature for p in first.plans] != [p.signature for p in second.plans]:
            return False, f"{case_id}: plan signatures are unstable"
    return True, f"{len(DETERMINISM_CASES)} scenarios recomputed byte-identically"


def _service_for(case: dict[str, Any]) -> tuple[PointsMaxService, int]:
    """A service with the scenario's wallet loaded and its goal stored."""
    world = metrics.eval_world(case["world"])
    repo = InMemoryRepository()
    for card_id in case["cards"]:
        repo.add_card(card_id, "2026-01-01T00:00:00Z")
    for program_id, points in sorted(case["balances"].items()):
        repo.set_balance(program_id, points, at="2026-01-01T00:00:00Z")
    service = PointsMaxService(repo, world)
    goal = repo.add_goal(
        Goal(**GoalSpec.model_validate(case["goal"]).model_dump(), created_at="2026-01-01T00:00:00Z")
    )
    assert goal.id is not None
    return service, goal.id


def check_ledger_replay() -> tuple[bool, str]:
    from pointsmax.engine.execution import replay_balances

    for script in metrics.exec_scripts():
        case = _case(script["scenario"])
        service, goal_id = _service_for(case)
        plan_set = service.compute_plans(
            goal_id, today=date.fromisoformat(case["today"]), at="2026-01-01T00:00:01Z"
        )
        if not plan_set.plans:
            return False, f"{script['id']}: the scenario produced no plan to execute"
        plan = plan_set.plans[0]
        assert plan.id is not None
        for step in script["steps"]:
            if step["seq"] > len(plan.steps):
                continue
            service.execute_step(
                plan.id,
                step["seq"],
                at=step["at"],
                confirm_irreversible=True,
            )
        entries = service.repo.list_ledger()
        replayed = replay_balances(entries)
        stored = service.repo.balances()
        if {p: n for p, n in replayed.items() if n} != stored:
            return False, f"{script['id']}: replayed balances differ from stored balances"
        running: dict[str, int] = {}
        for entry in entries:
            running[entry.program_id] = running.get(entry.program_id, 0) + entry.delta_points
            if running[entry.program_id] != entry.post_balance:
                return False, f"{script['id']}: post_balance chain broken on {entry.program_id}"
    return True, f"{len(metrics.exec_scripts())} execution scripts replayed exactly"


def check_world_pin() -> tuple[bool, str]:
    case = _case("hd_01")
    service, goal_id = _service_for(case)
    plan_set = service.compute_plans(
        goal_id, today=date.fromisoformat(case["today"]), at="2026-01-01T00:00:01Z"
    )
    plan = plan_set.plans[0]
    assert plan.id is not None

    # Same semver, different bytes: nudge one reference fare by a single cent.
    from worldgen import content_hash

    files = copy.deepcopy(metrics.world_files(case["world"]))
    files["reference_fares.json"][0]["fare_cents"] += 1
    original_version = files["version.json"]["version"]
    files["version.json"] = {
        "version": original_version,
        "as_of": files["version.json"]["as_of"],
        "content_hash": content_hash(files),
    }
    service.world = build_world(files, validate=True)
    try:
        service.execute_step(plan.id, 1, at="2026-05-02T09:00:00Z", confirm_irreversible=True)
    except WorldPinMismatch as exc:
        detail = exc.as_dict()
        if detail["pinned_version"] != original_version:
            return False, "the mismatch error did not report the pinned version"
        return True, "an edited world with an unchanged semver is refused"
    return False, "execution against a different world content hash was allowed"


def run_d0_checks() -> list[tuple[str, bool, str]]:
    checks = [
        ("D0(a) determinism & signature stability (FR-16/FR-9)", check_determinism),
        ("D0(b) ledger replay from zero (FR-2)", check_ledger_replay),
        ("D0(c) world content-hash pin on execute (FR-11)", check_world_pin),
    ]
    results = []
    for name, check in checks:
        ok, detail = check()
        results.append((name, ok, detail))
    return results

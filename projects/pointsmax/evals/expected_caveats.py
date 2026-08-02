"""Independent derivation of the FR-10 caveat table (eval ground truth).

Written straight from SCOPE.md's FR-10 table against **raw world dicts** and the
oracle's plan structure, sharing no code with ``pointsmax.engine.plan``.  M3
compares the *codes plus key params* this module produces against what the
engine emits, so an engine that forgets a warning — or invents one — fails the
gate (EVALS finding E2).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from oracle import OraclePlan, OracleWorld

#: The params M3 compares, per code (EVALS "M3" item 4).
KEY_PARAMS: dict[str, tuple[str, ...]] = {
    "irreversible_transfer": ("edge_id", "points"),
    "transfer_time_risk": ("program", "arrival_days", "deadline"),
    "stranded_points": ("program", "points", "value_cents"),
    "promo_expiring": ("subject", "valid_to"),
    "below_baseline": ("program", "option_id"),
    "stale_world": ("days_old",),
    "seats_limited": ("offer_id", "seats_available"),
}


def expected_caveats(
    world: OracleWorld,
    plan: OraclePlan,
    goal: dict[str, Any],
    today: date,
    params: dict[str, int],
) -> list[dict[str, Any]]:
    """Every caveat FR-10 says this plan must carry, as ``{code, params}`` rows."""
    slack_days = params.get("slack_days", 2)
    promo_days = params.get("promo_days", 30)
    stale_days = params.get("stale_days", 180)
    edges = {e["id"]: e for e in world.edges}
    options = {o["id"]: o for o in world.cashouts}
    out: list[dict[str, Any]] = []

    for use in plan.transfers:
        out.append(
            {
                "code": "irreversible_transfer",
                "params": {"edge_id": use.edge_id, "points": use.sent},
            }
        )

    seen_timing: set[tuple[str, str]] = set()
    for booking in plan.bookings:
        if booking.deadline is None:
            continue
        arrival = plan.arrival_days.get(booking.program_id, 0)
        if (booking.deadline - (today + timedelta(days=arrival))).days <= slack_days:
            key = (booking.program_id, booking.deadline.isoformat())
            if key in seen_timing:
                continue
            seen_timing.add(key)
            out.append(
                {
                    "code": "transfer_time_risk",
                    "params": {
                        "program": booking.program_id,
                        "arrival_days": arrival,
                        "deadline": booking.deadline.isoformat(),
                    },
                }
            )

    for program in sorted({use.to_program for use in plan.transfers}):
        leftover = plan.ending_holdings.get(program, 0)
        if leftover > 0:
            out.append(
                {
                    "code": "stranded_points",
                    "params": {
                        "program": program,
                        "points": leftover,
                        "value_cents": (leftover * world.mcpp[program]) // 1000,
                    },
                }
            )

    for use in plan.transfers:
        valid_to = edges[use.edge_id].get("valid_to")
        if valid_to and (date.fromisoformat(valid_to) - today).days <= promo_days:
            out.append(
                {
                    "code": "promo_expiring",
                    "params": {"subject": use.edge_id, "valid_to": valid_to},
                }
            )
    for booking in plan.bookings:
        if booking.cashout_id is None:
            continue
        valid_to = options[booking.cashout_id].get("valid_to")
        if valid_to and (date.fromisoformat(valid_to) - today).days <= promo_days:
            out.append(
                {
                    "code": "promo_expiring",
                    "params": {"subject": booking.cashout_id, "valid_to": valid_to},
                }
            )

    for booking in plan.bookings:
        if booking.cashout_id is None:
            continue
        option = options[booking.cashout_id]
        if option["cpp_milli"] < world.mcpp[option["program_id"]]:
            out.append(
                {
                    "code": "below_baseline",
                    "params": {"program": option["program_id"], "option_id": option["id"]},
                }
            )

    days_old = (today - world.as_of).days
    if days_old > stale_days:
        out.append({"code": "stale_world", "params": {"days_old": days_old}})

    passengers = goal.get("passengers") or 1
    for booking in plan.bookings:
        if booking.offer_id is None or booking.seats_available is None:
            continue
        if booking.seats_available - passengers <= 1:
            out.append(
                {
                    "code": "seats_limited",
                    "params": {
                        "offer_id": booking.offer_id,
                        "seats_available": booking.seats_available,
                    },
                }
            )

    return normalize(out)


def normalize(caveats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce to the comparable multiset M3 asserts: code + key params, sorted."""
    rows: list[dict[str, Any]] = []
    for caveat in caveats:
        code = str(caveat["code"])
        keys = KEY_PARAMS.get(code, ())
        params = caveat.get("params", {})
        subset = {}
        for key in keys:
            if key == "subject":
                subset["subject"] = (
                    params.get("subject") or params.get("edge_id") or params.get("option_id")
                )
            else:
                subset[key] = params.get(key)
        rows.append({"code": code, "params": subset})
    rows.sort(key=lambda row: (row["code"], sorted(row["params"].items(), key=lambda kv: kv[0])))
    return rows

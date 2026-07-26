# ADR 001: Maintenance Cadence

## Status

Accepted (Orbit Full Product Build)

## Context

Product constitution forbids ranking people by longest time without contact. Relationships have different natural rhythms; orbit proximity is not the same as maintenance need. Abdoul remains the authority via explicit `relationship_state`.

## Decision

1. **Authority order:** explicit `desired_cadence` → orbit default → signals (tasks, direction, deferred proposals).
2. **Orbit defaults (days):** inner 21, close 14, active 10, extended 45, outer 120, unset→extended.
3. **Ranking:** open threads sort first; then score = overdue_ratio (+ grow-closer boost, − softened drift). Open-task weight (+2.0) ensures context beats mild silence.
4. **API:** `GET /api/v1/maintenance` returns ranked suggestions with human-readable `reasons` and machine `reason_codes`. Never auto-messages.
5. **No inferred relationship_state** auto-promotion for cadence.

## Consequences

- Inner orbit is not nagged weekly by default.
- Outer ties with open tasks can outrank overdue inner contacts.
- Explicit “monthly” / “letting drift” overrides defaults and scoring pressure.

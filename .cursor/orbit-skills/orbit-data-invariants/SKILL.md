---
name: orbit-data-invariants
description: Enforces Orbit's core data-integrity rules whenever writing code that touches the person, fact, relationship_state, or profile_update_proposal tables (or their ORM/API equivalents). Use this skill for ANY task involving creating, updating, or migrating these tables, writing repository/DAO code, writing the extraction pipeline's persistence layer, or reviewing a PR that touches person history. Trigger even if the user just says "update the fact when X changes" or "save the extracted info" — these are exactly the requests where the naive implementation silently violates Orbit's design. Do not skip this skill just because a task seems like a simple CRUD change; in this codebase, "simple" writes to these tables are the highest-risk operation in the system.
---

# Orbit Data Invariants

Orbit's entire value proposition depends on never silently losing or overwriting a person's history. These invariants are not stylistic preferences — violating them breaks the product's core promise. Full rationale lives in `product_design_document.md` (Principle 2, Principle 5) and the schema is defined in `technical_design_document.md` Section 2.

## Invariant 1: `fact` and `relationship_state` are append-only

**Never** write an `UPDATE` that changes `fact.value` or `relationship_state.description`/`desired_closeness`/`desired_cadence`/`direction` on an existing row.

When something changes (new job, new relationship read):
1. `UPDATE` the existing "current" row: set `fact.valid_to = now()` and `fact.status = 'past'` (or `relationship_state.superseded_at = now()`).
2. `INSERT` a new row with the new value, `status = 'current'` / `superseded_at = null`.
3. Do both in the same DB transaction. A partial write (old row closed, new row missing, or vice versa) is worse than not writing at all.

If you find yourself writing `UPDATE fact SET value = $1 WHERE id = $2`, stop — that's the bug this skill exists to catch.

## Invariant 2: Nothing reaches `fact` except through `profile_update_proposal`

The extraction pipeline (and any future automated writer) must never write directly to `fact`. It writes a `profile_update_proposal` row with `decision = 'pending'`. A `fact` row is only created when a proposal's `decision` transitions to `'accepted'` or `'edited'` — via an explicit user action (an API call like `POST /proposals/{id}/accept`), never automatically.

If you're implementing something that "figures out" a fact from a transcript, event, or any AI inference, its output is a proposal, full stop — even if confidence is high, even if it seems obviously correct.

## Invariant 3: `relationship_state` distinguishes explicit vs. suggested — and only explicit ever becomes "current"

`relationship_state.source` is `'explicit'` (user typed/spoke it directly) or `'inferred_suggested'` (the system noticed a pattern and is proposing it). An `inferred_suggested` row must be surfaced to the user as a suggestion requiring action to accept — never treated as the "current" state in queries or UI just because it exists. This field is intentionally held to a stricter bar than `fact`: Orbit may never auto-write a person's relationship state, even behind a low threshold. See product doc Section 12/1.2 for why.

## Invariant 4: Ambiguity is flagged, never guessed

If code resolving a name/reference to a `person_id` has confidence below the configured threshold (default 0.7 — see `orbit-pipeline-conventions` skill), it must write an `ambiguity_flag` row instead of picking the most-likely match and proceeding. Do not add "just pick the best guess if only one candidate exists" logic without an explicit confidence check — even a single candidate can be a wrong match (e.g. a common first name).

## Quick self-check before submitting any change to these tables

- [ ] Does this ever `UPDATE` a value field on `fact` or `relationship_state` in place? → Fix it.
- [ ] Does any code path write to `fact` without going through `profile_update_proposal`? → Fix it.
- [ ] Can an `inferred_suggested` relationship_state ever be read as "the current state" without user confirmation? → Fix it.
- [ ] Does entity resolution ever proceed below the confidence threshold without an ambiguity_flag? → Fix it.

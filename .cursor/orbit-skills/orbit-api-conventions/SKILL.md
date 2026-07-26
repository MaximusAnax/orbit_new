---
name: orbit-api-conventions
description: Use when adding, modifying, or reviewing any FastAPI route in Orbit's backend (app/api/routes/), when designing a new endpoint's request/response shape, or when the iOS client needs a new backend capability. Also trigger for questions about how event processing should be exposed to the client (sync vs async), or how proposal accept/reject/defer actions should behave. This skill keeps new endpoints consistent with Orbit's existing API contract rather than each route inventing its own conventions.
---

# Orbit API Conventions

Reference contract: `technical_design_document.md` Section 3. This skill covers the patterns to follow when extending it.

## Event creation is async — never make the client wait on the pipeline

`POST /events` must return `202 Accepted` with the event id as soon as transcription is queued, not after the full extraction pipeline finishes. The pipeline (see `orbit-pipeline-conventions`) can take real time across six steps; blocking the request on it would make the "effortless capture" UX impossible. Any new endpoint that triggers pipeline work should follow this same pattern — return fast, let the client observe progress via polling or Realtime subscription.

## Proposals are the only write path into a person's profile

New endpoints must not offer a shortcut that writes `fact` or `relationship_state` directly on behalf of extracted/inferred content. If a new feature needs to record something the system inferred, it creates a `profile_update_proposal` and reuses the existing `/proposals/{id}/accept|reject|defer` actions — don't invent a parallel accept mechanism per feature.

## Response shapes should mirror what's already been read, not re-derive it ad hoc

`GET /people/{id}` returns current facts (latest per category where `status='current'`), timeline, open tasks, current relationship state — as one assembled object. If you're adding a new field to a person's profile, extend this response rather than requiring the client to make additional calls to piece together "the current profile." The client should never need to reconstruct "what's current" logic itself — that's backend responsibility, always computed the same way (see `orbit-data-invariants` for the underlying query pattern).

## Distinguish "explicit" writes from anything AI-touched, in the route layer too

Any endpoint accepting a direct relationship-state update from the user (e.g. `POST /people/{id}/relationship-state`) must hard-set `source = 'explicit'` — never let a client-supplied value override this, and never reuse this endpoint for pipeline-suggested updates. Suggested relationship-state updates get their own distinct path (proposal-style), matching Invariant 3 in `orbit-data-invariants`.

## New endpoint checklist

- [ ] If it triggers pipeline work, does it return immediately (202) rather than blocking?
- [ ] If it can produce inferred/AI-derived content, does it write a proposal rather than a live fact?
- [ ] Does the response shape avoid pushing "what's current" logic onto the client?
- [ ] If it's a relationship-state write, is `source` set correctly and not client-overridable?

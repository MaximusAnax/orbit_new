---
name: orbit-testing-conventions
description: Use whenever writing or reviewing tests for Orbit's backend (pipeline stages, API routes, database invariants) or iOS app, or when a task involves "make sure this works," "add tests for X," or touches the extraction pipeline or the fact/relationship_state tables. This skill specifies WHICH tests actually matter in this codebase — Orbit has specific invariants (append-only history, proposal-gated writes) that generic CRUD test coverage will not catch, so use this before assuming standard unit/integration tests are sufficient.
---

# Orbit Testing Conventions

Reference: `technical_design_document.md` Section 9. The goal here isn't test coverage for its own sake — it's catching the two failure modes most likely to silently break Orbit's core promises: bad extraction and history mutation.

## Priority 1: Pipeline stage tests with fixed transcript fixtures

This is the highest-value test surface in the codebase. For each pipeline stage (entity resolution, fact extraction, task extraction, context extraction), maintain a set of fixed input transcripts with hand-verified expected output (exact proposal shapes, confidence expectations, which cases should produce an `ambiguity_flag` instead of a resolution). When extraction quality regresses — and it will, as prompts get tuned — these are what catch it. Do not rely on "looks right when I tried it once" as the test bar for this layer.

Include fixtures that specifically exercise:
- A transcript where a fact supersedes an existing one (e.g. job change) — assert the old fact isn't mutated, a new one is proposed.
- A multi-person event with ambiguous attribution — assert an `ambiguity_flag` is produced, not a guessed resolution.
- A low-confidence entity match — assert steps 3–5 are skipped for that person rather than run against a guess.

## Priority 2: Append-only invariant tests

Direct integration tests against the invariants in `orbit-data-invariants`:
- Create a fact, create a superseding fact for the same person/category, assert the original row's `value` is byte-for-byte unchanged and `valid_to`/`status` are correctly set.
- Same pattern for `relationship_state` with `superseded_at`.
- Attempt (in a test) to write a `fact` row that doesn't originate from an accepted `profile_update_proposal` — this should not be possible through the normal application code path; if it is, that's the bug.

These tests should fail loudly if a future change reintroduces an in-place `UPDATE` on a value field — treat a failure here as a release blocker, not a flaky test to skip.

## Priority 3: API contract tests

Standard schema validation (Pydantic gives most of this for free) plus explicit tests for the async pattern: `POST /events` returns `202` immediately, doesn't block on pipeline completion.

## iOS: prioritize the Capture → Review flow

This is the highest-risk UX surface per the product design doc — extraction quality and review friction are the most likely places for the product to quietly fail in practice. UI tests should cover: recording → immediate save (event shows up before processing completes) → proposal review interactions (accept/edit/reject/defer) → verify deferred proposals don't affect the displayed "current" profile until acted on.

## What NOT to spend test effort on early

Don't prioritize exhaustive CRUD tests for simple, low-risk tables (e.g. `contact_point`) at the expense of the pipeline/invariant tests above — those are standard and low-risk; the invariant and extraction-quality tests are where this codebase actually differs from a generic app and where bugs will be expensive and hard to notice.

---
name: orbit-pipeline-conventions
description: Governs how to write or modify any part of Orbit's voice-capture extraction pipeline (transcription, entity resolution, fact extraction, task extraction, context extraction, group reconciliation, or the orchestrator that sequences them). Use this skill whenever implementing an LLM call in app/pipeline/, changing the extraction prompt/schema for any pipeline stage, adding a new extraction step, or debugging why extracted facts/proposals look wrong. Also trigger if the user asks to "improve extraction accuracy," "add a new field to extract," or "make capture faster" — these requests have specific right/wrong ways to implement within Orbit's pipeline design, covered here.
---

# Orbit Pipeline Conventions

Full pipeline spec is in `technical_design_document.md` Section 4. This skill covers the implementation rules that are easy to get wrong or "optimize away" without realizing they were load-bearing.

## The pipeline is sequenced, not a single call — keep it that way

Six ordered steps: transcribe → resolve entities → extract facts (per person) → extract tasks (global) → extract context (global) → reconcile group attribution. This was a deliberate accuracy-over-latency trade-off (see product doc). If you're tempted to collapse steps into one LLM call for speed, don't — file that as a proposal to discuss, not a unilateral optimization. The per-step separation is what makes ambiguity flagging, per-person skipping, and debugging possible.

## Every LLM call must use structured/schema-constrained output

No freeform text parsing, no regex-extracting JSON from prose. Every pipeline stage takes a Pydantic model as its output contract. If a stage's current implementation parses free text, that's a bug, not a stopgap to leave alone.

## Confidence scores are mandatory, not optional metadata

Every entity resolution and every extracted fact proposal must carry a `confidence` value. This isn't cosmetic — it's what routes low-confidence entity matches to `ambiguity_flag` instead of a wrong-person fact (see `orbit-data-invariants`). Default threshold is 0.7; treat this as a tunable constant in config, not a magic number scattered across files, since it will need empirical adjustment once real transcripts run through it.

## Per-person steps must be skippable

If entity resolution for a given person in a transcript is ambiguous, steps 3–5 (fact/task/context extraction for that person) must not run against a guessed identity. Structure the orchestrator so skipping one person's downstream steps doesn't block or corrupt processing for other people in the same multi-person event.

## Idempotency and retries

Pipeline steps must be safe to rerun against the same event without duplicating data:
- Before inserting a `profile_update_proposal`, dedupe on `(event_id, person_id, category, value)`.
- Transient failures (timeouts, rate limits) retry at the step level with backoff — not by rerunning the whole pipeline from transcription.
- Persist intermediate state after every step completes, so a crash mid-pipeline resumes rather than restarts.

## Logging

Every LLM call's raw input and output get logged (to a restricted-access store per the technical doc's security section — not general application logs, since transcripts contain third-party personal data). This pipeline will need real tuning against real usage; silent failures or "it just returned something weird once" without a trace defeats that.

## Before merging any pipeline change, check:

- [ ] New/changed steps still produce Pydantic-validated structured output.
- [ ] Confidence scoring is present and actually used to gate ambiguity flagging.
- [ ] The step is idempotent — rerunning it doesn't duplicate proposals/facts.
- [ ] Raw model I/O is logged for this step.

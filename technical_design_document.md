# Orbit — Technical Design Document

**Audience:** This document is written for an engineering agent (e.g. Claude Code) implementing Orbit. It assumes the product/UX rationale in `product_design_document.md` and does not repeat the "why" — it specifies the "how" precisely enough to build against without re-deriving decisions already made.

**Stack:** Python (FastAPI) backend · Swift/SwiftUI iOS app · Supabase (Postgres + pgvector + Auth + Storage)

---

## 1. Repository Structure

Two repos (or a monorepo with two top-level dirs — agent's call, no strong constraint either way):

```
orbit-backend/
  app/
    main.py
    api/
      routes/
        people.py
        events.py
        facts.py
        proposals.py
        relationship_state.py
        tasks.py
        search.py
        discover.py
    core/
      config.py          # env/config, Supabase client init
      security.py         # auth dependency
    pipeline/
      transcription.py
      entity_resolution.py
      fact_extraction.py
      task_extraction.py
      context_extraction.py
      group_reconciliation.py
      orchestrator.py     # runs the sequenced pipeline, step-by-step, per Section 4
    models/                # Pydantic schemas (API contracts)
    db/
      schema.sql           # source of truth DDL
      queries/              # hand-written SQL for complex joins/graph traversal
    tests/
  requirements.txt
  README.md

orbit-ios/
  Orbit/
    App/
    Features/
      Capture/
      Recall/
      Discover/
      Review/
    Networking/
    Models/
    DesignSystem/
  OrbitTests/
```

---

## 2. Database Schema (Supabase / Postgres + pgvector)

This is the authoritative DDL. Deviating from field names/types here should be treated as a decision requiring a note in the PR, not a silent change.

```sql
create extension if not exists vector;
create extension if not exists pgcrypto;

create table person (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  preferred_name text,
  photo_url text,
  pronunciation_note text,
  orbit text check (orbit in ('inner','close','active','extended','outer')),
  linked_phone_contact_id text,
  created_at timestamptz not null default now()
);

create table contact_point (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  type text not null check (type in ('phone','email','instagram','linkedin','x','website','other')),
  value text not null,
  is_primary boolean not null default false
);

create table event (
  id uuid primary key default gen_random_uuid(),
  date timestamptz not null,
  location text,
  raw_transcript text,
  summary text,
  source text not null check (source in ('voice','text','manual')),
  status text not null default 'captured' check (status in ('captured','confirmed','synced')),
  created_at timestamptz not null default now()
);

create table event_participant (
  event_id uuid not null references event(id) on delete cascade,
  person_id uuid not null references person(id) on delete cascade,
  role text not null check (role in ('attendee','introducer','introduced','mentioned_not_present')),
  per_person_notes text,
  primary key (event_id, person_id)
);

create table organization (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  type text check (type in ('company','school','other')),
  created_at timestamptz not null default now()
);

create table fact (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  category text not null check (category in ('job','location','school','interest','goal','family','other')),
  value text not null,
  organization_id uuid references organization(id),
  status text not null check (status in ('current','past','uncertain')),
  valid_from timestamptz,
  valid_to timestamptz,
  source_event_id uuid references event(id),
  confidence real,
  created_at timestamptz not null default now()
);
create index fact_person_category_idx on fact(person_id, category, status);

create table profile_update_proposal (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references event(id) on delete cascade,
  person_id uuid not null references person(id) on delete cascade,
  proposed_fact_json jsonb not null,
  decision text not null default 'pending' check (decision in ('pending','accepted','edited','rejected','deferred')),
  resolved_at timestamptz,
  created_at timestamptz not null default now()
);

create table relationship_state (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  description text,
  desired_closeness text,
  desired_cadence text,
  direction text,
  source text not null check (source in ('explicit','inferred_suggested')),
  recorded_at timestamptz not null default now(),
  superseded_at timestamptz
);
create index relationship_state_current_idx on relationship_state(person_id) where superseded_at is null;

create table task (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references person(id) on delete cascade,
  event_id uuid references event(id),
  description text not null,
  status text not null default 'open' check (status in ('open','done')),
  due_date date,
  created_at timestamptz not null default now()
);

create table ambiguity_flag (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references event(id) on delete cascade,
  raw_fragment text not null,
  candidate_person_ids uuid[] not null,
  resolved_person_id uuid references person(id),
  created_at timestamptz not null default now()
);

create table embedding (
  id uuid primary key default gen_random_uuid(),
  source_type text not null check (source_type in ('fact','event','person_summary')),
  source_id uuid not null,
  vector vector(1536) not null,
  text_content text not null,
  model_version text not null,
  created_at timestamptz not null default now()
);
create index embedding_vector_idx on embedding using ivfflat (vector vector_cosine_ops);
```

**Rules for the agent when touching this schema:**
- `fact` and `relationship_state` are append-only in application logic — never `UPDATE` a row to change `value`/`description`; insert a new row and set `valid_to`/`superseded_at` on the old one, in the same transaction.
- Never write directly to `fact` from the extraction pipeline — always via `profile_update_proposal`, materialized to `fact` only on `decision IN ('accepted','edited')`.
- `relationship_state.source = 'inferred_suggested'` rows must surface as a suggestion in the API/UI, never auto-promoted to the "current" state without a user action that flips them to effectively explicit.

---

## 3. API Contract (FastAPI)

Base path: `/api/v1`. All endpoints require the Supabase auth JWT (single user, but auth is still required — see Section 8).

| Method | Path | Purpose |
|---|---|---|
| POST | `/events` | Create event from audio (multipart) or raw text; triggers pipeline async |
| GET | `/events/{id}` | Fetch event incl. transcript, participants, proposal summary |
| GET | `/events?person_id=` | List events, optionally filtered |
| GET | `/proposals?event_id=` | List pending/all proposals for an event |
| POST | `/proposals/{id}/accept` | Accept (optionally with edited `value`) — materializes to `fact` |
| POST | `/proposals/{id}/reject` | Reject, no-op on `fact` |
| POST | `/proposals/{id}/defer` | Leave pending, event stays "unsynced" |
| GET | `/people` | List/search people |
| POST | `/people` | Manually create a person |
| GET | `/people/{id}` | Full profile: current facts, timeline, open tasks, current relationship state |
| GET | `/people/{id}/catchup` | On-demand "Catch me up" generation (see Section 5) |
| POST | `/people/{id}/relationship-state` | Explicit relationship state update (always `source=explicit`) |
| GET | `/tasks?status=open` | Open follow-ups across all people |
| PATCH | `/tasks/{id}` | Mark done |
| GET | `/search?q=` | Semantic + structured hybrid search (Section 6) |
| GET | `/discover?q=` | Network-reasoning queries (Section 6) |

**Async processing note:** `POST /events` should return `202 Accepted` with the `event.id` immediately after transcription is queued — the pipeline (Section 4) runs in the background (e.g. FastAPI `BackgroundTasks` or a lightweight queue like Supabase Edge Functions / a Celery-style worker if volume grows). The iOS client polls or subscribes (Supabase Realtime on `event`/`profile_update_proposal` tables is a natural fit here — worth using it rather than polling).

---

## 4. Extraction Pipeline — Implementation Spec

Each stage in `app/pipeline/` is a standalone, independently testable function with a typed input/output (Pydantic models). The orchestrator (`orchestrator.py`) sequences them and persists intermediate state after every step, so a failure mid-pipeline never loses prior progress.

```python
# orchestrator.py — shape, not final code
async def run_pipeline(event_id: UUID):
    event = await get_event(event_id)
    transcript = await transcribe(event.audio_url)       # step 1
    await save_transcript(event_id, transcript)

    resolutions = await resolve_entities(transcript, existing_people=await get_people_index())  # step 2
    await save_ambiguity_flags(event_id, resolutions.ambiguous)

    for person_id, segment in resolutions.resolved.items():
        proposals = await extract_facts(segment, existing_facts=await get_current_facts(person_id))  # step 3
        await save_proposals(event_id, person_id, proposals)

    tasks = await extract_tasks(transcript)               # step 4
    await save_tasks(event_id, tasks)

    context = await extract_context(transcript, resolutions.resolved)  # step 5
    await save_context_notes(event_id, context)

    await reconcile_group_attribution(event_id)            # step 6
    await mark_event_captured_complete(event_id)
```

**Per-step contract requirements:**
- Every LLM call uses structured output (JSON schema-constrained) — never freeform parsing of prose.
- Every step logs its raw model input/output for debuggability (not just the parsed result) — this pipeline will need tuning, and silent failures are unacceptable per Principle 4 (accuracy over automation).
- Confidence scores are mandatory on `fact` proposals and entity resolutions — `confidence < threshold` (start at 0.7, tune empirically) routes to `AmbiguityFlag` instead of a proposal.
- Steps 3–5 for a given person are skipped entirely if that person's entity resolution was ambiguous — do not extract facts against a guessed identity.
- Retries: transient failures (timeouts, rate limits) retry with backoff at the step level; steps are idempotent (safe to rerun against the same event without duplicating proposals — dedupe on `event_id + person_id + category + value` before insert).

---

## 5. "Catch Me Up" Generation

`GET /people/{id}/catchup`:

- **Input assembled server-side:** current facts (all categories), last 3–5 events (summary + date only, not full transcripts), open tasks, current relationship state.
- **Explicitly excluded:** raw transcripts, rejected/pending proposals — nothing not yet confirmed should influence this output.
- **Output contract:** `{ "summary": str, "talking_points": list[str] }`, generated fresh per request (not cached/precomputed — see product doc rationale).
- Cost/latency note: this is a single LLM call with a small, curated context window, so it should be fast (~1-2s) — no need for the multi-call rigor of the capture pipeline here, since there's no proposal/confirmation step involved.

---

## 6. Search & Discover

Two distinct code paths — do not try to unify them into one query mechanism:

**`/search` (semantic):**
1. Embed the query string (same model/version as stored embeddings)
2. `SELECT ... FROM embedding ORDER BY vector <=> $1 LIMIT n` (cosine distance via pgvector)
3. Join back to `fact`/`event` → `person` for display
4. Filter to only `fact.status = 'current'` or explicitly include past facts if the query implies history ("who used to work at Google") — this distinction should be handled by including recency/status hints in what gets embedded and searched, not by trying to make the LLM classify query intent as a separate step initially. Revisit if quality is poor.

**`/discover` (structured graph traversal):**
1. Parse the query intent (LLM call, structured output: target org/skill/interest + relationship type sought, e.g. "introduction path" vs "direct connection")
2. Execute a SQL join/traversal: `person → fact(category='job') → organization`, or `person → fact(category='interest')`, filtered on the parsed target
3. For introduction-path queries, traverse `event_participant.role = 'introducer'` chains
4. Blend with a semantic fallback (`/search`-style) when the structured query returns nothing — better a fuzzy hit than an empty result for a fuzzy question

---

## 7. iOS App Structure

SwiftUI, MVVM. Key structural notes:

- **Networking layer** talks to the FastAPI backend directly (not Supabase client SDK for app data — keep Supabase as backend-only infra so business logic/validation lives server-side, not duplicated on-device). Supabase Auth SDK is fine to use directly for login.
- **Realtime updates:** subscribe to Supabase Realtime (via a lightweight backend-proxied channel, or directly if acceptable to expose read-only realtime to the client) for proposal counts and event processing status, so the "5 events awaiting review" badge updates live without polling.
- **Feature modules** (`Capture`, `Recall`, `Discover`, `Review`) should be independently navigable — no feature should hard-depend on another's view models, only on shared `Models`/`Networking`.
- **Offline behavior:** recording should work offline (local audio file), with upload/transcription queued for when connectivity returns. Everything downstream of transcription requires connectivity — no on-device LLM fallback planned.

---

## 8. Auth & Security

Even single-user, auth is not optional:
- Supabase Auth (email/passwordless or Sign in with Apple, given iOS-native) gates the API — every FastAPI route depends on a valid JWT via `core/security.py`.
- Audio and transcripts contain sensitive personal data about third parties (people in Abdoul's life who never consented to being in this system) — encrypt at rest (Supabase default), and do not log raw transcript content in application logs beyond what's needed for pipeline debugging (Section 4's debug logging should go to a restricted-access store, not general logs).
- No data leaves the Supabase/OpenAI-or-equivalent-LLM-provider boundary beyond what's necessary for transcription and extraction calls.

---

## 9. Testing Strategy

- **Pipeline steps:** unit tests with fixed transcript fixtures → assert exact proposal/fact-shape output. This is the highest-value test surface — extraction quality regressions are the most likely failure mode in this system.
- **Append-only invariants:** integration tests that assert `fact`/`relationship_state` are never mutated in place — e.g. a test that creates a fact, creates a superseding fact, and asserts the original row's `value` is untouched and `valid_to`/`superseded_at` is set correctly.
- **API contract tests:** schema validation on every endpoint (Pydantic already gives most of this for free).
- **iOS:** standard XCTest for view models; UI tests for the Capture → Review flow specifically, since it's the highest-risk UX surface (per prior discussion — worth deliberately testing early and thoroughly).

---

## 10. Build Sequencing Guidance (not a rigid roadmap)

Full scope is in play from the start — this is guidance on dependency order, not a cut-down MVP:

- **Build Capture → Recall first.** This is both the highest UX-risk surface (extraction quality, review friction) and the only source of real data — Discover and network reasoning are untestable against an empty database, so building them first means designing blind.
- **Discover/network intelligence follows**, once there's a meaningful body of captured, confirmed data to reason over.
- Get real usage on the Capture review loop specifically before investing further polish into Discover — if the extraction pipeline's accuracy or the review UX needs rework, better to learn that before more is built on top of assumptions about clean data.

This is a technical/product sequencing note for whichever agent executes the build — not a scope cut.
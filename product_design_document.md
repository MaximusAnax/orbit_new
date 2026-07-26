**Status:** In progress — data model, architecture, and core UX flows defined. MVP scope pending.
**Primary user:** Abdoul (personal tool, single-user, no multi-tenant concerns)
**Platform:** Mobile app (voice capture is core to the product)
**Backend:** Cloud-backed (Postgres + pgvector), even for single-user, to support cross-device sync and future extensibility

This document builds on the original Orbit concept doc (vision, principles, and product constitution) and translates it into concrete technical and UX decisions.

---

## 1. Data Model

### 1.1 Design principles reflected in the schema

- History is **append-only**, never overwritten (Product Constitution, Principle 2)
- Nothing is finalized without explicit confirmation (Principle 5)
- Relationship state is subjective and kept separate from objective event/fact history (Section 12)
- Ambiguity is flagged, never silently resolved (Section 11, Principle 4)

### 1.2 Core entities

**Person**

- `id`, `name`, `preferred_name`, `photo_url`, `pronunciation_note`
- `orbit` (inner / close / active / extended / outer) — explicitly not a computed score (Principle 6)
- `created_at`, `linked_phone_contact_id` (optional)

**ContactPoint**

- `id`, `person_id`, `type` (phone / email / instagram / linkedin / x / website / other), `value`, `is_primary`

**Event** (immutable once confirmed)

- `id`, `date`, `location`, `raw_transcript`, `summary`, `source` (voice / text / manual)
- `status`: `captured` → `confirmed` → `synced`
- Many-to-many with Person via **EventParticipant**

**EventParticipant**

- `event_id`, `person_id`, `role` (attendee / introducer / introduced / mentioned-not-present)
- `per_person_notes` — what was learned about this specific person at this event

**Fact** (append-only — the mechanism that guarantees history is never lost)

- `id`, `person_id`, `category` (job / location / school / interest / goal / family / other)
- `value`, `status` (current / past / uncertain)
- `valid_from`, `valid_to` (null if ongoing)
- `source_event_id`, `confidence`
- A new job doesn't edit an existing row — it inserts a new Fact and marks the prior one `status = past` / sets `valid_to`. "Current profile" is simply the latest Fact per category where `status = current`.

**ProfileUpdateProposal** (implements the Captured → Confirmed → Synced → Finalized pipeline)

- `id`, `event_id`, `person_id`, `proposed_fact_json`
- `decision` (pending / accepted / edited / rejected / deferred), `resolved_at`
- Only `accepted`/`edited` proposals ever get materialized into live `Fact` rows. Nothing unreviewed affects what Recall or Discover surface.

**RelationshipState** (subjective, historized — same append-only pattern as Fact)

- `id`, `person_id`, `description`, `desired_closeness`, `desired_cadence`
- `direction` (optional — e.g. "want to grow closer," "naturally stable," "letting drift")
- `recorded_at`, `superseded_at` (null if current)
- `source` (explicit / inferred — always flagged which)
- **Decision:** Orbit may *suggest* a RelationshipState update (e.g. noticing increased contact frequency), but never auto-writes one. This field stays under Abdoul's direct authority more strictly than any other field in the schema, since it's the one place that's purely his own interpretation of the relationship.

**Task / FollowUp**

- `id`, `person_id`, `event_id` (source), `description`, `status` (open / done), `due_date` (optional)

**Organization** (company / school — first-class entity for network intelligence)

- `id`, `name`, `type` (company / school / other)
- Linked from Fact rows so queries like "who do I know at Anthropic" are a join, not a text search

**AmbiguityFlag** (implements "don't silently guess" — Section 11)

- `event_id`, `raw_fragment`, `candidate_person_ids`, `resolved_person_id` (nullable)

**Embedding** (semantic search layer)

- `id`, `source_type` (fact / event / person_summary), `source_id`
- `vector` (pgvector), `text_content`, `model_version`, `created_at`
- Kept as a separate table rather than a column on Fact/Event so that:
    - `person_summary` embeddings (periodically regenerated rollups of everything known about someone) can exist without mapping to a single Fact
    - Re-embedding after a model upgrade is isolated from core tables

### 1.3 Query patterns

- **"Current profile"** — latest Fact per category where `status = current`, joined with the latest non-superseded RelationshipState
- **Semantic search** ("who mentioned videography") — embed query → vector search across `Embedding` (fact/event scoped) → join back to Person
- **Network/graph queries** ("who can introduce me to someone at Anthropic") — structured traversal: Person → Fact (category=job) → Organization, not vector search. Fuzzy asks ("who'd be good for this project") blend both paths.

---

## 2. System Architecture

### 2.1 Backend shape

Mobile app → REST/GraphQL API → Postgres + pgvector. Voice audio is uploaded to a cloud transcription service (not on-device), prioritizing simplicity and transcription accuracy over the added privacy of on-device processing.

### 2.2 The Capture pipeline

A sequenced, multi-call pipeline rather than a single monolithic extraction call — chosen for accuracy over speed/cost, since correctness (Principle 4) matters more than latency here.

1. **Transcription** — audio → text via cloud API (e.g. Whisper-class model). Output: `Event.raw_transcript`.
2. **Entity Resolution call** — input: transcript + existing Person list (names + key identifying context, not full profiles, to keep the prompt lean). Output: matched `person_id` per reference, "new person" flags, or `AmbiguityFlag` rows where confidence is low. This is the gate everything downstream passes through.
3. **Per-person Fact Extraction call** — run once per resolved person (parallelizable). Input: the transcript + that person's existing current Facts, so the model can correctly distinguish "still true" from "this supersedes something." Output: `ProfileUpdateProposal` rows per category, marked current or past-superseding.
4. **Task/Follow-up Extraction call** — one global pass over the full transcript, since commitments often span multiple people (e.g. "I'll introduce Sarah to James").
5. **Emotional/Topical Context call** — one global pass, output attached per-person where attributable, else attached to the Event itself.
6. **Group Attribution reconciliation** — for multi-person events, a final lightweight pass cross-checks per-person outputs against each other, flagging conflicts (e.g. a fact attributed to two people at once) rather than resolving them silently.

Each call is independently retryable and debuggable. A low-confidence result at step 2 for a given person means steps 3–5 are skipped for them, and the ambiguity is surfaced directly rather than the pipeline guessing or failing outright.

### 2.3 Data integrity guarantee

Nothing produced by the pipeline touches the live `Fact`/`RelationshipState` tables directly — everything lands as a `ProfileUpdateProposal` first. Recall and Discover only ever read from confirmed, accepted data. This means unreviewed extraction output is fully inert until Abdoul acts on it.

---

## 3. Core UX Flows

### 3.1 Capture

1. **Entry** — prominent record button, minimal friction to start
2. **Recording** — waveform/timer, optional photo attachment
3. **Background processing** — transcription and the full extraction pipeline run automatically the moment recording stops
4. **Immediate save, deferred review** — the Event saves right away (`status = captured`). No blocking review screen. A lightweight badge ("3 people mentioned, 6 proposed updates") appears on the event, plus a small persistent global counter (e.g. "5 events awaiting review") — visible, never modal or interruptive
5. **Review, whenever** — Abdoul opens an event later, sees proposals grouped by person as toggle-able chips, ambiguity flags surfaced inline, tasks as a checklist. Accept-all / per-item / defer options. Proposed facts have zero effect on the live profile until accepted.

This directly implements Principle 3 (capture should be effortless) and the doc's original Captured/Confirmed/Synced/Finalized distinction (Section 10) — the UX and data model stay consistent with each other by construction.

### 3.2 Recall (pre-meeting experience)

1. **Entry** — search or tap a person from a contacts-style list
2. **Profile view** — structured as a story, not a form:
    - Header: name, photo, orbit, last interaction date
    - "Where things stand" — current facts, with a subtle "previously: X" affordance rather than a buried history tab
    - Timeline — reverse-chronological events
    - Open threads — unresolved follow-ups, things they were waiting on
    - Relationship state — Abdoul's own words, editable inline
3. **"Catch me up" view** — generated on-demand (not pre-computed, to avoid staleness and wasted compute):
    - Input to the LLM: current Facts, last 3–5 Events, open Tasks, current RelationshipState — explicitly *not* raw transcripts, to keep it fast and to avoid resurfacing anything Abdoul deliberately rejected during review
    - Output: a short synthesized paragraph ("It's been 8 months since you last saw Sarah...") plus 3–5 talking points, weighted toward open threads — a direct implementation of Principle 9 (context before contact)

### 3.3 Discover

1. **Entry** — natural-language search bar ("who do I know at Anthropic")
2. **Results** — people cards that surface *why* they matched ("Works at Anthropic since 2025" / "Mentioned interest in AI agents, Mar 2025"), not a bare name list
3. **Reasoning/network view** (later-stage capability) — path visualization for introduction-style queries (e.g. Alex → introduced you to → Sarah → works with → James at Anthropic)

---

## 4. Open Decisions / Deferred

- **Maintenance cadence computation** — how Orbit weighs relationship type, desired closeness, and natural rhythm to surface "who should I reach out to" (Section 14). Deferred for a dedicated design pass.
- **MVP scope** — what ships in v1 vs. later phases. Not yet defined.
- Minor schema questions (e.g. whether Organization needs its own history table for renames/acquisitions) — low priority, revisit if it becomes relevant.

---

## Appendix: Source Material

This document operationalizes the vision, principles, and product constitution defined in the original Orbit concept document (pillars of Perfect Relationship Recall, Network Intelligence, and Memory Fidelity; the 12-principle Product Constitution; and the Capture → Remember → Discover product loop).
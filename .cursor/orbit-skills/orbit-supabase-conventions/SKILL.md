---
name: orbit-supabase-conventions
description: Use for Supabase schema, migrations, RLS, Storage (audio), Realtime, and pgvector in Orbit. Trigger on migration files, supabase/ config, or any direct Postgres access from the backend.
---

# Orbit Supabase Conventions

## Source of truth

- DDL lives in `orbit-backend/app/db/schema.sql` (mirrors `technical_design_document.md` §2)
- Applied via `supabase/migrations/` — create with `supabase migration new <name>`, never hand-invent filenames

## Access model

- **Backend (FastAPI):** uses `service_role` / direct Postgres connection for all writes. Business logic and invariants live server-side.
- **iOS:** Supabase Auth SDK for JWT only. No direct table reads/writes from the client for app data.
- **Realtime:** subscribe to `event` and `profile_update_proposal` for processing status and review badges (read-only from client if exposed).

## RLS

Enable RLS on all `public` tables. Single-user app today, but policies should require `auth.role() = 'authenticated'` (or `auth.uid()` when user_id columns are added). Backend service role bypasses RLS — never expose service_role to the client.

## Storage

- Bucket: `event-audio` (private)
- Path pattern: `{user_id}/{event_id}.m4a` (or similar)
- Audio uploaded by backend after `POST /events`; URLs stored on event or passed to transcription step

## pgvector

- Extension: `vector`
- `embedding.vector` is `vector(1536)`; index `ivfflat` with `vector_cosine_ops`
- Re-embed on model version change without touching core `fact`/`event` rows

## Migrations checklist

- [ ] Extensions: `vector`, `pgcrypto`
- [ ] All TDD tables and indexes present
- [ ] RLS enabled + policies for authenticated read where client Realtime needs access
- [ ] No silent renames of TDD column names

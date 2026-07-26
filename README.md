# Orbit

Personal AI memory for relationships — capture events by voice, review proposed profile updates, recall people before you meet them, discover connections in your network.

**Primary user:** Abdoul (single-user)

## Stack

- **Backend:** Python, FastAPI, Supabase (Postgres + pgvector + Auth + Storage)
- **iOS:** Swift, SwiftUI
- **AI:** Cloud transcription + structured LLM extraction pipeline

## Docs

| Document | Purpose |
|----------|---------|
| [overview.md](overview.md) | Vision, principles, product constitution |
| [product_design_document.md](product_design_document.md) | Data model & UX flows |
| [technical_design_document.md](technical_design_document.md) | Implementation spec |
| [AGENTS.md](AGENTS.md) | Guide for coding agents |

## Repo layout

```
orbit-backend/     API + extraction pipeline
orbit-ios/         iOS app
supabase/          Database migrations
.cursor/orbit-skills/   Agent skills
```

## Quick start

### Prerequisites

- Python 3.11+
- [Supabase CLI](https://supabase.com/docs/guides/cli)
- Xcode 15+ (for iOS)

### Backend

```bash
cd orbit-backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill Supabase + OpenAI keys
uvicorn app.main:app --reload --port 8000
```

### Database

```bash
supabase start
supabase db reset
```

### Try it without Xcode

With the backend running (`AUTH_DISABLED=true` in `orbit-backend/.env`):

Open **http://127.0.0.1:8000/playground/** — Capture → Review → Recall in the browser.

API docs remain at http://127.0.0.1:8000/docs


### Tests

```bash
cd orbit-backend && pytest
```

## Core invariants

- Events save immediately; profile changes require explicit proposal review
- `fact` and `relationship_state` are append-only
- Extraction never writes live facts — only `profile_update_proposal`

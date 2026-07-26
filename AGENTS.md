# Orbit — Agent Guide

Personal relationship memory system. Build spec: [`technical_design_document.md`](technical_design_document.md).

## Start here

1. Read [`orbit-session-bootstrap`](.cursor/orbit-skills/orbit-session-bootstrap/SKILL.md) skill
2. Load task-specific skills from [`.cursor/orbit-skills/`](.cursor/orbit-skills/)

## Build order (TDD §10)

**Capture → Recall → Discover.** Do not skip proposal gating or append-only history.

## Skill index

| Skill | Use for |
|-------|---------|
| `orbit-session-bootstrap` | Session start, doc routing |
| `orbit-data-invariants` | fact, relationship_state, proposals |
| `orbit-api-conventions` | FastAPI routes |
| `orbit-pipeline-conventions` | Extraction pipeline |
| `orbit-testing-conventions` | Tests |
| `orbit-supabase-conventions` | DB, migrations, RLS, Storage |
| `orbit-ios-conventions` | SwiftUI app |
| `orbit-search-discover` | /search, /discover |
| `orbit-security-pii` | Auth, logging, PII |

## Layout

```
orbit-backend/     FastAPI + pipeline
orbit-ios/         SwiftUI client
supabase/          Migrations
overview.md        Vision & constitution
product_design_document.md
technical_design_document.md
```

## Commands

```bash
# Backend
cd orbit-backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Tests
cd orbit-backend && pytest

# Supabase (from repo root)
supabase start
supabase db reset
```

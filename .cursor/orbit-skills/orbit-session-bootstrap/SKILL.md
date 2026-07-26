---
name: orbit-session-bootstrap
description: Use at the start of any Orbit implementation session. Loads the right docs and skills by task type, enforces build order Capture→Recall→Discover, and prevents shortcuts that violate proposal gating or append-only history.
---

# Orbit Session Bootstrap

## Canonical docs (read before coding)

| Doc | When |
|-----|------|
| `overview.md` | Product principles, constitution, UX rationale |
| `product_design_document.md` | Data model intent, UX flows, deferred scope |
| `technical_design_document.md` | Schema, API, pipeline, iOS, auth, tests |

## Build order (TDD §10)

1. **Capture → Recall** — schema, pipeline, proposals, people profile, catchup, iOS Capture/Review/Recall
2. **Discover** — only after Capture path produces confirmed data (`/search`, `/discover`, embeddings)

Do not build Discover-first on an empty database. Do not cut MVP scope by skipping proposal gating or append-only `fact`/`relationship_state` rules.

## Skills by task type

| Task | Load these skills |
|------|-------------------|
| Any DB write on person/fact/proposal/relationship_state | `orbit-data-invariants` |
| `app/api/routes/*` | `orbit-api-conventions` + `orbit-data-invariants` |
| `app/pipeline/*` | `orbit-pipeline-conventions` + `orbit-data-invariants` + `orbit-security-pii` |
| Tests | `orbit-testing-conventions` |
| Supabase schema/migrations/RLS/Storage/Realtime | `orbit-supabase-conventions` |
| `orbit-ios/*` | `orbit-ios-conventions` + `orbit-api-conventions` |
| `/search` or `/discover` | `orbit-search-discover` |
| Auth, logging, PII | `orbit-security-pii` |

## Repo layout

```
orbit-backend/   # FastAPI, pipeline, db
orbit-ios/       # SwiftUI app
AGENTS.md        # Agent entry point
```

## Non-negotiables

- `POST /events` returns **202** immediately; pipeline runs async
- Extraction writes **proposals only**, never live `fact` rows
- `fact` and `relationship_state` are **append-only**
- iOS uses FastAPI for app data; Supabase Auth SDK only for login

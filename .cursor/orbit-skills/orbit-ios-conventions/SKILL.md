---
name: orbit-ios-conventions
description: Use when building or modifying the SwiftUI iOS app (orbit-ios/). Covers MVVM, networking via FastAPI, Supabase Auth only, offline capture, and feature module boundaries.
---

# Orbit iOS Conventions

Reference: `technical_design_document.md` §7.

## Architecture

- **SwiftUI + MVVM** per feature module
- **Networking:** `Networking/APIClient.swift` talks to FastAPI `/api/v1` with Supabase JWT in `Authorization: Bearer`
- **Auth:** Supabase Auth SDK (Sign in with Apple / email) — not used for Postgres data access
- **Models:** shared `Models/` types mirror Pydantic API responses

## Feature modules

| Module | Responsibility |
|--------|----------------|
| `Capture/` | Record audio (offline OK), queue upload, show processing badge |
| `Review/` | Proposals by person, accept/edit/reject/defer, ambiguity flags |
| `Recall/` | Profile story, timeline, catch me up |
| `Discover/` | Search bar, results with match rationale (Phase 5) |

Features must not import each other's view models — only `Models` and `Networking`.

## Capture UX rules

- Recording works **offline**; persist local file, upload when online
- `POST /events` → expect **202**; poll or Realtime for pipeline completion
- Never block save on review — event is `captured` immediately
- Deferred proposals must not change displayed "current" profile

## Testing priority

UI tests for **Capture → Review** first (highest product risk). XCTest for view models on proposal actions and profile assembly.

## Design

Follow product doc: memory-like, not CRM. Profile is a story, not a form.

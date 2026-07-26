---
name: orbit-search-discover
description: Use when implementing or modifying GET /search, GET /discover, embedding generation, or Discover UI. Keeps semantic search and graph traversal separate per TDD §6.
---

# Orbit Search & Discover

Reference: `technical_design_document.md` §6.

## Two paths — never unify

### `/search` (semantic)

1. Embed query (same model/version as `embedding.model_version`)
2. Cosine distance: `ORDER BY vector <=> $query LIMIT n`
3. Join `embedding` → `fact`/`event` → `person`
4. Prefer `fact.status = 'current'` unless query implies history

### `/discover` (structured graph)

1. LLM structured parse: target org/skill/interest + query type (direct vs introduction path)
2. SQL traversal: `person → fact → organization`, interest facts, `event_participant` introducer chains
3. If structured query returns empty → **semantic fallback** from `/search` logic (fuzzy hit beats empty)

## Data scope

- Only **confirmed** data: accepted facts, confirmed events — no pending/rejected proposals
- Embeddings generated when facts/events are accepted, not at proposal time

## API responses

Surface **why** each person matched (e.g. "Works at Anthropic since 2025"), not bare names.

## iOS Discover

Natural-language search bar; results cards with rationale; introduction-path visualization is later-stage.

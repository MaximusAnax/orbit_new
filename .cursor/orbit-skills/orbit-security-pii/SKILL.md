---
name: orbit-security-pii
description: Use for auth, JWT validation, logging, transcript/audio handling, and any code that touches third-party personal data in Orbit.
---

# Orbit Security & PII

Reference: `technical_design_document.md` §8.

## Auth

- Every FastAPI route under `/api/v1` requires valid Supabase JWT (`core/security.py`)
- Single-user today; auth is still mandatory
- Relationship-state user endpoint hard-sets `source = 'explicit'` — not client-overridable

## PII boundaries

- Audio and transcripts describe **third parties** who did not consent to the system
- Encrypt at rest (Supabase default)
- **Do not** log raw transcript text in general application logs
- Pipeline LLM raw I/O → restricted debug store only (`pipeline_debug_log` or equivalent), not stdout

## External services

- Transcription and LLM calls: send only what each step needs
- No copying full contact DB into prompts — entity resolution uses lean name + key context index

## Client

- Never ship `service_role` key in iOS
- JWT in Keychain; refresh via Supabase Auth SDK

## Checklist

- [ ] New route has auth dependency
- [ ] New log line does not include transcript or proposal PII
- [ ] New pipeline step logs to restricted store, not default logger

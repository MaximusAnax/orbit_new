# TickerPress — FR Coverage

Produced by the hardening pass (2026-07-31). Every FR in SCOPE.md is mapped to
the tests and/or eval gates that exercise it; status is the observed result of
`uv run python verify_all.py tickerpress` on this tree (377 tests passing,
27/27 eval metrics passing, lint clean, CLI exercised end to end).

Eval metric names refer to `evals/run.py` / `evals/test_gates.py`; test names
are real functions in `tests/` (all names carry their FR id, so
`grep -r fr7_ tests/` enumerates FR-7's coverage).

| FR | Requirement (short) | Covering tests / metrics | Status |
|---|---|---|---|
| FR-1 | Watchlist management: ticker validation, generated aliases, suffix strip, duplicate rejection, per-company terms | `tests/test_watchlist.py` (19 tests: `test_fr1_*` — ticker regex, US-1 generated-alias set, ISO-20275 suffix stripping, D20 collision rule, common-word demotion, term case-folding/dedup, prior bounds); CLI path `test_fr15_company_*`, `test_fr15_alias_*`, `test_fr15_term_*`; API path `test_fr14_create_company_*`, `test_fr14_alias_post_*` | PASS |
| FR-2 | Feed registry & fetching: FetchResult statuses, error isolation, ascending-feed-id order, conditional-GET state | `tests/test_feeds.py` (21 tests: `test_fr2_*` — fixture source, env-gated live adapter, 15-min poll spacing, broken-feed isolation, all-failed ⇒ `failed`, counter reconciliation) | PASS |
| FR-3 | Feed parsing & normalization (pure): RSS/Atom subset, guid fallback, RFC 822/3339 dates, HTML strip, NFC, sentence split, tokenization with offsets | `tests/test_feedparse.py` (12 tests), `tests/test_normalize.py` (19 tests: hyphen/slash/dot separators, abbreviation dots, title-as-one-sentence, all-caps runs, offset stability) | PASS |
| FR-4 | Article archive & identity: (feed,guid) uniqueness, canonical URL, immutability, first-version-wins, content hashes | `tests/test_archive.py` (8 tests: canonical-URL table, idempotent canonicalization, D16 first-version-wins, content_token_count ↔ content nullity) | PASS |
| FR-5 | Candidate scanning (hard part A): per-kind case rules, 1-char tickers never bare, common-word demotion, cashtag/exchange patterns, emission order | **M1 = 1.000 (≥0.92), M1_amb = 1.000 (≥0.85), M1_amb_base = 1.000 (≥0.82)** + `tests/test_detect.py` (19 tests: `test_fr5_*`) | PASS |
| FR-6 | Disambiguation scoring (hard part A): FR-6 formula, θ = 0.35, the five scoring rules, four worked examples, full feature persistence | **M1 family + M1_amb_abl = 0.984 (≥0.75), M6_lex = 1.0** + `tests/test_disambig.py` (28 tests: worked examples with exact feature vectors, each rule in isolation, θ boundary inclusive/exclusive, weight audit `test_fr6_weights_match_the_scope_formula`) | PASS |
| FR-7 | Syndication dedup (hard part B): 3-shingles, exact Jaccard, τ = 0.60, ±7-day window, fast paths, deterministic tie-breaks, representative re-pointing | **M2 P_pair = 1.000 (≥0.95), R_pair = 0.917 (≥0.85), M2_trap = 0 (=0), M2_near = 5 (≥4)** + `tests/test_dedup.py` (21 tests) | PASS |
| FR-8 | Relevance scoring: round_half_up formula, lede rule over content_token_count, story-level max | **M3 = 1.000 (≥0.95), M3_cov |O| = 226 (≥55) with 12/12 traps** + `tests/test_relevance.py` (16 tests: banker's-rounding trap, attainable-value set, NULL-content path, story max rollup) | PASS |
| FR-9 | Digest composition: selection filters, ordering law, template render, quiet-when-empty, inert dry run | **M4 checks 1 and 6** + `tests/test_digest.py` (`test_fr9_*`, `test_d14_footer_is_part_of_the_committed_template`) | PASS |
| FR-10 | Alerts: threshold boundary, mode matrix, shared ledger, alert-channel routing | **M4 check 3** + `tests/test_alerts.py` (10 tests: inclusive threshold, passing mentions never alert, re-ingest never re-alerts, per-channel exactly-once) | PASS |
| FR-11 | Exactly-once ledger: compose→persist→send→mark, partial unique index, failure release | **M4 = 1.0 (all six checks incl. the live `IntegrityError` proof), M5 = 1.0** + `tests/test_ledger.py` (11 tests: constraint present in the in-memory backend too) | PASS |
| FR-12 | Notifier adapters: console/file byte-identity, deterministic outbox names, env-gated live email/webhook | `tests/test_notifiers.py` (16 tests: outbox file equals `body_text` byte-for-byte, Slack-shape payload, unconfigured live channel refused) | PASS |
| FR-13 | Explainability: full features for accepted **and** rejected candidates, story membership, read-only | `tests/test_explain.py` (9 tests incl. `test_fr13_explain_reads_rows_and_never_recomputes`) + CLI `test_fr15_explain_*`, API `test_fr14_explain_*` | PASS |
| FR-14 | API per sketch, thin | `tests/test_api.py` (25 tests: status-code mapping, 204-on-empty digest both modes, dry-run persists nothing, 503 on unconfigured live channel) | PASS |
| FR-15 | CLI per sketch, `--now`, `--json`, exit codes | `tests/test_cli.py` (16 tests) + hardening-pass end-to-end run of every documented command against real fixture feeds (init → company/alias/term/feed → ingest → articles/stories/explain → digest dry-run/run/list/show → re-ingest idempotency → broken-feed isolation → mute/remove) | PASS |
| FR-16 | Determinism & hermeticity: time injected, no randomness, cross-`PYTHONHASHSEED` identity | **M5 = 1.0** (cross-process PYTHONHASHSEED 0 vs 12345 + committed golden hashes) + `tests/test_determinism.py` (6 tests: byte-identical double runs, engine purity scan, no lexicon reads from `engine/`); hardening pass ran the full eval suite twice — JSON summaries identical | PASS |

No FR is unimplemented or partially implemented. The one CLI defect found in
the hardening pass (`term add` silently discarding repeated `--context`/`--anti`
flags) was fixed and pinned by `test_fr15_term_add_appends_company_terms`.

## Falsifiability record (hardening pass)

Each hard-part gate was shown to fail under a targeted engine degradation and
to recover exactly after revert (details in REVIEW.md H-table):

| Gate | Mutation | Healthy | Mutated | Verdict |
|---|---|---|---|---|
| M1_amb (≥0.85) | accept every weak candidate | 1.000 | **0.611** | falsifiable |
| M1_amb (≥0.85) | reject every weak candidate | 1.000 | **0.575** (R=0.403; also trips M3_cov: \|O\|=49) | falsifiable |
| M2 R_pair (≥0.85) | Jaccard join disabled (URL fast path only) | 0.917 | **0.139** | falsifiable |
| M2 P_pair (≥0.95) / M2_trap (=0) | τ 0.60 → 0.10 (greedy over-merge) | 1.000 / 0 | **0.935 / 5 merged traps** | falsifiable |
| M3 (≥0.95) | placement-blind ranking (count only) | 1.000 | **0.801** | falsifiable |

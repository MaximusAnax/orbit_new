# datasweep — DATA MODEL

All domain models are Pydantic v2 classes in `engine/models.py`; the store maps
persistent entities to SQLite (stdlib `sqlite3`). Times are ISO-8601 UTC
strings supplied by callers via the Clock port (the engine never reads the
clock). Ids are UUID4 strings unless noted. Cell coordinates are always
**original parsed-table coordinates**: `row` is the 0-based data-row index
(header excluded), `col` is the 0-based column index; audit entries and issues
never use post-transform coordinates (SCOPE.md D4).

## 1. Entity overview

```
WatchedFolder 1 ──< SourceFile 1 ──< Run
Run 1 ──< ColumnProfile
Run 1 ──< IssueSummary
Run 1 ──< ReviewItem
Run 1 ──< Revision            (revision 1 = the auto-clean itself)
Run ──> artifact directory    (cleaned.*, audit.jsonl, report.md — files, not rows)
```

Two kinds of persistence, deliberately split:

- **SQLite** holds *state and summaries*: config, run history, profiles,
  aggregated issues, the review queue, revisions. Everything queryable.
- **Artifact files** hold *cell-level detail*: the cleaned table, the audit
  log (one JSONL line per change), the Markdown report. Immutable once
  written; the DB stores their paths. Rationale: audit logs are O(cells) and
  belong next to the data they describe; the DB stays small and fast.

## 2. Entities

### 2.1 WatchedFolder

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `path` | str, required | absolute; unique |
| `recursive` | bool | default true |
| `include` | list[str] (JSON) | glob patterns, default `["*.csv","*.tsv","*.xlsx","*.jsonl"]` |
| `policy_path` | str \| None | TOML file; None → built-in defaults |
| `output_dir` | str | default `<path>/.datasweep`; absolute allowed |
| `enabled` | bool | default true |
| `created_at` | datetime | |

**Invariants:** `path` unique and stored absolute/normalized. `output_dir` is
always excluded from scanning regardless of globs. Deleting a folder does not
delete SourceFiles or Runs (history is the audit trail).

### 2.2 SourceFile

One row per distinct path ever observed (watched or ad-hoc).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `path` | str, required | absolute; unique |
| `folder_id` | str \| None, FK WatchedFolder | None for ad-hoc `clean FILE` |
| `first_seen_at` | datetime | |
| `last_seen_at` | datetime | updated each scan that observes it |

**Invariants:** `path` unique. Content identity lives on Run
(`content_sha256`), not here — one path legitimately has many content
versions over time.

### 2.3 Run (append-only)

One processing attempt of one content version of one file under one policy.

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `file_id` | str, FK SourceFile | |
| `content_sha256` | str (64 hex) | hash of source bytes, computed before parse |
| `policy_hash` | str (16 hex) | sha256[:16] of the canonical-JSON effective policy |
| `policy_snapshot` | JSON | full effective Policy (defaults ⊕ file ⊕ CLI overrides) |
| `engine_version` | str | package version |
| `started_at` / `finished_at` | datetime | injected via Clock |
| `status` | enum `succeeded \| review_pending \| failed \| skipped` | `review_pending` = succeeded with a non-empty review queue |
| `trigger` | enum `scan \| manual \| forced` | |
| `format` | enum `csv \| tsv \| xlsx \| jsonl` \| None | None for failed-at-sniff |
| `encoding` | str \| None | e.g. `utf-8`, `cp1252`; recorded from EncodingDetector |
| `dialect` | JSON \| None | `{delimiter, quotechar, has_header, sheet}` |
| `n_rows` / `n_cols` | int \| None | parsed original dimensions (data rows) |
| `issue_counts` | JSON | per class: `{"ENC":0,"WS":41,...}` (8 classes + `STR`) |
| `change_counts` | JSON | per tier: `{"auto": 57, "review": 9, "report": 12}` — review/report counts are *proposals/findings*, not applied changes |
| `artifact_dir` | str \| None | `<output_dir>/<stem>.<sha256[:8]>/` |
| `error` | str \| None | set iff status = failed |

**Invariants:** append-only — never updated after `finished_at` is set, never
deleted. Idempotence (FR-2): before processing, if any Run with the same
(`content_sha256`, `policy_hash`, `engine_version`) has status
`succeeded | review_pending`, the new pass records a `skipped` Run (no
artifacts) unless forced. `artifact_dir` is non-null iff status ∈
{succeeded, review_pending}. Reproducibility: (`content_sha256`,
`policy_snapshot`, `engine_version`) determine the artifacts byte-for-byte
(FR-16).

### 2.4 ColumnProfile

| Field | Type | Notes |
|---|---|---|
| `run_id` | str, FK Run | composite PK with `col_index` |
| `col_index` | int ≥ 0 | original column index |
| `name` | str | header after structural repair (e.g. deduped `amount_2`) |
| `original_name` | str \| None | pre-repair header when it differs |
| `inferred_type` | enum `bool \| digits \| integer \| float \| datetime \| date \| categorical \| text` | SCOPE.md FR-5 |
| `type_coverage` | float [0,1] | share of non-null cells matching `inferred_type` |
| `non_null` | int | after sentinel normalization |
| `distinct_count` | int | |
| `stats` | JSON | type-dependent: numeric `{min,q1,median,q3,max,mad}`; date `{min,max,formats:{"%d/%m/%Y":412}}`; categorical `{top:[["USA",950],...]}`; number convention `{grouping:",",decimal:".",currency:"EUR"}` where inferred |

**Invariants:** one row per (run, col_index), written once with the run.
Camelot-style rule: derived values (e.g. type_coverage) are recomputed by
evals from the audit + cleaned table and must match — profiles cannot drift
from the data they summarize.

### 2.5 IssueSummary

Aggregated per (run, detector class, column) for queryability; cell-level
instances live in `audit.jsonl` (for fixed issues) and `report.md` /
ReviewItem `cells` (for proposed/reported ones).

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `run_id` | str, FK Run | |
| `klass` | enum `ENC \| STR \| WS \| MISS \| TYPE \| DATE \| CAT \| DUP \| OUT` | |
| `col_index` | int \| None | None for row/file-scope classes (DUP, STR-file) |
| `cell_count` | int ≥ 1 | affected cells (rows for DUP) |
| `disposition` | enum `fixed \| proposed \| reported` | tier outcome at plan time |
| `samples` | JSON | ≤ 10 examples: `[{row, before, after?}, ...]` |
| `evidence` | JSON | rule-specific (e.g. `{format_from:"%d/%m/%Y", proof_row: 17}`) |

**Invariants:** written once with the run; `Σ cell_count` per class equals
`Run.issue_counts[klass]` (checked by tests).

### 2.6 ReviewItem

One human decision unit. An item is an *action* (possibly many cells): "merge
label `U.S.A.` → `USA` (12 cells)", "interpret column `date` as day-first
(240 cells)".

| Field | Type | Notes |
|---|---|---|
| `id` | str, PK | 8-hex short id, unique per run, deterministic: sha256(run_id + rule + col + first_cell)[:8] — stable across re-runs for the same content |
| `run_id` | str, FK Run | |
| `rule` | str | e.g. `fix.date_canon_ambiguous`, `fix.label_merge_nn` |
| `col_index` | int \| None | |
| `description` | str | one human line, e.g. "date: ambiguous d/m — propose day-first" |
| `proposal` | JSON | rule-specific; always includes per-cell `[{row, col, before, after}]` under `cells`, plus alternatives where relevant (`{candidates: [{label:"day-first", ...}, {label:"month-first", ...}]}`) |
| `affected_cells` | int | = len(proposal.cells) |
| `confidence` | float [0,1] | from the D12 formula |
| `status` | enum `pending \| accepted \| rejected` | |
| `decided_at` | datetime \| None | |

**Invariants:** status transitions exactly once, `pending → accepted` or
`pending → rejected`; rows are never deleted. A rejected item's (rule,
col_index, proposal-hash) is never re-proposed for the same `content_sha256`
(services check past runs). Accepting items is only valid while their run is
the file's latest non-skipped run for that content hash.

### 2.7 Revision (append-only)

| Field | Type | Notes |
|---|---|---|
| `id` | str UUID, PK | |
| `run_id` | str, FK Run | |
| `revision_no` | int ≥ 1 | unique per run; 1 = the auto-clean produced by the run itself |
| `created_at` | datetime | |
| `accepted_item_ids` | JSON | [] for revision 1; the newly accepted ReviewItem ids for r ≥ 2 |
| `cleaned_path` | str | `cleaned.csv` (r1) / `cleaned.r2.csv` … |
| `audit_path` | str | `audit.jsonl` (r1) / `audit.r2.jsonl` — the *delta* audit for r ≥ 2 (changes applied on top of r−1) |

**Invariants:** append-only; `revision_no` dense from 1. Reversibility chain:
`revert(revision_r, audit_r) == revision_{r−1}` for r ≥ 2, and
`revert(revision_1, audit_1) == parsed original` (FR-9). Each revision's
files are written atomically and never rewritten.

## 3. Artifact & interchange schemas

### 3.1 Audit log (`audit.jsonl`)

Line 1 is a header entry; every subsequent line is one change. Entries are
ordered by (pipeline stage, col, row) — the exact application order.

```json
{"kind":"header","run_id":"7c9e…","source_path":"/data/in/sales.csv",
 "content_sha256":"9f86d0…","policy_hash":"3a5b12c4d5e6f708",
 "engine_version":"0.1.0","revision":1,"n_rows":412,"n_cols":7}
```

`cell_change` — value edits (WS, ENC, MISS, TYPE, DATE, CAT fixes):

```json
{"kind":"cell_change","rule":"fix.date_canon","tier":"auto","confidence":1.0,
 "row":17,"col":4,"col_name":"order_date","before":"31/01/2023","after":"2023-01-31",
 "evidence":{"format_from":"%d/%m/%Y","proof":"day>12 at row 17"}}
```

`row_drop` — duplicate removal; the full row is retained for reversibility:

```json
{"kind":"row_drop","rule":"fix.drop_duplicate_row","tier":"auto","confidence":1.0,
 "row":58,"before_row":["1042","2023-01-31","19.99","EUR","ACME","tools","paid"],
 "evidence":{"first_occurrence_row":31}}
```

`row_pad`, `header_rename`, `column_add` follow the same shape (`row_pad`
stores padded col indices; `header_rename` stores `before`/`after` names at
`col`; `column_add` stores the synthetic column's index, name, and cells).

**Invariants (FR-8/FR-9):** every difference between the parsed original and
the cleaned table corresponds to exactly one entry; no entry has
`before == after`; coordinates are original-table; applying entries in
reverse order to the cleaned table reproduces the parsed original exactly.

### 3.2 Policy (TOML)

Effective policy = built-in defaults ⊕ folder/CLI policy file ⊕ CLI flags;
the merged result (canonical JSON, sorted keys) is snapshotted on the Run and
hashed into `policy_hash`.

```toml
[general]
settle_seconds = 5
poll_interval_seconds = 5
max_file_mb = 100
max_rows = 500000
xlsx_sheet = 1                    # 1-based; name string allowed

[detectors]                       # true/false per family
enc = true; ws = true; miss = true; type = true
date = true; cat = true; dup = true; out = true

[thresholds]
type_majority = 0.90
auto_confidence = 0.95
merge_dominance = 0.80
nn_max_ratio = 0.05
nn_min_majority = 20
outlier_iqr_k = 3.0
outlier_mad_z = 3.5
outlier_min_n = 20

[tiers]                           # per-rule override: auto|review|report|off
"fix.drop_duplicate_row" = "off"  # example: event-log folder keeps repeats
"fix.label_merge_nn" = "report"   # example: demote typo merges to report-only
```

Unknown keys are a validation error (fail loud, not silent-ignore).

### 3.3 Report (`report.md`) skeleton

`# datasweep report — sales.csv` → summary table (rows/cols, encoding,
dialect, issues by class, changes by tier) → per-column table (name, type,
coverage, non-null, distinct, convention notes) → one section per issue class
with samples → `## Review queue` (item id, description, confidence, cells) →
`## Report-only findings` (outliers with values and fences, numeric
sentinels, coercion failures) → footer (run id, hashes, engine version).

## 4. Storage mapping (SQLite)

Default `~/.datasweep/datasweep.db`; tests use `:memory:` or tmp path. WAL
mode, foreign keys ON, all writes transactional.

```sql
CREATE TABLE watched_folders (
  id          TEXT PRIMARY KEY,
  path        TEXT NOT NULL UNIQUE,
  recursive   INTEGER NOT NULL DEFAULT 1,
  include     TEXT NOT NULL,                 -- JSON list of globs
  policy_path TEXT,
  output_dir  TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

CREATE TABLE source_files (
  id            TEXT PRIMARY KEY,
  path          TEXT NOT NULL UNIQUE,
  folder_id     TEXT REFERENCES watched_folders(id),
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE runs (
  id              TEXT PRIMARY KEY,
  file_id         TEXT NOT NULL REFERENCES source_files(id),
  content_sha256  TEXT NOT NULL,
  policy_hash     TEXT NOT NULL,
  policy_snapshot TEXT NOT NULL,             -- canonical JSON
  engine_version  TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  status          TEXT NOT NULL CHECK (status IN
                    ('succeeded','review_pending','failed','skipped')),
  trigger_kind    TEXT NOT NULL CHECK (trigger_kind IN ('scan','manual','forced')),
  format          TEXT CHECK (format IN ('csv','tsv','xlsx','jsonl')),
  encoding        TEXT,
  dialect         TEXT,                      -- JSON
  n_rows          INTEGER, n_cols INTEGER,
  issue_counts    TEXT NOT NULL DEFAULT '{}',
  change_counts   TEXT NOT NULL DEFAULT '{}',
  artifact_dir    TEXT,
  error           TEXT,
  CHECK ((status IN ('succeeded','review_pending')) = (artifact_dir IS NOT NULL))
);
CREATE INDEX idx_runs_identity ON runs(content_sha256, policy_hash, engine_version);
CREATE INDEX idx_runs_file     ON runs(file_id, started_at);

CREATE TABLE column_profiles (
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  col_index     INTEGER NOT NULL CHECK (col_index >= 0),
  name          TEXT NOT NULL,
  original_name TEXT,
  inferred_type TEXT NOT NULL CHECK (inferred_type IN
                  ('bool','digits','integer','float','datetime','date','categorical','text')),
  type_coverage REAL NOT NULL,
  non_null      INTEGER NOT NULL,
  distinct_count INTEGER NOT NULL,
  stats         TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, col_index)
);

CREATE TABLE issue_summaries (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  klass       TEXT NOT NULL CHECK (klass IN
                ('ENC','STR','WS','MISS','TYPE','DATE','CAT','DUP','OUT')),
  col_index   INTEGER,
  cell_count  INTEGER NOT NULL CHECK (cell_count >= 1),
  disposition TEXT NOT NULL CHECK (disposition IN ('fixed','proposed','reported')),
  samples     TEXT NOT NULL DEFAULT '[]',
  evidence    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_issues_run ON issue_summaries(run_id, klass);

CREATE TABLE review_items (
  id             TEXT NOT NULL,              -- 8-hex, deterministic
  run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  rule           TEXT NOT NULL,
  col_index      INTEGER,
  description    TEXT NOT NULL,
  proposal       TEXT NOT NULL,              -- JSON incl. cells[]
  affected_cells INTEGER NOT NULL,
  confidence     REAL NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','accepted','rejected')),
  decided_at     TEXT,
  PRIMARY KEY (run_id, id),
  CHECK ((status = 'pending') = (decided_at IS NULL))
);

CREATE TABLE revisions (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  revision_no       INTEGER NOT NULL CHECK (revision_no >= 1),
  created_at        TEXT NOT NULL,
  accepted_item_ids TEXT NOT NULL DEFAULT '[]',
  cleaned_path      TEXT NOT NULL,
  audit_path        TEXT NOT NULL,
  UNIQUE (run_id, revision_no)
);
```

Runs are never deleted by the application; `ON DELETE CASCADE` exists only for
manual maintenance. Status transitions on `runs` happen within the single
processing transaction (insert with `started_at`, finalize once with the rest)
— readers never observe a half-written run.

## 5. Example records

**WatchedFolder**

```json
{"id":"f2d1…","path":"/home/abdoul/exports","recursive":true,
 "include":["*.csv","*.tsv","*.xlsx","*.jsonl"],"policy_path":null,
 "output_dir":"/home/abdoul/exports/.datasweep","enabled":true,
 "created_at":"2026-07-31T09:00:00Z"}
```

**SourceFile**

```json
{"id":"a7c0…","path":"/home/abdoul/exports/sales.csv","folder_id":"f2d1…",
 "first_seen_at":"2026-07-31T09:05:12Z","last_seen_at":"2026-07-31T10:05:12Z"}
```

**Run** (abbreviated)

```json
{"id":"7c9e…","file_id":"a7c0…","content_sha256":"9f86d0…",
 "policy_hash":"3a5b12c4d5e6f708","engine_version":"0.1.0",
 "started_at":"2026-07-31T09:05:17Z","finished_at":"2026-07-31T09:05:19Z",
 "status":"review_pending","trigger_kind":"scan","format":"csv",
 "encoding":"cp1252","dialect":{"delimiter":",","quotechar":"\"","has_header":true,"sheet":null},
 "n_rows":412,"n_cols":7,
 "issue_counts":{"ENC":6,"STR":0,"WS":41,"MISS":18,"TYPE":9,"DATE":240,"CAT":15,"DUP":3,"OUT":2},
 "change_counts":{"auto":68,"review":2,"report":11},
 "artifact_dir":"/home/abdoul/exports/.datasweep/sales.9f86d09f/","error":null}
```

**ColumnProfile**

```json
{"run_id":"7c9e…","col_index":2,"name":"amount","original_name":null,
 "inferred_type":"float","type_coverage":0.978,"non_null":405,"distinct_count":388,
 "stats":{"min":0.99,"q1":12.5,"median":24.9,"q3":49.0,"max":2100.0,"mad":18.2,
          "convention":{"grouping":",","decimal":".","currency":null}}}
```

**IssueSummary**

```json
{"id":"11ab…","run_id":"7c9e…","klass":"CAT","col_index":5,"cell_count":12,
 "disposition":"fixed",
 "samples":[{"row":44,"before":"U.S.A.","after":"USA"},{"row":102,"before":" usa ","after":"USA"}],
 "evidence":{"rule":"fix.label_merge_fingerprint","cluster":["USA","U.S.A."," usa "],
             "canonical":"USA","dominance":0.94}}
```

**ReviewItem** (ambiguous date column — both interpretations attached)

```json
{"id":"a3f1c2d9","run_id":"7c9e…","rule":"fix.date_canon_ambiguous","col_index":4,
 "description":"order_date: 240 cells match both %d/%m/%Y and %m/%d/%Y; no cell proves day-first",
 "proposal":{"candidates":[
   {"label":"day-first","format":"%d/%m/%Y"},
   {"label":"month-first","format":"%m/%d/%Y"}],
  "recommended":"day-first",
  "cells":[{"row":0,"col":4,"before":"03/04/2021","after":"2021-04-03"},
           {"row":1,"col":4,"before":"07/01/2021","after":"2021-01-07"}]},
 "affected_cells":240,"confidence":0.31,"status":"pending","decided_at":null}
```

**Revision**

```json
{"id":"5e2f…","run_id":"7c9e…","revision_no":2,"created_at":"2026-07-31T09:40:00Z",
 "accepted_item_ids":["a3f1c2d9"],
 "cleaned_path":"/home/abdoul/exports/.datasweep/sales.9f86d09f/cleaned.r2.csv",
 "audit_path":"/home/abdoul/exports/.datasweep/sales.9f86d09f/audit.r2.jsonl"}
```

Audit entry examples: §3.1.

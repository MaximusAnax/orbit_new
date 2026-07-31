# datasweep — SCOPE

## One-liner

Watches folders for tabular files (CSV/TSV/Excel/JSONL), profiles them for the
classic data-quality defects — encoding damage, duplicate rows, missing-value
sentinels, inconsistent types, mixed date formats, stray whitespace, outliers,
inconsistent categorical labels — and applies a *safe*, confidence-tiered
cleaning pipeline: auto-apply only provably reversible normalizations, route
ambiguous fixes to a review queue, never touch the original file, and account
for every changed cell in a machine-readable audit log.

## Problem statement

Every personal data project starts the same way: a CSV export lands in a folder
and the first hour goes to the same drudgery — the file is cp1252 pretending to
be UTF-8 ("JosÃ©" for "José"), a fifth of the "numeric" column is `N/A` or
`1,234.56`, dates come in three formats (two of them ambiguous), `USA`,
`U.S.A.` and ` usa ` are three different countries, and forty rows are exact
duplicates. Spreadsheet tools make it worse rather than better — Excel's
silent autoconversion is infamous enough that it corrupted gene names in
roughly a fifth of published genomics supplements (Ziemann, Eren & El-Osta,
*Genome Biology* 2016: `SEPT2` → `2-Sep`) and forced the HGNC to rename genes
in 2020. Interactive tools that do this well exist (OpenRefine, Trifacta
Wrangler) but demand a session per file; naive scripts (`df.dropna()`,
`str.strip()`, `drop_duplicates()`) apply fixes blindly and destructively.

datasweep is the background middle path. It watches folders, and for each new
or changed tabular file it (1) parses defensively (encoding detection, dialect
sniffing), (2) infers per-column semantic types by per-cell micro-parser
voting (in the spirit of ptype's probabilistic type inference, Ceritli,
Williams & Geddes 2020), (3) runs eight issue detectors, (4) plans fixes and
assigns each a **confidence tier** — `auto` for reversible normalizations with
decisive evidence, `review` for plausible-but-ambiguous fixes, `report` for
things a cleaner must never silently change (outliers, imputations) — and (5)
emits three artifacts next to the untouched original: a cleaned copy, an
append-only JSONL audit log in which every changed cell appears exactly once
with its before/after values, and a human-readable Markdown report. The audit
log is constructive: applying it in reverse to the cleaned table reproduces the
parsed original exactly, and the eval suite gates on that.

The hard part is not moving bytes — it is *deciding correctly*: inferring
types from dirty evidence, detecting real issues without false alarms on clean
data, and calibrating which fixes are safe enough to apply unattended. A
background cleaner that guesses wrong is worse than no cleaner; the whole
design and the eval gates are built around that asymmetry.

## Target user

The repo owner: a single user on one machine, pointing datasweep at their
downloads/exports folders and at ad-hoc files. No accounts, no multi-tenancy,
no web UI. API + CLI only; the "background" mode is a local daemon loop.

## User stories

- **US-1: Drop a file, get a clean one.** As the owner, I drop `sales.csv`
  into a watched folder and, without doing anything else, get a cleaned copy,
  an audit log, and a report.
  *Acceptance:* with a watch configured, `datasweep run --once` (or the daemon
  within one poll interval) produces `<out>/sales.<hash8>/cleaned.csv`,
  `audit.jsonl`, `report.md`; the original file's bytes are unchanged
  (asserted by hash); a Run row records counts of issues by class and changes
  by tier.
- **US-2: See exactly what changed.** As the owner, I can enumerate every
  modification down to the cell.
  *Acceptance:* `datasweep show <run> --changes` lists audit entries
  (row, column, before, after, rule, tier, confidence); every cell that
  differs between the parsed original and the cleaned table is covered by
  exactly one entry, and no entry is a no-op.
- **US-3: Trust it with ambiguity.** As the owner, I never find an ambiguous
  fix silently applied. Dates like `03/04/2021` in a column where day/month is
  undecidable, `Slovakia`/`Slovenia`-style near-labels, and numeric sentinels
  like `-9999` are surfaced, not changed.
  *Acceptance:* on the trap fixtures (EVALS.md §4), zero auto-tier changes
  touch trap cells; ambiguous items appear in the review queue or the report
  with both candidate interpretations.
- **US-4: Review and accept.** As the owner, I can work the review queue and
  accept or reject flagged fixes, producing a revised cleaned file.
  *Acceptance:* `datasweep review <run>` lists pending items with proposals
  and confidence; `datasweep accept <run> --item a3f1 --item 9c02` writes
  `cleaned.r2.csv` + `audit.r2.jsonl` (revision 2) applying exactly the
  accepted items; rejected items are recorded and never re-proposed for the
  same content hash.
- **US-5: Clean or profile one file on demand.** As the owner, I can run the
  pipeline ad hoc, including a no-write dry run.
  *Acceptance:* `datasweep clean export.xlsx --out /tmp/x` processes a file
  outside any watch; `datasweep profile export.xlsx` prints the column
  profiles and issue list, writes nothing, and exits 0.
- **US-6: Tune the policy.** As the owner, I can disable detectors or demote
  tiers per folder (e.g. duplicate-row dropping off for event logs).
  *Acceptance:* with `duplicates = "off"` in the folder's policy TOML, a file
  with exact duplicate rows is cleaned without row drops and the report says
  the detector was disabled; the Run stores the effective policy snapshot and
  its hash.
- **US-7: Idempotent, partial-write-safe background loop.** As the owner, I
  can leave the daemon running without it reprocessing unchanged files or
  reading files mid-copy.
  *Acceptance:* a second `datasweep run --once` over an unchanged folder
  creates only `skipped` run records (same content hash + policy hash +
  engine version); a file whose size/mtime changed between two consecutive
  polls is deferred until stable (settle check), verified by unit test with an
  injected clock.
- **US-8: Excel in, portable out.** As the owner, I can drop `.xlsx` files and
  get the same treatment, including detection of Excel-specific damage.
  *Acceptance:* an `.xlsx` sheet with serial-number dates (e.g. `44927` in a
  date column) yields a review item proposing `2023-01-01` with the serial
  interpretation named; cleaned output is canonical CSV; leading-zero codes
  (ZIP `02134`) are typed as digit-strings and never numerically coerced.

## Functional requirements

Each FR is independently testable; test/eval names reference FR ids (mapping
table in EVALS.md §7).

- **FR-1 Watch configuration & scanning.** Watched folders are configurable
  (path, recursive flag, include globs — default `*.csv *.tsv *.xlsx *.jsonl`
  — policy path, output dir) via CLI/API and persisted. A scan pass
  deterministically enumerates candidate files (lexicographic path order),
  skipping the output dir, hidden files, and temp patterns (`~$*`, `*.partial`,
  `*.tmp`, `.*`). A file is *stable* when size and mtime are unchanged between
  two observations ≥ `settle_seconds` apart (times injected, never read from
  the wall clock in the engine/services); unstable files are deferred.
- **FR-2 Idempotence.** Before processing, the system computes the file's
  SHA-256. If a non-failed Run exists for (content_sha256, policy_hash,
  engine_version), the file is skipped with a `skipped` Run record.
  `--force` bypasses the check and creates a new Run.
- **FR-3 Defensive reading.** Readers produce a `RawTable` — header names plus
  all cells as verbatim strings (`str | None`) — from CSV/TSV (encoding
  detection per FR-4, dialect via `csv.Sniffer` with RFC 4180 fallback:
  delimiter from {`,`, `\t`, `;`, `|`} by max consistent column count over the
  first 50 rows; quoted fields with embedded newlines handled), JSONL (keys
  union in first-seen order; missing key → null; nested object/array values
  serialized to JSON strings and flagged `STR`), and XLSX (openpyxl,
  first sheet by default, `--sheet` to select; cell values read as displayed
  strings, numbers rendered without float artifacts). Structural anomalies are
  first-class: short rows padded with nulls (audited, auto), long rows
  overflow into synthetic `_extra_N` columns (review), duplicate header names
  deduplicated with `_2`, `_3` suffixes (audited, auto), headerless files
  (no cell in row 0 parses as text while rows below are typed) get synthetic
  `col_0..n` headers (review).
- **FR-4 Encoding detection & mojibake repair.** Detection order: BOM
  (UTF-8/UTF-16), strict UTF-8 decode, cp1252, latin-1 (never fails). The
  chosen encoding is recorded on the Run. Mojibake (UTF-8 bytes previously
  mis-decoded as cp1252/latin-1, e.g. `Ã©` for `é`) is detected per cell by
  the ftfy round-trip test: `cell.encode("cp1252", errors="strict")
  .decode("utf-8", errors="strict")` succeeds and strictly reduces the count
  of characters in the mojibake indicator set (`Ã`, `Â`, `â€`, `å`-sequences);
  the repair is the round-trip itself — bijective where it applies, hence
  auto-tier (D6).
- **FR-5 Column type inference.** Type vocabulary: `bool`, `integer`,
  `digits` (identifier-like digit string: any leading zero, or fixed width ≥ 4
  with distinct-ratio > 0.5), `float`, `date`, `datetime`, `categorical`
  (text with distinct_count ≤ 50 and distinct-ratio ≤ 0.10), `text`. Per-cell
  micro-parsers (D5) match each non-null cell to a set of types; the column
  type is the highest-priority type whose coverage ≥ `type_majority`
  (default 0.90) of non-null cells, priority `bool > digits > integer >
  float > datetime > date > categorical > text`. Non-conforming cells become
  `TYPE` issues. Inference is pure and deterministic.
- **FR-6 Issue detectors.** Eight detector families, each independently
  switchable, each emitting issue instances with cell coordinates (original
  row/column indices), class, and evidence: `ENC` (FR-4), `WS` (leading/
  trailing whitespace, U+00A0 and Unicode space separators, zero-width chars
  U+200B–U+200D/U+FEFF, non-NFC normalization per UAX #15, internal run of
  ≥ 2 spaces), `MISS` (sentinel tokens per D8, blank cells in otherwise-typed
  columns, numeric sentinel candidates like `-9999`), `TYPE` (cells failing
  the column type; number-format deviations: thousands separators, decimal
  comma, currency symbols, leading apostrophe), `DATE` (mixed formats in one
  column, ambiguous day/month order, two-digit years, Excel serial-number
  candidates: integers 20000–60000 in a column that also carries parseable
  dates or a date-typed header token per D9), `CAT` (label variants:
  strict-fingerprint groups, token-sort fingerprint groups, Damerau-
  Levenshtein near-labels per D10), `DUP` (exact duplicate rows after
  normalization, first occurrence kept), `OUT` (numeric outliers by Tukey
  fences at k=3.0 and modified z-score |M| > 3.5 per D11; report-only,
  n ≥ `outlier_min_n` = 20).
- **FR-7 Fix planning & confidence tiers (the hard part, with FR-5/FR-6).**
  Every proposed fix carries a rule id, a confidence ∈ [0,1] computed by that
  rule's documented evidence formula (D12 table), and a tier
  `auto | review | report` derived as `min(safety_cap(rule),
  conf_tier(confidence))` where `conf_tier` maps confidence ≥ 0.95 → auto,
  ≥ 0.50 → review, else report, and `safety_cap` is `auto` only for reversible
  normalizations, `review` for lossy/interpretive rules (NN label merges,
  serial dates, two-digit years, text-column sentinels), `report` for
  analytic findings (outliers, numeric sentinels, coercion failures). Policy
  may override per rule (including `off`); overrides are captured in the
  policy hash.
- **FR-8 Transform application & audit.** Auto-tier fixes are applied in the
  fixed pipeline order ENC → STR → WS → MISS → TYPE → DATE → CAT → DUP → OUT
  (D13). Application yields the cleaned table plus an audit log: one entry per
  change, typed `cell_change | row_drop | row_pad | header_rename |
  column_add`, addressed by *original* parsed-table coordinates, carrying
  before/after values, rule, tier, confidence, and evidence. Completeness
  invariant: the diff between parsed original and cleaned table is exactly the
  set of audit entries; no entry is an identity change.
- **FR-9 Reversibility.** A pure function `revert(cleaned_table, audit) ->
  RawTable` reconstructs the parsed original exactly (cell-for-cell string
  equality, row order, headers) by undoing entries in reverse order:
  re-insert dropped rows at original indices, drop added columns, restore
  header names, restore cell values. `datasweep revert <run>` runs it and
  reports match/mismatch; evals gate it at 1.0 (EVALS.md M5).
- **FR-10 Artifacts.** Per run, an immutable artifact directory
  `<output_dir>/<stem>.<sha256[:8]>/` containing `cleaned.csv` (UTF-8,
  RFC 4180 quoting, LF; `.tsv` input keeps tabs; `.jsonl` input yields
  `cleaned.jsonl`; `.xlsx` input yields canonical CSV per D14), `audit.jsonl`,
  and `report.md` (summary, per-column profile, issues by class, changes by
  tier, review queue). The source file is never opened for writing; runs
  record the source hash before and after processing and fail loudly on
  mismatch.
- **FR-11 Review workflow.** Review-tier fixes are persisted as ReviewItems
  (proposal JSON, affected cell coordinates, confidence,
  status `pending → accepted | rejected`, one transition, immutable after).
  Accepting any subset produces Revision r+1: a new cleaned file and audit
  delta applying exactly the accepted items on top of the previous revision;
  revisions are append-only. Rejected items are never re-proposed for the
  same content hash.
- **FR-12 Run persistence.** Runs are append-only records: source path,
  content hash, policy snapshot + hash, engine version, injected timestamps,
  status (`succeeded | review_pending | failed | skipped`), parse metadata
  (encoding, dialect, sheet), row/column counts, per-class issue counts,
  per-tier change counts, artifact dir. Column profiles and aggregated issues
  are queryable (DATA_MODEL.md §2.5–2.6); cell-level detail lives in the
  audit artifact.
- **FR-13 Daemon & notification.** `datasweep run` loops scan passes at
  `poll_interval_seconds` (default 5) using the configured watcher adapter;
  `--once` performs a single pass and exits (the deterministic mode used by
  tests). On each completed run the Notifier adapter is invoked with a
  one-line summary; offline default is a log/file notifier, live adapter is a
  desktop notification (D15).
- **FR-14 API.** FastAPI app exposing the endpoints in §Architecture-API with
  Pydantic schemas; engine/service errors map to 4xx with structured detail
  codes (`unknown_run`, `item_already_decided`, `file_unstable`,
  `unsupported_format`, `file_too_large`).
- **FR-15 CLI.** Typer CLI exposing the commands in §Architecture-CLI; exit
  code 0 on success, 1 on failure, 2 on usage errors; `--json` on read
  commands for machine-readable output.
- **FR-16 Determinism.** For fixed (file bytes, policy, engine version,
  injected timestamps), cleaning produces byte-identical `cleaned.*` and
  `audit.jsonl` artifacts across runs and platforms. No wall-clock reads, no
  unseeded randomness anywhere in engine or services (the pipeline is in fact
  randomness-free); dict/set iteration never determines output order (explicit
  sorts by index/name).

## Non-goals (this pass)

- **No web UI** (workspace-wide decision). The review queue lives in CLI/API.
- **No imputation, ever, in any tier.** Filling missing values (mean/median/
  model-based) is a *modeling* decision, not cleaning — it depends on the
  missingness mechanism (Rubin's MCAR/MAR/MNAR taxonomy; Little & Rubin,
  *Statistical Analysis with Missing Data*). datasweep normalizes the
  *representation* of missingness (sentinel → null) and reports counts;
  it never invents values. Same for outliers: detect and report (Tukey/MAD),
  never winsorize, clamp, or drop.
- **No fuzzy record deduplication.** Only exact full-row duplicates are
  dropped. Probabilistic record linkage (Fellegi–Sunter 1969, dedupe.io-style
  blocking + learned similarity) is an enterprise-sized problem and a
  different risk class; near-duplicate rows are out of scope entirely (not
  even flagged, to keep DUP precision exact).
- **No schema/unit semantics.** No cross-column constraints ("end_date ≥
  start_date"), no unit conversion, no address/phone/email canonicalization
  beyond whitespace/case handling that general rules already give. Great
  Expectations-style user-declared assertions are a plausible later phase.
- **No multi-sheet Excel orchestration, no .xls (BIFF), no ODS, no Parquet.**
  One sheet per run (selectable); legacy/binary formats deferred.
- **No streaming/chunked processing.** Files are processed in memory with
  hard caps (`max_file_mb` = 100, `max_rows` = 500,000) and a clear error
  beyond them.
- **Live adapters are best-effort and not eval-gated.** The watchdog
  filesystem-event watcher, charset-normalizer encoding detector, and desktop
  notifier activate only when their optional extras are installed; all tests
  and evals run against the deterministic offline adapters (CONVENTIONS.md
  §3).
- **No LLM-assisted cleaning.** Every rule here is deterministic and
  explainable; a semantic-suggestion adapter could slot in later behind the
  planner, but the MVP's trust story is "you can read the rule that did this".
- **No multi-user, auth, or hosting concerns.**

## Architecture

Package `datasweep` at `projects/datasweep/src/datasweep/`. Layering per
CONVENTIONS.md: pure engine, Protocol-based adapters, Repository store, thin
API/CLI. `services.py` orchestrates watcher → reader → engine → store →
artifacts and is the only layer touching both I/O and engine; API and CLI call
services only. Timestamps enter services from a `Clock` port and are passed
into the engine as data.

### Engine modules (`engine/`, pure, deterministic)

- `models.py` — Pydantic domain models: `RawTable`, `ColumnProfile`,
  `TableProfile`, `Issue`, `FixPlan`, `Fix`, `AuditEntry`, `CleanResult`,
  `Policy`, `Tier`, `IssueClass`.
- `table.py` — `RawTable` operations: cell access by (row, col), header
  handling, structural repairs (pad/overflow/dedup-headers) as planned fixes,
  canonical serialization order.
- `inference.py` — per-cell micro-parsers and column type voting (FR-5);
  number-convention inference (grouping/decimal style per D7); date-format
  assignment and day/month disambiguation (D9).
- `detectors.py` — the eight detector families (FR-6), each a pure function
  `detect_X(table, profile, policy) -> list[Issue]`; includes fingerprint and
  Damerau–Levenshtein label clustering (D10) and robust outlier statistics
  (D11).
- `planner.py` — issues → `FixPlan`: rule selection, confidence formulas,
  tier algebra `min(safety_cap, conf_tier)` (FR-7, D12), policy overrides,
  deterministic fix ordering (pipeline stage, then column index, then row
  index).
- `transforms.py` — fix application in pipeline order producing
  (cleaned `RawTable`, `list[AuditEntry]`) with original-coordinate
  bookkeeping (FR-8); `revert(table, audit)` (FR-9).
- `report.py` — Markdown report rendering from `CleanResult` (FR-10).

### Adapter interfaces (`adapters/`)

Each is a `typing.Protocol`; offline implementations are the defaults used by
tests and evals.

- **`Watcher`** — `scan(folders: Sequence[WatchedFolder], now: datetime) ->
  list[FileObservation]` (path, size, mtime).
  - `PollingScanner` (offline, default): deterministic `os.scandir` walk,
    lexicographic order, applies ignore patterns; the settle check compares
    two observations supplied by the caller — no sleeping, no clock reads.
  - `WatchdogWatcher` (live, extra `watch`): wraps the `watchdog` library's
    OS event stream (inotify/FSEvents), debounces events into the same
    `FileObservation` shape. Event delivery is inherently non-deterministic,
    so it is unit-smoke-tested only, never eval-gated.
- **`EncodingDetector`** — `detect(data: bytes) -> DetectedEncoding
  (name, had_bom, confidence)`.
  - `SimpleEncodingDetector` (offline, default): BOM → strict UTF-8 →
    cp1252 → latin-1 cascade (FR-4); fully deterministic.
  - `CharsetNormalizerDetector` (live, extra `charset`): delegates to
    `charset-normalizer` for long-tail encodings (Shift-JIS, KOI8-R…);
    result still passes through the same strict-decode validation.
- **`TableReader`** — `sniff(path, data) -> FileMeta` and
  `read(data, meta, policy) -> tuple[RawTable, list[Issue]]`.
  Implementations: `CsvReader` (also TSV; dialect sniffing per FR-3),
  `JsonlReader`, `XlsxReader` (openpyxl; see D14 for why it is a core dep,
  not an extra). Registry keyed by extension; unknown extensions →
  `unsupported_format`.
- **`ArtifactWriter`** — `write(run_dir, cleaned: RawTable, audit:
  list[AuditEntry], report_md: str, fmt) -> ArtifactPaths`.
  `LocalArtifactWriter` (offline, default) writes atomically
  (temp file + `os.replace`, the standard partial-write-safe rename idiom).
  No live variant needed in MVP; the port exists so a future sync target
  (S3, rclone) slots in.
- **`Notifier`** — `notify(summary: RunSummary) -> None`.
  - `LogNotifier` (offline, default): appends one line to
    `<db_dir>/notify.log`.
  - `DesktopNotifier` (live, extra `notify`): `notify-send` on Linux /
    `osascript` on macOS via subprocess; failures are swallowed and logged.
- **`Clock`** — `now() -> datetime`. `FixedClock` (tests/evals),
  `SystemClock` (production). Engine never sees this port — services stamp
  times and pass them as data.

### Store (`store/`)

`Repository` protocol with `SqliteRepository` (stdlib `sqlite3`, WAL, schema
in DATA_MODEL.md §4) and `InMemoryRepository` for tests. Operations: watched-
folder CRUD, source-file upsert, run append + query, column-profile/issue
bulk insert, review-item append + single-transition decide, revision append.
No engine logic. Default DB `~/.datasweep/datasweep.db`
(`DATASWEEP_DB` / `--db` override).

### API (FastAPI, `api/`)

```
GET    /health                              -> {status, version}
GET    /folders                             -> [WatchedFolder]
POST   /folders                             -> 201 WatchedFolder      (FR-1; body: {path, recursive?, include?, policy_path?, output_dir?})
DELETE /folders/{id}                        -> 204
POST   /scan                                -> ScanResult             (FR-1/2/13; one pass: {processed: [run_id], skipped: [...], deferred: [...]})
POST   /clean                               -> 201 Run                (US-5; body: {path, policy_path?, out?, force?, dry_run?})
GET    /runs?path=&status=                  -> [RunSummary]
GET    /runs/{run_id}                       -> Run + column profiles + issue summaries   (FR-12)
GET    /runs/{run_id}/audit                 -> audit.jsonl file response                 (FR-8)
GET    /runs/{run_id}/report                -> report.md file response                   (FR-10)
GET    /runs/{run_id}/review                -> [ReviewItem]                              (FR-11)
POST   /runs/{run_id}/decisions             -> 201 Revision           (FR-11; body: {accept: [item_id], reject: [item_id]})
GET    /runs/{run_id}/revisions             -> [Revision]
GET    /revisions/{rev_id}/cleaned          -> cleaned file response
POST   /runs/{run_id}/revert                -> RevertCheck            (FR-9; {match: bool, mismatches: [...]})
```

### CLI (Typer, `cli/`)

```
datasweep watch add PATH [--recursive/--no-recursive] [--include GLOB]...
                         [--policy PATH] [--out DIR]                  # FR-1
datasweep watch ls | rm ID
datasweep run [--once] [--interval SECS]                              # FR-13 daemon / single pass
datasweep clean FILE [--out DIR] [--policy PATH] [--force] [--sheet N]  # US-5, FR-2
datasweep profile FILE [--policy PATH]                                # US-5 dry run (no writes)
datasweep runs [--path P] [--status S]                                # FR-12
datasweep show RUN [--changes] [--issues] [--columns]                 # US-2
datasweep review RUN                                                  # FR-11
datasweep accept RUN (--item ID)... | --all                           # FR-11 -> revision
datasweep reject RUN (--item ID)... | --all                           # FR-11
datasweep revert RUN                                                  # FR-9 verification
datasweep report RUN [--out PATH]                                     # FR-10
datasweep serve [--port 8787]                                         # uvicorn wrapper
```

All commands accept `--db PATH` and read commands accept `--json`.

### Size budget (implementation phase)

engine ≈ 1,350 (models/table 300, inference 220, detectors 420, planner 180,
transforms 230); adapters ≈ 450; services ≈ 140; store ≈ 280; api ≈ 240;
cli ≈ 300; tests + evals ≈ 1,050. Total ≈ 3,800 — top of the 2,000–4,000
band, with inference, detection, and tier calibration (the hard part) getting
the depth.

## Key design decisions and assumptions

- **D1 — Safety asymmetry is the product thesis.** A background tool's wrong
  auto-fix is silent data corruption — the failure mode that made Excel's
  autoconversion a scientific scandal (Ziemann et al. 2016; HGNC's 2020
  gene renaming in response). So the tier system is biased hard toward
  under-acting: `auto` requires *both* a reversible rule class *and*
  confidence ≥ 0.95, everything interpretive caps at `review`, everything
  analytic caps at `report`. Eval gates encode the same asymmetry
  (auto-fix precision gated near 1.0; recall gated lower — EVALS.md §5).
- **D2 — Suggest-then-confirm, from the interactive-cleaning literature.**
  The auto/review split is the batch analogue of *predictive interaction* in
  Wrangler (Kandel, Paepcke, Hellerstein & Heer, CHI 2011) and the visual
  anomaly surfacing of Profiler (AVI 2012): the system proposes concrete,
  explainable transforms; the human ratifies the ambiguous ones. datasweep
  moves only the provably-safe subset out of the confirm loop.
- **D3 — The engine operates on verbatim strings.** `RawTable` cells are the
  file's cell texts (`str | None`), and the cleaned table is also strings.
  Typed values (ints, dates) exist only inside inference/detector logic.
  Rationale: string-level identity makes "original untouched", audit
  completeness, and reversibility (FR-9) exact and testable — no float
  round-trips, no locale surprises at serialization time.
- **D4 — Reversibility is constructive, at the logical-table level.** The
  original *file* is trivially preserved (never opened for writing); what the
  audit guarantees on top is that `revert(cleaned, audit)` rebuilds the
  *parsed* original table exactly. Audit entries use original coordinates and
  full before-values (dropped rows store their entire content), which makes
  the inverse a mechanical fold. This is the machine-checkable meaning of the
  locked requirement "every transform reversible or fully accounted for".
- **D5 — Type inference is deterministic micro-parser voting, ptype-lite.**
  ptype (Ceritli, Williams & Geddes 2020) frames column typing as inference
  over per-type probabilistic finite-state machines that jointly explain
  values, missing codes, and anomalies. We keep its key insight — type,
  missingness, and anomaly are one joint decision per column — but replace
  PFSMs with deterministic per-cell parsers + a coverage vote
  (`type_majority` = 0.90), which is auditable, fast, and sufficient for the
  eval fixtures. Assumption: for single-user files, the 10% slack absorbs
  dirty minorities without flipping column types; nonconforming cells surface
  as `TYPE` issues rather than being silently coerced.
- **D6 — Encoding repair follows ftfy's round-trip principle.** Mojibake is
  detectable and fixable because the damage (UTF-8 bytes decoded as
  cp1252/latin-1) is an invertible function; ftfy (R. Speer) repairs it by
  re-encoding and re-decoding. Where the strict round-trip succeeds and
  strictly reduces mojibake indicators, the fix is bijective on that cell —
  the one "exotic" repair that genuinely earns auto tier. Anything short of a
  clean round-trip is review. Detection cascade (BOM → UTF-8 → cp1252 →
  latin-1) covers the overwhelming share of single-user Western-locale files;
  the charset-normalizer live adapter exists for the long tail.
- **D7 — Number canonicalization is column-convention-first.** A cell like
  `1.234` is undecidable alone (one thousand two hundred thirty-four under
  dot-grouping vs 1.234 under dot-decimal). The convention (grouping char,
  decimal char, currency affix) is inferred *per column* from unambiguous
  cells (e.g. `1.234.567` proves dot-grouping; `12,5` proves comma-decimal);
  canonicalization to `-?digits[.digits]` is auto only when one convention
  explains ≥ 95% of parseable cells and no cell contradicts it; a column with
  live ambiguity goes to review with both readings shown. Currency symbols
  are stripped to the column profile (`currency=EUR`) only when uniform,
  else review. `digits`-typed columns (leading zeros — ZIP codes, SKUs) are
  *never* numerically canonicalized; this is the ZIP-code/Excel trap made a
  type-system rule.
- **D8 — Missing-value handling is representation-only.** Sentinel vocabulary,
  case-insensitive after trim — hard tier (auto in non-text columns): `""`,
  `na`, `n/a`, `null`, `nan`, `#n/a`, `#value!`; soft tier (review): `-`,
  `.`, `?`, `none`, `missing`, `unknown` (real categories in many datasets —
  a medication column's `None` means "no medication"). Numeric sentinels
  (`-999`, `-9999`, `9999`) are report-only: flagged when the value is a
  round number, is a distribution outlier by D11, and repeats ≥ 3× — the
  pattern of documented missing codes like GHCN's `-9999` — but never
  auto-nulled, because "convention" is not "proof". Canonical missing in
  cleaned CSV output is the empty field; in JSONL, `null`.
- **D9 — Date handling: explicit format set, proof-based disambiguation,
  ISO 8601 target.** Candidate formats (exhaustive, in priority order):
  `%Y-%m-%d`, `%Y-%m-%dT%H:%M:%S` (optional `Z`/offset/fraction — RFC 3339),
  `%Y/%m/%d`, `%d/%m/%Y`, `%m/%d/%Y`, `%d-%m-%Y`, `%m-%d-%Y`, `%d.%m.%Y`,
  `%b %d, %Y`, `%d %b %Y`, `%B %d, %Y`, plus two-digit-year variants of the
  slash forms (review-capped: century pivots are convention, not fact). A
  column's assignment must cover all parseable cells; day-first vs
  month-first is *proven* only by a cell with the deciding component > 12
  anywhere in the column — otherwise the column is ambiguous and capped at
  review, with both ISO interpretations attached and
  `conf = share of cells whose ISO value is identical under both readings`.
  Excel serial dates (integers 20000–60000 ≈ 1954–2064 under the 1900 epoch
  — actually 1899-12-30 thanks to Lotus 1-2-3's fictitious 1900-02-29) are
  proposed at review tier only when the column carries other date evidence.
  Canonical output: `YYYY-MM-DD` / RFC 3339.
- **D10 — Categorical consolidation is OpenRefine's playbook with a safety
  split.** Three clustering levels: (a) *strict fingerprint* — trim, casefold,
  NFC, strip punctuation, collapse whitespace, token order preserved — merges
  are representational (`USA` / `U.S.A.` / ` usa `) and auto-eligible with
  `conf = dominance = n_canonical / n_cluster` (canonical = most frequent,
  ties → first-seen; auto iff dominance ≥ 0.8); (b) *token-sort fingerprint*
  (OpenRefine's full key-collision method: sort + dedupe tokens) — catches
  `Smith John` / `John Smith` but reordering can change meaning → review cap;
  (c) *nearest-neighbor* via Damerau–Levenshtein distance ≤ 1 (≤ 2 for labels
  ≥ 8 chars) — catches typos (`Missisippi`) but also legitimate near-words
  (`Iran`/`Iraq`, `Slovakia`/`Slovenia`) → review cap always, and *proposed
  at all* only when the minority label is rare relative to its neighbor
  (`n_minor / (n_minor + n_major) ≤ 0.05` and `n_major ≥ 20`), which
  suppresses proposals for frequent legitimate pairs entirely. Columns
  qualify as categorical per FR-5 only; free text is never clustered.
- **D11 — Outliers are flagged by robust statistics and never modified.**
  Tukey's fences (EDA, 1977) at the "far out" multiplier k = 3.0
  (`[Q1 − 3·IQR, Q3 + 3·IQR]`) and the Iglewicz–Hoaglin modified z-score
  `M = 0.6745 (x − median) / MAD`, flagging `|M| > 3.5` — both chosen over
  mean/σ z-scores because mean and σ are themselves corrupted by the outliers
  being hunted (breakdown point 0 vs 50% for median/MAD). A value must trip
  *both* tests to be flagged (precision over recall — a background tool that
  cries wolf gets turned off). Report-only by design: an outlier may be the
  most important true value in the file; deleting or clamping it is analysis,
  not cleaning.
- **D12 — Confidence formulas are per-rule and documented.** Summary table
  (full evidence definitions live with each detector):

  | Rule | Confidence | Safety cap |
  |---|---|---|
  | trim / NBSP→space / zero-width strip / NFC | 1.0 (deterministic, meaning-preserving) | auto |
  | collapse internal whitespace | 1.0 if column type ≠ text, else 0.6 | auto / review |
  | mojibake round-trip | share of candidate cells with strict, indicator-reducing round-trip (per column batch) | auto iff 1.0 |
  | sentinel → null (hard list, non-text col) | 1.0 | auto |
  | sentinel → null (soft list or text col) | 0.7 | review |
  | numeric sentinel | n/a | report |
  | number canonicalization | share of parseable cells explained by the single inferred convention | auto |
  | date → ISO (proven format) | 1.0 | auto |
  | date → ISO (ambiguous d/m) | share of interpretation-invariant cells | review |
  | Excel serial date | 0.6 | review |
  | two-digit year | 0.6 | review |
  | label merge (strict fingerprint) | cluster dominance | auto |
  | label merge (token-sort) | cluster dominance | review |
  | label merge (edit-distance) | 1 − ratio/0.05 where ratio = n_minor/(n_minor+n_major) | review |
  | drop exact duplicate row | 1.0 | auto |
  | pad short row / dedupe headers | 1.0 | auto |
  | overflow columns / synthetic headers | 0.7 | review |
  | outlier / coercion failure | n/a | report |

- **D13 — Pipeline order is fixed and load-bearing.** ENC before everything
  (later stages must see repaired text); WS and MISS before TYPE/DATE
  (trimmed cells parse; sentinels don't pollute type votes — inference runs
  on the working table after each stage's auto fixes); CAT before DUP
  (label consolidation exposes duplicates that differ only by variant
  spelling); DUP second-to-last; OUT last (statistics computed on final
  canonical numerics). The order is part of the engine contract and of
  determinism (FR-16).
- **D14 — openpyxl is a core dependency, not an extra.** Excel input is
  locked scope, and eval fixtures include a committed `.xlsx`, so the core
  test+eval suite must read it (CONVENTIONS.md forbids optional deps there).
  openpyxl is pure-Python and light — unlike the genuinely heavy adapters
  (`watchdog`, `charset-normalizer`, desktop notifiers) which stay behind
  extras `watch`, `charset`, `notify`. Cleaned output for xlsx is canonical
  CSV: the cleaning contract is about values, and emitting a portable,
  diffable format *is* the cleaning (formatting/formula preservation is a
  non-goal).
- **D15 — "Background" = polling loop by default, OS events as an extra.**
  The deterministic `PollingScanner` + settle check (size/mtime stable across
  ≥ `settle_seconds`) is the testable core; it also sidesteps the classic
  partial-write race that event-driven watchers hit (events fire on create,
  before the writer finishes — which is why well-behaved producers write
  temp-then-rename, and why our own ArtifactWriter does). The `watchdog`
  live adapter reduces latency when installed but feeds the same code path.
- **D16 — Idempotence keys on content, not path or mtime.**
  (content_sha256, policy_hash, engine_version) triples decide "already
  done": touching a file, re-saving identical bytes, or moving it does not
  re-trigger work; changing the policy or upgrading the engine does. This is
  what makes an always-on daemon safe to leave running against a synced
  folder.
- **D17 — Hermetic evals.** Per CONVENTIONS.md: offline adapters only,
  committed fixtures, `FixedClock`, no network. The corrupted fixtures are
  generated by a committed seeded corrupter from committed golden files, so
  ground truth is known *by construction* (EVALS.md §4) — the same
  plant-then-recover strategy the flowlist evals use, adapted to cell-level
  corruption manifests.

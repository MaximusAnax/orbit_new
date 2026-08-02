# datasweep

**A background, non-destructive cleaner for tabular data files.**

datasweep watches folders for CSV/TSV/XLSX/JSONL files, profiles each one for
the classic data-quality defects — encoding damage, stray whitespace,
missing-value sentinels, inconsistent number and date formats, near-duplicate
category labels, exact duplicate rows, outliers — and applies a *safe*,
confidence-tiered cleaning pipeline:

- **auto** — provably reversible normalizations with decisive evidence, applied
  unattended;
- **review** — plausible but ambiguous fixes, routed to a queue with every
  candidate interpretation attached;
- **report** — things a cleaner must never silently change (outliers, numeric
  sentinels, coercion failures), recorded and left alone.

The original file is never opened for writing. Every changed cell appears
exactly once in a machine-readable audit log, and applying that log in reverse
to the cleaned table reproduces the parsed original *exactly* — a property the
eval suite gates at 1.0.

> The design thesis in one sentence: **a background tool's wrong auto-fix is
> silent data corruption**, the failure mode that turned Excel's autoconversion
> into a scientific scandal (Ziemann, Eren & El-Osta, *Genome Biology* 2016 —
> `SEPT2` → `2-Sep`). So the tier system is biased hard toward under-acting,
> and the gates encode that asymmetry: auto-fix precision at ≥ 0.98 with zero
> tolerance on hand-vetted ambiguity, recall gated lower.

Docs: [`docs/SCOPE.md`](docs/SCOPE.md) ·
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) · [`docs/EVALS.md`](docs/EVALS.md) ·
[`docs/REVIEW.md`](docs/REVIEW.md)

---

## Quickstart

```bash
cd projects
uv sync --all-packages
uv run datasweep --help
```

Clean one file — nothing is configured, nothing is written next to the source
except the artifact directory you point at:

```console
$ uv run datasweep clean contacts.csv --out /tmp/out
run 7825c105-26be-4802-8ca2-1ee285d7a886  review_pending
rows=400 cols=8 encoding=utf-8 format=csv
issues={'ENC': 12, 'WS': 28, 'MISS': 12, 'TYPE': 23, 'DATE': 18, 'CAT': 18, 'DUP': 6, 'OUT': 6}
changes={'auto': 111, 'review': 6, 'report': 6}
artifacts: /tmp/out/contacts.3c4f6d82
```

Four artifacts land in `<out>/<stem>.<sha256[:8]>/`:

| file | what it is |
|---|---|
| `cleaned.csv` | the cleaned table (`.tsv` keeps tabs, `.jsonl` stays JSONL, `.xlsx` becomes canonical CSV) |
| `audit.jsonl` | one line per **applied** change, with before/after, rule, tier, confidence |
| `findings.jsonl` | one line per **detected** issue instance at every tier — including the report-only ones that changed nothing |
| `report.md` | the human-readable summary, per-column profile, review queue and findings |

---

## CLI

```
datasweep watch add PATH [--recursive/--no-recursive] [--include GLOB]... [--policy PATH] [--out DIR]
datasweep watch ls | rm ID
datasweep run [--once] [--interval SECS] [--force]
datasweep clean FILE [--out DIR] [--policy PATH] [--force] [--sheet N]
datasweep profile FILE [--policy PATH] [--sheet N]
datasweep runs [--path P] [--status S]
datasweep show RUN [--changes] [--issues] [--columns] [--paths]
datasweep review RUN
datasweep accept RUN (--item ID)... | --all
datasweep reject RUN (--item ID)...
datasweep revert RUN [--revision N]
datasweep serve [--host H] [--port 8787]
```

Every command takes `--db PATH` (default `~/.datasweep/datasweep.db`, override
with `DATASWEEP_DB`); every read command takes `--json`. Exit codes: **0**
success, **1** a datasweep error, **2** a usage error. `RUN` accepts a unique
run-id prefix.

### Profile a file without writing anything

```console
$ uv run datasweep profile contacts.csv
/tmp/contacts.csv  csv  encoding=utf-8
rows=400 cols=8

  #  column                   type          cover non-null  nulls
  0  name                     text          1.000      394      0
  1  email                    text          1.000      394      0
  2  city                     categorical   1.000      394      0
  3  country                  categorical   1.000      394      0
  4  zip                      digits        1.000      394      0
  5  signup_date              date          1.000      394      0
  6  age                      integer       1.000      371     23
  7  value                    float         1.000      370     24

issues={'ENC': 12, 'WS': 28, 'MISS': 12, 'TYPE': 23, 'DATE': 18, 'CAT': 18, 'DUP': 6, 'OUT': 6}
changes={'auto': 111, 'review': 6, 'report': 6}
```

`zip` types as `digits`, not `integer` — leading zeros are identifiers, and
D7.8 forbids numerically coercing that column. Header names are never an input
to type inference.

### See exactly what changed

```console
$ uv run datasweep show 7825c105 --changes
run 7825c105-26be-4802-8ca2-1ee285d7a886  review_pending
sha256=3c4f6d8239dd4e75…  policy=e9f3c24aec24bcb6
issues={'CAT': 18, 'DATE': 18, 'DUP': 6, 'ENC': 12, 'MISS': 12, 'OUT': 6, 'TYPE': 23, 'WS': 28}
changes={'auto': 111, 'report': 6, 'review': 6}

111 audited change(s):
  row=58 col=0 name fix.mojibake [auto 1.0] 'Mikael SÃ¸rensen' -> 'Mikael Sørensen'
  row=74 col=0 name fix.mojibake [auto 1.0] 'Piotr KovÃ¡cs' -> 'Piotr Kovács'
  row=169 col=0 name fix.mojibake [auto 1.0] 'ZoÃ« Marchetti' -> 'Zoë Marchetti'
  row=205 col=0 name fix.mojibake [auto 1.0] 'JosÃ© Nakamura' -> 'José Nakamura'
```

### Work the review queue

```console
$ uv run datasweep review 7825c105
067f47c8  [pending] fix.label_merge_nn  conf=0.60  cells=1
    city: merge label 'Ironwoad' → 'Ironwood' (1 cells)
43acc10a  [pending] fix.label_merge_nn  conf=0.60  cells=1
    city: merge label 'Máaaga' → 'Málaga' (1 cells)

$ uv run datasweep accept 7825c105 --item 067f47c8 --item 43acc10a
revision 2
  accepted: 067f47c8, 43acc10a
  cleaned:  /tmp/out/contacts.3c4f6d82/cleaned.r2.csv
  audit:    /tmp/out/contacts.3c4f6d82/audit.r2.jsonl
```

Each revision is a **complete** artifact recomputed from the parsed original —
the auto plan plus *all* accepted items — never a delta on the previous one.
Rejected items are never re-proposed for the same content hash.

### Verify the non-destructive contract

```console
$ uv run datasweep revert 7825c105
revision 1: match — the audit reconstructs the parsed original
```

This reads `cleaned.*` and `audit.*` back **off disk**, replays the audit in
reverse and compares cell-for-cell with a fresh parse of the source file.

### Run it in the background

```bash
uv run datasweep watch add ~/Downloads --out ~/Downloads/.datasweep
uv run datasweep run --once          # one deterministic scan pass
uv run datasweep run --interval 5    # the daemon loop
```

A file is processed only once it has been quiet for `settle_seconds` and its
(size, mtime) signature is unchanged since the previous pass — which is how the
daemon avoids reading a file mid-copy. Reprocessing keys on
(content SHA-256, policy hash, engine version), so touching, re-saving or moving
a file does nothing; changing the policy or upgrading the engine reprocesses it.

### Tune the policy

```toml
# policy.toml
[general]
settle_seconds = 5
max_rows = 500000

[detectors]
out = false                       # switch off a whole detector family

[thresholds]
type_majority = 0.90
nn_max_ratio = 0.05

[tiers]                           # per-rule: auto | review | report | off
"fix.drop_duplicate_row" = "off"  # event logs keep their repeats
"fix.label_merge_nn" = "report"   # demote typo merges to report-only
```

```bash
uv run datasweep clean events.csv --policy policy.toml
```

Unknown keys are a hard error, not a silent ignore. The effective policy
(defaults ⊕ file ⊕ flags) is snapshotted on the run and hashed into
`policy_hash`.

---

## API

```bash
uv run datasweep serve --port 8787   # or: uvicorn datasweep.api:app
```

| method | path | purpose |
|---|---|---|
| `GET` | `/health` | `{status, version}` |
| `GET` / `POST` | `/folders` | list / create a watched folder (201) |
| `DELETE` | `/folders/{id}` | stop watching (204); runs and source files survive |
| `POST` | `/scan` | one scan pass → `{processed, skipped, deferred, failed}` |
| `POST` | `/clean` | clean one file → 201 `Run` |
| `POST` | `/profile` | profile one file, writing nothing |
| `GET` | `/runs?path=&status=` | run summaries |
| `GET` | `/runs/{id}` | run + column profiles + issue summaries + revisions + review items |
| `GET` | `/runs/{id}/audit` · `/findings` · `/report` | the artifacts, as files |
| `GET` | `/runs/{id}/columns` · `/issues` | the queryable rollups |
| `GET` | `/runs/{id}/review` | the review queue |
| `POST` | `/runs/{id}/decisions` | `{accept: [...], reject: [...]}` → 201 `Revision` |
| `POST` | `/runs/{id}/revert` | `{match, mismatches}` |

Service errors map to a structured 4xx body,
`{"detail": {"code": ..., "message": ...}}`:

| code | status | when |
|---|---|---|
| `unknown_run` | 404 | no such run (or folder) |
| `unknown_item` | 404 | no such review item on that run |
| `item_already_decided` | 409 | a review item transitions exactly once |
| `file_unstable` | 409 | the source changed while it was being processed |
| `unsupported_format` | 415 | no reader for that extension |
| `file_too_large` | 413 | over `max_file_mb` / `max_rows` |

---

## Evals

```bash
uv run python datasweep/evals/run.py     # the scorecard
uv run pytest datasweep/                 # tests + the same gates, enforced
```

Everything is hermetic: offline adapters, committed fixtures,
`FixedClock("2026-01-01T00:00:00Z")`, no network, no unseeded randomness.

**Ground truth never comes from the system under test.** Five canonical golden
tables are written by a committed seeded generator; a corrupter (which shares
no code with the engine — it formats `1,234.56` as a string, it never calls a
datasweep parser) injects a pinned mix of ~2,800 defects across 13 corrupted
files and records a per-cell manifest. Detection truth is that manifest, repair
truth is the golden cell value, tier truth is a hand-authored
`traps/forbidden.json` over seven trap files, and every corruption is
*information-preserving* so one uniform repair-truth definition holds for every
class.

Current scorecard (all gates pass; the naive column is the live-computed
afternoon-script baseline — decode with `errors="replace"`, `str.strip()`,
drop exact duplicates, type by all-or-nothing parse):

| metric | value | gate | naive |
|---|---|---|---|
| M1 type_inference_accuracy | 1.0000 | ≥ 0.95 | 0.1209 |
| M2 detection macro-F1 | 1.0000 | ≥ 0.85 | 0.2107 |
| M2_min per-class F1 | 1.0000 | ≥ 0.70 | 0.0000 |
| M3 auto_fix_precision | 1.0000 | ≥ 0.98 | 1.0000 |
| M3_trap trap-cell auto changes | 0 | = 0 | 0 |
| M3_clean clean-file auto changes | 0 | = 0 | 0 |
| M3_clean_findings | 0 | = 0 | 0 |
| M4 repair_auto | 0.9459 | ≥ 0.85 | 0.1746 |
| M4 repair_auto_min | 1.0000 | ≥ 0.85 | 0.0000 |
| M4 repair_total | 1.0000 | ≥ 0.90 | 0.1746 |
| M5 reversibility | 1.0000 | = 1.00 | 0.2778 |
| M6 determinism | 1.0000 | = 1.00 | 0.0000 |
| M7 trap_disposition | 1.0000 | = 1.00 | 0.3333 |

`repair_auto = 0.9459` is the expected number, not a shortfall: Excel serial
dates and typo merges are *correctly* held back for review, which is exactly
what the 0.85 gate leaves room for and what `repair_total = 1.0` then confirms.

M6 is the one metric that shells out — it re-invokes the CLI in two subprocesses
with `PYTHONHASHSEED=0` and `PYTHONHASHSEED=1` and compares all four artifacts
byte-for-byte, because Python's string hash randomization varies only *between*
processes.

### Regenerating fixtures

```bash
uv run python datasweep/evals/fixtures/generate.py --seed 1337
```

Regeneration is byte-identical, including `inventory.xlsx` (openpyxl's
`docProps` timestamps and the zip entry mtimes are pinned), so a fixture change
is reviewable as a diff. The generator refuses to write a tainted fixture: it
asserts the realized class mix is within ±2 pp of the pinned shares, that no
two ops stack on one cell, that the only exact duplicate rows are the injected
ones, that no label edit collides with a legitimate label, and that no golden
numeric column contains a self-inflicted outlier.

---

## Architecture

```
src/datasweep/
  engine/        pure domain logic — no I/O, no clock, no randomness
    models.py      Pydantic v2 entities, the 29-rule registry, the tier algebra
    inference.py   per-cell micro-parsers, the FR-5 coverage vote, D7 numbers, D9 dates
    detectors.py   the eight detector families, D10 clustering, D11 robust statistics
    planner.py     confidence formulas, tier decisions, review items
    transforms.py  fix application and the constructive inverse `revert`
    pipeline.py    the fixed 9-stage order ENC → STR → WS → MISS → TYPE → DATE → CAT → DUP → OUT
    report.py      report.md rendering
  adapters/      one Protocol per external capability, offline default + live variant
  store/         Repository protocol: SQLite (default) and in-memory
  services.py    the only layer touching both I/O and the engine
  api/  cli/     thin: parse, delegate, serialize
```

The engine is pure: same inputs ⇒ same outputs, with time supplied as data. All
artifact bytes are a function of (file bytes, effective policy, engine version)
— no run ids, timestamps or absolute paths appear in any artifact, which is
what makes the determinism gate achievable.

## Live adapters

Everything ships working offline. Three adapters have live variants that
activate only when an optional extra is installed, and none of them is
eval-gated:

| adapter | offline default | live variant | needs |
|---|---|---|---|
| `Watcher` | `PollingScanner` — deterministic `os.scandir` walk + settle check | `WatchdogWatcher` — inotify/FSEvents, lower latency | `pip install watchdog` |
| `EncodingDetector` | `SimpleEncodingDetector` — BOM → UTF-8 → cp1252 → latin-1 | `CharsetNormalizerDetector` — long-tail encodings (Shift-JIS, KOI8-R…) | `pip install charset-normalizer` |
| `Notifier` | `LogNotifier` — one line into `<db_dir>/notify.log` | `DesktopNotifier` — `notify-send` / `osascript` | a desktop session |

A live adapter raises a clear `missing_dependency` error if its extra is absent;
it is never imported at module load of the offline path. `openpyxl` is a core
dependency, not an extra, because Excel input is locked scope and the eval
fixtures include a committed `.xlsx`.

No credentials, no network calls, no secrets: datasweep is a local tool.

## Deliberate non-goals

No imputation, ever, in any tier — filling a missing value is a modeling
decision, not cleaning. No winsorizing, clamping or dropping of outliers. No
fuzzy record deduplication (only exact full-row duplicates). No token-sort label
clustering, no cross-column constraints, no unit conversion, no LLM-assisted
cleaning. Every rule here is deterministic and explainable: you can read the
rule that did this.

# datasweep — EVALS

## 1. What this product lives or dies on

Two capabilities, per SCOPE.md:

- **H1 — Detection fidelity.** Column types inferred correctly from dirty
  evidence, and the eight issue classes (ENC, WS, MISS, TYPE, DATE, CAT, DUP,
  OUT) detected at the right cells with few false alarms. Miss the issues and
  the cleaner is a no-op; hallucinate issues on clean data and the background
  tool gets uninstalled.
- **H2 — Safe-fix decisioning.** Of the fixes the tier system chooses to
  auto-apply, essentially all must be *correct* (restore the true value); no
  ambiguous case may ever land in the auto tier; **and** the ambiguous cases
  must actually surface for review rather than being silently dropped. That
  third clause is half of the locked contract ("flag ambiguous ones for
  review") and is gated by M7. A background cleaner whose auto tier is
  95%-right is a data corruption engine at scale (SCOPE.md D1 — the Excel
  gene-name lesson, Ziemann et al. 2016). Recall can be sacrificed; auto
  precision cannot.

Everything else (watching, storage, API/CLI plumbing, review workflow
mechanics) is covered by ordinary pytest unit/integration tests, not eval
gates.

All evals are hermetic: offline adapters only (`PollingScanner`,
`SimpleEncodingDetector`, `LocalArtifactWriter`, `LogNotifier`), committed
fixtures, `FixedClock("2026-01-01T00:00:00Z")`, no network, no unseeded
randomness (the pipeline is randomness-free; the fixture *generator* is
seeded).

## 2. Ground truth by construction, and how we avoid circularity

The central trick: corrupted fixtures are generated from committed **golden**
(clean) files by a committed, seeded **corrupter** that records a per-cell
**manifest** of every injected defect (class, coordinates, before/after).
Ground truth is therefore known by construction and independent of the engine:

- *Detection truth* = the manifest (did the engine report class K at cell
  (r,c) that the corrupter damaged?).
- *Repair truth* = **the golden value** (did the fix restore exactly what was
  there before corruption?).
- *False-alarm truth* = the golden files themselves (a clean file must pass
  through untouched and produce no findings at all).
- *Tier truth* = the hand-authored trap files and `forbidden.json` (§4.3).

**One uniform repair-truth definition holds for every class**, and the
corrupter is constrained to make that true: *every corruption is
information-preserving*. A corruption may only rewrite a cell into a form from
which the golden value is recoverable by the documented rules. This is why
`sentinel_missing` targets **only cells that are already canonical-null in the
golden** (§4.2): re-encoding an empty cell as `N/A` is reversible — the
correct fix `N/A → null` restores golden exactly — whereas overwriting a real
value with `N/A` would destroy information and no correct engine could ever
score for it (SCOPE.md D8 forbids imputation in every tier). Any op that
cannot satisfy `correct_fix(corrupted_cell) == golden_cell` is not a legal op.

The corrupter shares **no code** with the engine (separate module, string
templates only — e.g. it *writes* `1,234.56` by formatting, it never calls
engine parsers), so the engine cannot "agree with itself". Hand-authored trap
files (§4.3) additionally pin the tier system to decisions a human vetted,
the same role flowlist's hand-authored golden tables play.

## 3. Metrics

Vocabulary: for a corrupted fixture `f`, `M_f` is its manifest (set of injected
ops with class and cell coords), `A_f` the engine's auto-tier audit entries,
`F_f` the engine's `findings.jsonl` lines (all tiers), `R_f` the review items,
and `G_f` the golden table. `cell(e)` is an entry's original coordinate;
`golden(r,c)` the golden value (which may be canonical-null). The manifest
op → issue-class mapping is fixed in `evals/metrics.py` (e.g.
`op:pad_whitespace → WS`, `op:typo_label → CAT`, `op:inject_outlier → OUT`).

**Detection input.** Every detection metric reads `findings.jsonl` (SCOPE.md
FR-10, DATA_MODEL.md §3.2) — the complete, uncapped, per-instance record at
every tier. Metrics never read `IssueSummary.samples` (capped at 10) and never
reach into engine internals; the persisted artifact is the contract, so the
evals test what a downstream consumer would actually get.

### M1 — type_inference_accuracy (H1)

Over all labeled columns of all *corrupted* files (labels in `types.json`,
hand-authored per golden column and inherited by its corruptions — corruption
never changes a column's intended type). 91 labeled column instances (§4.1):

```
M1 = #(columns where ColumnProfile.inferred_type == label) / #labeled columns
```

Types must be recovered *despite* the corruption (sentinels polluting numeric
columns, thousands separators, mixed date formats). `digits` vs `integer`
confusions count as errors — that distinction is the leading-zero safety rule
(SCOPE.md D7.8). Header names are not admissible evidence: the goldens include
semantically opaque headers (`value`, `field3`) and one header (`code`) whose
correct type is `integer` in `transactions.csv` and `digits` in
`inventory.xlsx`, so a name→type dictionary cannot score 1.0. The rule is
additionally enforced at unit level by
`tests/test_inference.py::test_fr5_headers_do_not_influence_type`, which
re-runs inference with headers replaced by `c0..cn` and asserts identical
output.

### M2 — detection_f1 (H1)

Per class K over corrupted files: an injected op is **detected** iff
`findings.jsonl` carries an issue instance of class K at its cell (row-scope
match for DUP). A reported instance is a **false positive** iff it matches no
manifest op of its class — evaluated over corrupted *and* golden files (all
golden-file findings are FPs by construction).

**Column-scope ambiguity findings.** Exactly two rules assert "I cannot
decide" at column scope: `fix.date_canon_ambiguous` and
`fix.number_canon_ambiguous` (both carry `evidence.scope == "column"`). Such a
finding covers the column's injected ops of its class **only if the manifest's
`column_truth` marks that column as ambiguous**. Every corrupted date and
numeric column is `provable` by construction (§4.2), so:

- on a `provable` column, injected ops require *cell-level* issue instances to
  count as detected, and the column-scope ambiguity finding counts as **one
  false positive** of its class;
- on an `ambiguous` column (traps only), the column-scope finding covers every
  cell of that column and is not an FP.

This closes the loophole where declaring every date column ambiguous — i.e.
never doing the hard part of D9's proof search — would otherwise earn perfect
DATE recall with zero FPs.

**Column-scope advisories are outside M2.** The other column-scope findings —
`detect.mixed_number_conventions` (D7.6) and `fix.currency_mixed` — are true
statements about a column rather than claims about individual cells, and a
corrupted column that received `decimal_comma` ops genuinely *does* mix
conventions. Counting them either way would distort TYPE precision, so M2
ignores them entirely. They are covered instead by
`tests/test_detectors.py::test_fr6_mixed_convention_advisory` (which asserts
the advisory appears exactly on manifest columns flagged
`column_truth[col].mixed_conventions == true`) and by the
`M3_clean_findings = 0` gate, which forbids them on the pure dot-decimal
goldens.

```
recall_K = detected_K / injected_K
prec_K   = matched_reports_K / reports_K
F1_K     = 2·prec_K·recall_K / (prec_K + recall_K)
M2       = macro-F1 = mean over the 8 classes {ENC,WS,MISS,TYPE,DATE,CAT,DUP,OUT}
M2_min   = min over classes F1_K
```

`STR` is excluded by design: it is reader-emitted, not a detector family
(SCOPE.md FR-6), and is covered by `tests/test_readers.py`.

### M3 — auto_fix_precision (H2, the headline gate)

Over all auto-tier changes on corrupted files:

```
M3 = #{e ∈ A_f : after(e) == golden(cell(e))} / #{e ∈ A_f}
```

Comparison is on the canonical representation: a `sentinel → null` fix has
`after = null` and scores correct iff `golden(cell(e))` is canonical-null,
which §2's information-preserving constraint guarantees for every MISS op.
(`row_drop` entries count as correct iff the manifest marks that row as an
injected duplicate; `row_pad`/`header_rename`/`column_add` count against
golden structure.) Three companion hard checks, gated separately:

```
M3_trap           = #auto-tier changes touching any cell listed in forbidden.json   (must be 0)
M3_clean          = #auto-tier changes across all golden (clean) control runs       (must be 0)
M3_clean_findings = #findings.jsonl lines + #review items across all golden runs    (must be 0)
```

`M3_clean_findings` is the false-alarm half of H1 that `M3_clean` alone misses:
without it, an engine may spam the review queue and the report on perfectly
clean data and still pass every gate. Zero is the honest bar because the
goldens are alarm-free *by construction* (§4.1): canonical encoding, ISO dates,
dot-decimal, trimmed, no duplicates, seeded empty cells that are data rather
than defects (SCOPE.md FR-6), and `sensors.jsonl`'s legitimate extreme readings
placed inside both the Tukey-3.0 and MAD-3.5 fences. A golden tripping a
finding is therefore either an engine false positive or a fixture bug, and both
demand a human look.

### M4 — repair_rate (H2)

Restoration recall over *fixable* injected ops — all classes except OUT, which
is report-only **by design** (SCOPE.md D11) and is scored by M2 detection
instead. Fixable ops are 95% of injected ops under the pinned mix (§4.2).

```
repair_auto      = #{op ∈ M_f fixable : auto change at cell(op) with after == golden} / #fixable
repair_auto_min  = min over K ∈ {ENC, WS, MISS, TYPE, DUP} of repair_auto restricted to class K
repair_total     = repair_auto numerator plus review proposals whose proposed after == golden
                   (for multi-candidate proposals, the recommended candidate is scored), / #fixable
```

`repair_auto` measures hands-off value; `repair_total` measures value after a
review pass in which the user accepts correct proposals. `repair_auto_min`
ranges over exactly the five classes whose ops are *entirely* auto-expected
under the pinned mix, so demoting any one of them to review is caught even
though the aggregate might survive (§5 arithmetic).

### M5 — reversibility (FR-9)

Over every corrupted and golden run, and additionally over one revision-2 run
(accept-all on a review-bearing fixture):

```
M5 = #(runs where revert(cleaned, audit) == parsed original, cell-exact) / #runs
```

Because every revision carries a *complete* audit against the parsed original
rather than a delta (SCOPE.md FR-11, DATA_MODEL.md §2.7), the revision run is
scored by the same predicate: `revert(cleaned.r2, audit.r2) == parsed original`.

### M6 — determinism (FR-16)

On 4 fixtures spanning formats (csv, tsv, xlsx, jsonl): run the full pipeline
twice **in two separate subprocesses**, launched with `PYTHONHASHSEED=0` and
`PYTHONHASHSEED=1` respectively, writing to two temp artifact dirs, both with
`FixedClock`. All four artifacts — `cleaned.*`, `audit.jsonl`,
`findings.jsonl`, `report.md` — must be byte-identical across the two
processes; then recompute `issue_counts`/`change_counts` from the artifacts and
match the stored Run row.

The subprocess split is load-bearing, not ceremony: Python's string hash
randomization varies only *between* processes, so two in-process runs would
produce identical bytes even if output order depended on set/dict iteration —
exactly the FR-16 violation this metric exists to catch. Two seeds turn M6 from
a tautology into a real test. This also requires that no run id, timestamp, or
absolute path appear in any artifact, which DATA_MODEL.md §3.1/§3.4 enforce.

```
M6 = 1.0 if all identity/consistency checks pass else 0.0
```

### M7 — trap_disposition (H2, the positive half of the tier contract)

M3_trap gates what must *not* happen on the trap fixtures. M7 gates what
*must*: every hand-authored expectation in `forbidden.json` (§4.3) is checked
against the actual run output.

```
M7 = #(expectations satisfied) / #expectations
```

An expectation `{scope, cells, expect, expect_rule, expect_candidates?}` is
satisfied iff:

- `expect: "review"` — a ReviewItem with `rule == expect_rule` exists whose
  `proposal.cells` cover every listed cell; and where `expect_candidates` is
  given, the proposal's candidate formats/readings equal that set exactly (this
  is how "with both candidate interpretations" in US-3 becomes machine-checked
  — the flagship `03/04/2021` case);
- `expect: "report"` — `findings.jsonl` carries a `report`-tier line with
  `rule == expect_rule` at every listed cell;
- `expect: "none"` — no finding of the expectation's class appears at any
  listed cell, at any tier (the correct behaviour for legitimate near-labels
  and near-duplicate rows, where proposing anything at all is the error).

M7 is what forces the ambiguous-date review item, the ambiguous-number-convention
review item, the soft-sentinel review item, and — since no corrupter op injects
numeric sentinels — the **only positive gate on numeric-sentinel detection**
(`trap_numeric_sentinel.csv`, `expect: "report"`). Without M7, an engine that
proposes and reports absolutely nothing on the trap set passes every other gate
while failing half the locked scope.

### Report-only metrics (printed, not gated)

- `review_precision` = share of review proposals whose recommended candidate
  equals golden — tier-calibration diagnostic (if this nears 1.0 for a rule,
  the rule is a promotion candidate; if low, proposals are noise).
- `tier_distribution` — auto/review/report change counts per class.
- `per_file_table` — M2/M3/M4 per fixture, to localize regressions.
- `naive_deltas` — each gated metric minus its naive-baseline value (§5).

## 4. Fixture strategy

Everything lives in `evals/fixtures/`, deterministic and committed. All data
is synthetic (invented names, merchants, devices); no real PII, no scraped
data. Layout:

```
evals/fixtures/
  golden/
    contacts.csv      # 400 rows  x 8 cols
    transactions.csv  # 1200 rows x 8 cols
    sensors.jsonl     # 1200 rows x 5 cols
    inventory.xlsx    # 300 rows  x 6 cols
    survey.tsv        # 600 rows  x 7 cols
  corrupted/
    contacts.c1.csv … survey.c3.tsv           # 13 files (3/3/2/2/3 per golden)
    <name>.manifest.json                      # one per corrupted file
  traps/
    trap_ambiguous_dates.csv    # every date component <= 12 in both positions
    trap_similar_labels.csv     # Iran/Iraq, Slovakia/Slovenia — frequent legit near-labels
    trap_leading_zeros.csv      # zip/sku columns; numeric coercion forbidden
    trap_near_dup_rows.csv      # rows identical except timestamp — must NOT be dropped
    trap_decimal_ambiguity.csv  # `1.234`-style cells with no convention proof
    trap_sentinel_words.csv     # medication column where "None" is a real category
    trap_numeric_sentinel.csv   # temperature column with -9999 runs — report-only
    forbidden.json              # hand-authored expectations: forbidden auto cells + expected dispositions
  types.json          # hand-authored semantic type per golden column
  generate.py         # seeded generator: goldens + corruptions + manifests (--seed 1337)
  expected.json       # generator-measured counts per fixture (informational only)
```

### 4.1 Golden files

Written by `generate.py` from fixed vocabularies (name/city/merchant/device
lists) with genre-realistic distributions: amounts log-normal with 2 decimals,
ages 18–90, temperatures N(21, 4) — tame distributions so goldens are genuinely
alarm-free, which `M3_clean_findings = 0` then enforces. Goldens are canonical:
UTF-8, ISO dates, dot-decimal, no duplicate rows, trimmed, no sentinels.

| File | Columns (type per `types.json`) |
|---|---|
| `contacts.csv` | `name` text, `email` text, `city` categorical, `country` categorical, `zip` digits, `signup_date` date, `age` integer, `value` float |
| `transactions.csv` | `txn_id` integer, `date` date, `amount` float, `currency` categorical, `merchant` categorical, `category` categorical, `status` categorical, `code` integer |
| `sensors.jsonl` | `ts` datetime, `device_id` categorical, `temperature_c` float, `humidity_pct` float, `status` categorical |
| `inventory.xlsx` | `sku` digits, `product` text, `qty` integer, `unit_price` float, `restock_date` date, `code` digits |
| `survey.tsv` | `respondent_id` integer, `gender` categorical, `satisfaction` integer, `comment` text, `submitted` date, `income` float, `field3` categorical |

Three deliberate anti-overfit properties (they are what stop M1 from being a
header-name lookup): `value` and `field3` are semantically opaque; `code` is
`integer` in one file and `digits` (leading zeros) in another; `satisfaction`
is a 1–5 integer that a header-driven guesser would call categorical.

**Seeded empty cells.** Each golden designates **two nullable columns**, all
non-`text`, in which 6% of cells are genuinely empty (empty field in CSV/TSV,
JSON `null` in JSONL, empty cell in xlsx): contacts `age`,`value`;
transactions `code`,`category`; sensors `humidity_pct`,`status`; inventory
`restock_date`,`code`; survey `income`,`satisfaction`. These exist for two
reasons: they give `sentinel_missing` an information-preserving target (§2),
and they exercise `ColumnProfile.null_count` and the FR-6 rule that an empty
cell is data, not a defect — which is why they cost nothing against
`M3_clean_findings = 0`.

**Legit extremes.** `sensors.jsonl` includes four labeled *legitimate* extreme
values (heat-wave readings ≈ 41 °C) placed inside both the Tukey-3.0 and
MAD-3.5 fences by construction, pressure-testing outlier precision.

**xlsx byte stability.** `inventory.xlsx` is produced by a committed openpyxl
section inside `generate.py` (values only, no styling). Because openpyxl
stamps `docProps/core.xml` from the wall clock and the zip container records
entry mtimes, `generate.py` explicitly pins `wb.properties.creator`,
`wb.properties.created` and `.modified` to `2020-01-01T00:00:00`, then rewrites
the saved archive with fixed entry timestamps `(1980,1,1,0,0,0)` and a fixed
entry order. Only then is `--seed 1337` byte-reproducible, which is what makes
a fixture regeneration reviewable as a diff.

Totals: 3,700 golden rows / 24,800 cells / 34 labeled columns.

### 4.2 Corruptions

The corrupter applies a per-file op mix (seeded `random.Random(1337)`).
`n_ops = round(op_rate × n_cells)` with `op_rate` drawn per file from
[0.03, 0.06] (0.06 fixed for the two `inventory.xlsx` corruptions, which are
small and carry the Excel-specific ops). Across the 13 corrupted files
(9,600 rows / 66,600 cells) this yields **≈ 3,050 injected ops**.

**Pinned class shares.** The mix is *not* left to chance, because the M4 gate's
calibration depends on it. `generate.py` allocates ops by class to these
shares and **asserts each realized share is within ±2 percentage points**,
raising rather than writing a fixture that would silently move the gate:

| Class | Ops | Share of injected ops | Expected tier | ≈ count |
|---|---|---|---|---|
| ENC | `mojibake_encode` | 10% | auto | 305 |
| WS | `pad_whitespace`, `insert_nbsp`, `insert_zero_width`, `double_internal_space` | 22% | auto | 670 |
| MISS | `sentinel_missing` | 10% | auto | 305 |
| TYPE | `thousands_sep`, `decimal_comma`, `currency_prefix`, `leading_apostrophe` | 18% | auto | 550 |
| DATE | `date_reformat` / `excel_serial` | 15% | auto (serial: review) | 455 |
| CAT | `case_label`, `punct_label` | 10% | auto | 305 |
| CAT | `typo_label` | 5% | review | 153 |
| DUP | `duplicate_row` | 5% | auto | 153 |
| OUT | `inject_outlier` | 5% | report | 153 |

Op definitions and the constraints that keep each information-preserving:

- `mojibake_encode` — cell → its UTF-8 bytes decoded as cp1252 (the ftfy
  round-trip is exactly invertible). Applied only to cells containing
  non-ASCII.
- WS ops — insert leading/trailing spaces, U+00A0, U+200B, or a doubled
  internal space. The golden cell is trimmed and NFC by construction, so
  removal restores it.
- `sentinel_missing` — a **golden-empty** cell (§4.1) → a token from the
  **hard** list (`NA`, `N/A`, `null`, `NaN`, `#N/A`), in a non-`text` column.
  The correct fix (`→ canonical null`) restores golden exactly. The soft list
  and the text-column case are deliberately *not* corrupted here — they are
  review-tier by design (SCOPE.md D8) and are gated by M7 via
  `trap_sentinel_words.csv` and by `tests/test_planner.py`.
- `thousands_sep` — `1234.56` → `1,234.56`, applied **only to cells that
  already carry a decimal part**, so the result is decisive DOT and never the
  ambiguous `1,234`. `decimal_comma` — `19.99` → `19,99` (decisive COMMA).
  Both are *decisive* cells under SCOPE.md D7.2, so a correct engine repairs
  each from its own unique parse at auto tier (D7.4) regardless of how many
  cells were touched — no corrupted cell is ever left in the ambiguous class,
  which is what keeps every TYPE op auto-repairable and the M4 arithmetic
  honest.
  The corrupter additionally never pushes a column's decisive agreement below
  0.60, and the manifest header records the column's true convention, so the
  behaviour is auditable. `currency_prefix` — prefix a **single** symbol
  (`€`), the same symbol for every touched cell in a column, satisfying D7's
  single-symbol condition; coverage is partial by design, which D7 explicitly
  allows. `leading_apostrophe` — `'` prefix on a numeric or `digits` cell (the
  Excel text-guard artifact); stripping restores golden in both cases, and in a
  `digits` column D7.8 still forbids numeric coercion.
- `date_reformat` — cell → an alternate format from the D9 list, with the
  column kept **provably** day-first or month-first by leaving ≥ 1 cell whose
  deciding component is > 12. The manifest header records
  `column_truth[col] = {"date_order": "provable", "proof_row": r}`. No op ever
  makes a corrupted column ambiguous — ambiguity ground truth lives only in
  the traps, so M2's column-scope rule (§3) has a clean partition.
- `excel_serial` — date → serial int (xlsx only; 7 pp of that file's 15% DATE
  share). Review-expected; scored by `repair_total`, not `repair_auto`.
- `case_label` / `punct_label` — casing or punctuation variant of a
  categorical value (fingerprint-mergeable). `typo_label` — a
  Damerau–Levenshtein-1 edit that collides with no other legitimate label.
- `duplicate_row` — copy an earlier row verbatim to a later position.
- `inject_outlier` — numeric cell → median ± (8–15)·IQR, beyond both fences.

**Collision hygiene** (asserted by `generate.py`; a violation raises and no
fixture is written):

1. **Ops never stack.** Target cells are drawn without replacement from a
   single pool, so no cell receives two ops and no partially-correct repair can
   be scored as wrong.
2. **Duplicate rows are frozen.** `duplicate_row` ops are allocated first;
   both the source row and its copy are removed from the cell pool, so the pair
   cannot diverge (which would cost DUP recall) and no later op can drop a row
   the manifest never marked.
3. **No accidental duplicates.** After corruption, the only exact duplicate
   rows in the file are the injected ones.
4. **No accidental label collisions.** No CAT edit result equals an existing
   legitimate label in its column.
5. **CAT ops touch ≤ 25% of any single label's occurrences**, so the golden
   spelling always remains the cluster's most frequent form and a correct
   fingerprint/NN merge canonicalizes *to* the golden value.
6. **MISS ops never exceed the golden-null pool** of the file's nullable
   columns.
7. **Every op's recorded `before` equals the golden cell** at those
   coordinates.

Each op records `{op, class, row, col, before, after}`; the manifest header
records `{golden, seed, op_rate, n_ops, class_shares, column_truth,
nullable_columns}`. `column_truth` carries, per column, whatever ground truth a
metric needs: `{"date_order": "provable", "proof_row": 93}` for date columns,
`{"number_convention": "DOT", "mixed_conventions": true}` for numeric columns
that received `decimal_comma` ops. No corrupted column is ever marked
ambiguous.

### 4.3 Traps and `forbidden.json`

Traps are hand-authored small files (20–60 rows) checked in as-is. They encode
the judgment calls the corrupter can't: ambiguity that *looks* fixable.
`trap_similar_labels.csv` deliberately puts both members of each near-pair well
above the `nn_max_ratio` threshold so a correct engine proposes nothing at all.

`forbidden.json` is a map from trap filename to a list of expectations, each
carrying both halves of the tier contract — the cells that must receive no auto
change (feeding M3_trap) and the disposition that must actually occur (feeding
M7):

```json
{"trap_ambiguous_dates.csv": [
   {"id":"amb_date_col","klass":"DATE","scope":"column","col_name":"event_date",
    "cells":[[0,3],[4,3],[9,3]],
    "forbid_auto":true,"expect":"review","expect_rule":"fix.date_canon_ambiguous",
    "expect_candidates":["%d/%m/%Y","%m/%d/%Y"]}],
 "trap_similar_labels.csv": [
   {"id":"iran_iraq","klass":"CAT","scope":"cells","col_name":"country",
    "cells":[[2,1],[7,1]],"forbid_auto":true,"expect":"none"}],
 "trap_numeric_sentinel.csv": [
   {"id":"minus9999","klass":"MISS","scope":"cells","col_name":"temperature_c",
    "cells":[[3,2],[4,2],[5,2],[11,2]],
    "forbid_auto":true,"expect":"report","expect_rule":"detect.numeric_sentinel"}]}
```

The seven traps and their expectations:

| Trap | forbid_auto | expect |
|---|---|---|
| `trap_ambiguous_dates.csv` | date cells | `review` / `fix.date_canon_ambiguous`, both candidates |
| `trap_decimal_ambiguity.csv` | numeric cells | `review` / `fix.number_canon_ambiguous`, both readings |
| `trap_sentinel_words.csv` | `None` cells in a text column | `review` / `fix.sentinel_null_soft` |
| `trap_numeric_sentinel.csv` | `-9999` cells | `report` / `detect.numeric_sentinel` |
| `trap_similar_labels.csv` | near-label cells | `none` (CAT) |
| `trap_near_dup_rows.csv` | the near-duplicate rows | `none` (DUP) |
| `trap_leading_zeros.csv` | zip/sku cells | `none` (TYPE) — and the columns must type as `digits` |

### 4.4 Controls, labels, and bookkeeping

- **Clean controls** are the golden files themselves run through the full
  pipeline (M3_clean, M3_clean_findings, and FPs feeding M2 precision).
- **`types.json`** — hand-labeled `{file: {column: type}}` for M1; authored
  once against SCOPE.md FR-5's vocabulary. 34 golden columns → 91 labeled
  column instances across the 13 corrupted files.
- **`expected.json`** — generator-recorded per-fixture op counts and class
  totals, informational for reviewing gate levels; gates are always asserted
  against live-computed values, never against this file.

Regenerating fixtures (`python evals/fixtures/generate.py --seed 1337`) is a
reviewed change, as in flowlist.

**Ground truth summary:** M1 truth = hand labels; M2/M3/M4 truth = corruption
manifests + golden values (construction); M7 and M3_trap truth = hand-authored
`forbidden.json`; M5 truth = the parsed original itself; M6 truth = identity.
No metric's truth is produced by the system under test.

## 5. Baselines and gates

The naive baseline — implemented *live* in `evals/metrics.py`, ~40 lines — is
what an honest afternoon script does: decode UTF-8 with `errors="replace"`,
`str.strip()` every cell, drop exact duplicate rows, and infer types by
all-or-nothing parse (`int` → `float` → `%Y-%m-%d` → `str`). Its expected
scores follow from what it can and cannot see; `run.py` prints its actual
live-computed numbers alongside the gates:

| Metric | Naive baseline | Why |
|---|---|---|
| M1 | ≈ 0.55–0.65 | sentinel-polluted and separator-formatted numeric columns fall to `text`; every mixed/non-ISO date column falls to `text`; leading-zero columns mistyped `integer` |
| M2 macro-F1 | ≈ 0.18–0.25 | only WS (partially — no NBSP/zero-width/NFC) and DUP detectable; 0 on the other six |
| M3 | ≈ 0.90–0.97 | stripping is usually right, but `errors="replace"` mangles mojibake cells (U+FFFD ≠ golden) |
| M3_trap | > 0 | it strips/dedupes blindly inside trap files |
| M3_clean_findings | 0 | trivially: it reports nothing at all. A one-sided safety gate, not a capability metric — M2 and M4 carry the capability load |
| M4 repair_auto | ≈ 0.28 | repairs only the WS (22%) and DUP (5%) shares of the 95% fixable ops |
| M4 repair_total | ≈ 0.28 | it has no review tier |
| M5 | 0.0 | no audit log ⇒ nothing reversible |
| M7 | 0.0 | no tiers, no review items, no findings |

### Gate arithmetic for M4

Under the pinned shares (§4.2), of 100 injected ops: 95 are fixable, and a
correct engine auto-repairs ENC 10 + WS 22 + MISS 10 + TYPE 18 + DATE-proven
≈ 14.5 + CAT-fingerprint 10 + DUP 5 ≈ **89.5**, leaving Excel-serial ≈ 0.5 and
CAT-typo 5 correctly in review. So:

- a correct engine lands at `repair_auto ≈ 89.5/95 = 0.94` and
  `repair_total ≈ 1.0`;
- the "route the hard classes to review" degenerate that M4 exists to block
  lands at `(10+22+10+5)/95 = 0.50`;
- demoting any *single* fully-auto class lands at: TYPE → 0.75, WS → 0.71,
  DATE → 0.79, ENC/MISS/CAT → 0.84, DUP → 0.89.

A 0.85 aggregate gate therefore catches every demotion except DUP-alone, which
`repair_auto_min ≥ 0.85` over {ENC, WS, MISS, TYPE, DUP} catches directly
(a demoted class scores ≈ 0 on its own slice). Correct-engine slack is ≈ 9
points on the aggregate and ≈ 12 on the per-class minimum.

### Gates (asserted on live-computed values)

| Gate | Threshold | Rationale |
|---|---|---|
| M1 type_inference_accuracy | ≥ 0.95 | 91 labeled column instances, so ≤ 4 errors. Micro-parser voting with a 0.90 majority should recover essentially all columns despite ≤ 6% cell damage; the slack covers genuinely underdetermined columns. Naive ≈ 0.60, and header-name lookup cannot reach the gate (§4.1). |
| M2 detection macro-F1 | ≥ 0.85 | Needs real recall *and* precision on all eight classes at once; naive ≈ 0.20 by construction. Not 1.0: boundary ops (an injected outlier landing near a fence, a typo the fingerprint rule already absorbs) legitimately cost a few points. |
| M2_min per-class F1 | ≥ 0.70 | Macro mean must not hide a dead detector. Every class has ≥ 150 injected instances under the pinned mix, so no class's F1 is sampling noise. |
| M3 auto_fix_precision | ≥ 0.98 | The safety contract (SCOPE.md D1). With information-preserving corruptions and the §4.2 collision invariants, a correctly tiered engine approaches 1.0 because ambiguous cases are *routed out* of the auto tier; the 2% slack covers genuinely hard boundary cases, not judgment errors. Naive's ≈ 0.93 with trap violations shows why "usually right" is not the bar. |
| M3_trap trap-cell auto changes | = 0 | Hand-vetted ambiguity may never be auto-touched. Zero, not small: any violation is a tier-design bug. |
| M3_clean clean-file auto changes | = 0 | A clean file must pass through byte-equivalent (idempotence of cleaning). |
| M3_clean_findings clean-file findings + review items | = 0 | The false-alarm half of H1. Goldens are alarm-free by construction (§4.1), so any finding is an engine FP or a fixture bug. Blocks the "spam the review queue" evasion of M3_clean. |
| M4 repair_auto | ≥ 0.85 | Blocks broad timidity; expected ≈ 0.94, degenerate ≈ 0.50 (arithmetic above). Naive ≈ 0.28. |
| M4 repair_auto_min over {ENC,WS,MISS,TYPE,DUP} | ≥ 0.85 | Blocks demoting one class while the aggregate survives; expected ≈ 0.97 per class. |
| M4 repair_total | ≥ 0.90 | With review proposals counted, only genuinely undecidable ops may remain; 10% slack. Measures that the review tier carries real proposals, not just warnings. |
| M5 reversibility | = 1.00 | The non-destructive contract is constructive (SCOPE.md D4); one irreversible run is a broken audit invariant, not noise. |
| M6 determinism | = 1.00 | CONVENTIONS.md hermeticity; cross-process with two `PYTHONHASHSEED` values, which is what makes every other number trustworthy. |
| M7 trap_disposition | = 1.00 | The positive half of "confidence-tiered: auto-apply safe fixes, flag ambiguous ones for review". Hand-vetted, so a miss is a tier-design bug — same logic as M3_trap. Naive 0.0. |

**Anti-gaming summary.** M3 alone is trivially gamed by never auto-fixing
anything — `repair_auto` ≥ 0.85 and `repair_auto_min` ≥ 0.85 block that. M4
alone is gamed by auto-fixing everything — M3 / M3_trap block that. Both
together are still gamed by an engine that stays silent on hard cases — M7
blocks that. And all three are gamed by an engine that shouts on clean data —
`M3_clean_findings` = 0 blocks that. The four together are the product.

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed, detail)`,
  `EvalReport`, the manifest-matching logic, the op→class map, the
  `forbidden.json` expectation checker, the naive baseline implementation, and
  F1/precision/recall helpers. All detection metrics read the persisted
  `findings.jsonl` (§3) — never engine internals, never `IssueSummary.samples`.
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Loads fixtures, runs the full pipeline (services with offline
  adapters, `InMemoryRepository`, `FixedClock`, tmp artifact dirs) over
  corrupted + golden + trap files, computes M1–M7 and the report-only
  metrics, prints a scorecard — one line per metric with value, gate,
  PASS/FAIL, and detail (per-class F1s, per-file rows, naive deltas) — then a
  JSON summary. Exit code 0 iff all gates pass. M6 is the one metric that
  shells out: it re-invokes the CLI in two subprocesses with differing
  `PYTHONHASHSEED`.
- **`evals/test_gates.py`** — one pytest test per gate row in §5, names
  carrying FR ids (e.g. `test_fr7_auto_precision_gate`,
  `test_fr7_trap_zero_auto`, `test_fr7_trap_disposition_gate`,
  `test_fr9_reversibility_gate`, `test_fr16_determinism_gate`), each calling
  the metric functions directly; plus `test_eval_runner_passes` executing
  `run.py` end-to-end asserting exit code 0. `uv run pytest datasweep/`
  therefore fails on any quality regression.

Runtime budget: one full pass processes 3,700 golden rows + 9,600 corrupted
rows + ≈ 280 trap rows ≈ **13,600 rows across 25 files**, plus M6's 8
subprocess re-runs of 4 fixtures and one revision-2 run — call it ≈ 20,000
row-passes of string-level work with no optimization loops. Well under 30 s on
a laptop; runs in every CI invocation.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_watcher.py` (scan order, ignore patterns, settle deferral w/ injected clock) |
| FR-2 | `tests/test_services.py::test_fr2_idempotent_skip`, `::test_fr2_force_rerun`, `::test_fr2_failed_run_does_not_suppress` |
| FR-3 | `tests/test_readers.py` (dialects, ragged rows → pad / `_overflow`, dup headers, headerless heuristic, jsonl key union, xlsx sheet) |
| FR-4 | **M2(ENC), M3, M4** + `tests/test_encoding.py` (BOM/cascade, round-trip repair unit cases) |
| FR-5 | **M1** + `tests/test_inference.py` (each micro-parser incl. the bool token list, vote thresholds, digits-vs-integer, `test_fr5_headers_do_not_influence_type`) |
| FR-6 | **M2** (eight detector classes; `STR` excluded by design) + `tests/test_detectors.py` (one class per test group, incl. fence math on known arrays, `test_fr6_empty_cell_is_not_an_issue`, `test_fr6_mixed_convention_advisory`) |
| FR-7 | **M3, M3_trap, M3_clean, M3_clean_findings, M4, M7** + `tests/test_planner.py` (tier algebra, policy overrides, D7 convention algorithm and D12 confidence formulas on constructed evidence) |
| FR-8 | `tests/test_transforms.py` (audit completeness: diff == entries, no no-ops, pipeline order) |
| FR-9 | **M5** + `tests/test_transforms.py::test_fr9_revert_row_drop_and_headers` |
| FR-10 | `tests/test_artifacts.py` (naming, atomic write, original-bytes-unchanged hash assert, `findings.jsonl` completeness vs `Run.issue_counts`, no ids/timestamps/paths in artifacts) |
| FR-11 | `tests/test_review.py` (single transition, content-derived item ids stable across re-runs, complete-audit revision, no re-proposal after reject, every review cell present in `findings.jsonl`) |
| FR-12 | `tests/test_store.py` (append-only, run finalize transaction, count reconciliation, folder delete leaves runs with `folder_id` NULL) |
| FR-13 | `tests/test_cli.py::test_fr13_run_once` + notifier smoke (`LogNotifier` output) |
| FR-14 | `tests/test_api.py` (FastAPI TestClient, error-code mapping, `/runs/{id}/findings`) |
| FR-15 | `tests/test_cli.py` (CliRunner, exit codes, `--json`) |
| FR-16 | **M6** (cross-process, two `PYTHONHASHSEED` values) |
| US-3 | **M3_trap + M7** (auto-forbidden *and* expected disposition produced, with both candidate interpretations) |
| US-8 | **M4 repair_total** (Excel serial ops scored via the recommended review candidate) + `tests/test_readers.py` xlsx cases |

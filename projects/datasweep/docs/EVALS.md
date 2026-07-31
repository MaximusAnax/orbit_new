# datasweep — EVALS

## 1. What this product lives or dies on

Two capabilities, per SCOPE.md:

- **H1 — Detection fidelity.** Column types inferred correctly from dirty
  evidence, and the eight issue classes (ENC, WS, MISS, TYPE, DATE, CAT, DUP,
  OUT) detected at the right cells with few false alarms. Miss the issues and
  the cleaner is a no-op; hallucinate issues on clean data and the background
  tool gets uninstalled.
- **H2 — Safe-fix decisioning.** Of the fixes the tier system chooses to
  auto-apply, essentially all must be *correct* (restore the true value), and
  no ambiguous case may ever land in the auto tier. This is the product's
  contract: a background cleaner whose auto tier is 95%-right is a data
  corruption engine at scale (SCOPE.md D1 — the Excel gene-name lesson,
  Ziemann et al. 2016). Recall can be sacrificed; auto precision cannot.

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
- *Repair truth* = the golden value (did the fix restore exactly what was
  there before corruption?).
- *False-alarm truth* = the golden files themselves (a clean file must pass
  through untouched).

The corrupter shares **no code** with the engine (separate module, string
templates only — e.g. it *writes* `1,234.56` by formatting, it never calls
engine parsers), so the engine cannot "agree with itself". Hand-authored trap
files (§4.3) additionally pin the tier system to decisions a human vetted,
the same role flowlist's hand-authored golden tables play.

## 3. Metrics

Vocabulary: for a corrupted fixture f, `M_f` is its manifest (set of injected
ops with class and cell coords), `A_f` the engine's auto-tier audit entries,
`R_f` the review-tier proposals, `G_f` the golden table. `cell(e)` is an
entry's original coordinate; `golden(r,c)` the golden value. The manifest
op → issue-class mapping is fixed in `evals/metrics.py` (e.g.
`op:pad_whitespace → WS`, `op:typo_label → CAT`, `op:inject_outlier → OUT`).

### M1 — type_inference_accuracy (H1)

Over all labeled columns of all *corrupted* files (labels in `types.json`,
hand-authored per golden column and inherited by its corruptions — corruption
never changes a column's intended type):

```
M1 = #(columns where ColumnProfile.inferred_type == label) / #labeled columns
```

Types must be recovered *despite* the corruption (sentinels polluting numeric
columns, thousands separators, mixed date formats). `digits` vs `integer`
confusions count as errors — that distinction is the leading-zero safety rule
(SCOPE.md D7).

### M2 — detection_f1 (H1)

Per class K over corrupted files: an injected op is **detected** iff the run
reports an issue instance of class K at its cell (row-scope match for DUP;
for column-level DATE ambiguity findings, every cell of that column counts as
covered). A reported instance is a **false positive** iff it matches no
manifest op of its class — evaluated over corrupted *and* golden files (all
golden-file reports are FPs by construction).

```
recall_K = detected_K / injected_K
prec_K   = matched_reports_K / reports_K
F1_K     = 2·prec_K·recall_K / (prec_K + recall_K)
M2       = macro-F1 = mean over the 8 classes {ENC,WS,MISS,TYPE,DATE,CAT,DUP,OUT}
M2_min   = min over classes F1_K
```

### M3 — auto_fix_precision (H2, the headline gate)

Over all auto-tier changes on corrupted files:

```
M3 = #{e ∈ A_f : after(e) == golden(cell(e))} / #{e ∈ A_f}
```

(`row_drop` entries count as correct iff the manifest marks that row as an
injected duplicate; `row_pad`/`header_rename` count against golden
structure.) Two companion hard checks, gated separately:

```
M3_trap  = #auto-tier changes touching any cell listed in forbidden.json  (must be 0)
M3_clean = #auto-tier changes across all golden (clean) control runs      (must be 0)
```

### M4 — repair_rate (H2)

Restoration recall over *fixable* injected ops — all classes except OUT and
numeric-sentinel MISS ops, which are report-only **by design** (SCOPE.md D8,
D11) and are scored by M2 detection instead:

```
repair_auto  = #{op ∈ M_f fixable : auto change at cell(op) with after == golden} / #fixable
repair_total = same, counting also review proposals whose proposed after == golden
               (for multi-candidate proposals, the recommended candidate is scored)
```

`repair_auto` measures hands-off value; `repair_total` measures value after a
review pass in which the user accepts correct proposals.

### M5 — reversibility (FR-9)

Over every corrupted and golden run, and additionally over one revision-2 run
(accept-all on a review-bearing fixture):

```
M5 = #(runs where revert(cleaned, audit) == parsed original, cell-exact) / #runs
```

For the revision run the chain is checked: `revert(r2, audit_r2) == r1` and
`revert(r1, audit_r1) == parsed original`.

### M6 — determinism (FR-16)

On 4 fixtures spanning formats (csv, tsv, xlsx, jsonl): run the full pipeline
twice with `FixedClock`; `cleaned.*` and `audit.jsonl` must be byte-identical;
recompute `issue_counts`/`change_counts` from artifacts and match the stored
Run row.

```
M6 = 1.0 if all identity/consistency checks pass else 0.0
```

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
    contacts.csv      # 400 rows: name, email, city, country, zip(digits), signup_date(ISO), age
    transactions.csv  # 1200 rows: txn_id, date, amount, currency, merchant, category, status
    sensors.jsonl     # 2000 rows: ts(RFC3339), device_id, temperature_c, humidity_pct, status
    inventory.xlsx    # 300 rows: sku(digits), product, qty, unit_price, restock_date
    survey.tsv        # 600 rows: respondent_id, gender, satisfaction(1-5), comment(text), submitted, income
  corrupted/
    contacts.c1.csv … transactions.c3.csv …    # 13 files, ≥2 per golden
    <name>.manifest.json                       # one per corrupted file
  traps/
    trap_ambiguous_dates.csv    # every date component ≤ 12 in both positions
    trap_similar_labels.csv     # Iran/Iraq, Slovakia/Slovenia — frequent legit near-labels
    trap_leading_zeros.csv      # zip/sku columns; numeric coercion forbidden
    trap_near_dup_rows.csv      # rows identical except timestamp — must NOT be dropped
    trap_decimal_ambiguity.csv  # `1.234`-style cells with no convention proof
    trap_sentinel_words.csv     # medication column where "None" is a real category
    trap_numeric_sentinel.csv   # temperature column with -9999 runs — report-only
    forbidden.json              # hand-authored: cells/columns that must receive zero auto changes
  types.json          # hand-authored semantic type per golden column
  generate.py         # seeded generator: goldens + corruptions + manifests (--seed 1337)
  expected.json       # generator-measured counts per fixture (informational only)
```

- **Golden files** are written by `generate.py` from fixed vocabularies
  (name/city/merchant/device lists) with genre-realistic distributions:
  amounts log-normal with 2 decimals, ages 18–90, temperatures N(21, 4) —
  tame distributions so goldens are genuinely alarm-free (the M3_clean /
  M2-precision control assumes it), except `sensors.jsonl`, which includes
  four labeled *legitimate* extreme values (heat-wave readings ~41 °C) that
  sit inside the Tukey-3.0/MAD-3.5 fences by construction — pressure-testing
  outlier precision. Goldens are canonical: UTF-8, ISO dates, dot-decimal,
  no duplicates, trimmed. `inventory.xlsx` is produced by a committed
  openpyxl script section inside `generate.py` (values only, no styling), so
  the binary is reproducible.
- **Corruptions.** The corrupter applies a per-file op mix (seeded
  `random.Random(1337)`, op list and rates recorded in the manifest header).
  Ops and their classes: `mojibake_encode` (cell → cp1252-misdecoded UTF-8;
  ENC), `pad_whitespace` / `insert_nbsp` / `insert_zero_width` /
  `double_internal_space` (WS), `sentinel_missing` (value → token from the
  hard list; MISS), `thousands_sep` / `decimal_comma` / `currency_prefix` /
  `leading_apostrophe` (TYPE), `date_reformat` (cell → alternate format from
  the D9 list, column kept provably day-first by leaving ≥1 day>12 cell;
  DATE), `excel_serial` (date → serial int; DATE, xlsx fixture only),
  `case_label` / `punct_label` / `typo_label` (Damerau–Levenshtein-1 edit of
  a categorical value whose result collides with no other legit label; CAT),
  `duplicate_row` (copy an earlier row to a later position; DUP),
  `inject_outlier` (numeric cell → median ± (8–15)·IQR, beyond both fences;
  OUT). Per corrupted file: 5–10% of cells damaged, 60–300 ops. Each op
  records `{op, class, row, col, before, after}` — `before` *is* the golden
  value, giving M3/M4 their truth for free.
- **Traps** are hand-authored small files (20–60 rows) checked in as-is, each
  with an entry in `forbidden.json` naming the cells/columns where an
  auto-change would be a tier-system failure, plus the expected disposition
  (`review` or `report`). They encode the judgment calls the corrupter can't:
  ambiguity that *looks* fixable. `trap_similar_labels.csv` deliberately puts
  both members of each near-pair well above the `nn_max_ratio` threshold so a
  correct engine proposes nothing at all.
- **Clean controls** are the golden files themselves run through the full
  pipeline (M3_clean, and FPs feeding M2 precision).
- **`types.json`** — hand-labeled `{file: {column: type}}` for M1; authored
  once against SCOPE.md FR-5's vocabulary.
- **`expected.json`** — generator-recorded per-fixture op counts and class
  totals, informational for reviewing gate levels; gates are always asserted
  against live-computed values, never against this file.

Regenerating fixtures (`python evals/fixtures/generate.py --seed 1337`) is a
reviewed change, as in flowlist.

**Ground truth summary:** M1 truth = hand labels; M2/M3/M4 truth = corruption
manifests + golden values (construction) and hand-authored `forbidden.json`;
M5 truth = the parsed original itself; M6 truth = identity. No metric's truth
is produced by the system under test.

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
| M2 macro-F1 | ≈ 0.22–0.28 | only WS (partially — no NBSP/zero-width/NFC) and DUP classes detectable; 0 on the other six |
| M3 | ≈ 0.90–0.97 | stripping is usually right, but `errors="replace"` mangles mojibake cells (U+FFFD ≠ golden) and it strips trap cells too — no tier system to stop it |
| M3_trap | > 0 | it strips/dedupes blindly inside trap files |
| M4 repair_auto | ≈ 0.25–0.35 | repairs only the WS and DUP shares of injected ops |
| M5 | 0.0 | no audit log ⇒ nothing reversible |

Gates (asserted on live-computed values):

| Gate | Threshold | Rationale |
|---|---|---|
| M1 type_inference_accuracy | ≥ 0.95 | Micro-parser voting with 0.90 majority should recover essentially all labeled columns despite ≤10% cell damage; 5% slack for genuinely underdetermined columns. Naive sits ≈ 0.60 — the margin is the entire point of joint type/missing/anomaly inference (ptype's insight). |
| M2 detection macro-F1 | ≥ 0.85 | Needs real recall *and* precision on all eight classes at once; naive scores ≈ 0.25 by construction (six classes invisible to it). Not 1.0: boundary ops (e.g. an injected outlier landing near the fence, a typo colliding with fingerprint rules) legitimately cost a few points. |
| M2_min per-class F1 | ≥ 0.70 | Macro mean must not hide a dead detector; every class must individually work. |
| M3 auto_fix_precision | ≥ 0.98 | The safety contract (SCOPE.md D1). On manifest-grounded fixtures a correctly tiered engine approaches 1.0 because ambiguous cases are *routed out* of the auto tier; 2% slack covers corrupter edge collisions, not judgment errors. Naive's ≈ 0.93 with trap violations shows why "usually right" is not the bar. |
| M3_trap trap-cell auto changes | = 0 | Hand-vetted ambiguity may never be auto-touched. This is the machine-readable form of "confidence-tiered" in the locked scope. Zero, not small: any violation is a tier-design bug. |
| M3_clean clean-file auto changes | = 0 | A clean file must pass through byte-equivalent (idempotence of cleaning; also anchors M2 precision). |
| M4 repair_auto | ≥ 0.70 | WS, ENC, hard-sentinel MISS, convention-proven TYPE, proven DATE, and DUP ops are all auto-fixable ≈ 75–80% of injected fixable ops under the default mix; ambiguous DATE/CAT ops correctly land in review and are *supposed* to be missed here. Naive ≈ 0.30. |
| M4 repair_total | ≥ 0.90 | With review proposals counted, only genuinely undecidable ops (interpretation-variant cells of ambiguous columns) may remain; 10% slack. Measures that the review tier carries real proposals, not just warnings. |
| M5 reversibility | = 1.00 | The non-destructive contract is constructive (SCOPE.md D4); one irreversible run is a broken audit invariant, not noise. |
| M6 determinism | = 1.00 | CONVENTIONS.md hermeticity; what makes every other number trustworthy. |

Anti-gaming note: M3 alone is trivially gamed by never auto-fixing anything —
M4 repair_auto ≥ 0.70 blocks that; M4 alone is gamed by auto-fixing
everything — M3/M3_trap block that. The pair is the product.

## 6. How the suite runs

Mirrors `orbit-backend/evals/` and the sibling projects:

- **`evals/metrics.py`** — `MetricResult(name, value, gate, passed, detail)`,
  `EvalReport`, the manifest-matching logic, the op→class map, the naive
  baseline implementation, and F1/precision/recall helpers.
- **`evals/run.py`** — `python -m` runnable with zero configuration and no
  network. Loads fixtures, runs the full pipeline (services with offline
  adapters, `InMemoryRepository`, `FixedClock`, tmp artifact dirs) over
  corrupted + golden + trap files, computes M1–M6 and the report-only
  metrics, prints a scorecard — one line per metric with value, gate,
  PASS/FAIL, and detail (per-class F1s, per-file rows, naive deltas) — then a
  JSON summary. Exit code 0 iff all gates pass.
- **`evals/test_gates.py`** — one pytest test per gate row in §5, names
  carrying FR ids (e.g. `test_fr7_auto_precision_gate`,
  `test_fr7_trap_zero_auto`, `test_fr9_reversibility_gate`,
  `test_fr16_determinism_gate`), each calling the metric functions directly;
  plus `test_eval_runner_passes` executing `run.py` end-to-end asserting exit
  code 0. `uv run pytest datasweep/` therefore fails on any quality
  regression.

Runtime budget: ~4,600 fixture rows total, string-level pipeline, no
optimization loops — full suite well under 30 s on a laptop; runs in every CI
invocation.

## 7. FR → test/eval mapping

| FR | Covered by |
|---|---|
| FR-1 | `tests/test_watcher.py` (scan order, ignore patterns, settle deferral w/ injected clock) |
| FR-2 | `tests/test_services.py::test_fr2_idempotent_skip`, `::test_fr2_force_rerun` |
| FR-3 | `tests/test_readers.py` (dialects, ragged rows, dup headers, jsonl key union, xlsx sheet) |
| FR-4 | **M2(ENC), M3, M4** + `tests/test_encoding.py` (BOM/cascade, round-trip repair unit cases) |
| FR-5 | **M1** + `tests/test_inference.py` (each micro-parser, vote thresholds, digits-vs-integer) |
| FR-6 | **M2** + `tests/test_detectors.py` (one class per test group, incl. fence math on known arrays) |
| FR-7 | **M3, M3_trap, M3_clean, M4** + `tests/test_planner.py` (tier algebra, policy overrides, D12 confidence formulas on constructed evidence) |
| FR-8 | `tests/test_transforms.py` (audit completeness: diff == entries, no no-ops, pipeline order) |
| FR-9 | **M5** + `tests/test_transforms.py::test_fr9_revert_row_drop_and_headers` |
| FR-10 | `tests/test_artifacts.py` (naming, atomic write, original-bytes-unchanged hash assert) |
| FR-11 | `tests/test_review.py` (single transition, revision chain, no re-proposal after reject) |
| FR-12 | `tests/test_store.py` (append-only, run finalize transaction, count reconciliation) |
| FR-13 | `tests/test_cli.py::test_fr13_run_once` + notifier smoke (`LogNotifier` output) |
| FR-14 | `tests/test_api.py` (FastAPI TestClient, error-code mapping) |
| FR-15 | `tests/test_cli.py` (CliRunner, exit codes, `--json`) |
| FR-16 | **M6** |

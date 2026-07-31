# datasweep — REVIEW

Audit trail of the scoping critique loop. Two adversarial reviews were run
against the first draft of `SCOPE.md`, `DATA_MODEL.md`, and `EVALS.md`: a
**design** review (product/architecture/intent fidelity) and an **evals**
review (metric validity, gameability, achievability). Every finding below is
either fixed in the docs or rejected with a reason. Both reviews independently
found the same blocker (#1 / #12), and three other findings overlap; the
overlaps are cross-referenced rather than merged so the audit trail stays
one-to-one with the reports.

**Totals:** 21 findings — 2 blockers, 9 majors, 10 minors. 21 addressed
(no finding rejected outright); 3 *sub-recommendations* rejected in favour of a
different fix for the same finding, noted inline and summarised at the bottom.

| # | Source | Severity | Finding | Resolution |
|---|---|---|---|---|
| 1 | design | blocker | `sentinel_missing` corrupts a real golden value into `N/A`, but the correct fix (D8: sentinel → null) can never equal the destroyed value, so every correct hard-sentinel auto fix scores as an M3 error and an M4 miss; the ≥ 0.98 headline gate is unpassable by any correct engine. EVALS §2's "repair truth = the golden value" is false for information-destroying ops. | **Fixed** by making every corruption information-preserving instead of carving out an exception. EVALS §4.1 now seeds 6% genuinely-empty cells in two designated non-`text` nullable columns per golden, and §4.2 restricts `sentinel_missing` to *those cells only* (empty → hard-list token), so `after == golden(canonical null)` holds under the single uniform truth definition. EVALS §2 states the constraint as a law the corrupter must obey ("any op that cannot satisfy `correct_fix(corrupted) == golden` is not a legal op"). To keep the goldens alarm-free under this change, SCOPE FR-6 now states that an already-empty cell is **not** an issue — MISS detects non-canonical *representations* of missingness only — and null counts moved to `ColumnProfile.null_count` (DATA_MODEL §2.4, §4). M3 §3 spells out the canonical-null comparison. |
| 2 | design | major | M6 is unsatisfiable: `audit.jsonl`'s header carries a UUID4 `run_id`, so two runs can never be byte-identical; UUID4 minting in services also violates FR-16's blanket "no unseeded randomness". | **Fixed** on both halves. All ids, timestamps, and absolute paths are removed from artifacts: the audit header is now `{source_name, content_sha256, policy_hash, engine_version, revision, n_rows, n_cols}` (DATA_MODEL §3.1), `report.md`'s footer drops the run id (§3.4), and `source_path` became `source_name` so byte-identity survives across machines. FR-16 is rescoped precisely: the randomness ban covers "any computation that influences engine output or artifact bytes"; DB surrogate keys (`Run.id`, `IssueSummary.id`, `Revision.id`) stay UUID4 and are explicitly exempt *because they never appear in an artifact* — stated in the DATA_MODEL preamble and FR-16. Ids that do appear (`ReviewItem.id`) are content-derived (#6). M6 now covers all four artifacts including `report.md`. |
| 3 | design | major | D7 and the corrupter are incoherent: `decimal_comma` scatters comma-decimal cells through a dot-decimal column, manufacturing exactly the "cell contradicts the convention" condition D7 demoted to review — yet M4's rationale counts convention-proven TYPE as auto. `currency_prefix` "uniform" is undefined. `leading_apostrophe` has no D12 row. | **Fixed** by rewriting D7 as an eight-step algorithm with worked examples (SCOPE D7). The key distinction is now explicit: a cell is *decisive* when it parses under exactly one convention, and **a decisive cell is canonicalized from its own unique reading at auto tier (D7.4)** — a parse is a fact, not a guess, so a minority `19,99` is repairable per-cell without the column agreeing. Only *ambiguous* cells inherit the column convention, auto iff `\|D\| ≥ 3 and agree ≥ 0.95` (D7.5); a mixed column additionally raises a report-tier advisory (D7.6) that blocks nothing. Currency "uniform" is defined exactly: all affixed cells bear the *same single* symbol; partial coverage is explicitly fine because stripping is representation-only; ≥ 2 distinct symbols → one `fix.currency_mixed` review item. `fix.strip_apostrophe` added to the D12 table (conf 1.0, cap auto) with the non-empty-remainder condition, plus `convention_agree`/`convention_min_decisive` policy knobs (DATA_MODEL §3.3). *Sub-recommendation rejected:* the alternative "keep D7 strict and apply convention ops column-wide" was rejected because column-wide conversion would make the whole column comma-decimal — a *correct* reading with no defect to detect — deleting the TYPE eval signal instead of fixing it. |
| 4 | design | major | The positive half of the locked contract ("flag ambiguous ones for review") is ungated: `forbidden.json` records expected dispositions but no metric asserts them, so an engine that proposes nothing on traps passes everything. US-3's acceptance criterion has no eval backing, and the flagship ambiguous `03/04/2021` case is verified nowhere. | **Fixed** by adding **M7 `trap_disposition`**, gated at 1.0 (EVALS §3, §5). `forbidden.json` gains a specified schema (§4.3) where each expectation names `klass`, `scope`, `cells`, `forbid_auto`, `expect ∈ {review, report, none}`, `expect_rule`, and optional `expect_candidates`; a `review` expectation is satisfied only if a ReviewItem of the named rule covers every cell *and its candidate set matches exactly*, which is how "with both candidate interpretations" becomes machine-checked. A table of all seven traps and their expectations is in §4.3. M7 added to the §7 mapping under FR-7 and to a new US-3 row; SCOPE US-3's acceptance criterion now cites M3_trap + M7. *Sub-recommendation rejected:* adding an ambiguous-date op to the corrupter — an ambiguous column has no recoverable golden reading, which would reintroduce exactly the truth contradiction of #1. Trap fixtures carry the ambiguity ground truth instead. |
| 5 | design | major | Report-tier findings (OUT, numeric sentinels, coercion failures) have no complete machine-readable home — no audit entries, `IssueSummary.samples` capped at 10, `report.md` is prose — so M2 cannot be computed from persisted data as modeled. | **Fixed** by adding a fourth artifact, **`findings.jsonl`** (SCOPE FR-10, DATA_MODEL §3.2): one line per detected issue instance at every tier, with class, rule, tier, coordinates, value, optional `item_id`, and evidence — the serialization of `CleanResult.issues`. EVALS §3 states that *all* detection metrics read this artifact and never `IssueSummary.samples` or engine internals, so the evals test the contract a downstream consumer actually gets. FR-12 and DATA_MODEL §2.5 were reworded to make `IssueSummary` an explicit queryability rollup rather than the system of record; API gains `GET /runs/{id}/findings`; FR-10 test row covers completeness vs `Run.issue_counts`. *Sub-recommendation rejected:* removing the 10-sample cap on `IssueSummary` — that grows the DB to O(cells) for no query benefit now that a complete artifact exists. |
| 6 | design | minor | `ReviewItem.id` is documented as "deterministic, stable across re-runs" but hashes `run_id`, a per-run UUID4 — the formula defeats its own purpose and breaks the "never re-proposed for the same content hash" check. | **Fixed** — DATA_MODEL §2.6 now hashes content-stable inputs only: `sha256(content_sha256 \| policy_hash \| engine_version \| rule \| col_index \| first_cell_row)[:8]`, with a stated deterministic collision-suffix rule. This is also what lets `report.md` (which prints item ids) be byte-identical under M6, and it simplifies the re-proposal check to a lookup by (content_sha256, item id). |
| 7 | design | minor | DATA_MODEL §2.1's invariant "deleting a folder does not delete SourceFiles or Runs" contradicts the §4 schema — with FKs ON and no `ON DELETE` action, SQLite rejects the folder DELETE that `DELETE /folders/{id} -> 204` promises. | **Fixed** — `source_files.folder_id` now declares `ON DELETE SET NULL` (§4); §2.1 and §2.2 state the resulting behaviour (history survives, the file is simply no longer scanned); the API line documents it; `tests/test_store.py` covers it in the §7 mapping. |
| 8 | design | minor | FR-2 says "if a non-failed Run exists" (which includes `skipped`) while DATA_MODEL §2.3 says `succeeded \| review_pending`. | **Fixed** — FR-2 now reads "a Run with status `succeeded` or `review_pending`", with an explicit note that `failed` and `skipped` runs never suppress reprocessing. A `test_fr2_failed_run_does_not_suppress` case was added to the §7 mapping. |
| 9 | design | minor | FR-5 never specifies the `bool` token vocabulary although `bool` tops the type priority, so whether `0/1` is boolean silently decides the type of every integer flag column; FR-3's headerless heuristic ("no cell in row 0 parses as text") is vacuous since every string parses as text. | **Fixed** — FR-5 enumerates the vocabulary (`true/false/yes/no/y/n/t/f`, case-insensitive after trim) and states that `0` and `1` are **not** boolean tokens. FR-3 restates the headerless rule constructively: row 0 is data iff *every* row-0 cell matches the micro-parser of its column's body type (typed from rows 1..n) and at least one body column is non-`text`, with a note explaining why a normal header row cannot trigger it. |
| 10 | design | minor | The size budget gives tests+evals ≈ 1,050 lines to cover the seeded generator, metrics, runner, gates *and* ~14 unit-test modules — leaving ~300 for all unit tests, which cannot honour the FR-mapping quality bar; the total will exceed the 4,000 band. EVALS' "~4,600 fixture rows" is also wrong, since corrupted files duplicate golden rows. | **Fixed, with an accepted overage.** The budget is now a per-area table (SCOPE §Size budget): source ≈ 2,760, tests ≈ 790 (13 modules), evals ≈ 695 → **≈ 4,245**, with the reason stated plainly (a CONVENTIONS-mandated eval harness plus eight detector families and four formats). Real trims were taken to pay for the eval work rather than pretending: the r≥2 delta-audit chain is gone (every revision now carries a *complete* audit — FR-11, DATA_MODEL §2.7, which also strengthens M5's invariant), token-sort label clustering moved to non-goals, `_extra_N` overflow collapsed to a single `_overflow` column, the standalone `report` CLI command and two API endpoints were cut. Three pre-approved further trims are named in priority order for the implementation phase. Fixture arithmetic corrected throughout: 3,700 golden rows / 24,800 cells, 9,600 corrupted rows / 66,600 cells, ≈ 13,600 rows per eval pass (EVALS §4.1, §6). |
| 11 | design | minor | `STR` is framed inconsistently: FR-6 says "eight detector families" and M2 gates eight classes, yet `Run.issue_counts` and the `issue_summaries` CHECK include `STR` and the FR-8 pipeline has an STR stage. | **Fixed** — FR-6 now carries an explicit framing rule: "`STR` is not a detector family. Structural repairs are emitted by the readers per FR-3, run as the second pipeline stage (D13), and are covered by `tests/test_readers.py` / `tests/test_transforms.py`; `STR` therefore appears in `Run.issue_counts` and the CHECK constraint but is deliberately outside EVALS M2's eight classes." D13 and EVALS M2 repeat the exclusion. |
| 12 | evals | blocker | Same as #1, from the metrics side: repair truth ("restore what was there before corruption") and the `sentinel_missing` op spec cannot both be right; either no correct engine reaches M3 ≥ 0.98, or an implementer silently redefines the truth. | **Fixed** — see #1. The reviewer's preferred option (a) was taken: seeded golden-empty cells that `sentinel_missing` exclusively targets, keeping one uniform truth definition rather than a per-class carve-out. The recommended side benefit was taken too: the seeded nulls give MISS realistic context and exercise `ColumnProfile.null_count`. |
| 13 | evals | major | The corrupter's op mix is unpinned ("5–10% of cells, 60–300 ops"), so the M4 ≥ 0.70 gate — the only defence against "route everything hard to review" — is unauditable and possibly miscalibrated; a lazy engine demoting TYPE and DATE may pass. Per-class sample sizes behind M2_min are also unstated. | **Fixed** — EVALS §4.2 pins the mix in a table (ENC 10%, WS 22%, MISS 10%, TYPE 18%, DATE 15%, CAT-auto 10%, CAT-typo 5%, DUP 5%, OUT 5%) with `n_ops = round(op_rate × n_cells)`, `op_rate ∈ [0.03, 0.06]`, ≈ 3,050 ops total, and `generate.py` **asserting every realized share within ±2 pp** rather than writing a fixture that would silently move the gate. §5 shows the arithmetic: correct engine ≈ 0.94, the degenerate ≈ 0.50, and each single-class demotion (TYPE 0.75, WS 0.71, DATE 0.79, ENC/MISS/CAT 0.84, DUP 0.89). The gate is raised **0.70 → 0.85** accordingly, and a new **`repair_auto_min ≥ 0.85`** over the five fully-auto classes {ENC, WS, MISS, TYPE, DUP} catches the DUP-only demotion the aggregate would survive. Per-class op counts (≥ 150 each) are tabulated so M2_min ≥ 0.70 is visibly not sampling noise. The `decimal_comma`/D7 conflict is resolved in #3. |
| 14 | evals | major | Trap expected dispositions are recorded but never gated, and numeric-sentinel detection has no positive eval anywhere — `trap_numeric_sentinel.csv` is gated only on "don't auto-touch it", so an engine that detects nothing on traps passes every gate. | **Fixed** by M7 (see #4). Numeric sentinels specifically: EVALS §3 states M7 is "the **only** positive gate on numeric-sentinel detection", via `trap_numeric_sentinel.csv`'s `expect: "report"` / `detect.numeric_sentinel` expectation covering every `-9999` cell. Adding a numeric-sentinel corrupter op was considered and rejected in favour of the trap: the op would be report-only, hence outside M4's fixable set, and would blur the clean fixable/report-only partition that makes M4's arithmetic auditable. |
| 15 | evals | major | Clean-file false alarms are gated only at the auto tier: an engine can spam the review queue and report on every golden control file and still pass, which is exactly the H1 failure mode ("hallucinate issues on clean data and the tool gets uninstalled"). M2 precision at F1 ≥ 0.70 tolerates ~0.54 precision, and nothing says review items are backed by reported findings. | **Fixed** — new hard gate **`M3_clean_findings = 0`**: total `findings.jsonl` lines plus review items across all five golden control runs (EVALS §3, §5). Zero is defensible because the goldens are alarm-free by construction, and §3 spells out why the changes made for #1 do not spoil it (seeded empty cells are data, not defects, per FR-6; `sensors.jsonl`'s legit extremes sit inside both fences). The backing invariant is now stated in two places: SCOPE FR-11 and DATA_MODEL §2.6 require every ReviewItem cell to also be a `findings.jsonl` instance, so review-tier noise is visible to M2 precision as well. §5's anti-gaming summary is rewritten around the four-way lock (M3 / M4 / M7 / M3_clean_findings). |
| 16 | evals | major | M6 runs the pipeline twice inside one process, where string hashing is stable — so an implementation whose output order depends on set/dict iteration passes M6 = 1.0 in CI and is nondeterministic in the field. The metric is a tautology w.r.t. the FR-16 clause it is meant to enforce. | **Fixed** — EVALS M6 now executes the two runs in **separate subprocesses with `PYTHONHASHSEED=0` and `PYTHONHASHSEED=1`**, comparing all four artifacts byte-for-byte across processes, with the reasoning stated inline so nobody "simplifies" it back. §6 notes M6 is the one metric that shells out. This is only achievable because #2 removed ids/timestamps/paths from artifacts, and the two fixes are cross-referenced. |
| 17 | evals | major | M2's column-level DATE credit rule rewards a wrong diagnosis: since a column-level ambiguity finding covers every cell and matches ≥ 1 manifest op, an engine that declares *every* date column ambiguous scores F1_DATE ≈ 1.0 while never proving a format — the actual hard part of D9. | **Fixed** — EVALS §3 restricts the rule: a column-scope *ambiguity* finding (`fix.date_canon_ambiguous`, `fix.number_canon_ambiguous`) covers a column's ops only if the manifest's `column_truth` marks that column **ambiguous**; on a `provable` column it covers nothing *and* counts as one class false positive. Every corrupted date/numeric column is `provable` by construction (§4.2 records `column_truth[col] = {"date_order": "provable", "proof_row": r}`), so the wrong diagnosis now collapses F1_DATE from both sides. SCOPE D9 was restructured to match, stating the two structurally different outcomes (proven → per-cell auto fixes; ambiguous → exactly one column-level review item, zero cell-level auto fixes) and that "declaring a provable column ambiguous is a detection error". A companion clause keeps the honest `detect.mixed_number_conventions` / `fix.currency_mixed` advisories out of M2 entirely (they are true statements about the column) with a unit test instead. |
| 18 | evals | minor | The data source for M2's per-cell matching is unspecified and no persisted artifact can supply it: `samples` caps at 10, `audit.jsonl` holds only applied changes, `report.md` is prose. | **Fixed** — see #5. EVALS §3 opens with an explicit "Detection input" paragraph naming `findings.jsonl` as the sole source and stating the rationale (test the persisted contract, not engine internals). The reviewer's cheaper alternative — a sentence saying metrics consume the in-memory `CleanResult` — was rejected: it would leave persistence of report-tier detail untested and leave a real product gap (no machine-readable outlier record for the user). |
| 19 | evals | minor | M1 is satisfiable by a header-name dictionary: the goldens' ~30 columns have transparent headers inherited by every corruption, so a lookup tuned once scores 1.0 with zero micro-parser capability — classic fixed-fixture overfit. | **Fixed** — EVALS §4.1 restructures the goldens with three anti-overfit properties: semantically opaque headers (`value` float, `field3` categorical), one header (`code`) whose correct type is `integer` in `transactions.csv` and `digits` in `inventory.xlsx`, and `satisfaction` (a 1–5 integer a header-driven guesser calls categorical). §3's M1 section states header names are inadmissible evidence; SCOPE FR-5 makes it a requirement ("a function of cell values only — header names are never inputs") enforced by `tests/test_inference.py::test_fr5_headers_do_not_influence_type`, which re-runs inference with headers replaced by `c0..cn` and asserts identical output. |
| 20 | evals | minor | Corrupter collision hygiene is unspecified — stacked ops on one cell, duplicate-pair rows receiving later ops, accidental post-corruption duplicates — each silently charging a correct engine against M3/M4's tight slack. | **Fixed** — EVALS §4.2 adds a numbered "collision hygiene" block asserted by `generate.py`, which raises rather than writing a tainted fixture: (1) ops never stack (cells drawn without replacement); (2) `duplicate_row` is allocated first and both the source row and its copy are frozen out of the pool; (3) the only exact duplicates post-corruption are the injected ones; (4) no CAT edit collides with an existing legitimate label; (5) **CAT ops touch ≤ 25% of any label's occurrences** so the golden spelling stays the cluster canonical (a case the reviewer did not raise but which would have cost M3 directly); (6) MISS ops never exceed the golden-null pool; (7) every op's `before` equals the golden cell. |
| 21 | evals | minor | The "reproducible binary" claim for `inventory.xlsx` is false with default openpyxl: `docProps/core.xml` timestamps and zip entry mtimes come from the wall clock, so regeneration is not diffable. | **Fixed** — EVALS §4.1 specifies the pinning explicitly: `generate.py` sets `wb.properties.creator` and `.created`/`.modified` to `2020-01-01T00:00:00`, then rewrites the saved archive with fixed entry timestamps `(1980,1,1,0,0,0)` and a fixed entry order, "only then is `--seed 1337` byte-reproducible, which is what makes a fixture regeneration reviewable as a diff". |

## Sub-recommendations rejected (the findings themselves were all fixed)

| From | Rejected sub-recommendation | Reason |
|---|---|---|
| #3 | Keep D7 strict and apply convention ops column-wide instead | A column-wide comma-decimal file is *correct data in another locale*, not a defect; the fixture would have nothing for TYPE to detect, deleting the eval signal rather than repairing it. Fixing D7's semantics (decisive cells are per-cell provable) is the real bug fix. |
| #4 | Add an ambiguous-date column op to the corrupter | An ambiguous column has no uniquely recoverable golden reading, so the op would be information-destroying — reintroducing exactly the truth contradiction of blocker #1. Ambiguity ground truth belongs in hand-vetted traps, gated by M7. |
| #5 / #18 | Drop the 10-sample cap on `IssueSummary`, or just declare that metrics read the in-memory `CleanResult` | The first makes the DB O(cells) for no query benefit; the second leaves report-tier persistence untested and leaves the user with no machine-readable outlier record. `findings.jsonl` solves the metric problem and a real product gap at once. |

## Net effect on the docs

- **New:** `findings.jsonl` artifact (FR-10, DATA_MODEL §3.2); metric **M7
  trap_disposition** and gate **M3_clean_findings**; `repair_auto_min`;
  `ColumnProfile.null_count`; `convention_agree` / `convention_min_decisive`
  policy thresholds; a specified `forbidden.json` schema; a pinned corrupter op
  mix with asserted invariants; rule-id vocabulary in D12.
- **Rewritten:** D7 (number canonicalization, now an eight-step algorithm),
  D9 (proven vs ambiguous columns as structurally different outcomes), M2's
  column-scope accounting, M6 (cross-process), the M4 gate and its arithmetic
  (0.70 → 0.85), the fixture set and its row/cell/op arithmetic.
- **Cut** (to pay for the above without inflating scope): the r≥2 delta-audit
  revision chain, token-sort label clustering, `_extra_N` overflow columns, the
  standalone `report` CLI command, and two API endpoints.
- **No FR was renumbered**; FR-1…FR-16 and entity names are unchanged, and the
  three documents were re-checked for mutual consistency on FR ids, entity
  names, rule ids, metric names, and gate values after every edit.

---

## Implementation notes (SURFACE + EVALS stage)

**No gate threshold was adjusted.** All thirteen gates in EVALS.md §5 are
implemented and asserted at exactly the documented values, and all thirteen
pass. What follows records where the *implementation* had to make a call the
docs left open, and where a measured number differs from a doc's estimate.

### 1. Naive-baseline estimates vs live-measured values

EVALS.md §5 tabulates *expected* baseline scores; `run.py` prints the
live-computed ones. Six differ materially, and each difference is a property of
the fixtures rather than of the baseline implementation, which follows the
specified recipe exactly (decode UTF-8 with `errors="replace"`, `str.strip()`
every cell, drop exact duplicate rows, type by all-or-nothing parse):

| metric | EVALS estimate | measured | why |
|---|---|---|---|
| M1 | ≈ 0.55–0.65 | **0.12** | all-or-nothing parsing is harsher than the estimate assumed: with ≤ 6% cell damage, essentially *every* structured column contains at least one cell that will not parse, so almost everything collapses to `text` |
| M3 | ≈ 0.90–0.97 | **1.00** | the estimate assumed `errors="replace"` would mangle mojibake cells. It cannot: `mojibake_encode` produces *text* (`JosÃ©`) stored as valid UTF-8, which is the realistic on-disk representation, so the naive decode reads it cleanly and simply never repairs it — an M4 miss, not an M3 error |
| M3_trap | > 0 | **0** | the trap files are hand-authored clean of whitespace damage and exact duplicates (their ambiguity is semantic), so a strip-and-dedupe script has nothing to touch there |
| M4 | ≈ 0.28 | **0.17** | `str.strip()` repairs `pad_whitespace` and `insert_nbsp` but not `insert_zero_width` or `double_internal_space`, so it earns roughly half of the WS share, not all of it |
| M5 | 0.0 | **0.28** | measured live rather than assumed: with no audit log a run is reconstructible only if the cleaner changed nothing, which is true of the five golden control runs |
| M7 | 0.0 | **0.33** | three of the nine trap expectations are `expect: "none"`, which a baseline that proposes nothing satisfies vacuously. This is a property of M7's shape, not a capability of the baseline; the gate is unaffected because it sits at 1.0 |

The load-bearing comparisons are unchanged: M1 0.12 → gate 0.95, M2 0.21 →
0.85, M4 0.17 → 0.85, M5 0.28 → 1.0, M7 0.33 → 1.0. M3 and
`M3_clean_findings` remain one-sided *safety* gates that a do-nothing baseline
passes trivially — EVALS.md §5 already says exactly that about
`M3_clean_findings`, and the measured M3 = 1.00 shows it is equally true of M3.
The capability load is carried by M2, M4 and M7, as designed.

### 2. Corrupter constraints tightened beyond EVALS.md §4.2

- **CAT variant budget 25% → 4%.** §4.2's collision-hygiene rule 5 caps CAT ops
  at ≤ 25% of a label's occurrences so the golden spelling stays the cluster
  canonical. That is necessary but not sufficient for the *tier* the same table
  pins: `fix.label_merge_fingerprint`'s confidence **is** the cluster dominance
  (D12), so at 25% dominance is 0.75 and the merge lands in `review`, not the
  pinned `auto`. The generator therefore caps variants at 4% of a label's
  occurrences (dominance ≥ 0.96) and additionally emits **one** variant spelling
  per (column, label) — a second spelling would split the cluster and lower
  dominance again. `assert_corruption_hygiene` enforces the budget.
- **One typo cell per variant.** D10b only proposes a nearest-neighbour merge
  when the minority is rare (ratio ≤ 0.05) *and* the majority has ≥ 20
  occurrences. A `typo_label` op repeated dozens of times would be *correctly*
  ignored by a correct engine and would silently cost CAT recall, so each typo
  is a distinct one-cell variant, and `_make_typo` rejects any candidate that
  lands inside D10b's window around a different label — the fixture can never
  hand the engine an unwinnable choice.
- **Categorical columns must survive FR-5's ceiling.** Every whitespace,
  encoding and label op adds a distinct raw value, and the pipeline re-profiles
  the working table at each of D13's nine stages. A column that drifted past 50
  distinct values at, say, the WS stage would type as `text`, silently demoting
  `fix.collapse_spaces` to review (D12 scores it 0.6 in text columns) and
  leaving un-repaired variants for CAT to re-detect. The vocabularies are sized
  so this cannot happen and `assert_categorical_still_categorical` re-checks it
  on the raw corrupted column.
- **Bounded-uniform numeric columns.** §4.1 asks for "tame distributions so
  goldens are genuinely alarm-free". Log-normal amounts are *not* tame under
  Tukey-3.0 (a wide log range puts its own upper tail outside the fence), so the
  goldens use bounded uniform draws, whose fences provably sit outside their own
  support. `assert_golden_is_tame` re-checks every golden numeric cell against
  both tests. `sensors.jsonl`'s four labeled heat-wave readings (≈ 41 °C) are
  placed inside both fences by construction, as §4.1 requires.

### 3. Measured M2 = 1.00 where EVALS.md §5 expected "not 1.0"

The M2 rationale anticipated a few points lost to boundary ops — "an injected
outlier landing near a fence, a typo the fingerprint rule already absorbs". The
generator eliminates both categories by construction: `inject_outlier` resamples
until the value trips *both* of D11's tests, and typos are guaranteed
unambiguous. That makes the fixture stricter, not weaker — the gate still fails
on any real regression — but the headroom the 0.85 gate was sized for is spent
on future fixture growth rather than on known boundary noise.

### 4. Op total 2,835, not ≈ 3,050

`op_rate` is drawn per file from [0.03, 0.06] exactly as §4.2 specifies; the
seeded draws happen to average 4.3% rather than the 4.6% the estimate assumed.
Every per-class share is within the asserted ±2 pp, and every class carries
≥ 137 injected instances, so M2_min is still not sampling noise.

### 5. `sensors.jsonl` DATE ops

§4.2's `date_reformat` rewrites a cell into an alternate format from D9's list.
`sensors.jsonl` has a `datetime` column (`ts`) and no `date` column, so its DATE
ops use the RFC 3339 space-separated variant (`2024-03-05 14:22:31`), which
canonicalizes back to the golden exactly and keeps the op
information-preserving. Golden timestamps therefore carry no timezone suffix —
with one, the space form would not round-trip and the op would be illegal under
§2's constraint.

### 6. Error catalog extended by one code

FR-14 names five detail codes. Services distinguishes "no such run" from "no
such review item **on this run**", so `unknown_item` (404) was added rather than
overloading `unknown_run`. The five documented codes are implemented unchanged.

### 7. Run rows are written in two phases

DATA_MODEL.md §4 requires that "readers never observe a half-written run".
Services inserts a provisional row with `status = failed` and
`error = "run did not complete"`, then finalizes it once with the real status,
counts and artifact directory. A crash therefore leaves an honest `failed`
record — and per FR-2 a `failed` run never suppresses reprocessing, so the file
is picked up again on the next pass.

### 8. `revert` is verified against the persisted artifacts

FR-9's CLI/API check re-reads `cleaned.*` and `audit.*` **from disk** and
compares the reconstruction with a fresh parse of the source file. The cleaned
artifact is parsed by a dedicated mechanical inverse of the serializer, not by
the reader stack: the reader applies dialect sniffing and the FR-3 headerless
heuristic, which are decisions about an *unknown* file, and using it here would
test the sniffer instead of the audit.

---

## HARDEN stage — falsifiability audit

**No gate threshold was changed at this stage either.** The purpose here was
to demonstrate empirically that the gates *can* fail — a gate that cannot be
made to fail is worthless — and to record the numbers. Three engine mutations
were applied, measured, and reverted; the suite was confirmed fully green
before and after each.

### Mutation experiments (metric, mutation, before → after)

**A — constant type inference (H1).** `infer_column_type` degraded to always
return `text` (the trivial classifier a header-lookup or lazy engine would
amount to):

| metric | healthy | mutated | gate | result |
|---|---|---|---|---|
| M1 type_inference_accuracy | 1.0000 | **0.1209** | ≥ 0.95 | FAIL (falls to exactly the naive baseline) |
| M2 detection macro-F1 | 1.0000 | **0.5000** | ≥ 0.85 | FAIL |
| M2_min per-class F1 | 1.0000 | **0.0000** | ≥ 0.70 | FAIL |
| M4 repair_auto | 0.9459 | **0.3421** | ≥ 0.85 | FAIL |
| M4 repair_auto_min | 1.0000 | **0.0000** | ≥ 0.85 | FAIL |
| M4 repair_total | 1.0000 | **0.5015** | ≥ 0.90 | FAIL |
| M7 trap_disposition | 1.0000 | **0.4444** | = 1.00 | FAIL |

Seven gates discriminate against a dead inference engine; the detectors'
dependence on column types propagates the damage exactly as D5's "one joint
decision" framing predicts.

**B — greedy tier algebra (H2).** `resolve_tier` degraded to return `auto`
for every enabled rule (ignore safety caps and confidence — the "background
cleaner that guesses wrong" D1 exists to prevent):

| metric | healthy | mutated | gate | result |
|---|---|---|---|---|
| M3_trap trap-cell auto changes | 0 | **11** | = 0 | FAIL (`fix.date_canon_ambiguous` and `fix.sentinel_null_soft` auto-applied on trap cells) |
| M7 trap_disposition | 1.0000 | **0.5556** | = 1.00 | FAIL (expected review items never created) |

Notably M3 itself stays 1.0000 under this mutation — on the *corrupted*
fixtures the recommended candidates equal golden, so precision alone cannot
see recklessness. That is precisely the one-sidedness EVALS.md §5's
anti-gaming summary claims, now demonstrated rather than asserted: the trap
gates, not M3, carry the safety load. Under this mutation the eval harness
also fails loudly earlier (`revision_two_reversibility` finds an empty review
queue) — the scorecard numbers above were probed with the revision-2
precondition bypassed, which only *removes* a failure.

**C — single-class timidity (H2, the M4 arithmetic).** `fix.sentinel_null_hard`
confidence dropped 1.0 → 0.7, demoting the MISS class to review:

| metric | healthy | mutated | gate | result |
|---|---|---|---|---|
| M4 repair_auto | 0.9459 | **0.8395** | ≥ 0.85 | FAIL — matches EVALS.md §5's predicted ≈ 0.84 for an ENC/MISS/CAT-sized demotion |
| M4 repair_auto_min | 1.0000 | **0.0000** | ≥ 0.85 | FAIL (the MISS slice goes to zero) |
| M4 repair_total | 1.0000 | 1.0000 | ≥ 0.90 | pass, correctly — the review proposal still restores golden |

M2 also moved (1.0000 → 0.9958) because unfixed sentinels then pollute later
pipeline stages — evidence that the pipeline re-profiling (D13) is real, not
staged.

After reverting each mutation the full scorecard returned to the healthy
column exactly.

### Other HARDEN findings

- **Fixture-share test hardened.** `test_fixture_class_shares_are_pinned`
  previously read the generator-recorded `expected.json`; it now recomputes
  every class share live from the committed corruption manifests (2,835 ops)
  and additionally asserts `expected.json` agrees with the manifests to 1e-6,
  so a stale informational file can no longer mask fixture drift. This is the
  only code change the audit required; no fake work, hardcoded results,
  swallowed failures, or assertion-free tests were found elsewhere.
- **Determinism re-verified end to end:** two consecutive full eval passes
  produced byte-identical scorecard JSON, on top of M6's cross-process
  `PYTHONHASHSEED` 0-vs-1 artifact comparison.
- **CLI walked end to end on real inputs** (a hand-made mojibake/currency/
  ambiguous-date/duplicate-row file plus the committed fixtures): `watch
  add/ls`, `run --once` twice (idempotent skip), `clean`, `profile`, `runs`,
  `show --changes/--columns/--paths`, `review`, `accept --all`, `reject`
  (correct `item_already_decided` failure), `revert` on r1 and r2, policy
  `off` override with loud unknown-key rejection, and `serve` (live HTTP:
  health, filtered runs, report download, structured 404). Source hashes
  unchanged throughout.

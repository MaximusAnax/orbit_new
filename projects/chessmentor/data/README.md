# Committed datasets

Read-only at runtime, validated at `chessmentor init` (DATA_MODEL.md).

| File | Contents | Source of truth for |
|---|---|---|
| `levels.json` | the 10-rung difficulty ladder | FR-4 throttle configs, FR-5 calibrated fields, FR-7b ACPL anchors, FR-8 level selection |
| `openings.json` | 113 ECO-tagged lines, 4–12 plies | FR-4 step 1 book moves, ECO naming, FR-9 `book_depth` |
| `advice.json` | curated coaching catalog | FR-12 suggestion text (snapshotted into reports) |

## Status of `levels.json`'s calibrated fields

`max_depth`, `node_budget`, `noise_sigma_cp`, `blunder_prob`,
`blunder_margin_*` and `book_plies` are **design data** — the knobs FR-4 turns.

`elo_internal`, `acpl_mean` and `acpl_std` are **calibrated fields** and the
values committed here are the nominal pre-calibration ladder of DATA_MODEL.md
("depth 1→5, nodes 250→16 000, σ 200→0 cp, `blunder_prob` 0.30→0, margins
narrowing 80–900 → 100–300 cp, book 2→12 plies, nominal Elo 400→1750") laid out
on exact 150-Elo spacing. They satisfy every FR-5/M1b structural invariant
(strictly increasing `elo_internal`, adjacent gaps of 150 Elo inside [100, 170],
strictly decreasing `acpl_mean`) but they are **not yet the output of the FR-5
calibration run**: `evals/fixtures/generate_calibration.py` is the committed,
seeded script that produces them, it takes ≈ 3–6 h on 4 cores, and it runs
offline before a release — never in CI. Until that run lands,
`calibration_seed` / `calibrated_at` record the intended provenance and
`evals/fixtures/calibration.json` (which M1b gates, including the
`levels_sha256` integrity check) does not exist yet.

## Deliberate departure: `node_budget`

The nominal table says nodes 250 → 16 000. The committed budgets are
1 000 → 16 000 (1 000 / 1 400 / 2 000 / 2 800 / 3 800 / 5 000 / 6 500 / 8 500 /
11 500 / 16 000).

Reason, measured rather than guessed: a *depth-1* iteration of this engine costs
a median of ≈ 900 nodes over a 120-position sample, because every leaf enters
quiescence over captures and promotions. FR-4 step 2 discards partial
iterations, so at 250 nodes L1 would almost always fall back to the depth-0
static score vector and `max_depth` would stop meaning anything at the bottom of
the ladder. The retuned budgets let depth 1 complete in ordinary positions while
keeping the top level at the 16 000 nodes D19 sizes for ≈ 1–4 s/move.

Retuning level *configs* (data) is exactly what FR-5 prescribes when the ladder
does not land where it should — the gates are never retuned. Calibration
re-derives `elo_internal` from whatever configs are committed, so this change is
absorbed by the calibration run, not by any threshold.

Two consequences for the eval stage:

* Because quiescence can be expensive in sharp positions, the depth-0 static
  fallback is still reachable at the bottom of the ladder. It is deterministic
  and recorded (`cpu_meta.depth == 0` with `root_moves > 1`), not an error.
* The mean committed budget is ≈ 5 850 nodes/move. At the ≈ 8 700 nodes/s this
  engine measures, M1a's 72 adjudicated games are the suite's dominant cost;
  EVALS.md's 6–12 min estimate for M1a assumed cheaper budgets, so either the
  M1a game count or these budgets may need a further pass to hold the ≤ 35 min
  whole-suite target. That is a fixture/data decision, not a code one.

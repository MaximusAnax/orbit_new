# Committed datasets

Read-only at runtime, validated at `chessmentor init` (DATA_MODEL.md).

| File | Contents | Source of truth for |
|---|---|---|
| `levels.json` | the 10-rung difficulty ladder | FR-4 throttle configs, FR-5 calibrated fields, FR-7b ACPL anchors, FR-8 level selection |
| `openings.json` | 113 ECO-tagged lines, 4–12 plies | FR-4 step 1 book moves, ECO naming, FR-9 `book_depth` |
| `advice.json` | curated coaching catalog | FR-12 suggestion text (snapshotted into reports) |

## `levels.json`: knobs vs calibrated fields

`max_depth`, `node_budget`, `noise_sigma_cp`, `blunder_prob`,
`blunder_margin_*` and `book_plies` are **design data** — the knobs FR-4 turns.

`elo_internal`, `acpl_mean` and `acpl_std` are **calibrated output**: they are
written by `evals/fixtures/generate_calibration.py` (FR-5), which plays the
committed match schedule at each level's exact runtime config, fits the
internal Elo scale (anchored `L1 ≡ 400`), and measures each level's ACPL
against the internal analyst at `JUDGE_BUDGET` with book plies excluded.  The
full record, including per-pair scores, both fits, per-gap standard errors and
the `levels_sha256` integrity hash that M1b verifies, is committed at
`evals/fixtures/calibration.json`. `docs/REVIEW.md` (B3) records the sample
size the committed record was produced at and the exact regeneration command.

## Shape of the committed ladder (docs/REVIEW.md B1 + B5)

The nominal pre-calibration sketch in DATA_MODEL.md (depth 1→5, nodes
250→16 000, σ→0, p→0) turned out to span ~2 200 internal Elo — the FR-5 window
of [100, 170] Elo per adjacent gap plus the `L1 ≡ 400` anchor bounds a 10-rung
ladder to a 900–1 530-Elo span, so the committed ladder covers the *lower*
portion of that knob space and differs from the sketch in two deliberate ways:

* **Every rung is throttled** (`noise_sigma_cp` 205 → 35 cp, `blunder_prob`
  0.31 → 0.045). A clean σ = 0 / p = 0 top rung would sit far more than 170 Elo
  above its neighbour, and US-3's human-plausible-error machinery (gated by M9)
  is meant to be operative wherever the user plays.
* **Budgets run 240 → 4 200 nodes** with `max_depth = 5` everywhere. At the
  bottom, quiescence can exhaust the budget before depth 1 completes; FR-4 then
  falls back to the depth-0 static score vector. That is deterministic and
  recorded (`cpu_meta.depth == 0` with `root_moves > 1`), not an error — it is
  part of what makes L1 beatable.

Retuning level *configs* (data) is exactly what FR-5 prescribes when the ladder
does not land where it should — the gates are never retuned. Calibration
re-derives `elo_internal` from whatever configs are committed, so knob changes
are absorbed by the calibration run, not by any threshold. A pleasant side
effect of the final budgets: the whole ladder is ~4× cheaper per move than the
nominal sketch, which keeps the game-playing eval metrics (M1a, M9, M10) well
inside EVALS.md's whole-suite time budget.

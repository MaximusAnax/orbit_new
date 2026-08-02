"""Pure domain logic.

Deterministic: no network, no filesystem, no clock reads.  Time and randomness
are explicit inputs (``now: str``, ``seed: int``, ``node_budget: int``).

| Module | FR | Responsibility |
|---|---|---|
| ``search`` | FR-2 | negamax alpha-beta, iterative deepening, quiescence, per-call TT, ordering, node budgets |
| ``evaluate`` | FR-3 | tapered material + PeSTO PSTs, passed pawns, tempo |
| ``throttle`` | FR-4 | level configs, single-pass full-window root, seeded noise + blunder injection |
| ``rating`` | FR-7 | Glicko-1 + surprise inflation, ACPL→perf interpolation, precision-weighted blend |
| ``adapt`` | FR-8 | cold start, ideal-opponent selection, hysteresis, placement, clamping |
| ``judge`` | FR-9 | cp loss, win-probability model, severity, book exclusion, ACPL, accuracy |
| ``phase`` | FR-10 | boundary rules (thresholds as data) |
| ``taxonomy`` | FR-11 | SEE, motif detectors, precedence classifier, evidence |
| ``coach`` | FR-12 | window/analysis selection, priority formula, advice snapshot |
| ``session`` | FR-6 | pure game-flow orchestration |
| ``pgn`` | FR-13 | PGN parsing, side resolution, result mapping |
"""

from __future__ import annotations

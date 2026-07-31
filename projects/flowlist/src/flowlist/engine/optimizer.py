"""Max-weight Hamiltonian path over playlist entries (FR-8, FR-9, D6).

Ordering a playlist to maximise total transition quality is open-loop TSP with
an *asymmetric* weight matrix: ``S(a->b) != S(b->a)`` because tempo folding,
the energy arc and the Camelot table are all directional.  That rules out the
usual symmetric shortcuts -- in particular a 2-opt reversal changes the
direction of every edge inside the reversed segment, so its delta must be
recomputed rather than read off two boundary edges.

The solver is the one D6 specifies:

1. **Multi-start greedy construction** -- ``k = min(n, 12)`` best-next walks.
   Unanchored, the starts are the ``ceil(k/2)`` nodes with the lowest
   best-incoming score (natural path endpoints) plus seeded random picks.
   Anchored, every walk roots at the start anchor and diversification comes
   from seeded near-tie choice instead.
2. **First-improvement local search** -- alternating Or-opt (relocate a
   segment of length 1-3, direction preserved) and 2-opt (reverse a segment,
   internal edges recomputed) sweeps until a full pass finds nothing or
   ``max_passes`` is reached.

Determinism (FR-15) comes from three places: a seeded :class:`random.Random`,
a fixed scan order with lowest-index tie-breaks, and rounding every score and
delta to 12 decimals before any comparison (D6).

:func:`exact_optimal` is the Held-Karp ground truth the evals compare against;
it is guarded to ``n <= 14`` and supports fixed endpoints so anchored
instances are scored under the same constraints the heuristic receives (FR-9).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

from flowlist.engine.models import (
    MAX_EXACT_SIZE,
    MAX_PLAYLIST_SIZE,
    SCORE_PRECISION,
    Algorithm,
    ReorderResult,
    round_score,
)
from flowlist.errors import InstanceTooLargeError, InvalidAnchorError, PlaylistTooLargeError

#: Number of greedy constructions attempted (D6).
MAX_STARTS = 12
#: Near-tie window used to diversify anchored constructions (D6).
TIE_WINDOW = 0.05
#: Longest segment Or-opt relocates (D6).
MAX_SEGMENT = 3


class PathSolution(NamedTuple):
    """Exact solver result; unpacks as ``order, total``."""

    order: list[int]
    total: float


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_array(matrix: Sequence[Sequence[float]]) -> np.ndarray:
    if len(matrix) == 0:
        return np.zeros((0, 0), dtype=np.float64)
    array = np.asarray(matrix, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] != array.shape[1]:
        raise ValueError("score matrix must be square")
    return array


def _validate_anchors(n: int, start: int | None, end: int | None) -> None:
    for name, value in (("start", start), ("end", end)):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidAnchorError(f"{name} anchor must be an integer index", anchor=value)
        if not 0 <= value < n:
            raise InvalidAnchorError(
                f"{name} anchor {value} is outside 0..{n - 1}", anchor=value, size=n
            )
    if start is not None and end is not None and start == end and n > 1:
        raise InvalidAnchorError(
            "start and end anchors must differ when the playlist has more than one entry",
            anchor=start,
        )


def total_of(order: Sequence[int], matrix: np.ndarray) -> float:
    """Sum of consecutive edges, rounded to the D6 precision.

    Accumulated sequentially in Python rather than with ``ndarray.sum`` (which
    sums pairwise) so the number agrees bit-for-bit with
    :func:`flowlist.engine.scoring.order_total`.
    """
    if len(order) < 2:
        return 0.0
    idx = np.asarray(order, dtype=np.intp)
    total = 0.0
    for value in matrix[idx[:-1], idx[1:]].tolist():
        total += value
    return round_score(total)


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def _start_candidates(matrix: np.ndarray, k: int, rng: random.Random) -> list[int]:
    """Unanchored start set (D6): weak-incoming nodes plus seeded random picks.

    A node no other track leads into well is a natural *beginning* of a path,
    so seeding greedy there tends to avoid stranding it at a seam later.
    """
    n = matrix.shape[0]
    if k >= n:
        return list(range(n))
    incoming = matrix.copy()
    np.fill_diagonal(incoming, -np.inf)
    best_incoming = np.round(incoming.max(axis=0), SCORE_PRECISION)
    ranked = sorted(range(n), key=lambda i: (float(best_incoming[i]), i))
    endpoints = (k + 1) // 2
    chosen = ranked[:endpoints]
    remaining = sorted(set(range(n)) - set(chosen))
    extra = k - len(chosen)
    if extra > 0 and remaining:
        chosen.extend(rng.sample(remaining, min(extra, len(remaining))))
    return chosen


def _greedy(
    matrix: np.ndarray,
    start: int,
    *,
    allowed: np.ndarray,
    diversify: bool,
    rng: random.Random,
) -> list[int]:
    """Best-next walk from ``start`` over the nodes flagged in ``allowed``.

    With ``diversify`` the next hop is drawn uniformly (seeded) from the
    candidates within :data:`TIE_WINDOW` of the best score, which is how the
    anchored case gets k distinct constructions when every walk must root at
    the same node (D6).
    """
    unvisited = allowed.copy()
    unvisited[start] = False
    order = [start]
    current = start
    while True:
        candidates = np.flatnonzero(unvisited)
        if candidates.size == 0:
            break
        scores = np.round(matrix[current, candidates], SCORE_PRECISION)
        best = float(scores.max())
        if diversify:
            near = candidates[scores >= best - TIE_WINDOW]
            nxt = int(near[rng.randrange(near.size)])
        else:
            # argmax returns the first maximum, i.e. the lowest index: the D6
            # tie-break, made stable by the rounding above.
            nxt = int(candidates[int(scores.argmax())])
        unvisited[nxt] = False
        order.append(nxt)
        current = nxt
    return order


def _constructions(
    matrix: np.ndarray,
    seed: int,
    start: int | None,
    end: int | None,
) -> list[list[int]]:
    """The k greedy orders that seed local search (D6)."""
    n = matrix.shape[0]
    rng = random.Random(seed)
    k = min(n, MAX_STARTS)

    free = np.ones(n, dtype=bool)
    if end is not None:
        free[end] = False

    orders: list[list[int]] = []
    if start is not None:
        # All constructions root at the anchor; construction 0 is pure
        # best-next and 1..k-1 diversify on near-ties (D6).
        for index in range(k):
            order = _greedy(matrix, start, allowed=free, diversify=index > 0, rng=rng)
            if end is not None:
                order.append(end)
            orders.append(order)
        return orders

    begins = [b for b in _start_candidates(matrix, k, rng) if b != end]
    if not begins:  # only reachable when every node is the end anchor, i.e. n == 1
        begins = [int(np.flatnonzero(free)[0])] if free.any() else []
    for begin in begins:
        order = _greedy(matrix, begin, allowed=free, diversify=False, rng=rng)
        if end is not None:
            order.append(end)
        orders.append(order)
    return orders


# --------------------------------------------------------------------------- #
# Local search
# --------------------------------------------------------------------------- #


def _or_opt_pass(order: np.ndarray, matrix: np.ndarray, lo: int, hi: int) -> int:
    """One first-improvement Or-opt sweep; returns the number of moves applied.

    Segments of length 1-3 are relocated with their direction preserved, which
    is what keeps the move valid under an asymmetric score.  ``[lo, hi)`` is
    the movable window: anchored endpoints sit outside it and can neither be
    picked up nor displaced (FR-9).
    """
    n = order.shape[0]
    moves = 0
    i = lo
    while i < hi:
        for length in range(1, MAX_SEGMENT + 1):
            if i + length > hi:
                break
            segment = order[i : i + length].copy()
            head, tail = int(segment[0]), int(segment[-1])
            prev = int(order[i - 1]) if i > 0 else -1
            nxt = int(order[i + length]) if i + length < n else -1

            removal = 0.0
            if prev >= 0:
                removal -= matrix[prev, head]
            if nxt >= 0:
                removal -= matrix[tail, nxt]
            if prev >= 0 and nxt >= 0:
                removal += matrix[prev, nxt]

            rest = np.concatenate((order[:i], order[i + length :]))
            gaps = np.arange(lo, hi - length + 1)
            if gaps.size == 0 or rest.size == 0:
                continue  # nothing left to insert against
            has_left = gaps > 0
            has_right = gaps < rest.shape[0]
            left = rest[np.where(has_left, gaps - 1, 0)]
            right = rest[np.where(has_right, np.minimum(gaps, rest.shape[0] - 1), 0)]

            delta = np.full(gaps.shape, removal, dtype=np.float64)
            delta += np.where(has_left, matrix[left, head], 0.0)
            delta += np.where(has_right, matrix[tail, right], 0.0)
            delta -= np.where(has_left & has_right, matrix[left, right], 0.0)
            delta[gaps == i] = 0.0  # re-inserting where it came from

            improving = np.flatnonzero(np.round(delta, SCORE_PRECISION) > 0.0)
            if improving.size == 0:
                continue
            gap = int(gaps[int(improving[0])])
            order[:] = np.concatenate((rest[:gap], segment, rest[gap:]))
            moves += 1
            break
        i += 1
    return moves


def _two_opt_pass(order: np.ndarray, matrix: np.ndarray, lo: int, hi: int) -> int:
    """One first-improvement 2-opt sweep; returns the number of moves applied.

    Reversing ``order[i..j]`` flips every internal edge, so the delta is

        (sum of backward internal edges - sum of forward internal edges)
        + new boundary edges - old boundary edges

    Running cumulative sums over ``j`` make the whole sweep O(n^2) despite the
    recomputation (Croes 1958, adapted to an asymmetric path objective).
    """
    n = order.shape[0]
    moves = 0
    i = lo
    while i < hi - 1:
        head = int(order[i])
        forward = matrix[order[i : hi - 1], order[i + 1 : hi]]
        backward = matrix[order[i + 1 : hi], order[i : hi - 1]]
        delta = np.cumsum(backward) - np.cumsum(forward)
        js = np.arange(i + 1, hi)
        if i > 0:
            prev = int(order[i - 1])
            delta = delta + matrix[prev, order[js]] - matrix[prev, head]
        tail_mask = js < n - 1
        if tail_mask.any():
            after = order[js[tail_mask] + 1]
            adjust = np.zeros(js.shape, dtype=np.float64)
            adjust[tail_mask] = matrix[head, after] - matrix[order[js[tail_mask]], after]
            delta = delta + adjust

        improving = np.flatnonzero(np.round(delta, SCORE_PRECISION) > 0.0)
        if improving.size:
            j = i + 1 + int(improving[0])
            order[i : j + 1] = order[i : j + 1][::-1]
            moves += 1
        i += 1
    return moves


def _local_search(
    order: list[int], matrix: np.ndarray, lo: int, hi: int, max_passes: int
) -> tuple[list[int], int, int]:
    """Alternate Or-opt and 2-opt sweeps to a local optimum (D6).

    A *pass* is one full sweep of each neighbourhood; the loop stops when a
    whole pass applies no move (a local optimum with respect to both
    neighbourhoods) or ``max_passes`` is spent.
    """
    working = np.asarray(order, dtype=np.intp)
    passes = 0
    moves = 0
    while passes < max_passes:
        passes += 1
        applied = _or_opt_pass(working, matrix, lo, hi)
        applied += _two_opt_pass(working, matrix, lo, hi)
        moves += applied
        if applied == 0:
            break
    return [int(x) for x in working], passes, moves


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def reorder(
    matrix: Sequence[Sequence[float]],
    seed: int = 0,
    start: int | None = None,
    end: int | None = None,
    max_passes: int = 50,
) -> ReorderResult:
    """Find a high-quality Hamiltonian path over the score matrix (FR-8).

    ``start``/``end`` are optional anchors (FR-9): the returned order begins
    and/or ends with them and no local-search move may displace them.
    """
    array = _as_array(matrix)
    n = array.shape[0]
    if n > MAX_PLAYLIST_SIZE:
        raise PlaylistTooLargeError(
            f"playlist has {n} entries; the optimizer caps at {MAX_PLAYLIST_SIZE}",
            entries=n,
            cap=MAX_PLAYLIST_SIZE,
        )
    if max_passes < 1:
        raise ValueError("max_passes must be >= 1")
    _validate_anchors(n, start, end)

    if n == 0:
        return ReorderResult(
            order=[], total=0.0, construction_total=0.0, passes=0, moves=0, starts=0, seed=seed
        )
    if n == 1:
        return ReorderResult(
            order=[0], total=0.0, construction_total=0.0, passes=0, moves=0, starts=1, seed=seed
        )

    orders = _constructions(array, seed, start, end)
    lo = 1 if start is not None else 0
    hi = n - 1 if end is not None else n

    evaluated: list[tuple[float, float, list[int]]] = []
    total_passes = 0
    total_moves = 0

    for candidate in orders:
        construction_total = total_of(candidate, array)
        improved, passes, moves = _local_search(candidate, array, lo, hi, max_passes)
        total_passes += passes
        total_moves += moves
        evaluated.append((total_of(improved, array), construction_total, improved))

    # ``max`` keeps the first maximal element, so ties fall to the earliest
    # construction — the D6 index tie-break, applied at the multi-start level.
    best_total, best_construction, best_order = max(evaluated, key=lambda item: item[0])

    return ReorderResult(
        order=best_order,
        total=round_score(best_total),
        construction_total=round_score(best_construction),
        passes=total_passes,
        moves=total_moves,
        starts=len(orders),
        seed=seed,
        algorithm=Algorithm.GREEDY_2OPT,
    )


def exact_optimal(
    matrix: Sequence[Sequence[float]],
    start: int | None = None,
    end: int | None = None,
) -> PathSolution:
    """Maximum-weight Hamiltonian path by Held-Karp DP (evals' ground truth).

    ``dp[mask][j]`` is the best total over a path that visits exactly ``mask``
    and ends at ``j``; the answer is the best full-mask state (restricted to
    ``end`` when an end anchor is given).  O(n^2 * 2^n), hence the ``n <= 14``
    guard.  Ties break to the lowest index so the returned order is
    deterministic.
    """
    array = _as_array(matrix)
    n = array.shape[0]
    if n > MAX_EXACT_SIZE:
        raise InstanceTooLargeError(
            f"exact_optimal is guarded to n <= {MAX_EXACT_SIZE}; got {n}",
            entries=n,
            cap=MAX_EXACT_SIZE,
        )
    _validate_anchors(n, start, end)
    if n == 0:
        return PathSolution([], 0.0)
    if n == 1:
        return PathSolution([0], 0.0)

    weights = [[float(array[i, j]) for j in range(n)] for i in range(n)]
    size = 1 << n
    neg = -float("inf")
    dp = [[neg] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]

    starts = [start] if start is not None else list(range(n))
    for s in starts:
        if end is not None and s == end:
            continue
        dp[1 << s][s] = 0.0

    full = size - 1
    for mask in range(size):
        row = dp[mask]
        for j in range(n):
            current = row[j]
            if current == neg:
                continue
            base = weights[j]
            for nxt in range(n):
                bit = 1 << nxt
                if mask & bit:
                    continue
                # The end anchor may only be appended last.
                if end is not None and nxt == end and (mask | bit) != full:
                    continue
                value = current + base[nxt]
                target = mask | bit
                if value > dp[target][nxt] + 1e-15:
                    dp[target][nxt] = value
                    parent[target][nxt] = j

    finishers = [end] if end is not None else list(range(n))
    best_total = neg
    best_last = -1
    for j in finishers:
        if dp[full][j] > best_total + 1e-15:
            best_total = dp[full][j]
            best_last = j
    if best_last < 0:
        raise InvalidAnchorError("no Hamiltonian path satisfies the given anchors")

    order: list[int] = []
    mask, node = full, best_last
    while node >= 0:
        order.append(node)
        previous = parent[mask][node]
        mask ^= 1 << node
        node = previous
    order.reverse()
    return PathSolution(order, round_score(best_total))


__all__ = [
    "MAX_SEGMENT",
    "MAX_STARTS",
    "TIE_WINDOW",
    "PathSolution",
    "exact_optimal",
    "reorder",
    "total_of",
]

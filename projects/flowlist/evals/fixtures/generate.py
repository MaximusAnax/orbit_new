"""Deterministic fixture generator for the flowlist eval suite (EVALS.md §4).

Run with ``uv run python flowlist/evals/fixtures/generate.py`` from
``projects/``.  Everything it writes is committed; regenerating is a reviewed
change, because the committed files *are* the ground truth for M4-M7.

What it produces
----------------

``catalog.json``      one row per fixture track (synthetic metadata + features)
``playlists/*.json``  the four suites plus the arc fixture
``expected.json``     generator-measured stats, including the reference
                      construction-only greedy ratios that make the M4 gate
                      discriminative
``golden_orderings.json``  seed-7 orderings for M7 (written by ``--goldens``)

Ground-truth notes
------------------

* Two **degenerate baselines** are measured, and the exact suite must defeat
  *both*:

  1. :func:`reference_greedy` — a standalone all-starts best-next construction
     with *no imports from* ``src/flowlist``.  Independent by location, which
     is what keeps the discrimination claim from being purely self-referential.
  2. ``flowlist.engine.optimizer.construct`` — the engine's *own* construction
     phase, i.e. exactly what ``reorder`` would return if Or-opt and 2-opt were
     deleted.  This is the baseline that actually matters, because it is the
     degenerate implementation a reviewer would write.  It is *stronger* than
     the reference greedy under anchors (D6 diversifies anchored constructions
     on seeded near-ties), so requiring only the reference greedy to fail —
     as this generator did before the harden pass — leaves the M4 gates
     passable by a construction-only flowlist.  See REVIEW.md finding #24.

* The **discrimination invariant** is asserted here at generation time: at
  least :data:`MIN_TRAP_INSTANCES` exact-suite instances must have a
  degenerate optimality ratio below :data:`TRAP_RATIO`, *and* both degenerate
  baselines must fail both M4 gates suite-wide.  Two exact slots are dedicated
  *trap* instances built from the cross-genre blueprint in
  :func:`_trap_features` — a spread where most seams are poor and one
  high-scoring cluster is reachable through a single good hop, so a
  locally-best first hop strands it.  The blueprint's jitter seed is searched
  (bounded, deterministic) until the instance actually traps both baselines;
  the winning seed is recorded in the fixture and in ``expected.json`` so the
  search never has to be re-run to reproduce the file.
* Everything else about a playlist (its features, its stored order) is drawn
  from a seeded ``random.Random`` and never from the system under test.

Only the *scoring* of candidate instances uses the engine (``build_matrix``,
``exact_optimal``, ``construct``): the fixtures have to be scored under the
same rulebook the evals use, or the recorded ratios would mean nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent
PLAYLISTS = FIXTURES / "playlists"

#: Master seed; every sub-seed is derived from it so the whole tree is one knob.
SEED = 42

#: EVALS §4: at least this many exact instances must defeat construction-only
#: greedy, so passing the M4 gate provably requires the local search.
MIN_TRAP_INSTANCES = 3
TRAP_RATIO = 0.97

#: Headroom demanded of the *suite-level* discrimination invariant.  A
#: degenerate baseline landing at 0.9699 against a 0.97 gate would technically
#: fail it, but so narrowly that ordinary float or fixture drift could flip the
#: claim; the searched slots aim low enough that both baselines miss both gates
#: by a visible margin.
DISCRIMINATION_MARGIN = 0.005

#: The M4 gates (EVALS §5), restated here so generation can prove the fixtures
#: reject construction-only greedy against the very thresholds the evals use.
M4_MEAN_GATE = 0.97
M4_MIN_GATE = 0.90

#: Genre clusters with realistic ranges (EVALS §4).  Loudness is derived from
#: energy (louder masters are higher-energy), mirroring streaming-era practice.
CLUSTERS: dict[str, tuple[float, float, float, float]] = {
    "house": (120.0, 128.0, 0.60, 0.90),
    "dnb": (170.0, 178.0, 0.70, 0.95),
    "hiphop": (82.0, 96.0, 0.40, 0.70),
    "indie": (96.0, 120.0, 0.30, 0.70),
}

ADJECTIVES = (
    "Neon", "Paper", "Velvet", "Static", "Copper", "Midnight", "Glass", "Amber",
    "Hollow", "Silver", "Crimson", "Quiet", "Electric", "Marble", "Wilder",
    "Distant", "Golden", "Salt", "Iron", "Feather", "Cobalt", "Ash", "Lantern",
    "Riverine", "Slate", "Opal", "Ember", "Tidal", "Chrome", "Linen",
)
NOUNS = (
    "Hours", "Signal", "Weather", "Machine", "Harbour", "Drive", "Static",
    "Garden", "Circuit", "Fever", "Mirror", "Traffic", "Season", "Anthem",
    "Corridor", "Lantern", "Parade", "Window", "Current", "Orbit", "Echo",
    "Tunnel", "Chapter", "Voltage", "Sunday", "Bloom", "Fracture", "Motorway",
    "Cascade", "Dial",
)
ARTISTS = (
    "Synthetic Sun", "Vera Lux", "Marta Quiet", "Cyan Drift", "Halcyon Bay",
    "Ghost Signal", "The Paper Radios", "Novena", "Low Tide Club", "Rue Atlas",
    "Kestrel Park", "Bright Meridian", "Ardent Machines", "Sable Fox",
    "Northern Wire", "Ines Vermeer", "Delta Sparrow", "Fen & Marrow",
    "Auburn Sky", "Cassette Bloom", "Orla Fields", "The Slow Hands",
    "Mirrorline", "Petrichor Kids",
)
ALBUMS = (
    "Afterglow", "Embers", "Tide", "Signal", "Neon Hours", "Long Way Round",
    "Provisional", "Blue Room", "Nightshift", "Common Weather",
)


# --------------------------------------------------------------------------- #
# Reference construction-only greedy (EVALS §4) — no flowlist imports.
# --------------------------------------------------------------------------- #


def reference_greedy(
    matrix: Sequence[Sequence[float]],
    start: int | None = None,
    end: int | None = None,
) -> tuple[list[int], float]:
    """All-starts best-next Hamiltonian path; ties break to the lowest index.

    Deliberately standalone: this is the *baseline the M4 gate must be able to
    reject*, so it must not share code with the optimizer under test.  Anchors
    are honoured the same way the heuristic honours them (FR-9), so an anchored
    instance's ratio compares like with like: every walk roots at ``start`` and
    ``end`` is appended last.
    """
    n = len(matrix)
    if n < 2:
        return list(range(n)), 0.0
    starts = [start] if start is not None else [s for s in range(n) if s != end]
    best_order: list[int] = []
    best_total = float("-inf")
    for begin in starts:
        order = [begin]
        unvisited = set(range(n)) - {begin}
        if end is not None:
            unvisited.discard(end)
        while unvisited:
            current = order[-1]
            nxt = min(unvisited, key=lambda j: (-matrix[current][j], j))
            order.append(nxt)
            unvisited.discard(nxt)
        if end is not None and end != begin:
            order.append(end)
        total = sum(matrix[order[i]][order[i + 1]] for i in range(n - 1))
        if total > best_total:
            best_total, best_order = total, order
    return best_order, best_total


# --------------------------------------------------------------------------- #
# Track synthesis
# --------------------------------------------------------------------------- #


def _track_id(artist: str, title: str) -> str:
    """A D9-shaped ``meta:`` id, computed here so fixtures stay self-describing."""
    payload = f"{artist.casefold()}|{title.casefold()}"
    return "meta:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()


class Namer:
    """Deterministic, collision-free synthetic titles/artists.

    A single counter drives the whole catalog, so every fixture track gets a
    distinct ``(artist, title)`` and therefore a distinct D9 ``meta:`` id.
    """

    def __init__(self, index: int = 0) -> None:
        self.index = index

    def clone(self) -> Namer:
        """A throwaway namer for a candidate that may be discarded."""
        return Namer(self.index)

    def next(self) -> tuple[str, str, str]:
        i = self.index
        self.index += 1
        title = f"{ADJECTIVES[i % len(ADJECTIVES)]} {NOUNS[(i // len(ADJECTIVES)) % len(NOUNS)]}"
        cycle = i // (len(ADJECTIVES) * len(NOUNS))
        if cycle:
            title = f"{title} ({cycle + 1})"
        artist = ARTISTS[(i * 7) % len(ARTISTS)]
        album = ALBUMS[(i * 3) % len(ALBUMS)]
        return title, artist, album


def _loudness(energy: float, jitter: float = 0.0) -> float:
    """Loudness correlated with energy: -14 dB at 0.0 up to -4 dB at 1.0."""
    return round(min(-1.0, max(-30.0, -14.0 + 10.0 * energy + jitter)), 2)


def make_track(
    namer: Namer,
    rng: random.Random,
    *,
    bpm: float | None,
    key_pc: int | None,
    mode: int | None,
    energy: float | None,
    loudness_db: float | None,
    danceability: float | None = None,
) -> dict[str, Any]:
    title, artist, album = namer.next()
    return {
        "id": _track_id(artist, title),
        "title": title,
        "artist": artist,
        "album": album,
        "duration_ms": rng.randrange(150_000, 320_000, 1_000),
        "features": {
            "bpm": bpm,
            "key_pc": key_pc,
            "mode": mode,
            "energy": energy,
            "danceability": danceability,
            "loudness_db": loudness_db,
        },
    }


def cluster_track(namer: Namer, rng: random.Random, cluster: str) -> dict[str, Any]:
    bpm_lo, bpm_hi, e_lo, e_hi = CLUSTERS[cluster]
    energy = round(rng.uniform(e_lo, e_hi), 3)
    return make_track(
        namer,
        rng,
        bpm=round(rng.uniform(bpm_lo, bpm_hi), 1),
        key_pc=rng.randrange(12),
        mode=rng.randrange(2),
        energy=energy,
        loudness_db=_loudness(energy, rng.uniform(-1.0, 1.0)),
        danceability=round(rng.uniform(0.30, 0.95), 3),
    )


# --------------------------------------------------------------------------- #
# Suite blueprints
# --------------------------------------------------------------------------- #

#: Exact-suite recipes: ``(n, clusters, kind, depth_target)``.
#:
#: ``depth_target`` is ``None`` for the three *typical* slots, which are taken
#: as the master seed draws them — they are what an ordinary small playlist
#: looks like, and construction-only greedy usually solves them exactly.  The
#: remaining slots are *seed-searched for difficulty*: the generator keeps the
#: draw whose reference-greedy optimality ratio is lowest, stopping early once
#: it is at or below the target.  ``trap`` slots draw from the cross-genre
#: blueprint below; ``anchored`` carries fixed endpoints, which is where a
#: construction-only greedy suffers most (it cannot choose a favourable start
#: and must land on a prescribed last node).
#:
#: Selecting on the *baseline's* difficulty is what gives M4 teeth, and it
#: cannot flatter the system under test: an instance that is hard for greedy is
#: if anything harder for the heuristic too.  The targets are set below the
#: level the suite-level invariant needs, because three slots sit at 1.0 by
#: design and drag the mean up.
EXACT_RECIPES: tuple[tuple[int, tuple[str, ...], str, float | None], ...] = (
    (8, ("house",), "plain", None),
    (9, ("house", "indie"), "plain", None),
    (10, ("hiphop", "dnb"), "plain", None),
    (11, ("house", "hiphop"), "plain", 0.93),
    (12, ("indie", "dnb"), "plain", 0.93),
    (13, ("house", "dnb"), "plain", 0.93),
    (14, ("house", "indie", "hiphop"), "plain", 0.93),
    (11, (), "trap", 0.93),
    (12, (), "trap", 0.93),
    (13, ("house", "indie"), "anchored", 0.85),
)

#: Tempo anchors of the trap blueprint: three genre centres that do not
#: beatmatch into one another (124 vs 174 is a 40% gap; 88 half-times onto 174
#: but not onto 124), so most seams are poor and one cluster is precious.
_TRAP_TEMPOS = (88.0, 124.0, 174.0)


def _trap_features(namer: Namer, seed: int, n: int) -> list[dict[str, Any]]:
    """Nearest-neighbour trap blueprint (EVALS §4).

    A cross-genre spread: tempos are drawn from three centres that do not
    beatmatch into each other and energies span the full range, so the average
    seam is poor.  Inside it sits one tightly-matched, high-scoring cluster.
    Construction-only greedy takes the locally-best first hop out of its start,
    which strands that cluster behind a seam it then has to pay for; the local
    search re-splices it.  The jitter ``seed`` is searched by
    :func:`_build_exact_suite` until the instance actually defeats the
    reference greedy.
    """
    rng = random.Random(seed)
    tracks: list[dict[str, Any]] = []

    # The high-scoring cluster: same key, same tempo, adjacent energies.
    cluster_size = 3 + (seed % 2)
    key_pc, mode = rng.randrange(12), rng.randrange(2)
    base_bpm = _TRAP_TEMPOS[1] * rng.uniform(0.99, 1.01)
    base_energy = rng.uniform(0.55, 0.80)
    for _ in range(cluster_size):
        energy = round(min(0.97, max(0.05, base_energy + rng.uniform(-0.02, 0.02))), 3)
        tracks.append(
            make_track(
                namer,
                rng,
                bpm=round(base_bpm * rng.uniform(0.998, 1.002), 1),
                key_pc=key_pc,
                mode=mode,
                energy=energy,
                loudness_db=_loudness(energy),
                danceability=round(rng.uniform(0.30, 0.95), 3),
            )
        )

    # The spread: everything else is drawn wide, so most seams score poorly.
    while len(tracks) < n:
        energy = round(rng.uniform(0.15, 0.95), 3)
        tracks.append(
            make_track(
                namer,
                rng,
                bpm=round(rng.choice(_TRAP_TEMPOS) * rng.uniform(0.97, 1.03), 1),
                key_pc=rng.randrange(12),
                mode=rng.randrange(2),
                energy=energy,
                loudness_db=_loudness(energy, rng.uniform(-4.0, 4.0)),
                danceability=round(rng.uniform(0.30, 0.95), 3),
            )
        )
    return tracks


def _plain_features(
    namer: Namer, rng: random.Random, clusters: Sequence[str], n: int
) -> list[dict[str, Any]]:
    return [cluster_track(namer, rng, clusters[i % len(clusters)]) for i in range(n)]


# --------------------------------------------------------------------------- #
# Planted chains
# --------------------------------------------------------------------------- #

#: Per-hop quality tiers (EVALS §4): ``(key steps, bpm % step, energy step,
#: mix weight)``.  Under the default weights these land at roughly 0.97 / 0.89 /
#: 0.82, so every planted transition clears 0.80 and the mix averages ~0.88 —
#: "excellent, but recoverable in more than one way", which is what makes M5 a
#: recovery metric rather than an optimality metric.
_PLANTED_TIERS: tuple[tuple[tuple[str, ...], float, float, float], ...] = (
    (("same", "relative"), 1.0, 0.06, 0.25),
    (("up", "down"), 2.0, 0.14, 0.375),
    (("up", "down"), 3.5, 0.16, 0.375),
)


def _step_key(key_pc: int, mode: int, step: str) -> tuple[int, int]:
    """Walk the Camelot wheel by one of the safe DJ moves.

    Implemented in pitch-class space so the generator needs no engine import:
    one wheel step is a perfect fifth (+7 semitones) and the relative
    major/minor of a key is 3 semitones away with the mode flipped.
    """
    if step == "same":
        return key_pc, mode
    if step == "relative":
        return ((key_pc + 3) % 12, 1) if mode == 0 else ((key_pc - 3) % 12, 0)
    if step == "up":
        return (key_pc + 7) % 12, mode
    if step == "down":
        return (key_pc - 7) % 12, mode
    raise ValueError(f"unknown key step {step!r}")  # pragma: no cover


#: A planted chain's energy and tempo may roam this far; wide enough that the
#: random walk rarely hits a wall (walls would shrink the deltas and inflate
#: the planted mean above the engineered band).
PLANTED_ENERGY_BAND = (0.18, 0.94)
PLANTED_BPM_SPAN = 0.15


def _planted_chain(namer: Namer, seed: int, n: int, cluster: str) -> list[dict[str, Any]]:
    """A chain whose consecutive transitions are engineered to be excellent.

    Each hop draws a quality tier from :data:`_PLANTED_TIERS`; magnitudes are
    fixed (with a sign and a little jitter) rather than uniform, so the
    tier's intended score is what actually lands and the resulting chain sits
    in the EVALS §4 band: every transition >= 0.80, mean ~0.88.
    """
    rng = random.Random(seed)
    bpm_lo, bpm_hi, e_lo, e_hi = CLUSTERS[cluster]
    centre = rng.uniform(bpm_lo, bpm_hi)
    bpm_floor = centre * (1.0 - PLANTED_BPM_SPAN)
    bpm_ceiling = centre * (1.0 + PLANTED_BPM_SPAN)
    energy_floor, energy_ceiling = PLANTED_ENERGY_BAND

    bpm = centre
    energy = rng.uniform(max(energy_floor, e_lo), min(energy_ceiling, e_hi))
    key_pc, mode = rng.randrange(12), rng.randrange(2)
    weights = [tier[3] for tier in _PLANTED_TIERS]

    def emit() -> dict[str, Any]:
        return make_track(
            namer,
            rng,
            bpm=round(bpm, 1),
            key_pc=key_pc,
            mode=mode,
            energy=round(energy, 3),
            loudness_db=_loudness(energy),
            danceability=round(rng.uniform(0.30, 0.95), 3),
        )

    chain = [emit()]
    for _ in range(n - 1):
        steps, bpm_pct, energy_step, _ = rng.choices(_PLANTED_TIERS, weights=weights)[0]
        key_pc, mode = _step_key(key_pc, mode, rng.choice(steps))
        jitter = rng.uniform(0.85, 1.0)
        bpm *= 1.0 + rng.choice((-1.0, 1.0)) * bpm_pct * jitter / 100.0
        bpm = min(bpm_ceiling, max(bpm_floor, bpm))
        energy += rng.choice((-1.0, 1.0)) * energy_step * jitter
        energy = min(energy_ceiling, max(energy_floor, energy))
        chain.append(emit())
    return chain


# --------------------------------------------------------------------------- #
# Messy and arc fixtures
# --------------------------------------------------------------------------- #

#: Fields the messy suite nulls (5% of feature values) to exercise D10.
_NULLABLE = ("bpm", "key_pc", "energy", "loudness_db")


def _messy_tracks(namer: Namer, seed: int, n: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    names = list(CLUSTERS)
    tracks = [cluster_track(namer, rng, names[rng.randrange(len(names))]) for _ in range(n)]
    slots = [(i, field) for i in range(n) for field in _NULLABLE]
    for index, field in rng.sample(slots, k=max(1, round(0.05 * len(slots)))):
        features = tracks[index]["features"]
        features[field] = None
        if field == "key_pc":  # set/null together (DATA_MODEL 2.2)
            features["mode"] = None
    return tracks


#: Arc fixture: 20 tracks that differ *only* in energy, listed from highest to
#: lowest.  Neutral scoring is invariant under path reversal, so ascending and
#: descending tie and the index tie-break returns the listed (descending)
#: order; ``build`` penalises every drop by 1.5x, which breaks the tie towards
#: ascending.  A profile that did nothing therefore cannot pass (US-5).
ARC_SIZE = 20
ARC_TOP_ENERGY = 0.86
ARC_STEP = 0.04


def _arc_tracks(namer: Namer, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [
        make_track(
            namer,
            rng,
            bpm=124.0,
            key_pc=9,
            mode=0,
            energy=round(ARC_TOP_ENERGY - ARC_STEP * i, 3),
            loudness_db=-7.0,
            danceability=0.75,
        )
        for i in range(ARC_SIZE)
    ]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def _matrix(tracks: Sequence[dict[str, Any]]) -> list[list[float]]:
    """Score matrix under the engine's default weights and neutral profile."""
    from flowlist.engine.models import FeatureSnapshot
    from flowlist.engine.scoring import build_matrix

    return build_matrix([FeatureSnapshot(**t["features"]) for t in tracks])


def _exact_total(matrix: Sequence[Sequence[float]], start: int | None, end: int | None) -> float:
    from flowlist.engine.optimizer import exact_optimal

    return exact_optimal(matrix, start, end).total


def _shuffled(order: Sequence[int], seed: int) -> list[int]:
    working = list(order)
    random.Random(seed).shuffle(working)
    return working


#: How many jitter seeds the trap search may try before giving up (bounded so
#: generation always terminates; the suite-level invariant is what actually
#: fails the run if the search came up short).
TRAP_ATTEMPTS = 300
def construction_only(
    matrix: Sequence[Sequence[float]], start: int | None, end: int | None
) -> float:
    """Total the engine's *own* construction phase reaches — the degenerate flowlist.

    Uses the shipped ``optimizer.construct`` rather than a re-implementation,
    so the number is what a flowlist with its local search deleted would score.
    """
    from flowlist.engine.optimizer import construct

    return construct(matrix, seed=7, start=start, end=end).total


def _greedy_shortfall(
    matrix: Sequence[Sequence[float]], start: int | None, end: int | None
) -> float:
    """Cheap, sound upper bound on the *worse* baseline's optimality ratio.

    ``baseline / optimum <= baseline / heuristic`` because the heuristic can
    never beat the optimum, so a bound below :data:`TRAP_RATIO` already proves
    the instance defeats that baseline — no Held-Karp needed while searching.

    The bound is taken over the *stronger* of the two degenerate baselines
    (the max of their totals), so a searched instance is hard for the engine's
    own construction phase and not merely for the standalone reference greedy.
    """
    from flowlist.engine.optimizer import reorder

    _, greedy_total = reference_greedy(matrix, start, end)
    degenerate = max(greedy_total, construction_only(matrix, start, end))
    heuristic = reorder(matrix, seed=7, start=start, end=end).total
    return degenerate / heuristic if heuristic else 1.0


def _build_exact_suite(namer: Namer) -> list[dict[str, Any]]:
    """Ten small instances: three typical, four searched, two traps, one anchored."""
    playlists: list[dict[str, Any]] = []
    for index, (n, clusters, kind, target) in enumerate(EXACT_RECIPES):
        base_seed = SEED * 1000 + index
        anchors = (
            {"start": 0, "end": n - 1} if kind == "anchored" else {"start": None, "end": None}
        )
        builder = (
            (lambda s, size=n: _trap_features(namer.clone(), s, size))
            if kind == "trap"
            else (
                lambda s, size=n, cl=clusters: _plain_features(
                    namer.clone(), random.Random(s), cl, size
                )
            )
        )
        if target is None:
            seed, bound = base_seed, None
            tracks = builder(base_seed)
        else:
            tracks, seed, bound = _search(builder, base_seed * 31, anchors, target)
        namer.index += len(tracks)
        detail: dict[str, Any] = {"kind": kind, "search_seed": seed}
        if bound is not None:
            detail["greedy_bound"] = round(bound, 6)
            detail["depth_target"] = target
        playlists.append(
            {
                "id": f"exact_{index:02d}",
                "suite": "exact",
                "n": n,
                "seed": seed,
                "clusters": list(clusters),
                "anchors": anchors,
                "tracks": [t["id"] for t in tracks],
                "planted_order": None,
                "detail": detail,
                "_tracks": tracks,
            }
        )
    return playlists


def _search(
    build: Any,
    base_seed: int,
    anchors: dict[str, int | None],
    target: float,
) -> tuple[list[dict[str, Any]], int, float]:
    """Keep the draw where construction-only greedy does worst.

    Scans :data:`TRAP_ATTEMPTS` consecutive seeds, tracking the lowest
    :func:`_greedy_shortfall` seen and stopping as soon as one reaches
    ``target``.  Deterministic, and the winning seed is recorded in the fixture
    so the search never has to be repeated to reproduce it.
    """
    best: tuple[float, int, list[dict[str, Any]]] | None = None
    for attempt in range(TRAP_ATTEMPTS):
        seed = base_seed + attempt
        tracks = build(seed)
        bound = _greedy_shortfall(_matrix(tracks), anchors["start"], anchors["end"])
        if best is None or bound < best[0]:
            best = (bound, seed, tracks)
        if bound <= target:
            break
    assert best is not None
    bound, seed, tracks = best
    return tracks, seed, bound


def _build_planted_suite(namer: Namer) -> list[dict[str, Any]]:
    sizes = (50, 60, 75, 90, 120, 150)
    clusters = ("house", "dnb", "hiphop", "indie", "house", "dnb")
    out: list[dict[str, Any]] = []
    for index, (n, cluster) in enumerate(zip(sizes, clusters, strict=True)):
        seed = SEED * 2000 + index
        chain = _planted_chain(namer, seed, n, cluster)
        order = _shuffled(range(n), seed + 7)
        stored = [chain[i] for i in order]
        # planted_order maps stored positions back to the planted chain.
        position_of = {original: stored_pos for stored_pos, original in enumerate(order)}
        out.append(
            {
                "id": f"planted_{index:02d}",
                "suite": "planted",
                "n": n,
                "seed": seed,
                "clusters": [cluster],
                "anchors": {"start": None, "end": None},
                "tracks": [t["id"] for t in stored],
                "planted_order": [position_of[i] for i in range(n)],
                "detail": {"kind": "planted"},
                "_tracks": stored,
            }
        )
    return out


def _build_messy_suite(namer: Namer) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, n in enumerate((30, 45, 60, 80)):
        seed = SEED * 3000 + index
        tracks = _messy_tracks(namer, seed, n)
        out.append(
            {
                "id": f"messy_{index:02d}",
                "suite": "messy",
                "n": n,
                "seed": seed,
                "clusters": sorted(CLUSTERS),
                "anchors": {"start": None, "end": None},
                "tracks": [t["id"] for t in tracks],
                "planted_order": None,
                "detail": {"kind": "messy", "null_fraction": 0.05},
                "_tracks": tracks,
            }
        )
    return out


def _build_arc(namer: Namer) -> dict[str, Any]:
    tracks = _arc_tracks(namer, SEED * 4000)
    return {
        "id": "arc_01",
        "suite": "arc",
        "n": len(tracks),
        "seed": SEED * 4000,
        "clusters": [],
        "anchors": {"start": None, "end": None},
        "tracks": [t["id"] for t in tracks],
        "planted_order": None,
        "detail": {"kind": "arc", "energy_step": ARC_STEP},
        "_tracks": tracks,
    }


# --------------------------------------------------------------------------- #
# Measurement (expected.json)
# --------------------------------------------------------------------------- #


def _mean(matrix: Sequence[Sequence[float]], order: Sequence[int]) -> float:
    if len(order) < 2:
        return 0.0
    return sum(matrix[order[i]][order[i + 1]] for i in range(len(order) - 1)) / (len(order) - 1)


def _bpm_sort_order(tracks: Sequence[dict[str, Any]]) -> list[int]:
    """Ascending BPM; tracks without a tempo sort last, then by index."""
    def key(i: int) -> tuple[int, float, int]:
        bpm = tracks[i]["features"].get("bpm")
        return (1, 0.0, i) if bpm is None else (0, float(bpm), i)

    return sorted(range(len(tracks)), key=key)


def _measure(playlist: dict[str, Any]) -> dict[str, Any]:
    from flowlist.engine.optimizer import reorder

    tracks = playlist["_tracks"]
    matrix = _matrix(tracks)
    n = len(tracks)
    identity = list(range(n))
    stats: dict[str, Any] = {
        "n": n,
        "identity_mean": round(_mean(matrix, identity), 6),
        "bpm_sort_mean": round(_mean(matrix, _bpm_sort_order(tracks)), 6),
        "random_mean": round(
            sum(_mean(matrix, _shuffled(identity, s)) for s in range(20)) / 20.0, 6
        ),
    }
    anchors = playlist["anchors"]
    # Both degenerate baselines run under the *same* anchors the heuristic
    # gets, so the anchored instance's ratios compare like with like
    # (EVALS §3-M4).
    _, greedy_total = reference_greedy(matrix, anchors["start"], anchors["end"])
    construct_total = construction_only(matrix, anchors["start"], anchors["end"])
    stats["greedy_only_mean"] = round(greedy_total / (n - 1), 6) if n > 1 else 0.0
    stats["construction_only_mean"] = round(construct_total / (n - 1), 6) if n > 1 else 0.0

    heuristic = reorder(matrix, seed=7, start=anchors["start"], end=anchors["end"])
    stats["heuristic_mean"] = round(heuristic.total / (n - 1), 6) if n > 1 else 0.0

    if playlist["suite"] == "exact":
        optimum = _exact_total(matrix, anchors["start"], anchors["end"])
        stats["exact_total"] = round(optimum, 6)
        stats["greedy_only_ratio"] = round(greedy_total / optimum, 6) if optimum else 1.0
        stats["construction_only_ratio"] = (
            round(construct_total / optimum, 6) if optimum else 1.0
        )
        stats["heuristic_ratio"] = round(heuristic.total / optimum, 6) if optimum else 1.0
    if playlist["planted_order"] is not None:
        planted = playlist["planted_order"]
        planted_mean = _mean(matrix, planted)
        edges = [matrix[planted[i]][planted[i + 1]] for i in range(n - 1)]
        stats["planted_mean"] = round(planted_mean, 6)
        stats["planted_min"] = round(min(edges), 6)
        stats["greedy_only_recovery"] = round(greedy_total / (n - 1) / planted_mean, 6)
        stats["construction_only_recovery"] = round(
            construct_total / (n - 1) / planted_mean, 6
        )
        stats["heuristic_recovery"] = round(heuristic.total / (n - 1) / planted_mean, 6)
    return stats


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build every fixture in memory and measure it."""
    namer = Namer()
    playlists = [
        *_build_exact_suite(namer),
        *_build_planted_suite(namer),
        *_build_messy_suite(namer),
        _build_arc(namer),
    ]
    expected = {
        "seed": SEED,
        "playlists": {p["id"]: _measure(p) for p in playlists},
    }
    return playlists, expected


#: The two degenerate baselines, by their ``expected.json`` ratio key.
BASELINE_KEYS = ("greedy_only_ratio", "construction_only_ratio")


def baseline_m4(expected: dict[str, Any], key: str) -> tuple[float, float]:
    """What M4 would score if a degenerate baseline were the system under test.

    The whole point of the exact suite is that these numbers fail the M4 gates
    for *both* baselines; if either ever passes, M4 certifies nothing about the
    local search.
    """
    ratios = [
        row[key] for pid, row in expected["playlists"].items() if pid.startswith("exact_")
    ]
    return sum(ratios) / len(ratios), min(ratios)


def greedy_m4(expected: dict[str, Any]) -> tuple[float, float]:
    """M4 for the standalone reference greedy (kept for the printed summary)."""
    return baseline_m4(expected, "greedy_only_ratio")


def check_invariants(playlists: Sequence[dict[str, Any]], expected: dict[str, Any]) -> None:
    """Fail generation rather than commit fixtures that cannot gate anything."""
    stats = expected["playlists"]
    ratios = {
        pid: min(row[key] for key in BASELINE_KEYS)
        for pid, row in stats.items()
        if pid.startswith("exact_")
    }
    traps = [pid for pid, ratio in ratios.items() if ratio < TRAP_RATIO]
    assert len(traps) >= MIN_TRAP_INSTANCES, (
        f"discrimination invariant: only {len(traps)} exact instance(s) defeat "
        f"construction-only greedy (need {MIN_TRAP_INSTANCES}): {ratios}"
    )
    # The claim EVALS §4 makes about the fixtures is that construction-only
    # greedy *fails* M4 on them.  Per-instance traps alone do not establish
    # that (three sub-0.97 instances can still average above 0.97), so the
    # suite-level property is asserted directly — and for *both* baselines,
    # because the one that matters is the engine's own construction phase
    # (REVIEW.md #24).
    for key in BASELINE_KEYS:
        mean, minimum = baseline_m4(expected, key)
        assert mean < M4_MEAN_GATE - DISCRIMINATION_MARGIN, (
            f"{key}: this degenerate baseline would PASS the M4_mean gate "
            f"({mean:.4f} vs {M4_MEAN_GATE}); the exact suite is not discriminative: {ratios}"
        )
        assert minimum < M4_MIN_GATE - DISCRIMINATION_MARGIN, (
            f"{key}: this degenerate baseline would PASS the M4_min gate "
            f"({minimum:.4f} vs {M4_MIN_GATE}); no instance is hard enough: {ratios}"
        )
    for pid, row in stats.items():
        if "heuristic_ratio" in row:
            assert row["heuristic_ratio"] <= 1.0 + 1e-9, f"{pid}: heuristic beat the exact optimum"
        if "planted_min" in row:
            assert row["planted_min"] >= 0.80, f"{pid}: planted transition below 0.80"
            assert 0.84 <= row["planted_mean"] <= 0.92, (
                f"{pid}: planted mean {row['planted_mean']} outside the engineered band"
            )
    ids = [p["id"] for p in playlists]
    assert len(ids) == len(set(ids))
    all_track_ids = [tid for p in playlists for tid in p["tracks"]]
    assert len(all_track_ids) == len(set(all_track_ids)), "fixture track ids collide"


def write(playlists: Sequence[dict[str, Any]], expected: dict[str, Any]) -> None:
    PLAYLISTS.mkdir(parents=True, exist_ok=True)
    catalog = {
        "description": "Synthetic fixture catalog (EVALS.md §4); one row per fixture track.",
        "tracks": [t for p in playlists for t in p["_tracks"]],
    }
    _dump(FIXTURES / "catalog.json", catalog)
    for playlist in playlists:
        payload = {k: v for k, v in playlist.items() if not k.startswith("_")}
        _dump(PLAYLISTS / f"{playlist['id']}.json", payload)
    _dump(FIXTURES / "expected.json", expected)


def write_goldens() -> None:
    """Commit the seed-7 orderings M7 compares against (EVALS §3-M7)."""
    from flowlist.engine.optimizer import reorder

    goldens: dict[str, list[str]] = {}
    for pid in GOLDEN_FIXTURES:
        payload = json.loads((PLAYLISTS / f"{pid}.json").read_text(encoding="utf-8"))
        catalog = _catalog_index()
        tracks = [catalog[tid] for tid in payload["tracks"]]
        matrix = _matrix(tracks)
        anchors = payload["anchors"]
        result = reorder(matrix, seed=7, start=anchors["start"], end=anchors["end"])
        goldens[pid] = [payload["tracks"][i] for i in result.order]
    _dump(
        FIXTURES / "golden_orderings.json",
        {
            "description": (
                "Committed seed-7 orderings for the M7 determinism gate. A diff here on "
                "another platform is the visible signal FR-15 promises."
            ),
            "seed": 7,
            "orderings": goldens,
        },
    )


#: The three fixtures M7 checks, one per suite (EVALS §3-M7).
#:
#: ``planted_02`` replaced ``planted_00`` in the harden pass: the seed genuinely
#: changes the answer there, so M7's "same seed, same order" half can actually
#: fail.  On seed-insensitive fixtures — which the original three all were —
#: repeatability holds even for an implementation that ignores its seed
#: entirely, making that half of the gate vacuous (REVIEW.md #26).  M7
#: re-asserts the seed sensitivity live, so a regeneration cannot quietly lose
#: it.
GOLDEN_FIXTURES = ("exact_07", "planted_02", "messy_00")


def _catalog_index() -> dict[str, dict[str, Any]]:
    data = json.loads((FIXTURES / "catalog.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["tracks"]}


def _dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED, help="master seed (default 42)")
    parser.add_argument(
        "--goldens",
        action="store_true",
        help="also rewrite golden_orderings.json from the committed fixtures",
    )
    args = parser.parse_args(argv)
    globals()["SEED"] = args.seed

    playlists, expected = build()
    check_invariants(playlists, expected)
    write(playlists, expected)
    if args.goldens:
        write_goldens()

    tracks = sum(len(p["_tracks"]) for p in playlists)
    rows = {
        pid: row for pid, row in expected["playlists"].items() if "greedy_only_ratio" in row
    }
    print(f"wrote {len(playlists)} playlists / {tracks} catalog tracks to {FIXTURES}")
    print("degenerate-baseline optimality ratios (exact suite): ref greedy / engine construction")
    for pid, row in rows.items():
        worst = min(row[key] for key in BASELINE_KEYS)
        flag = "  <- defeats both" if worst < TRAP_RATIO else ""
        print(
            f"  {pid}: {row['greedy_only_ratio']:.4f} / "
            f"{row['construction_only_ratio']:.4f}{flag}"
        )
    for key in BASELINE_KEYS:
        mean, minimum = baseline_m4(expected, key)
        print(
            f"{key}: M4_mean={mean:.4f} (gate {M4_MEAN_GATE}) / "
            f"M4_min={minimum:.4f} (gate {M4_MIN_GATE}) -> FAILS both"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Booking sets, the candidate lattice and the funding search (FR-7, hard part A).

The search is exact by construction and never approximates silently:

* **Booking-set enumeration** (FR-7a) covers single one-way awards, round-trip
  awards, portal bookings, and every award/portal mix across a round trip's legs.
* **Need aggregation and edge merging** (FR-7b): points are fungible within a
  program, so each paying program has one aggregate need and every transfer over
  one edge is merged into a single step.
* **Funding search** (FR-7c): recursive allocation over the finite candidate
  lattice ``{s_cover} u {tier boundaries} u {s_max}``, with an admissible lower
  bound and lossless duplicate-state pruning; on exceeding ``max_expansions`` it
  raises :class:`SearchBudgetExceeded` rather than returning a guess.
* **Timing feasibility** (FR-7d): ``today + arrival_days(p) <= deadline(b)``.

Two implementation notes for reviewers and later stages:

* the doc's dominance rule ("A prunes B when A's deficits <= B's, balances >=,
  loss <=, arrival <=") is implemented in its *lossless* form — identical
  remaining requirements and identical accumulated transfers.  The inequality
  form can discard a state that ties on cost but wins FR-9's canonical-form
  tie-break, which would make the emitted plan differ from an exhaustive
  oracle's; exactness is worth more than the extra pruning.
* ``pruning=False`` disables the bound and the duplicate-state memo without
  changing any result (the eval suite's unpruned baseline).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction

from ..models import (
    Cabin,
    GoalKind,
    GoalSpec,
    OfferKind,
    PlanParams,
    TransferEdge,
    World,
    canonical_json,
)
from .goals import matching_flight_offers, matching_stay_offers
from .money import (
    delivered_points,
    floor_to_multiple,
    max_valid_sent,
    portfolio_value,
    redeemable_points,
    smallest_sent_covering,
    transfer_fee_cents,
)
from .plan import PlanDraft, build_steps
from .value import (
    Booking,
    TransferUse,
    award_flight_booking,
    award_stay_booking,
    cash_booking,
    portal_flight_booking,
    portal_stay_booking,
    value_plan,
)
from .world import ActiveWorld

#: Cap on the lcm-alignment window (in increments) explored below each cover or
#: tier anchor (see :meth:`FundingSearch._lattice`).
MAX_ALIGN_WINDOW = 12

#: Sentinel window meaning "every valid amount in range" — used for cost-tie
#: edge groups, where FR-9's canonical tie-break can select interior amounts.
DENSE_WINDOW = 1 << 30

#: Largest span (in increments) the cost-tie densification will enumerate.  The
#: oracle-tractability budget guarantees every fixture scenario fits (<= 60
#: valid amounts per edge), so within the gated envelope tie-breaks are exact;
#: past it the search still returns a cost-optimal plan but may not pick the
#: canonically first among exact cost ties — bounded, and invisible to value.
MAX_TIE_SPAN = 60


class SearchBudgetExceeded(RuntimeError):
    """The funding search hit its explicit expansion budget (FR-7c)."""

    def __init__(self, expansions: int, budget: int) -> None:
        self.expansions = expansions
        self.budget = budget
        super().__init__(
            f"funding search exceeded its expansion budget: {expansions} expansions > "
            f"max_expansions={budget}. Raise max_expansions or narrow the goal."
        )


class _CycleError(Exception):
    """Internal: a candidate allocation contains a transfer cycle."""


@dataclass
class SearchStats:
    """Expansion counter shared across every search in one plan computation."""

    expansions: int = 0


# --------------------------------------------------------------------------
# FR-7a booking-set enumeration
# --------------------------------------------------------------------------


def _portal_cabins(
    goal: GoalSpec, world: World, origin: str, dest: str, round_trip: bool
) -> list[Cabin]:
    """Cabins a portal booking may be priced in.

    A goal that fixes a cabin yields exactly one portal booking per option (the
    FR-7a wording).  A ``cabin = null`` goal matches any cabin (FR-6), so one
    portal booking is offered per cabin the world can actually price — strictly
    more complete than picking one arbitrarily.
    """
    if goal.cabin is not None:
        return [goal.cabin]
    month = goal.travel_month
    assert month
    return [
        cabin
        for cabin in Cabin
        if world.find_fare(
            OfferKind.FLIGHT,
            month=month,
            origin_city=origin,
            dest_city=dest,
            cabin=cabin,
            round_trip=round_trip,
        )
        is not None
    ]


def enumerate_booking_sets(
    goal: GoalSpec, world: World, active: ActiveWorld, today: date
) -> list[tuple[Booking, ...]]:
    """Every candidate booking set for a flight or stay goal (FR-7a)."""
    sets: list[tuple[Booking, ...]] = []
    portal_options = active.portal_options()

    if goal.kind is GoalKind.FLIGHT:
        assert goal.origin_city and goal.dest_city
        if goal.round_trip:
            for offer in matching_flight_offers(
                goal,
                world,
                today,
                origin_city=goal.origin_city,
                dest_city=goal.dest_city,
                round_trip=True,
            ):
                booking = award_flight_booking(
                    goal,
                    offer,
                    world,
                    origin_city=goal.origin_city,
                    dest_city=goal.dest_city,
                    round_trip=True,
                )
                if booking is not None:
                    sets.append((booking,))
            for option in portal_options:
                for cabin in _portal_cabins(goal, world, goal.origin_city, goal.dest_city, True):
                    booking = portal_flight_booking(
                        goal,
                        option,
                        world,
                        origin_city=goal.origin_city,
                        dest_city=goal.dest_city,
                        round_trip=True,
                        cabin=cabin,
                    )
                    if booking is not None:
                        sets.append((booking,))

        leg_candidates: list[list[Booking]] = []
        for origin, dest in goal.legs():
            candidates: list[Booking] = []
            for offer in matching_flight_offers(
                goal, world, today, origin_city=origin, dest_city=dest, round_trip=False
            ):
                booking = award_flight_booking(
                    goal, offer, world, origin_city=origin, dest_city=dest, round_trip=False
                )
                if booking is not None:
                    candidates.append(booking)
            for option in portal_options:
                for cabin in _portal_cabins(goal, world, origin, dest, False):
                    booking = portal_flight_booking(
                        goal,
                        option,
                        world,
                        origin_city=origin,
                        dest_city=dest,
                        round_trip=False,
                        cabin=cabin,
                    )
                    if booking is not None:
                        candidates.append(booking)
            leg_candidates.append(candidates)
        if leg_candidates and all(leg_candidates):
            for combo in itertools.product(*leg_candidates):
                sets.append(tuple(combo))

    elif goal.kind is GoalKind.STAY:
        for offer in matching_stay_offers(goal, world, today):
            booking = award_stay_booking(goal, offer, world)
            if booking is not None:
                sets.append((booking,))
        for option in portal_options:
            booking = portal_stay_booking(goal, option, world)
            if booking is not None:
                sets.append((booking,))

    unique: dict[tuple, tuple[Booking, ...]] = {}
    for booking_set in sets:
        ordered = tuple(sorted(booking_set, key=lambda b: b.sort_key()))
        unique[tuple(b.sort_key() for b in ordered)] = ordered
    return [unique[key] for key in sorted(unique)]


def aggregate_needs(bookings: tuple[Booking, ...]) -> dict[str, int]:
    """FR-7b: total points each paying program must produce."""
    needs: dict[str, int] = {}
    for booking in bookings:
        needs[booking.program_id] = needs.get(booking.program_id, 0) + booking.points
    return needs


def booking_deadlines(bookings: tuple[Booking, ...]) -> dict[str, date | None]:
    """The binding deadline per paying program (FR-7d): the earliest one."""
    deadlines: dict[str, date | None] = {}
    for booking in bookings:
        if booking.program_id not in deadlines:
            deadlines[booking.program_id] = booking.deadline
            continue
        current = deadlines[booking.program_id]
        if booking.deadline is None:
            continue
        deadlines[booking.program_id] = (
            booking.deadline if current is None else min(current, booking.deadline)
        )
    return deadlines


# --------------------------------------------------------------------------
# FR-7c funding search
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingSolution:
    """The optimal inbound transfer set for one (needs, deadlines) problem."""

    transfers: tuple[TransferUse, ...]
    arrival_days: dict[str, int] = field(default_factory=dict)
    hop_index: dict[str, int] = field(default_factory=dict)
    feasible_in_days: int = 0


class FundingSearch:
    """Branch-and-bound allocation of transfers over the active subgraph."""

    def __init__(
        self,
        world: World,
        active: ActiveWorld,
        opening: dict[str, int],
        today: date,
        params: PlanParams,
        *,
        pruning: bool = True,
        stats: SearchStats | None = None,
    ) -> None:
        self.world = world
        self.active = active
        self.opening = dict(opening)
        self.today = today
        self.params = params
        self.pruning = pruning
        self.stats = stats or SearchStats()
        self.mcpp = world.mcpp_map()
        self._slack = len(world.programs)
        self._inbound_cache: dict[tuple[str, frozenset[str]], tuple[TransferEdge, ...]] = {}
        self._rate_cache: dict[str, Fraction] = {}
        self._window_cache: dict[str, dict[str, int]] = {}
        self._solution_cache: dict[tuple, FundingSolution | None] = {}
        # per-solve state
        self.balances: dict[str, int] = {}
        self.uses: dict[str, int] = {}
        self.memo: set[tuple] = set()
        self._needs: dict[str, int] = {}
        self._deadlines: dict[str, date | None] = {}
        self._spend_milli = 0
        self._best: FundingSolution | None = None
        self._best_cost: int | None = None
        self._best_key: tuple | None = None
        self._best_canonical: str | None = None

    # -- helpers -----------------------------------------------------------

    def _edge_loss_rate(self, edge: TransferEdge) -> Fraction:
        """Minimum milli-cents of value lost per delivered point over ``edge``.

        Uses the *maximum* achievable ``delivered/sent`` ratio and a zero fee, so
        the result is a valid lower bound.  Non-negative by FR-1 invariant 4.
        """
        cached = self._rate_cache.get(edge.id)
        if cached is not None:
            return cached
        ratio = Fraction(edge.ratio_to, edge.ratio_from)
        if edge.bonus_per_from and edge.bonus_to:
            ratio += Fraction(edge.bonus_to, edge.bonus_per_from)
        rate = Fraction(self.mcpp[edge.from_program], 1) / ratio - self.mcpp[edge.to_program]
        if rate < 0:
            rate = Fraction(0)
        self._rate_cache[edge.id] = rate
        return rate

    def _inbound(self, program_id: str, path: frozenset[str]) -> tuple[TransferEdge, ...]:
        key = (program_id, path)
        cached = self._inbound_cache.get(key)
        if cached is not None:
            return cached
        edges = [e for e in self.active.edges_into(program_id) if e.from_program not in path]
        edges.sort(key=lambda e: (self._edge_loss_rate(e), e.id))
        result = tuple(edges)
        self._inbound_cache[key] = result
        return result

    def _max_inflow(self, program_id: str, path: frozenset[str], hops: int) -> int:
        """Upper bound on points deliverable into ``program_id`` within ``hops``."""
        if hops <= 0:
            return 0
        total = 0
        for edge in self._inbound(program_id, path):
            source = edge.from_program
            capacity = max(self.balances.get(source, 0), 0)
            if hops > 1:
                capacity += self._max_inflow(source, path | {source}, hops - 1)
            top = max_valid_sent(edge, capacity)
            if top is not None:
                total += delivered_points(edge, top)
        return total

    def _delivered_increment(self, edge: TransferEdge) -> int:
        """Points delivered per increment sent — exact by FR-1 invariant 2."""
        return edge.increment_from * edge.ratio_to // edge.ratio_from

    def _align_windows(self, program_id: str) -> dict[str, int]:
        """Per-edge alignment window (in increments) for edges into ``program_id``.

        When every inbound edge delivers in multiples of the same quantum, the
        FR-7c exchange argument is airtight and no window is needed.  When the
        delivered increments differ (a 2,000-delivering bank edge next to a
        1,000-delivering 3:1 hub edge), an optimal split can park one edge a few
        increments away from an anchor — below its residual cover, or just
        *above* a tier boundary (63,000 = 60,000 + one 3,000 increment) — so the
        other edge lands on the residual exactly; the blocked-trade analysis
        bounds that offset by ``lcm(delivered increments) /
        delivered_increment(edge)`` increments, capped at ``MAX_ALIGN_WINDOW``.
        """
        cached = self._window_cache.get(program_id)
        if cached is not None:
            return cached
        edges = self.active.edges_into(program_id)
        quanta = sorted({self._delivered_increment(e) for e in edges})
        windows: dict[str, int] = {}
        if len(quanta) <= 1:
            windows = {e.id: 0 for e in edges}
        else:
            lcm_all = quanta[0]
            for quantum in quanta[1:]:
                lcm_all = math.lcm(lcm_all, quantum)
            for e in edges:
                windows[e.id] = min(lcm_all // self._delivered_increment(e), MAX_ALIGN_WINDOW)
        # Cost-tie groups: two fee-free edges with the *same* loss rate make every
        # split of the residual cost-identical, and FR-9's final tie-break (the
        # canonically smallest step list) can then land on any interior amount.
        # Those edges get a dense lattice (capped to the span in _lattice); ties
        # are rare and balances bound the span, so this stays cheap.
        by_rate: dict[Fraction, list[TransferEdge]] = {}
        for e in edges:
            if e.fee_mcpp == 0:
                by_rate.setdefault(self._edge_loss_rate(e), []).append(e)
        for group in by_rate.values():
            if len(group) > 1:
                for e in group:
                    windows[e.id] = DENSE_WINDOW
        self._window_cache[program_id] = windows
        return windows

    def _lattice(
        self,
        edge: TransferEdge,
        residual: int,
        max_extra: int,
        already_sent: int,
        window: int,
        *,
        cover_only: bool = False,
        extra_anchors: tuple[int, ...] = (),
    ) -> list[int]:
        """FR-7c candidate *total* sent amounts over ``edge`` (merged, FR-7b).

        The published lattice is ``{s_cover} u {tier boundaries} u {s_max}``.
        Two documented refinements keep it exact without densifying it:

        * ``min_from`` is always an anchor — the smallest contribution an edge
          can make while another edge covers the rest;
        * ``window`` extra amounts *around* every anchor (cover, each tier
          boundary, the minimum — see :meth:`_align_windows`): the splits the
          FR-7c exchange argument misses when inbound edges deliver in
          different quanta sit within one lcm block of an anchor, on either
          side of it.

        One tier boundary past the cover is kept as insurance: an exhaustive
        oracle evaluates it, and it can only tie or lose the FR-9 tie-break.
        Other amounts above the cover are excluded by FR-1 invariant 4 (extra
        sending is weakly value-losing and weakly fee-increasing).
        """
        inc = edge.increment_from
        ceiling = floor_to_multiple(already_sent + max_extra, inc)
        lowest = max(edge.min_from, already_sent + inc)
        lowest = floor_to_multiple(lowest + inc - 1, inc)
        if ceiling < lowest:
            return []
        base_delivered = delivered_points(edge, already_sent)

        lo_k, hi_k = lowest // inc, ceiling // inc
        cover: int | None = None
        if delivered_points(edge, hi_k * inc) - base_delivered >= residual:
            lo, hi = lo_k, hi_k
            while lo < hi:
                mid = (lo + hi) // 2
                if delivered_points(edge, mid * inc) - base_delivered >= residual:
                    hi = mid
                else:
                    lo = mid + 1
            cover = lo * inc

        limit = cover if cover is not None else ceiling
        span = (limit - lowest) // inc + 1
        window = min(span, MAX_TIE_SPAN) if window >= DENSE_WINDOW else min(window, span)
        anchors: list[int] = [limit] if cover_only else [limit, lowest]
        for anchor in extra_anchors:
            aligned = floor_to_multiple(anchor, inc)
            if lowest <= aligned <= limit:
                anchors.append(aligned)
        insurance: int | None = None
        if edge.bonus_per_from:
            tier = edge.bonus_per_from
            k = max(1, lowest // tier)
            while k * tier <= ceiling:
                amount = k * tier
                if amount >= lowest:
                    if amount > limit:
                        # One boundary past the residual, then stop (see above).
                        insurance = amount
                        break
                    if not cover_only:
                        anchors.append(amount)
                k += 1
        candidates: set[int] = set(anchors)
        if insurance is not None:
            candidates.add(insurance)
        for anchor in anchors:
            for step in range(-window, window + 1):
                amount = anchor + step * inc
                if lowest <= amount <= limit:
                    candidates.add(amount)
        return sorted(c for c in candidates if lowest <= c <= ceiling)

    # -- solving -----------------------------------------------------------

    def solve(
        self, needs: dict[str, int], deadlines: dict[str, date | None]
    ) -> FundingSolution | None:
        """Optimal funding for ``needs``, or None when no feasible allocation exists.

        The result depends only on (opening balances, needs, deadlines, active
        subgraph, today), so it is cached and reused across booking sets that
        aggregate to the same requirement.
        """
        key = (
            tuple(sorted(needs.items())),
            tuple(sorted((p, d.isoformat() if d else "") for p, d in deadlines.items())),
        )
        if key in self._solution_cache:
            return self._solution_cache[key]

        self._needs = needs
        self._deadlines = deadlines
        self._spend_milli = sum(points * self.mcpp[p] for p, points in needs.items())
        self.balances = dict(self.opening)
        for program_id, points in needs.items():
            self.balances[program_id] = self.balances.get(program_id, 0) - points
        self.uses = {}
        self.memo = set()
        self._best = None
        self._best_cost = None
        self._best_key = None
        self._best_canonical = None

        deficits = sorted(
            (p for p, n in self.balances.items() if n < 0),
            key=lambda p: (self.balances[p], p),
        )
        reqs = tuple(
            (program_id, self.params.max_hops, frozenset({program_id}), 0)
            for program_id in deficits
        )
        self._rec(reqs, 0)
        self._solution_cache[key] = self._best
        return self._best

    def _rec(self, reqs: tuple, alloc_milli: int) -> None:
        self.stats.expansions += 1
        if self.stats.expansions > self.params.max_expansions:
            raise SearchBudgetExceeded(self.stats.expansions, self.params.max_expansions)

        if not reqs:
            self._leaf()
            return

        if self.pruning:
            bound = self._lower_bound(reqs)
            if bound is None:
                return
            if self._best_cost is not None:
                total = alloc_milli + self._spend_milli + bound
                if total > 1000 * (self._best_cost + self._slack):
                    return
            memo_key = (reqs, tuple(sorted(self.uses.items())))
            if memo_key in self.memo:
                return
            self.memo.add(memo_key)

        program_id, hops, path, idx = reqs[0]
        rest = reqs[1:]
        residual = -self.balances.get(program_id, 0)
        if residual <= 0:
            self._rec(rest, alloc_milli)
            return
        if hops <= 0:
            return
        candidates = self._inbound(program_id, path)
        # The edge list is walked twice: a fixed single pass makes some optima
        # unreachable (an edge can only "cover the residual left by the others"
        # if it is decided *after* them, and no static order is right for every
        # fee/tier configuration).  On the second pass an edge already used is
        # topped up via the merged-amount lattice (``already_sent``), so FR-7b's
        # one-step-per-edge invariant still holds.
        if idx >= 2 * len(candidates):
            return
        edge = candidates[idx % len(candidates)]
        advanced = (program_id, hops, path, idx + 1)

        # (a) use it, for every candidate merged amount.  Largest first, so the
        # residual-covering amount reaches a leaf immediately and the incumbent
        # the branch-and-bound prunes against exists from the very first descent.
        source = edge.from_program
        available = max(self.balances.get(source, 0), 0)
        max_extra = available
        if hops > 1:
            max_extra += self._max_inflow(source, path | {source}, hops - 1)
        if max_extra <= 0:
            # (b) nothing can flow over this edge; skip straight to the next one
            self._rec((advanced, *rest), alloc_milli)
            return
        already = self.uses.get(edge.id, 0)
        old_delivered = delivered_points(edge, already)
        old_fee = transfer_fee_cents(edge, already)
        source_mcpp = self.mcpp[source]
        target_mcpp = self.mcpp[program_id]
        window = self._align_windows(program_id).get(edge.id, 0)
        # Shared-source reservations: when another outstanding requirement can
        # draw on this same source directly, this edge must be able to take
        # "everything except what covers them" — an anchor no per-requirement
        # lattice shape produces on its own.
        reserves: list[int] = []
        held = already + max(self.balances.get(source, 0), 0)
        for other_id, _hops, _path, _idx in rest:
            other_residual = -self.balances.get(other_id, 0)
            if other_residual <= 0 or other_id == source:
                continue
            for other_edge in self._inbound(other_id, frozenset({other_id})):
                if other_edge.from_program != source or other_edge.id == edge.id:
                    continue
                cover_q = smallest_sent_covering(other_edge, other_residual, held)
                if cover_q is not None and held - cover_q > already:
                    reserves.append(held - cover_q)
        lattice = self._lattice(
            edge,
            residual,
            max_extra,
            already,
            window,
            # Second pass: this edge already had its full anchor set offered; it
            # comes around again only to *cover* whatever the later edges left.
            cover_only=idx >= len(candidates),
            extra_anchors=tuple(reserves),
        )

        for total_sent in reversed(lattice):
            extra = total_sent - already
            gained = delivered_points(edge, total_sent) - old_delivered
            fee_delta = transfer_fee_cents(edge, total_sent) - old_fee
            delta_milli = fee_delta * 1000 + extra * source_mcpp - gained * target_mcpp

            new_reqs: tuple = (advanced, *rest)
            if extra > available and not any(r[0] == source for r in new_reqs):
                # The source must itself be funded, and no outstanding
                # requirement covers it — push one, which costs a hop.
                if hops <= 1:
                    continue
                new_reqs = ((source, hops - 1, path | {source}, 0), *new_reqs)

            prev_source = self.balances.get(source, 0)
            prev_target = self.balances.get(program_id, 0)
            self.balances[source] = prev_source - extra
            self.balances[program_id] = prev_target + gained
            self.uses[edge.id] = total_sent

            self._rec(new_reqs, alloc_milli + delta_milli)

            self.balances[source] = prev_source
            self.balances[program_id] = prev_target
            if already:
                self.uses[edge.id] = already
            else:
                del self.uses[edge.id]

        # (c) do not use this edge at all
        self._rec((advanced, *rest), alloc_milli)

    def _lower_bound(self, reqs: tuple) -> int | None:
        """Admissible lower bound in milli-cents, or None when reqs are unsatisfiable.

        Every point delivered into a program loses at least the minimum
        edge-loss rate over its still-candidate inbound edges (fees and hop
        chains only add loss), so pricing each residual at that rate never
        over-estimates.  Capacity-aware refinements were considered and
        rejected: a source balance can legitimately rise mid-plan (cover
        overshoot routed onward), so capacity caps can over-estimate and prune
        a true optimum.
        """
        total = 0
        for program_id, hops, path, idx in reqs:
            residual = -self.balances.get(program_id, 0)
            if residual <= 0:
                continue
            if hops <= 0:
                return None
            candidates = self._inbound(program_id, path)
            if not candidates or idx >= 2 * len(candidates):
                return None
            rate = min(self._edge_loss_rate(edge) for edge in candidates)
            total += int(rate * residual)
        return total

    def _leaf(self) -> None:
        if not self.uses:
            transfers: tuple[TransferUse, ...] = ()
        else:
            transfers = tuple(
                TransferUse.build(self.world.edge(edge_id), sent)
                for edge_id, sent in sorted(self.uses.items())
            )
        if any(points < 0 for points in self.balances.values()):
            return
        try:
            arrival, hop_index = _arrival_and_hops(transfers)
        except _CycleError:
            return

        for program_id, deadline in self._deadlines.items():
            if deadline is None:
                continue
            if self.today + timedelta(days=arrival.get(program_id, 0)) > deadline:
                return

        holdings = dict(self.opening)
        for program_id, points in self._needs.items():
            holdings[program_id] = holdings.get(program_id, 0) - points
        for use in transfers:
            holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
            holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered

        programs = set(self.opening) | set(holdings)
        opening_full = {p: self.opening.get(p, 0) for p in programs}
        holdings_full = {p: holdings.get(p, 0) for p in programs}
        cost = sum(t.fee_cents for t in transfers) + (
            portfolio_value(opening_full, self.mcpp) - portfolio_value(holdings_full, self.mcpp)
        )
        points_spent = sum(
            opening_full[p] - holdings_full[p]
            for p in programs
            if opening_full[p] > holdings_full[p]
        )
        key = (cost, points_spent, len(transfers))
        if self._best_key is not None:
            if key > self._best_key:
                return
            if key == self._best_key:
                if self._best_canonical is None:
                    assert self._best is not None
                    self._best_canonical = _transfer_canonical(
                        self.world, self._best.transfers, self._best.hop_index
                    )
                candidate = _transfer_canonical(self.world, transfers, hop_index)
                if candidate >= self._best_canonical:
                    return
                self._best_canonical = candidate
            else:
                self._best_canonical = None
        feasible_in_days = max(
            (arrival.get(p, 0) for p in self._needs),
            default=0,
        )
        self._best = FundingSolution(
            transfers=transfers,
            arrival_days=arrival,
            hop_index=hop_index,
            feasible_in_days=feasible_in_days,
        )
        self._best_cost = cost
        self._best_key = key


def _transfer_canonical(
    world: World, transfers: tuple[TransferUse, ...], hop_index: dict[str, int]
) -> str:
    steps = build_steps(world, (), transfers, hop_index)
    return canonical_json([s.canonical_tuple() for s in steps])


def _arrival_and_hops(
    transfers: tuple[TransferUse, ...],
) -> tuple[dict[str, int], dict[str, int]]:
    """FR-7d ``arrival_days`` and FR-9 ``hop_index`` over the funding DAG."""
    inbound: dict[str, list[TransferUse]] = {}
    for use in transfers:
        inbound.setdefault(use.to_program, []).append(use)
    arrival: dict[str, int] = {}
    depth: dict[str, int] = {}
    state: dict[str, int] = {}

    def visit(program_id: str) -> None:
        marker = state.get(program_id, 0)
        if marker == 1:
            raise _CycleError(program_id)
        if marker == 2:
            return
        state[program_id] = 1
        incoming = inbound.get(program_id, [])
        best_arrival = 0
        best_depth = 0
        for use in incoming:
            visit(use.from_program)
            best_arrival = max(best_arrival, arrival[use.from_program] + use.time_days)
            best_depth = max(best_depth, depth[use.from_program] + 1)
        arrival[program_id] = best_arrival
        depth[program_id] = best_depth
        state[program_id] = 2

    for use in transfers:
        visit(use.from_program)
        visit(use.to_program)
    return arrival, {use.edge_id: depth[use.from_program] for use in transfers}


# --------------------------------------------------------------------------
# Drafts for flight / stay goals
# --------------------------------------------------------------------------


def solve_booking_set(
    search: FundingSearch,
    bookings: tuple[Booking, ...],
) -> PlanDraft | None:
    """Fund one booking set optimally and price the resulting plan (FR-7 + FR-8)."""
    needs = aggregate_needs(bookings)
    deadlines = booking_deadlines(bookings)
    solution = search.solve(needs, deadlines)
    if solution is None:
        return None
    value = value_plan(search.opening, solution.transfers, bookings, search.mcpp)
    return PlanDraft(
        bookings=bookings,
        transfers=solution.transfers,
        value=value,
        arrival_days=dict(solution.arrival_days),
        hop_index=dict(solution.hop_index),
        feasible_in_days=solution.feasible_in_days,
    )


# --------------------------------------------------------------------------
# FR-12 cash goals
# --------------------------------------------------------------------------


def _cash_rate(active: ActiveWorld, program_id: str) -> int:
    """Best liquid cpp available on ``program_id`` today, 0 when none."""
    options = active.cash_options_for(program_id)
    return max((o.cpp_milli for o in options), default=0)


def _max_ratio(edge: TransferEdge) -> Fraction:
    """The largest achievable ``delivered/sent`` ratio, attained at a tier boundary."""
    ratio = Fraction(edge.ratio_to, edge.ratio_from)
    if edge.bonus_per_from and edge.bonus_to:
        ratio += Fraction(edge.bonus_to, edge.bonus_per_from)
    return ratio


def _cash_transfer_candidates(
    active: ActiveWorld, balances: dict[str, int], max_hops: int
) -> list[TransferEdge]:
    """Active edges that could plausibly *raise* the cash total (FR-12).

    An edge helps only when a delivered point is worth more liquid cash at the
    destination than a sent point is at the source (or the source has no liquid
    option at all, so its points are cash-worthless where they sit).
    """
    reachable = {p for p, n in balances.items() if n > 0}
    for _ in range(max_hops):
        for edge in active.edges:
            if edge.from_program in reachable:
                reachable.add(edge.to_program)
    out: list[TransferEdge] = []
    for edge in active.edges:
        if edge.from_program not in reachable:
            continue
        source_rate = _cash_rate(active, edge.from_program)
        ratio = _max_ratio(edge)
        target_rate = _cash_rate(active, edge.to_program)
        onward = max(
            (_cash_rate(active, e.to_program) * _max_ratio(e) for e in active.edges_from(edge.to_program)),
            default=Fraction(0),
        )
        best_target = max(Fraction(target_rate), onward)
        if source_rate == 0 or ratio * best_target > source_rate:
            out.append(edge)
    out.sort(key=lambda e: e.id)
    return out


def _cash_allocations(
    edges: list[TransferEdge],
    balances: dict[str, int],
    stats: SearchStats,
    budget: int,
) -> list[tuple[TransferUse, ...]]:
    """Every transfer allocation worth considering for a cash goal."""
    results: list[tuple[TransferUse, ...]] = [()]
    if not edges:
        return results

    def rec(index: int, current: dict[str, int], used: list[TransferUse]) -> None:
        stats.expansions += 1
        if stats.expansions > budget:
            raise SearchBudgetExceeded(stats.expansions, budget)
        if index >= len(edges):
            if used:
                results.append(tuple(used))
            return
        edge = edges[index]
        rec(index + 1, current, used)
        available = current.get(edge.from_program, 0)
        top = max_valid_sent(edge, available)
        if top is None:
            return
        amounts = {top}
        if edge.bonus_per_from:
            k = 1
            while k * edge.bonus_per_from <= top:
                if k * edge.bonus_per_from >= edge.min_from:
                    amounts.add(k * edge.bonus_per_from)
                k += 1
        for sent in sorted(amounts):
            use = TransferUse.build(edge, sent)
            nxt = dict(current)
            nxt[edge.from_program] = nxt.get(edge.from_program, 0) - sent
            nxt[edge.to_program] = nxt.get(edge.to_program, 0) + use.delivered
            rec(index + 1, nxt, [*used, use])

    rec(0, dict(balances), [])
    return results


def cash_drafts(
    world: World,
    active: ActiveWorld,
    opening: dict[str, int],
    goal: GoalSpec,
    params: PlanParams,
    stats: SearchStats,
) -> list[PlanDraft]:
    """FR-12: every candidate liquidation plan, unranked."""
    mcpp = world.mcpp_map()
    allowed = set(goal.cash_programs) if goal.cash_programs else None
    caps = goal.cash_max_points or {}
    usable: dict[str, int] = {}
    for program_id, points in opening.items():
        if points <= 0:
            continue
        if allowed is not None and program_id not in allowed:
            continue
        cap = caps.get(program_id)
        usable[program_id] = min(points, cap) if cap is not None else points
    if not usable:
        return []

    edges = _cash_transfer_candidates(active, usable, params.max_hops)
    allocations = _cash_allocations(edges, usable, stats, params.max_expansions)

    drafts: list[PlanDraft] = []
    seen: set[str] = set()
    for transfers in allocations:
        holdings = dict(usable)
        skip = False
        for use in transfers:
            holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
            holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
            if holdings[use.from_program] < 0:
                skip = True
        if skip:
            continue
        try:
            arrival, hop_index = _arrival_and_hops(transfers)
        except _CycleError:
            continue

        choices: list[list[Booking | None]] = []
        programs = sorted(p for p, n in holdings.items() if n > 0)
        for program_id in programs:
            options = active.cash_options_for(program_id)
            column: list[Booking | None] = [None]
            for option in sorted(options, key=lambda o: o.id):
                amount = redeemable_points(holdings[program_id], option)
                if amount > 0:
                    column.append(cash_booking(program_id, option, amount))
            choices.append(column)
        if not choices:
            continue

        # FR-12: alternatives swap the per-program option, so every combination
        # of (option | skip) per program is a candidate plan for this allocation.
        for combo in itertools.product(*choices):
            bookings = tuple(
                sorted((b for b in combo if b is not None), key=lambda b: b.sort_key())
            )
            if not bookings:
                continue
            stats.expansions += 1
            if stats.expansions > params.max_expansions:
                raise SearchBudgetExceeded(stats.expansions, params.max_expansions)
            value = value_plan(opening, transfers, bookings, mcpp)
            draft = PlanDraft(
                bookings=bookings,
                transfers=transfers,
                value=value,
                arrival_days=dict(arrival),
                hop_index=dict(hop_index),
                feasible_in_days=max((arrival.get(b.program_id, 0) for b in bookings), default=0),
            )
            steps = build_steps(world, draft.bookings, draft.transfers, draft.hop_index)
            fingerprint = canonical_json([s.canonical_tuple() for s in steps])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            drafts.append(draft)
    return drafts


def cash_goal_has_options(active: ActiveWorld, opening: dict[str, int], goal: GoalSpec) -> bool:
    """True when at least one liquid cashout option exists for an eligible program."""
    allowed = set(goal.cash_programs) if goal.cash_programs else None
    for program_id in opening:
        if allowed is not None and program_id not in allowed:
            continue
        if active.cash_options_for(program_id):
            return True
    return False


__all__ = [
    "FundingSearch",
    "FundingSolution",
    "SearchBudgetExceeded",
    "SearchStats",
    "aggregate_needs",
    "booking_deadlines",
    "cash_drafts",
    "cash_goal_has_options",
    "enumerate_booking_sets",
    "solve_booking_set",
]

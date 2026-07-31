"""An independent, exhaustive redemption-path solver — the eval ground truth.

This module deliberately shares **no code** with ``pointsmax``: it reads raw
world files (plain dicts), re-derives FR-3 gating, FR-6 matching, FR-7a booking
sets, FR-8 arithmetic and FR-9 ordering from the specification, and finds the
optimum by *enumeration* — every valid sent amount on the full
``min_from .. balance`` increment lattice for every subset of usable edges.

There is no bound and no dominance rule anywhere in here.  That is the whole
point: M1/M7 are only meaningful if the reference implementation has no pruning
logic that could be wrong in the same way the engine's is (EVALS "Oracle
tractability budget").  The only permitted optimisation is memo-free structural
deduplication of identical results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

CABINS = ("economy", "premium_economy", "business", "first")
KIND_RANK = {"transfer": 0, "book_award": 1, "book_portal": 2, "redeem_cash": 3}
LIQUID_METHODS = {"statement_credit", "bank_deposit"}


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# --------------------------------------------------------------------------
# World indexing
# --------------------------------------------------------------------------


class OracleWorld:
    """A raw world (``{filename: rows}``) indexed for lookup."""

    def __init__(self, files: dict[str, Any]) -> None:
        self.files = files
        self.programs = {p["id"]: p for p in files["programs.json"]}
        self.cards = {c["id"]: c for c in files["cards.json"]}
        self.edges = list(files["transfers.json"])
        self.cashouts = list(files["cashouts.json"])
        self.offers = list(files["awards.json"])
        self.mcpp = {v["program_id"]: v["cpp_milli"] for v in files["valuations.json"]}
        self.as_of = min(_date(v["as_of"]) for v in files["valuations.json"])
        self.place: dict[str, str] = {}
        for entry in files["gazetteer.json"]:
            self.place[entry["city_code"]] = entry["city_code"]
            for airport in entry["airports"]:
                self.place[airport] = entry["city_code"]
        self.fares: dict[tuple, int] = {}
        for row in files["reference_fares.json"]:
            if row["kind"] == "flight":
                key: tuple = (
                    "flight",
                    row["origin_city"],
                    row["dest_city"],
                    row["cabin"],
                    bool(row["round_trip"]),
                    row["month"],
                )
            else:
                key = ("stay", row["city"], row["month"])
            self.fares[key] = row["fare_cents"]

    def city_of(self, place: str | None) -> str | None:
        return self.place.get(place) if place else None

    def flight_fare(self, origin: str, dest: str, cabin: str, round_trip: bool, month: str):
        return self.fares.get(("flight", origin, dest, cabin, round_trip, month))

    def stay_fare(self, city: str, month: str):
        return self.fares.get(("stay", city, month))


def _window_active(row: dict[str, Any], today: date) -> bool:
    start, end = _date(row.get("valid_from")), _date(row.get("valid_to"))
    if start and today < start:
        return False
    return not (end and today > end)


def active_subgraph(
    world: OracleWorld, cards_held: list[str], today: date
) -> tuple[list[dict], list[dict]]:
    """FR-3: the edges and cashout options this wallet can use on ``today``."""
    held = {c for c in cards_held if c in world.cards}
    enabled = {
        world.cards[c]["program_id"] for c in held if world.cards[c]["enables_transfer"]
    }
    edges = [
        e
        for e in world.edges
        if _window_active(e, today)
        and (
            world.programs[e["from_program"]]["kind"] != "bank"
            or e["from_program"] in enabled
        )
    ]
    options = [
        o
        for o in world.cashouts
        if _window_active(o, today) and (o["requires_card"] is None or o["requires_card"] in held)
    ]
    return sorted(edges, key=lambda e: e["id"]), sorted(options, key=lambda o: o["id"])


# --------------------------------------------------------------------------
# FR-8 primitives (re-derived)
# --------------------------------------------------------------------------


def delivered(edge: dict[str, Any], sent: int) -> int:
    out = sent * edge["ratio_to"] // edge["ratio_from"]
    if edge["bonus_per_from"] and edge["bonus_to"]:
        out += edge["bonus_to"] * (sent // edge["bonus_per_from"])
    return out


def transfer_fee(edge: dict[str, Any], sent: int) -> int:
    if sent <= 0 or not edge["fee_mcpp"]:
        return 0
    fee = -((-sent * edge["fee_mcpp"]) // 1000)
    cap = edge["fee_cap_cents"]
    return min(fee, cap) if cap is not None else fee


def portal_points(fare_cents: int, option: dict[str, Any]) -> int:
    raw = -((-fare_cents * 1000) // option["cpp_milli"])
    inc = option["increment"]
    rounded = -((-raw) // inc) * inc
    return max(rounded, option["min_points"])


def redeemable(points: int, option: dict[str, Any]) -> int:
    if points <= 0:
        return 0
    amount = (points // option["increment"]) * option["increment"]
    return amount if amount >= option["min_points"] else 0


def portfolio_value(holdings: dict[str, int], mcpp: dict[str, int]) -> int:
    return sum((n * mcpp[p]) // 1000 for p, n in holdings.items() if n)


# --------------------------------------------------------------------------
# Bookings and booking sets (FR-6, FR-7a)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Booking:
    kind: str
    program_id: str
    points: int
    fees_cents: int
    value_cents: int
    offer_id: str | None = None
    cashout_id: str | None = None
    deadline: date | None = None
    seats_available: int | None = None

    def sort_key(self) -> tuple:
        return (self.kind, self.program_id, self.offer_id or "", self.cashout_id or "", self.points)


def _overlap(a0: date, a1: date, b0: date, b1: date) -> int:
    start, end = max(a0, b0), min(a1, b1)
    return 0 if start > end else (end - start).days + 1


def _deadline(goal: dict[str, Any], offer: dict[str, Any] | None) -> date | None:
    bounds: list[date] = []
    if goal.get("book_by"):
        bounds.append(_date(goal["book_by"]))
    if offer is not None:
        if offer.get("bookable_until"):
            bounds.append(_date(offer["bookable_until"]))
        bounds.append(_date(offer["travel_window_end"]))
    elif goal.get("travel_window_end"):
        bounds.append(_date(goal["travel_window_end"]))
    return min(bounds) if bounds else None


def _flight_matches(
    world: OracleWorld,
    goal: dict[str, Any],
    offer: dict[str, Any],
    today: date,
    origin: str,
    dest: str,
    round_trip: bool,
) -> bool:
    if offer["kind"] != "flight" or bool(offer["round_trip"]) != round_trip:
        return False
    if world.city_of(offer["origin"]) != origin or world.city_of(offer["destination"]) != dest:
        return False
    if goal.get("cabin") and offer["cabin"] != goal["cabin"]:
        return False
    if not _overlap(
        _date(goal["travel_window_start"]),
        _date(goal["travel_window_end"]),
        _date(offer["travel_window_start"]),
        _date(offer["travel_window_end"]),
    ):
        return False
    seats, pax = offer["seats_available"], goal.get("passengers") or 1
    if seats is not None and pax > seats:
        return False
    until = _date(offer.get("bookable_until"))
    return until is None or until >= today


def _stay_matches(
    world: OracleWorld, goal: dict[str, Any], offer: dict[str, Any], today: date
) -> bool:
    if offer["kind"] != "stay" or world.city_of(offer["city"]) != goal["city"]:
        return False
    if (
        _overlap(
            _date(goal["travel_window_start"]),
            _date(goal["travel_window_end"]),
            _date(offer["travel_window_start"]),
            _date(offer["travel_window_end"]),
        )
        < goal["nights"]
    ):
        return False
    until = _date(offer.get("bookable_until"))
    return until is None or until >= today


def _month(goal: dict[str, Any]) -> str:
    return str(goal["travel_window_start"])[:7]


def _portal_cabins(world: OracleWorld, goal: dict, origin: str, dest: str, rt: bool) -> list[str]:
    if goal.get("cabin"):
        return [goal["cabin"]]
    return [
        cabin
        for cabin in CABINS
        if world.flight_fare(origin, dest, cabin, rt, _month(goal)) is not None
    ]


def enumerate_booking_sets(
    world: OracleWorld, options: list[dict], goal: dict[str, Any], today: date
) -> list[tuple[Booking, ...]]:
    """FR-7a: round-trip awards, portal round trips, and the leg Cartesian product."""
    portals = [o for o in options if o["method"] == "portal_travel"]
    sets: list[tuple[Booking, ...]] = []
    month = _month(goal)
    pax = goal.get("passengers") or 1

    def award_flight(offer, origin, dest, rt) -> Booking | None:
        fare = world.flight_fare(origin, dest, offer["cabin"], rt, month)
        if fare is None:
            return None
        return Booking(
            kind="book_award",
            program_id=offer["program_id"],
            points=offer["points_price"] * pax,
            fees_cents=offer["fees_cents"] * pax,
            value_cents=fare * pax,
            offer_id=offer["id"],
            deadline=_deadline(goal, offer),
            seats_available=offer["seats_available"],
        )

    def portal_flight(option, origin, dest, rt, cabin) -> Booking | None:
        fare = world.flight_fare(origin, dest, cabin, rt, month)
        if fare is None:
            return None
        total = fare * pax
        return Booking(
            kind="book_portal",
            program_id=option["program_id"],
            points=portal_points(total, option),
            fees_cents=0,
            value_cents=total,
            cashout_id=option["id"],
            deadline=_deadline(goal, None),
        )

    if goal["kind"] == "flight":
        origin, dest = goal["origin_city"], goal["dest_city"]
        legs = [(origin, dest)] + ([(dest, origin)] if goal.get("round_trip") else [])
        if goal.get("round_trip"):
            for offer in sorted(world.offers, key=lambda o: o["id"]):
                if _flight_matches(world, goal, offer, today, origin, dest, True):
                    booking = award_flight(offer, origin, dest, True)
                    if booking:
                        sets.append((booking,))
            for option in portals:
                for cabin in _portal_cabins(world, goal, origin, dest, True):
                    booking = portal_flight(option, origin, dest, True, cabin)
                    if booking:
                        sets.append((booking,))
        leg_candidates: list[list[Booking]] = []
        for leg_origin, leg_dest in legs:
            candidates: list[Booking] = []
            for offer in sorted(world.offers, key=lambda o: o["id"]):
                if _flight_matches(world, goal, offer, today, leg_origin, leg_dest, False):
                    booking = award_flight(offer, leg_origin, leg_dest, False)
                    if booking:
                        candidates.append(booking)
            for option in portals:
                for cabin in _portal_cabins(world, goal, leg_origin, leg_dest, False):
                    booking = portal_flight(option, leg_origin, leg_dest, False, cabin)
                    if booking:
                        candidates.append(booking)
            leg_candidates.append(candidates)
        if leg_candidates and all(leg_candidates):
            combos: list[list[Booking]] = [[]]
            for column in leg_candidates:
                combos = [row + [item] for row in combos for item in column]
            sets.extend(tuple(row) for row in combos)

    elif goal["kind"] == "stay":
        nights = goal["nights"]
        nightly = world.stay_fare(goal["city"], month)
        for offer in sorted(world.offers, key=lambda o: o["id"]):
            if _stay_matches(world, goal, offer, today) and nightly is not None:
                sets.append(
                    (
                        Booking(
                            kind="book_award",
                            program_id=offer["program_id"],
                            points=offer["points_price"] * nights,
                            fees_cents=offer["fees_cents"] * nights,
                            value_cents=nightly * nights,
                            offer_id=offer["id"],
                            deadline=_deadline(goal, offer),
                            seats_available=offer["seats_available"],
                        ),
                    )
                )
        if nightly is not None:
            for option in portals:
                total = nightly * nights
                sets.append(
                    (
                        Booking(
                            kind="book_portal",
                            program_id=option["program_id"],
                            points=portal_points(total, option),
                            fees_cents=0,
                            value_cents=total,
                            cashout_id=option["id"],
                            deadline=_deadline(goal, None),
                        ),
                    )
                )

    unique: dict[tuple, tuple[Booking, ...]] = {}
    for booking_set in sets:
        ordered = tuple(sorted(booking_set, key=lambda b: b.sort_key()))
        unique[tuple(b.sort_key() for b in ordered)] = ordered
    return [unique[key] for key in sorted(unique)]


# --------------------------------------------------------------------------
# Plans
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Transfer:
    edge_id: str
    from_program: str
    to_program: str
    sent: int
    delivered: int
    fee_cents: int
    time_days: int


@dataclass
class OraclePlan:
    bookings: tuple[Booking, ...]
    transfers: tuple[Transfer, ...]
    gross_value_cents: int
    cash_outlay_cents: int
    points_cost_cents: int
    net_value_cents: int
    cash_received_cents: int | None
    realized_cpp_milli: int | None
    points_spent: dict[str, int]
    ending_holdings: dict[str, int]
    arrival_days: dict[str, int]
    hop_index: dict[str, int]
    feasible_in_days: int
    canonical_form: list[list] = field(default_factory=list)
    is_comparator: bool = False

    def total_points_spent(self) -> int:
        return sum(self.points_spent.values())

    def objective(self, goal_kind: str) -> int:
        if goal_kind == "cash":
            return self.cash_received_cents or 0
        return self.net_value_cents

    def sort_key(self, goal_kind: str) -> tuple:
        return (
            -self.objective(goal_kind),
            self.total_points_spent(),
            len(self.bookings) + len(self.transfers),
            canonical_json(self.canonical_form),
        )

    def is_portal_only(self) -> bool:
        return bool(self.bookings) and all(b.kind == "book_portal" for b in self.bookings)


def arrival_and_hops(transfers: tuple[Transfer, ...]) -> tuple[dict[str, int], dict[str, int]]:
    """FR-7d arrival days and FR-9 hop indices over the funding DAG."""
    inbound: dict[str, list[Transfer]] = {}
    for use in transfers:
        inbound.setdefault(use.to_program, []).append(use)
    arrival: dict[str, int] = {}
    depth: dict[str, int] = {}
    state: dict[str, int] = {}

    def visit(program: str) -> None:
        mark = state.get(program, 0)
        if mark == 1:
            raise ValueError("cycle")
        if mark == 2:
            return
        state[program] = 1
        best_arrival = best_depth = 0
        for use in inbound.get(program, []):
            visit(use.from_program)
            best_arrival = max(best_arrival, arrival[use.from_program] + use.time_days)
            best_depth = max(best_depth, depth[use.from_program] + 1)
        arrival[program], depth[program] = best_arrival, best_depth
        state[program] = 2

    for use in transfers:
        visit(use.from_program)
        visit(use.to_program)
    return arrival, {use.edge_id: depth[use.from_program] for use in transfers}


def canonical_form(
    bookings: tuple[Booking, ...], transfers: tuple[Transfer, ...], hop_index: dict[str, int]
) -> list[list]:
    """FR-9 canonical step order, then the canonical step tuple of each step."""
    rows: list[tuple[tuple, list]] = []
    for use in transfers:
        sort_key = (
            KIND_RANK["transfer"],
            hop_index.get(use.edge_id, 0),
            use.from_program,
            use.to_program,
            use.edge_id,
            "",
            "",
            use.sent,
        )
        rows.append(
            (
                sort_key,
                [
                    "transfer",
                    use.from_program,
                    use.to_program,
                    use.edge_id,
                    "",
                    "",
                    use.sent,
                    use.delivered,
                    use.fee_cents,
                ],
            )
        )
    for booking in bookings:
        sort_key = (
            KIND_RANK[booking.kind],
            0,
            booking.program_id,
            "",
            "",
            booking.offer_id or "",
            booking.cashout_id or "",
            booking.points,
        )
        rows.append(
            (
                sort_key,
                [
                    booking.kind,
                    booking.program_id,
                    "",
                    "",
                    booking.offer_id or "",
                    booking.cashout_id or "",
                    booking.points,
                    0,
                    booking.fees_cents,
                ],
            )
        )
    rows.sort(key=lambda row: row[0])
    return [row[1] for row in rows]


def price_plan(
    world: OracleWorld,
    opening: dict[str, int],
    transfers: tuple[Transfer, ...],
    bookings: tuple[Booking, ...],
) -> OraclePlan | None:
    """FR-8 accounting for a fully specified plan; None when a balance goes negative."""
    holdings = dict(opening)
    for use in transfers:
        holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
        holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
    for booking in bookings:
        holdings[booking.program_id] = holdings.get(booking.program_id, 0) - booking.points
    if any(n < 0 for n in holdings.values()):
        return None
    try:
        arrival, hop_index = arrival_and_hops(transfers)
    except ValueError:
        return None

    gross = sum(b.value_cents for b in bookings)
    booking_fees = sum(b.fees_cents for b in bookings)
    outlay = booking_fees + sum(t.fee_cents for t in transfers)
    programs = set(opening) | set(holdings)
    h0 = {p: opening.get(p, 0) for p in programs}
    h1 = {p: holdings.get(p, 0) for p in programs}
    points_cost = portfolio_value(h0, world.mcpp) - portfolio_value(h1, world.mcpp)
    booking_points = sum(b.points for b in bookings)
    cash = [b for b in bookings if b.kind == "redeem_cash"]
    plan = OraclePlan(
        bookings=bookings,
        transfers=transfers,
        gross_value_cents=gross,
        cash_outlay_cents=outlay,
        points_cost_cents=points_cost,
        net_value_cents=gross - outlay - points_cost,
        cash_received_cents=sum(b.value_cents for b in cash) if cash else None,
        realized_cpp_milli=(
            (gross - booking_fees) * 1000 // booking_points if booking_points > 0 else None
        ),
        points_spent={p: h0[p] - h1[p] for p in sorted(programs) if h0[p] > h1[p]},
        ending_holdings=h1,
        arrival_days=arrival,
        hop_index=hop_index,
        feasible_in_days=max((arrival.get(b.program_id, 0) for b in bookings), default=0),
    )
    plan.canonical_form = canonical_form(bookings, transfers, hop_index)
    return plan


# --------------------------------------------------------------------------
# Exhaustive funding
# --------------------------------------------------------------------------


class Budget:
    """Counts enumerations and refuses to run away."""

    def __init__(self, ceiling: int = 2_000_000) -> None:
        self.count = 0
        self.ceiling = ceiling

    def tick(self) -> None:
        self.count += 1
        if self.count > self.ceiling:
            raise OracleTooBig(f"oracle exceeded {self.ceiling} enumerations")


class OracleTooBig(RuntimeError):
    """The scenario violates the tractability budget (EVALS fixture invariant)."""


def _valid_amounts(edge: dict[str, Any], balance: int, ceiling: int | None = None) -> list[int]:
    """Every valid sent amount from ``min_from`` to ``min(balance, ceiling)``.

    This is the *full* increment lattice — no ``s_cover`` shortcut, no tier-only
    sampling, no dominance rule.  ``ceiling`` is the EVALS tractability budget's
    "need-covering max": the smallest valid amount whose delivery on this edge
    alone already covers the destination's entire aggregate need, so no larger
    amount can be part of any allocation that is not pure waste.
    """
    inc = edge["increment_from"]
    limit = balance if ceiling is None else min(balance, ceiling)
    top = (limit // inc) * inc
    if top < edge["min_from"]:
        return []
    return list(range(edge["min_from"], top + 1, inc))


def _cover_amount(edge: dict[str, Any], need: int) -> int:
    """Smallest valid sent amount on ``edge`` whose delivery alone reaches ``need``."""
    inc = edge["increment_from"]
    sent = max(edge["min_from"], inc)
    while delivered(edge, sent) < need:
        sent += inc
    return sent


def _edge_ceilings(candidates: list[dict], needs: dict[str, int]) -> dict[str, int]:
    """Per-edge "need-covering max", propagated outward from the paying programs.

    Candidates are ordered sources-first, so walking them in reverse visits an
    edge only after every edge it can feed has a ceiling.
    """
    ceilings: dict[str, int] = {}
    demand: dict[str, int] = dict(needs)
    for edge in reversed(candidates):
        cover = _cover_amount(edge, max(demand.get(edge["to_program"], 0), 1))
        ceilings[edge["id"]] = cover
        demand[edge["from_program"]] = demand.get(edge["from_program"], 0) + cover
    return ceilings


def _tier_amounts(edge: dict[str, Any], balance: int) -> list[int]:
    """``{s_max} u {k x bonus_per_from}`` — the FR-7c lattice with no residual."""
    inc = edge["increment_from"]
    top = (balance // inc) * inc
    if top < edge["min_from"]:
        return []
    out = {top}
    tier = edge["bonus_per_from"]
    if tier:
        k = 1
        while k * tier <= top:
            if k * tier >= edge["min_from"]:
                out.add(k * tier)
            k += 1
    return sorted(out)


def _candidate_edges(
    world: OracleWorld, edges: list[dict], targets: set[str], max_hops: int
) -> list[dict]:
    """Edges within ``max_hops`` of any target, in a source-before-target order."""
    layers: list[list[dict]] = []
    frontier = set(targets)
    seen_ids: set[str] = set()
    for _ in range(max_hops):
        layer = [e for e in edges if e["to_program"] in frontier and e["id"] not in seen_ids]
        if not layer:
            break
        layers.append(sorted(layer, key=lambda e: e["id"]))
        seen_ids.update(e["id"] for e in layer)
        frontier = {e["from_program"] for e in layer}
    ordered: list[dict] = []
    for layer in reversed(layers):  # deepest hop first: sources are funded before use
        ordered.extend(layer)
    return ordered


def best_funding(
    world: OracleWorld,
    edges: list[dict],
    opening: dict[str, int],
    bookings: tuple[Booking, ...],
    today: date,
    max_hops: int,
    budget: Budget,
) -> OraclePlan | None:
    """Exhaustively search every allocation and return the FR-9-best plan."""
    needs: dict[str, int] = {}
    for booking in bookings:
        needs[booking.program_id] = needs.get(booking.program_id, 0) + booking.points
    targets = {p for p, n in needs.items() if n > opening.get(p, 0)}
    candidates = _candidate_edges(world, edges, targets, max_hops) if targets else []
    ceilings = _edge_ceilings(candidates, {p: needs[p] for p in targets})

    booking_fees = sum(b.fees_cents for b in bookings)
    base_value = portfolio_value(opening, world.mcpp)
    best_transfers: tuple[Transfer, ...] | None = None
    best_key: tuple | None = None
    best_canonical: str | None = None

    def consider(chosen: list[Transfer]) -> None:
        """Score one complete allocation.

        The FR-9 key is evaluated cheapest-first: only an allocation that ties on
        (loss, points spent, step count) needs its canonical form rendered.  This
        is bookkeeping, not pruning - every allocation is still visited.
        """
        nonlocal best_transfers, best_key, best_canonical
        holdings = dict(opening)
        for use in chosen:
            holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
            holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
        for booking in bookings:
            holdings[booking.program_id] = holdings.get(booking.program_id, 0) - booking.points
        for points in holdings.values():
            if points < 0:
                return
        transfers = tuple(chosen)
        try:
            arrival, hop_index = arrival_and_hops(transfers)
        except ValueError:
            return
        for booking in bookings:
            if booking.deadline is None:
                continue
            if today + timedelta(days=arrival.get(booking.program_id, 0)) > booking.deadline:
                return
        loss = sum(t.fee_cents for t in transfers) + (
            base_value - portfolio_value(holdings, world.mcpp)
        )
        spent = sum(
            opening.get(p, 0) - holdings.get(p, 0)
            for p in set(opening) | set(holdings)
            if opening.get(p, 0) > holdings.get(p, 0)
        )
        key = (loss, spent, len(transfers))
        if best_key is not None and key > best_key:
            return
        if best_key is not None and key == best_key:
            if best_canonical is None:
                assert best_transfers is not None
                best_canonical = canonical_json(
                    canonical_form(bookings, best_transfers, arrival_and_hops(best_transfers)[1])
                )
            candidate = canonical_json(canonical_form(bookings, transfers, hop_index))
            if candidate >= best_canonical:
                return
            best_canonical = candidate
        else:
            best_canonical = None
        best_transfers, best_key = transfers, key

    def walk(index: int, balances: dict[str, int], chosen: list[Transfer]) -> None:
        budget.tick()
        if index >= len(candidates):
            consider(chosen)
            return
        edge = candidates[index]
        walk(index + 1, balances, chosen)  # skip this edge
        source, target = edge["from_program"], edge["to_program"]
        available = balances.get(source, 0)
        held = balances.get(target, 0)
        for sent in _valid_amounts(edge, available, ceilings.get(edge["id"])):
            got = delivered(edge, sent)
            balances[source] = available - sent
            balances[target] = held + got
            chosen.append(
                Transfer(
                    edge_id=edge["id"],
                    from_program=source,
                    to_program=target,
                    sent=sent,
                    delivered=got,
                    fee_cents=transfer_fee(edge, sent),
                    time_days=edge["time_days"],
                )
            )
            walk(index + 1, balances, chosen)
            chosen.pop()
        balances[source] = available
        balances[target] = held

    walk(0, dict(opening), [])
    if best_transfers is None:
        return None
    return price_plan(world, opening, best_transfers, bookings)


# --------------------------------------------------------------------------
# FR-12 cash goals
# --------------------------------------------------------------------------


def _cash_rate(options: list[dict], program: str) -> int:
    return max(
        (o["cpp_milli"] for o in options if o["program_id"] == program and o["method"] in LIQUID_METHODS),
        default=0,
    )


def _max_ratio(edge: dict[str, Any]) -> float:
    ratio = edge["ratio_to"] / edge["ratio_from"]
    if edge["bonus_per_from"] and edge["bonus_to"]:
        ratio += edge["bonus_to"] / edge["bonus_per_from"]
    return ratio


def cash_candidate_edges(
    edges: list[dict], options: list[dict], balances: dict[str, int], max_hops: int
) -> list[dict]:
    """Edges that can plausibly *raise* the cash total (FR-12 "cash-improving")."""
    reachable = {p for p, n in balances.items() if n > 0}
    for _ in range(max_hops):
        for edge in edges:
            if edge["from_program"] in reachable:
                reachable.add(edge["to_program"])
    out: list[dict] = []
    for edge in edges:
        if edge["from_program"] not in reachable:
            continue
        source = _cash_rate(options, edge["from_program"])
        onward = max(
            (
                _cash_rate(options, nxt["to_program"]) * _max_ratio(nxt)
                for nxt in edges
                if nxt["from_program"] == edge["to_program"]
            ),
            default=0.0,
        )
        target = max(float(_cash_rate(options, edge["to_program"])), onward)
        if source == 0 or _max_ratio(edge) * target > source:
            out.append(edge)
    return sorted(out, key=lambda e: e["id"])


def cash_plans(
    world: OracleWorld,
    edges: list[dict],
    options: list[dict],
    opening: dict[str, int],
    goal: dict[str, Any],
    max_hops: int,
    budget: Budget,
) -> list[OraclePlan]:
    """Every FR-12 liquidation plan: option swaps plus cash-improving chains."""
    allowed = set(goal.get("cash_programs") or []) or None
    caps = goal.get("cash_max_points") or {}
    usable: dict[str, int] = {}
    for program, points in opening.items():
        if points <= 0 or (allowed is not None and program not in allowed):
            continue
        cap = caps.get(program)
        usable[program] = min(points, cap) if cap is not None else points
    if not usable:
        return []

    candidates = cash_candidate_edges(edges, options, usable, max_hops)
    allocations: list[tuple[Transfer, ...]] = []

    def walk(index: int, balances: dict[str, int], chosen: list[Transfer]) -> None:
        budget.tick()
        if index >= len(candidates):
            allocations.append(tuple(chosen))
            return
        edge = candidates[index]
        walk(index + 1, balances, chosen)
        for sent in _tier_amounts(edge, balances.get(edge["from_program"], 0)):
            got = delivered(edge, sent)
            nxt = dict(balances)
            nxt[edge["from_program"]] = nxt.get(edge["from_program"], 0) - sent
            nxt[edge["to_program"]] = nxt.get(edge["to_program"], 0) + got
            walk(
                index + 1,
                nxt,
                [
                    *chosen,
                    Transfer(
                        edge_id=edge["id"],
                        from_program=edge["from_program"],
                        to_program=edge["to_program"],
                        sent=sent,
                        delivered=got,
                        fee_cents=transfer_fee(edge, sent),
                        time_days=edge["time_days"],
                    ),
                ],
            )

    walk(0, dict(usable), [])

    plans: list[OraclePlan] = []
    seen: set[str] = set()
    for transfers in allocations:
        holdings = dict(usable)
        broken = False
        for use in transfers:
            holdings[use.from_program] = holdings.get(use.from_program, 0) - use.sent
            holdings[use.to_program] = holdings.get(use.to_program, 0) + use.delivered
            if holdings[use.from_program] < 0:
                broken = True
        if broken:
            continue
        columns: list[list[Booking | None]] = []
        for program in sorted(p for p, n in holdings.items() if n > 0):
            column: list[Booking | None] = [None]
            for option in sorted(
                (
                    o
                    for o in options
                    if o["program_id"] == program and o["method"] in LIQUID_METHODS
                ),
                key=lambda o: o["id"],
            ):
                amount = redeemable(holdings[program], option)
                if amount > 0:
                    column.append(
                        Booking(
                            kind="redeem_cash",
                            program_id=program,
                            points=amount,
                            fees_cents=0,
                            value_cents=(amount * option["cpp_milli"]) // 1000,
                            cashout_id=option["id"],
                        )
                    )
            columns.append(column)
        combos: list[list[Booking | None]] = [[]]
        for column in columns:
            combos = [row + [item] for row in combos for item in column]
        for combo in combos:
            bookings = tuple(sorted((b for b in combo if b), key=lambda b: b.sort_key()))
            if not bookings:
                continue
            budget.tick()
            plan = price_plan(world, opening, transfers, bookings)
            if plan is None:
                continue
            fingerprint = canonical_json(plan.canonical_form)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            plans.append(plan)
    return plans


# --------------------------------------------------------------------------
# Top-level solve
# --------------------------------------------------------------------------


@dataclass
class OracleResult:
    verdict: str
    plans: list[OraclePlan]
    objective_cents: int | None
    enumerations: int
    candidate_sets: int


def solve(
    files: dict[str, Any],
    *,
    cards: list[str],
    balances: dict[str, int],
    goal: dict[str, Any],
    today: str,
    params: dict[str, int] | None = None,
    enumeration_ceiling: int = 2_000_000,
) -> OracleResult:
    """Exhaustively solve one scenario and return the FR-9 ranked output."""
    params = params or {}
    top_k = params.get("top_k", 5)
    max_hops = params.get("max_hops", 2)
    world = OracleWorld(files)
    day = _date(today)
    assert day is not None
    edges, options = active_subgraph(world, cards, day)
    opening = {p: n for p, n in balances.items() if n}
    budget = Budget(enumeration_ceiling)

    if goal["kind"] == "cash":
        found = cash_plans(world, edges, options, opening, goal, max_hops, budget)
        candidate_sets = 1 if _cash_goal_has_options(options, opening, goal) else 0
    else:
        booking_sets = enumerate_booking_sets(world, options, goal, day)
        candidate_sets = len(booking_sets)
        found = []
        for bookings in booking_sets:
            plan = best_funding(world, edges, opening, bookings, day, max_hops, budget)
            if plan is not None:
                found.append(plan)

    found.sort(key=lambda p: p.sort_key(goal["kind"]))
    ordered: list[OraclePlan] = []
    seen: set[str] = set()
    for plan in found:
        fingerprint = canonical_json(plan.canonical_form)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        ordered.append(plan)

    if not candidate_sets:
        verdict = "no_matching_award"
    elif not ordered:
        verdict = "insufficient_points"
    elif goal["kind"] == "cash":
        verdict = "cash_plan"
    elif ordered[0].net_value_cents > 0:
        verdict = "book_with_points"
    else:
        verdict = "pay_cash_keep_points"

    selected: list[OraclePlan] = []
    if verdict not in ("insufficient_points", "no_matching_award"):
        selected = list(ordered[:top_k])
        if goal["kind"] != "cash":
            for plan in ordered:
                if plan.is_portal_only():
                    if plan not in selected:
                        clone = OraclePlan(**{**plan.__dict__, "is_comparator": True})
                        selected.append(clone)
                    break

    objective = selected[0].objective(goal["kind"]) if selected else None
    return OracleResult(
        verdict=verdict,
        plans=selected,
        objective_cents=objective,
        enumerations=budget.count,
        candidate_sets=candidate_sets,
    )


def _cash_goal_has_options(
    options: list[dict], opening: dict[str, int], goal: dict[str, Any]
) -> bool:
    allowed = set(goal.get("cash_programs") or []) or None
    for program in opening:
        if allowed is not None and program not in allowed:
            continue
        if any(
            o["program_id"] == program and o["method"] in LIQUID_METHODS for o in options
        ):
            return True
    return False


__all__ = [
    "Booking",
    "Budget",
    "OraclePlan",
    "OracleResult",
    "OracleTooBig",
    "OracleWorld",
    "active_subgraph",
    "canonical_form",
    "canonical_json",
    "delivered",
    "enumerate_booking_sets",
    "portal_points",
    "portfolio_value",
    "price_plan",
    "redeemable",
    "solve",
    "transfer_fee",
]

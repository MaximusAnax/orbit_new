"""World validation, content hashing and card gating (FR-1, FR-3).

Everything here is a pure function of its inputs.  ``CommittedWorldProvider``
does the file reading; this module only ever sees parsed data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..models import (
    CashoutOption,
    OfferKind,
    ProgramKind,
    TransferEdge,
    World,
    canonical_bytes,
)

#: World files covered by the content hash, in ascending filename order.
#: ``version.json`` is excluded — it *carries* the hash (DATA_MODEL D9).
HASHED_WORLD_FILES: tuple[str, ...] = (
    "awards.json",
    "cards.json",
    "cashouts.json",
    "gazetteer.json",
    "programs.json",
    "reference_fares.json",
    "transfers.json",
    "valuations.json",
)


class WorldValidationError(Exception):
    """Raised when a world fails FR-1 validation.  Carries every named issue."""

    def __init__(self, issues: list[WorldIssue]) -> None:
        self.issues = issues
        detail = "; ".join(f"[{i.code}] {i.message}" for i in issues)
        super().__init__(f"world failed validation ({len(issues)} issue(s)): {detail}")


@dataclass(frozen=True)
class WorldIssue:
    """A single named, actionable validation failure."""

    code: str
    message: str


def compute_content_hash(files: Mapping[str, Any]) -> str:
    """sha256 over ``filename + "\\n" + canonical_json(contents) + "\\n"`` per file.

    Files are concatenated in ascending filename order; ``version.json`` must not
    be present.  Lists keep their file order, so the hash is stable across
    formatting-only edits and changes on any data edit (DATA_MODEL).
    """
    if "version.json" in files:
        raise ValueError("version.json must be excluded from the content hash")
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\n")
        digest.update(canonical_bytes(files[name]))
        digest.update(b"\n")
    return digest.hexdigest()


# --------------------------------------------------------------------------
# FR-1 validation
# --------------------------------------------------------------------------


def months_touched(start: date, end: date) -> list[str]:
    """Every ``YYYY-MM`` between ``start`` and ``end`` inclusive."""
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return out


def _duplicates(ids: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for value in ids:
        if value in seen:
            dupes.add(value)
        seen.add(value)
    return sorted(dupes)


def validate_world(world: World, *, computed_hash: str | None = None) -> list[WorldIssue]:
    """Run every FR-1 invariant and return the issues found (empty when valid)."""
    issues: list[WorldIssue] = []
    add = issues.append

    # -- invariant 1: unique ids, referential integrity ---------------------
    for label, ids in (
        ("program", [p.id for p in world.programs]),
        ("card", [c.id for c in world.cards]),
        ("transfer_edge", [e.id for e in world.edges]),
        ("cashout_option", [o.id for o in world.cashouts]),
        ("award_offer", [o.id for o in world.offers]),
        ("reference_fare", [f.id for f in world.reference_fares]),
        ("gazetteer_city", [g.city_code for g in world.gazetteer]),
    ):
        for dupe in _duplicates(ids):
            add(WorldIssue("duplicate_id", f"duplicate {label} id {dupe!r}"))

    program_ids = {p.id for p in world.programs}
    card_ids = {c.id for c in world.cards}

    for card in world.cards:
        if card.program_id not in program_ids:
            add(
                WorldIssue(
                    "unknown_program",
                    f"card {card.id!r} references unknown program {card.program_id!r}",
                )
            )
        elif world.program(card.program_id).kind is not ProgramKind.BANK:
            add(
                WorldIssue(
                    "card_program_not_bank",
                    f"card {card.id!r} must hold a bank currency, not "
                    f"{world.program(card.program_id).kind}",
                )
            )

    edge_keys: list[str] = []
    for edge in world.edges:
        for field, value in (("from_program", edge.from_program), ("to_program", edge.to_program)):
            if value not in program_ids:
                add(
                    WorldIssue(
                        "unknown_program",
                        f"transfer edge {edge.id!r} references unknown program "
                        f"{value!r} in {field}",
                    )
                )
        edge_keys.append(f"{edge.from_program}->{edge.to_program}@{edge.valid_from}")
        # -- invariant 2: divisibility -------------------------------------
        if edge.increment_from % edge.ratio_from != 0:
            add(
                WorldIssue(
                    "increment_not_divisible",
                    f"transfer edge {edge.id!r}: increment_from {edge.increment_from} "
                    f"is not a multiple of ratio_from {edge.ratio_from}",
                )
            )
        if edge.min_from % edge.increment_from != 0:
            add(
                WorldIssue(
                    "min_not_multiple_of_increment",
                    f"transfer edge {edge.id!r}: min_from {edge.min_from} is not a "
                    f"multiple of increment_from {edge.increment_from}",
                )
            )
        if edge.bonus_per_from is not None and edge.bonus_per_from % edge.increment_from != 0:
            add(
                WorldIssue(
                    "bonus_tier_not_multiple_of_increment",
                    f"transfer edge {edge.id!r}: bonus_per_from {edge.bonus_per_from} is "
                    f"not a multiple of increment_from {edge.increment_from}",
                )
            )

    for dupe in _duplicates(edge_keys):
        add(
            WorldIssue(
                "duplicate_edge",
                f"more than one transfer edge for (from_program, to_program, valid_from) {dupe}",
            )
        )

    for option in world.cashouts:
        if option.program_id not in program_ids:
            add(
                WorldIssue(
                    "unknown_program",
                    f"cashout option {option.id!r} references unknown program "
                    f"{option.program_id!r}",
                )
            )
        if option.requires_card is not None and option.requires_card not in card_ids:
            add(
                WorldIssue(
                    "unknown_card",
                    f"cashout option {option.id!r} requires unknown card {option.requires_card!r}",
                )
            )

    # -- invariant 3: exactly one valuation per program ---------------------
    valued: dict[str, int] = {}
    for valuation in world.valuations:
        if valuation.program_id not in program_ids:
            add(
                WorldIssue(
                    "unknown_program",
                    f"valuation references unknown program {valuation.program_id!r}",
                )
            )
        valued[valuation.program_id] = valued.get(valuation.program_id, 0) + 1
    for program in world.programs:
        count = valued.get(program.id, 0)
        if count != 1:
            add(
                WorldIssue(
                    "valuation_cardinality",
                    f"program {program.id!r} has {count} valuations, expected exactly 1",
                )
            )

    # -- invariant 4: no value-increasing transfer edge ---------------------
    for edge in world.edges:
        if edge.from_program not in valued or edge.to_program not in valued:
            continue
        mcpp_from = world.mcpp(edge.from_program)
        mcpp_to = world.mcpp(edge.to_program)
        if edge.bonus_per_from is None or edge.bonus_to is None:
            lhs = edge.ratio_to * mcpp_to
            rhs = edge.ratio_from * mcpp_from
        else:
            lhs = (edge.ratio_to * edge.bonus_per_from + edge.ratio_from * edge.bonus_to) * mcpp_to
            rhs = edge.ratio_from * edge.bonus_per_from * mcpp_from
        if lhs > rhs:
            add(
                WorldIssue(
                    "value_increasing_edge",
                    f"transfer edge {edge.id!r} manufactures value: delivered value "
                    f"{lhs} exceeds source value {rhs} (FR-1 invariant 4)",
                )
            )

    # -- invariant 6: offers resolve in the gazetteer, airports unique ------
    airport_owner: dict[str, str] = {}
    for entry in world.gazetteer:
        for airport in entry.airports:
            if airport in airport_owner and airport_owner[airport] != entry.city_code:
                add(
                    WorldIssue(
                        "duplicate_airport",
                        f"airport {airport!r} appears in both {airport_owner[airport]!r} "
                        f"and {entry.city_code!r}",
                    )
                )
            airport_owner.setdefault(airport, entry.city_code)
        if entry.city_code in airport_owner and airport_owner[entry.city_code] != entry.city_code:
            add(
                WorldIssue(
                    "duplicate_airport",
                    f"city code {entry.city_code!r} collides with an airport of "
                    f"{airport_owner[entry.city_code]!r}",
                )
            )

    flight_coverage: set[tuple[str, str, str, str]] = set()
    stay_coverage: set[tuple[str, str]] = set()
    for offer in world.offers:
        if offer.program_id not in program_ids:
            add(
                WorldIssue(
                    "unknown_program",
                    f"award offer {offer.id!r} references unknown program {offer.program_id!r}",
                )
            )
        places = [offer.origin, offer.destination, offer.city]
        resolved: list[str | None] = []
        for place in places:
            if place is None:
                resolved.append(None)
                continue
            city = world.city_of(place)
            if city is None:
                add(
                    WorldIssue(
                        "unresolved_place",
                        f"award offer {offer.id!r}: {place!r} does not resolve in the gazetteer",
                    )
                )
            resolved.append(city)
        origin_city, dest_city, stay_city = resolved
        months = months_touched(offer.travel_window_start, offer.travel_window_end)
        if offer.kind is OfferKind.FLIGHT and origin_city and dest_city and offer.cabin:
            for month in months:
                flight_coverage.add((origin_city, dest_city, str(offer.cabin), month))
        elif offer.kind is OfferKind.STAY and stay_city:
            for month in months:
                stay_coverage.add((stay_city, month))

    # -- invariant 5: reference-fare coverage -------------------------------
    from ..models import Cabin  # local import keeps the module's public surface small

    for origin_city, dest_city, cabin, month in sorted(flight_coverage):
        cabin_enum = Cabin(cabin)
        required = (
            (origin_city, dest_city, False),
            (dest_city, origin_city, False),
            (origin_city, dest_city, True),
        )
        for o_city, d_city, round_trip in required:
            fare = world.find_fare(
                OfferKind.FLIGHT,
                month=month,
                origin_city=o_city,
                dest_city=d_city,
                cabin=cabin_enum,
                round_trip=round_trip,
            )
            if fare is None:
                add(
                    WorldIssue(
                        "missing_reference_fare",
                        f"no reference fare for ({o_city}, {d_city}, {cabin}, "
                        f"round_trip={round_trip}, {month}) required by flight offers",
                    )
                )
    for city, month in sorted(stay_coverage):
        if world.find_fare(OfferKind.STAY, month=month, city=city) is None:
            add(
                WorldIssue(
                    "missing_reference_fare",
                    f"no stay reference fare for ({city}, {month}) required by stay offers",
                )
            )

    for fare in world.reference_fares:
        for place in (fare.origin_city, fare.dest_city, fare.city):
            if place is not None and world.city(place) is None:
                add(
                    WorldIssue(
                        "unresolved_place",
                        f"reference fare {fare.id!r}: unknown city code {place!r}",
                    )
                )

    # -- invariant 7: content hash -----------------------------------------
    if computed_hash is not None and computed_hash != world.version.content_hash:
        add(
            WorldIssue(
                "content_hash_mismatch",
                f"version.json content_hash {world.version.content_hash} does not match "
                f"the recomputed hash {computed_hash}",
            )
        )

    return issues


def assert_world_valid(world: World, *, computed_hash: str | None = None) -> None:
    """Raise :class:`WorldValidationError` when the world violates any FR-1 rule."""
    issues = validate_world(world, computed_hash=computed_hash)
    if issues:
        raise WorldValidationError(issues)


# --------------------------------------------------------------------------
# FR-3 gating
# --------------------------------------------------------------------------


def _window_active(valid_from: date | None, valid_to: date | None, today: date) -> bool:
    if valid_from is not None and today < valid_from:
        return False
    return not (valid_to is not None and today > valid_to)


@dataclass(frozen=True)
class ActiveWorld:
    """The transfer/redemption subgraph a given wallet can actually use today."""

    today: date
    cards: frozenset[str]
    edges: tuple[TransferEdge, ...]
    options: tuple[CashoutOption, ...]

    def edges_into(self, program_id: str) -> tuple[TransferEdge, ...]:
        return tuple(e for e in self.edges if e.to_program == program_id)

    def edges_from(self, program_id: str) -> tuple[TransferEdge, ...]:
        return tuple(e for e in self.edges if e.from_program == program_id)

    def options_for(self, program_id: str) -> tuple[CashoutOption, ...]:
        return tuple(o for o in self.options if o.program_id == program_id)

    def cash_options_for(self, program_id: str) -> tuple[CashoutOption, ...]:
        """Active *liquid* options only (FR-12)."""
        return tuple(o for o in self.options_for(program_id) if o.is_cash)

    def portal_options(self) -> tuple[CashoutOption, ...]:
        from ..models import CashoutMethod

        return tuple(o for o in self.options if o.method is CashoutMethod.PORTAL_TRAVEL)

    def has_edge(self, edge_id: str) -> bool:
        return any(e.id == edge_id for e in self.edges)

    def has_option(self, option_id: str) -> bool:
        return any(o.id == option_id for o in self.options)


def active_subgraph(world: World, cards_held: Iterable[str], today: date) -> ActiveWorld:
    """FR-3: the active edges and cashout options for ``cards_held`` on ``today``.

    * transfer edges out of a **bank** program need a held card on that program
      with ``enables_transfer``; edges out of airline/hotel programs (the
      "Marriott hub") need no card;
    * cashout options with ``requires_card`` need that exact card;
    * promo windows must contain ``today``.

    Pure function of (world, wallet, today).
    """
    held = frozenset(c for c in cards_held if world.has_card(c))
    transfer_enabled: set[str] = {
        world.card(card_id).program_id for card_id in held if world.card(card_id).enables_transfer
    }

    edges: list[TransferEdge] = []
    for edge in world.edges:
        if not _window_active(edge.valid_from, edge.valid_to, today):
            continue
        source = world.program(edge.from_program)
        if source.kind is ProgramKind.BANK and edge.from_program not in transfer_enabled:
            continue
        edges.append(edge)

    options: list[CashoutOption] = []
    for option in world.cashouts:
        if not _window_active(option.valid_from, option.valid_to, today):
            continue
        if option.requires_card is not None and option.requires_card not in held:
            continue
        options.append(option)

    edges.sort(key=lambda e: e.id)
    options.sort(key=lambda o: o.id)
    return ActiveWorld(today=today, cards=held, edges=tuple(edges), options=tuple(options))

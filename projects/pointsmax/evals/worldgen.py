"""Helpers for *building* fixture worlds (inputs, never ground truth).

Deliberately independent of ``pointsmax``: the canonical-JSON and content-hash
implementations below are re-derived from DATA_MODEL.md so that a fixture world
is a pure artefact of this file, not of the package under evaluation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: World files covered by the content hash, in ascending filename order.
HASHED_FILES: tuple[str, ...] = (
    "awards.json",
    "cards.json",
    "cashouts.json",
    "gazetteer.json",
    "programs.json",
    "reference_fares.json",
    "transfers.json",
    "valuations.json",
)

#: Realistic city vocabulary shared by every fixture world (synthetic economics,
#: real place names — so the FR-5 parser cases read like real utterances).
GAZETTEER: list[dict[str, Any]] = [
    {
        "city_code": "NYC",
        "name": "New York",
        "airports": ["JFK", "EWR", "LGA"],
        "aliases": ["nyc", "new york", "new york city"],
    },
    {"city_code": "PAR", "name": "Paris", "airports": ["CDG", "ORY"], "aliases": ["paris"]},
    {"city_code": "LON", "name": "London", "airports": ["LHR", "LGW"], "aliases": ["london"]},
    {"city_code": "TYO", "name": "Tokyo", "airports": ["HND", "NRT"], "aliases": ["tokyo"]},
    {"city_code": "CHI", "name": "Chicago", "airports": ["ORD", "MDW"], "aliases": ["chicago"]},
    {"city_code": "ROM", "name": "Rome", "airports": ["FCO"], "aliases": ["rome"]},
    {"city_code": "MAD", "name": "Madrid", "airports": ["MAD"], "aliases": ["madrid"]},
    {
        "city_code": "LAX",
        "name": "Los Angeles",
        "airports": ["LAX"],
        "aliases": ["los angeles"],
    },
    {
        "city_code": "SFO",
        "name": "San Francisco",
        "airports": ["SFO"],
        "aliases": ["san francisco"],
    },
    {"city_code": "SIN", "name": "Singapore", "airports": ["SIN"], "aliases": ["singapore"]},
    {"city_code": "SYD", "name": "Sydney", "airports": ["SYD"], "aliases": ["sydney"]},
    {"city_code": "MIA", "name": "Miami", "airports": ["MIA"], "aliases": ["miami"]},
    {"city_code": "LIS", "name": "Lisbon", "airports": ["LIS"], "aliases": ["lisbon"]},
    {"city_code": "HKG", "name": "Hong Kong", "airports": ["HKG"], "aliases": ["hong kong"]},
]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(files: dict[str, Any]) -> str:
    """sha256 over ``name + "\\n" + canonical_json(rows) + "\\n"``, names ascending."""
    digest = hashlib.sha256()
    for name in sorted(n for n in files if n != "version.json"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\n")
        digest.update(canonical_json(files[name]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Row builders
# --------------------------------------------------------------------------


def program(pid: str, name: str, kind: str) -> dict[str, Any]:
    return {"id": pid, "name": name, "kind": kind, "source_note": "eval fixture"}


def valuation(pid: str, cpp_milli: int, as_of: str = "2026-07-01") -> dict[str, Any]:
    return {
        "program_id": pid,
        "cpp_milli": cpp_milli,
        "as_of": as_of,
        "source_note": "eval fixture",
    }


def card(
    cid: str, program_id: str, *, enables_transfer: bool = True, issuer: str = "Fixture Bank"
) -> dict[str, Any]:
    return {
        "id": cid,
        "issuer": issuer,
        "name": cid.replace("_", " ").title(),
        "program_id": program_id,
        "enables_transfer": enables_transfer,
        "annual_fee_cents": 0,
        "source_note": "eval fixture",
    }


def edge(
    eid: str,
    from_program: str,
    to_program: str,
    *,
    ratio_from: int = 1,
    ratio_to: int = 1,
    min_from: int = 2000,
    increment_from: int = 2000,
    fee_mcpp: int = 0,
    fee_cap_cents: int | None = None,
    time_days: int = 0,
    bonus_per_from: int | None = None,
    bonus_to: int | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, Any]:
    return {
        "id": eid,
        "from_program": from_program,
        "to_program": to_program,
        "ratio_from": ratio_from,
        "ratio_to": ratio_to,
        "min_from": min_from,
        "increment_from": increment_from,
        "fee_mcpp": fee_mcpp,
        "fee_cap_cents": fee_cap_cents,
        "time_days": time_days,
        "bonus_per_from": bonus_per_from,
        "bonus_to": bonus_to,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source_note": "eval fixture",
    }


def cashout(
    oid: str,
    program_id: str,
    method: str,
    cpp_milli: int,
    *,
    min_points: int = 1,
    increment: int = 1,
    requires_card: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, Any]:
    return {
        "id": oid,
        "program_id": program_id,
        "method": method,
        "cpp_milli": cpp_milli,
        "min_points": min_points,
        "increment": increment,
        "requires_card": requires_card,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source_note": "eval fixture",
    }


def flight(
    oid: str,
    program_id: str,
    origin: str,
    destination: str,
    *,
    cabin: str = "business",
    points_price: int,
    fees_cents: int,
    round_trip: bool = False,
    window: tuple[str, str] = ("2026-10-01", "2026-10-31"),
    seats_available: int | None = 4,
    bookable_until: str | None = None,
) -> dict[str, Any]:
    return {
        "id": oid,
        "program_id": program_id,
        "kind": "flight",
        "origin": origin,
        "destination": destination,
        "cabin": cabin,
        "round_trip": round_trip,
        "points_price": points_price,
        "fees_cents": fees_cents,
        "city": None,
        "travel_window_start": window[0],
        "travel_window_end": window[1],
        "seats_available": seats_available,
        "bookable_until": bookable_until,
        "source_note": "eval fixture",
    }


def stay(
    oid: str,
    program_id: str,
    city: str,
    *,
    points_price: int,
    fees_cents: int = 0,
    window: tuple[str, str] = ("2026-10-01", "2026-10-31"),
    seats_available: int | None = None,
    bookable_until: str | None = None,
) -> dict[str, Any]:
    return {
        "id": oid,
        "program_id": program_id,
        "kind": "stay",
        "origin": None,
        "destination": None,
        "cabin": None,
        "round_trip": None,
        "points_price": points_price,
        "fees_cents": fees_cents,
        "city": city,
        "travel_window_start": window[0],
        "travel_window_end": window[1],
        "seats_available": seats_available,
        "bookable_until": bookable_until,
        "source_note": "eval fixture",
    }


def flight_fare(
    origin: str, dest: str, cabin: str, round_trip: bool, month: str, cents: int
) -> dict[str, Any]:
    rt = "rt" if round_trip else "ow"
    return {
        "id": f"rf_{origin}_{dest}_{cabin[:3]}_{rt}_{month.replace('-', '_')}".lower(),
        "kind": "flight",
        "origin_city": origin,
        "dest_city": dest,
        "cabin": cabin,
        "round_trip": round_trip,
        "city": None,
        "month": month,
        "fare_cents": cents,
        "source_note": "eval fixture",
    }


def stay_fare(city: str, month: str, cents: int) -> dict[str, Any]:
    return {
        "id": f"rf_stay_{city}_{month.replace('-', '_')}".lower(),
        "kind": "stay",
        "origin_city": None,
        "dest_city": None,
        "cabin": None,
        "round_trip": None,
        "city": city,
        "month": month,
        "fare_cents": cents,
        "source_note": "eval fixture",
    }


def months_touched(start: str, end: str) -> list[str]:
    year, month = int(start[:4]), int(start[5:7])
    last_year, last_month = int(end[:4]), int(end[5:7])
    out: list[str] = []
    while (year, month) <= (last_year, last_month):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def fare_table(
    routes: dict[tuple[str, str, str], tuple[int, int]],
    months: list[str],
    stays: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Build every fare row FR-1 invariant 5 requires.

    ``routes`` maps ``(origin, dest, cabin) -> (one_way_cents, round_trip_cents)``
    and is expanded into both directions plus the round-trip row for every month.
    """
    rows: list[dict[str, Any]] = []
    for (origin, dest, cabin), (one_way, round_trip) in sorted(routes.items()):
        for month in months:
            rows.append(flight_fare(origin, dest, cabin, False, month, one_way))
            rows.append(flight_fare(dest, origin, cabin, False, month, one_way))
            rows.append(flight_fare(origin, dest, cabin, True, month, round_trip))
            rows.append(flight_fare(dest, origin, cabin, True, month, round_trip))
    for city, nightly in sorted((stays or {}).items()):
        for month in months:
            rows.append(stay_fare(city, month, nightly))
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        seen[row["id"]] = row
    return [seen[key] for key in sorted(seen)]


def seal(files: dict[str, Any], version: str = "1.0.0", as_of: str = "2026-07-01") -> dict[str, Any]:
    """Attach a ``version.json`` whose ``content_hash`` matches ``files``."""
    payload = dict(files)
    payload["version.json"] = {
        "version": version,
        "as_of": as_of,
        "content_hash": content_hash(files),
    }
    return payload


def assemble(
    *,
    programs: list[dict[str, Any]],
    valuations: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    cashouts: list[dict[str, Any]],
    offers: list[dict[str, Any]],
    fares: list[dict[str, Any]],
    gazetteer: list[dict[str, Any]] | None = None,
    version: str = "1.0.0",
    as_of: str = "2026-07-01",
) -> dict[str, Any]:
    return seal(
        {
            "programs.json": programs,
            "cards.json": cards,
            "transfers.json": edges,
            "cashouts.json": cashouts,
            "valuations.json": valuations,
            "awards.json": offers,
            "reference_fares.json": fares,
            "gazetteer.json": gazetteer if gazetteer is not None else GAZETTEER,
        },
        version=version,
        as_of=as_of,
    )

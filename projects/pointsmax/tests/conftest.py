"""Shared fixtures: a small synthetic world plus the shipped dataset.

The synthetic world exercises every mechanic the engine implements — card
gating, a fee edge with a cap, a 3:1 hotel hub with a tier bonus, a slow edge, a
promo window, liquid and non-liquid cashout options — while staying small enough
that expected values can be worked out by hand inside the tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pointsmax.adapters.world_provider import CommittedWorldProvider, build_world
from pointsmax.engine.world import HASHED_WORLD_FILES, compute_content_hash
from pointsmax.models import Wallet, World

TODAY = date(2026, 7, 31)

PROGRAMS = [
    {"id": "bank_a", "name": "Bank A Points", "kind": "bank", "source_note": "fixture"},
    {"id": "bank_b", "name": "Bank B Points", "kind": "bank", "source_note": "fixture"},
    {"id": "air_x", "name": "Air X Miles", "kind": "airline", "source_note": "fixture"},
    {"id": "air_y", "name": "Air Y Miles", "kind": "airline", "source_note": "fixture"},
    {"id": "hotel_h", "name": "Hotel H Points", "kind": "hotel", "source_note": "fixture"},
]

VALUATIONS = [
    {"program_id": "bank_a", "cpp_milli": 2000, "as_of": "2026-07-01", "source_note": "fixture"},
    {"program_id": "bank_b", "cpp_milli": 1800, "as_of": "2026-07-01", "source_note": "fixture"},
    {"program_id": "air_x", "cpp_milli": 1300, "as_of": "2026-07-01", "source_note": "fixture"},
    {"program_id": "air_y", "cpp_milli": 1000, "as_of": "2026-07-01", "source_note": "fixture"},
    {"program_id": "hotel_h", "cpp_milli": 800, "as_of": "2026-07-01", "source_note": "fixture"},
]

CARDS = [
    {
        "id": "card_a",
        "issuer": "Bank A",
        "name": "Card A Premium",
        "program_id": "bank_a",
        "enables_transfer": True,
        "annual_fee_cents": 55000,
        "source_note": "fixture",
    },
    {
        "id": "card_a_basic",
        "issuer": "Bank A",
        "name": "Card A Basic",
        "program_id": "bank_a",
        "enables_transfer": False,
        "annual_fee_cents": 0,
        "source_note": "fixture",
    },
    {
        "id": "card_b",
        "issuer": "Bank B",
        "name": "Card B",
        "program_id": "bank_b",
        "enables_transfer": True,
        "annual_fee_cents": 9500,
        "source_note": "fixture",
    },
]


def _edge(
    edge_id: str,
    from_program: str,
    to_program: str,
    *,
    ratio_from: int = 1,
    ratio_to: int = 1,
    min_from: int = 1000,
    increment_from: int = 1000,
    fee_mcpp: int = 0,
    fee_cap_cents: int | None = None,
    time_days: int = 0,
    bonus_per_from: int | None = None,
    bonus_to: int | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict[str, Any]:
    return {
        "id": edge_id,
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
        "source_note": "fixture",
    }


EDGES = [
    _edge("bank_a__air_x", "bank_a", "air_x"),
    _edge("bank_a__air_y", "bank_a", "air_y", fee_mcpp=60, fee_cap_cents=9900),
    _edge("bank_a__hotel_h", "bank_a", "hotel_h"),
    _edge("bank_b__air_x", "bank_b", "air_x", time_days=2),
    _edge("bank_b__hotel_h", "bank_b", "hotel_h"),
    _edge(
        "hotel_h__air_x",
        "hotel_h",
        "air_x",
        ratio_from=3,
        ratio_to=1,
        min_from=3000,
        increment_from=3000,
        time_days=2,
        bonus_per_from=60000,
        bonus_to=5000,
    ),
    _edge(
        "bank_b__air_y_promo",
        "bank_b",
        "air_y",
        valid_from="2026-07-01",
        valid_to="2026-08-10",
    ),
]

CASHOUTS = [
    {
        "id": "a_credit",
        "program_id": "bank_a",
        "method": "statement_credit",
        "cpp_milli": 1000,
        "min_points": 1,
        "increment": 1,
        "requires_card": None,
        "valid_from": None,
        "valid_to": None,
        "source_note": "fixture",
    },
    {
        "id": "a_portal",
        "program_id": "bank_a",
        "method": "portal_travel",
        "cpp_milli": 1500,
        "min_points": 1,
        "increment": 1,
        "requires_card": "card_a",
        "valid_from": None,
        "valid_to": None,
        "source_note": "fixture",
    },
    {
        "id": "a_gift",
        "program_id": "bank_a",
        "method": "gift_card",
        "cpp_milli": 1200,
        "min_points": 2500,
        "increment": 2500,
        "requires_card": None,
        "valid_from": None,
        "valid_to": None,
        "source_note": "fixture",
    },
    {
        "id": "b_deposit",
        "program_id": "bank_b",
        "method": "bank_deposit",
        "cpp_milli": 800,
        "min_points": 1000,
        "increment": 1000,
        "requires_card": None,
        "valid_from": None,
        "valid_to": None,
        "source_note": "fixture",
    },
]


def _flight_offer(
    offer_id: str,
    program_id: str,
    origin: str,
    destination: str,
    *,
    cabin: str = "business",
    points_price: int = 60000,
    fees_cents: int = 25000,
    round_trip: bool = False,
    seats_available: int | None = 4,
    bookable_until: str | None = None,
    window: tuple[str, str] = ("2026-10-01", "2026-10-31"),
) -> dict[str, Any]:
    return {
        "id": offer_id,
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
        "source_note": "fixture",
    }


OFFERS = [
    _flight_offer("x_out", "air_x", "AAA", "BBB"),
    _flight_offer("x_back", "air_x", "BBB", "AAA"),
    _flight_offer(
        "y_out",
        "air_y",
        "AAA",
        "BBB",
        cabin="premium_economy",
        points_price=70000,
        fees_cents=9000,
    ),
    _flight_offer(
        "y_back",
        "air_y",
        "BBB",
        "AAA",
        cabin="premium_economy",
        points_price=70000,
        fees_cents=9000,
    ),
    _flight_offer(
        "x_rt", "air_x", "AAA", "BBB", points_price=115000, fees_cents=50000, round_trip=True
    ),
    _flight_offer(
        "x_soon",
        "air_x",
        "AAA",
        "BBB",
        points_price=50000,
        fees_cents=25000,
        bookable_until="2026-08-01",
        seats_available=2,
    ),
    {
        "id": "h_stay",
        "program_id": "hotel_h",
        "kind": "stay",
        "origin": None,
        "destination": None,
        "cabin": None,
        "round_trip": None,
        "points_price": 20000,
        "fees_cents": 0,
        "city": "AAA",
        "travel_window_start": "2026-10-01",
        "travel_window_end": "2026-10-31",
        "seats_available": None,
        "bookable_until": None,
        "source_note": "fixture",
    },
]


def _fare(
    fare_id: str,
    *,
    origin_city: str | None = None,
    dest_city: str | None = None,
    cabin: str | None = None,
    round_trip: bool | None = None,
    city: str | None = None,
    month: str = "2026-10",
    fare_cents: int = 200000,
    kind: str = "flight",
) -> dict[str, Any]:
    return {
        "id": fare_id,
        "kind": kind,
        "origin_city": origin_city,
        "dest_city": dest_city,
        "cabin": cabin,
        "round_trip": round_trip,
        "city": city,
        "month": month,
        "fare_cents": fare_cents,
        "source_note": "fixture",
    }


FARES = [
    _fare("f_ab_bus_ow", origin_city="AAA", dest_city="BBB", cabin="business", round_trip=False),
    _fare("f_ba_bus_ow", origin_city="BBB", dest_city="AAA", cabin="business", round_trip=False),
    _fare(
        "f_ab_bus_rt",
        origin_city="AAA",
        dest_city="BBB",
        cabin="business",
        round_trip=True,
        fare_cents=380000,
    ),
    _fare(
        "f_ba_bus_rt",
        origin_city="BBB",
        dest_city="AAA",
        cabin="business",
        round_trip=True,
        fare_cents=380000,
    ),
    _fare(
        "f_ab_pre_ow",
        origin_city="AAA",
        dest_city="BBB",
        cabin="premium_economy",
        round_trip=False,
        fare_cents=120000,
    ),
    _fare(
        "f_ba_pre_ow",
        origin_city="BBB",
        dest_city="AAA",
        cabin="premium_economy",
        round_trip=False,
        fare_cents=120000,
    ),
    _fare(
        "f_ab_pre_rt",
        origin_city="AAA",
        dest_city="BBB",
        cabin="premium_economy",
        round_trip=True,
        fare_cents=228000,
    ),
    _fare(
        "f_ba_pre_rt",
        origin_city="BBB",
        dest_city="AAA",
        cabin="premium_economy",
        round_trip=True,
        fare_cents=228000,
    ),
    _fare("f_stay_aaa", city="AAA", kind="stay", fare_cents=30000),
]

GAZETTEER = [
    {"city_code": "AAA", "name": "Alfaville", "airports": ["AAX"], "aliases": ["alfaville"]},
    {"city_code": "BBB", "name": "Betatown", "airports": ["BBX"], "aliases": ["betatown"]},
    {"city_code": "CCC", "name": "Gammaport", "airports": ["CCX"], "aliases": ["gammaport"]},
]


def world_files() -> dict[str, Any]:
    """The synthetic world as raw parsed files, ready for :func:`build_world`."""
    return {
        "programs.json": [dict(row) for row in PROGRAMS],
        "cards.json": [dict(row) for row in CARDS],
        "transfers.json": [dict(row) for row in EDGES],
        "cashouts.json": [dict(row) for row in CASHOUTS],
        "valuations.json": [dict(row) for row in VALUATIONS],
        "awards.json": [dict(row) for row in OFFERS],
        "reference_fares.json": [dict(row) for row in FARES],
        "gazetteer.json": [dict(row) for row in GAZETTEER],
    }


def sealed(files: dict[str, Any], version: str = "1.0.0") -> dict[str, Any]:
    """Add a ``version.json`` whose ``content_hash`` matches ``files``."""
    payload = dict(files)
    payload["version.json"] = {
        "version": version,
        "as_of": "2026-07-01",
        "content_hash": compute_content_hash(
            {name: files[name] for name in HASHED_WORLD_FILES if name in files}
        ),
    }
    return payload


def make_world(files: dict[str, Any] | None = None, *, validate: bool = True) -> World:
    return build_world(sealed(files or world_files()), validate=validate)


@pytest.fixture
def world() -> World:
    return make_world()


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def wallet() -> Wallet:
    """Holds the gating card for bank_a and bank_b, with a spread of balances."""
    return Wallet(
        cards=["card_a", "card_b"],
        balances={"bank_a": 100000, "bank_b": 80000, "hotel_h": 150000},
    )


@pytest.fixture(scope="session")
def shipped_world() -> World:
    """The committed ``data/world/`` dataset, fully FR-1 validated."""
    return CommittedWorldProvider().load()


@pytest.fixture
def world_dir(tmp_path) -> Path:
    """The synthetic world written to disk, for surfaces that take ``--world DIR``."""
    import json

    directory = tmp_path / "world"
    directory.mkdir()
    for name, payload in sealed(world_files()).items():
        (directory / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return directory


@pytest.fixture
def repo():
    from pointsmax.store import InMemoryRepository

    return InMemoryRepository()


@pytest.fixture
def service(repo, world):
    """A service on the synthetic world with an empty in-memory store."""
    from pointsmax.service import PointsMaxService

    return PointsMaxService(repo, world)


@pytest.fixture
def stocked_service(service):
    """A service holding both gating cards and a spread of balances."""
    service.add_card("card_a", at="2026-07-01T00:00:00Z")
    service.add_card("card_b", at="2026-07-01T00:00:00Z")
    service.set_balance("bank_a", 100000, at="2026-07-01T00:00:00Z")
    service.set_balance("bank_b", 80000, at="2026-07-01T00:00:00Z")
    return service


@pytest.fixture
def client(stocked_service):
    """FastAPI TestClient bound to the stocked in-memory service."""
    from fastapi.testclient import TestClient

    from pointsmax.api.app import create_app

    return TestClient(create_app(stocked_service))

"""Build the committed fixture worlds under ``evals/fixtures/worlds/``.

Run ``python evals/make_worlds.py`` to regenerate; the output is byte-identical
every time (no randomness, no clock).  Worlds are *inputs* — never ground truth
— and are synthetic-but-realistic: real mechanics (card gating, 3:1 hotel hubs
with 60k tier bonuses, $99-capped excise fees, 1:2 ratios, multi-day posting
times, promo windows) with invented balances and prices, so every expected value
in the fixtures is determined by the fixture itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worldgen import assemble, card, cashout, edge, fare_table, flight, program, stay, valuation

WORLDS_DIR = Path(__file__).resolve().parent / "fixtures" / "worlds"

OCT = ("2026-10-01", "2026-10-31")
OCT_NOV = ("2026-10-01", "2026-11-30")


# --------------------------------------------------------------------------
# small_a — direct bank transfers, one fee edge, a hotel hub, two portals
# --------------------------------------------------------------------------


def small_a() -> dict[str, Any]:
    return assemble(
        programs=[
            program("ur", "Ultimate Rewards", "bank"),
            program("mr", "Membership Rewards", "bank"),
            program("fb", "Flying Blue", "airline"),
            program("ac", "Aeroplan", "airline"),
            program("dl", "Delta SkyMiles", "airline"),
            program("mb", "Bonvoy", "hotel"),
        ],
        valuations=[
            valuation("ur", 2050),
            valuation("mr", 2000),
            valuation("fb", 1300),
            valuation("ac", 1500),
            valuation("dl", 1150),
            valuation("mb", 800),
        ],
        cards=[
            card("ur_premium", "ur"),
            card("ur_basic", "ur", enables_transfer=False),
            card("mr_premium", "mr"),
        ],
        edges=[
            edge("ur__fb", "ur", "fb"),
            edge("ur__ac", "ur", "ac"),
            edge("ur__mb", "ur", "mb"),
            edge("mr__fb", "mr", "fb"),
            edge("mr__dl", "mr", "dl", fee_mcpp=60, fee_cap_cents=9900),
            edge("mr__mb", "mr", "mb"),
            edge(
                "mb__fb",
                "mb",
                "fb",
                ratio_from=3,
                ratio_to=1,
                min_from=3000,
                increment_from=3000,
                time_days=2,
                bonus_per_from=60000,
                bonus_to=5000,
            ),
        ],
        cashouts=[
            cashout("ur_credit", "ur", "statement_credit", 1000),
            cashout("ur_portal", "ur", "portal_travel", 1500, requires_card="ur_premium"),
            cashout("mr_credit", "mr", "statement_credit", 600),
            cashout("mr_deposit", "mr", "bank_deposit", 600, min_points=1000, increment=1000),
            cashout("mr_portal", "mr", "portal_travel", 1000, requires_card="mr_premium"),
        ],
        offers=[
            flight("fb_bus_nyc_par", "fb", "NYC", "PAR", points_price=60000, fees_cents=25100),
            flight("fb_bus_par_nyc", "fb", "PAR", "NYC", points_price=60000, fees_cents=25100),
            flight("ac_bus_nyc_par", "ac", "NYC", "PAR", points_price=70000, fees_cents=12800),
            flight(
                "fb_saver_nyc_par",
                "fb",
                "NYC",
                "PAR",
                points_price=30000,
                fees_cents=90000,
            ),
            flight(
                "fb_rt_nyc_par",
                "fb",
                "NYC",
                "PAR",
                points_price=125000,
                fees_cents=50200,
                round_trip=True,
            ),
            flight(
                "fb_eco_nyc_par",
                "fb",
                "NYC",
                "PAR",
                cabin="economy",
                points_price=22000,
                fees_cents=22400,
                seats_available=6,
            ),
            flight(
                "fb_eco_par_nyc",
                "fb",
                "PAR",
                "NYC",
                cabin="economy",
                points_price=22000,
                fees_cents=22400,
                seats_available=6,
            ),
            stay("mb_stay_par", "mb", "PAR", points_price=20000),
        ],
        fares=fare_table(
            {
                ("NYC", "PAR", "business"): (210000, 400000),
                ("NYC", "PAR", "economy"): (60000, 110000),
            },
            ["2026-10"],
            stays={"PAR": 30000},
        ),
    )


# --------------------------------------------------------------------------
# small_b — a hub world: one airline is reachable only through the hotel
# --------------------------------------------------------------------------


def small_b() -> dict[str, Any]:
    return assemble(
        programs=[
            program("bank_p", "Bank P Points", "bank"),
            program("bank_q", "Bank Q Points", "bank"),
            program("hub", "Hub Hotel Points", "hotel"),
            program("air_alpha", "Alpha Miles", "airline"),
            program("air_beta", "Beta Miles", "airline"),
        ],
        valuations=[
            valuation("bank_p", 2000),
            valuation("bank_q", 1900),
            valuation("hub", 900),
            valuation("air_alpha", 1400),
            valuation("air_beta", 1200),
        ],
        cards=[
            card("card_p", "bank_p"),
            card("card_q", "bank_q"),
            card("card_p_basic", "bank_p", enables_transfer=False),
        ],
        edges=[
            edge("bank_p__hub", "bank_p", "hub"),
            edge("bank_q__hub", "bank_q", "hub"),
            edge("bank_p__air_beta", "bank_p", "air_beta"),
            edge("bank_q__air_beta", "bank_q", "air_beta", time_days=1),
            edge(
                "hub__air_alpha",
                "hub",
                "air_alpha",
                ratio_from=3,
                ratio_to=1,
                min_from=3000,
                increment_from=3000,
                time_days=2,
                bonus_per_from=60000,
                bonus_to=5000,
            ),
            edge(
                "hub__air_beta",
                "hub",
                "air_beta",
                ratio_from=3,
                ratio_to=1,
                min_from=3000,
                increment_from=3000,
                time_days=1,
                bonus_per_from=60000,
                bonus_to=5000,
            ),
        ],
        cashouts=[
            cashout("p_credit", "bank_p", "statement_credit", 1000),
            cashout("q_credit", "bank_q", "statement_credit", 900),
            cashout("p_portal", "bank_p", "portal_travel", 1400, requires_card="card_p"),
        ],
        offers=[
            flight(
                "alpha_bus_nyc_lon",
                "air_alpha",
                "NYC",
                "LON",
                points_price=60000,
                fees_cents=20000,
                window=OCT_NOV,
            ),
            flight(
                "alpha_bus_lon_nyc",
                "air_alpha",
                "LON",
                "NYC",
                points_price=60000,
                fees_cents=20000,
                window=OCT_NOV,
            ),
            flight(
                "beta_bus_nyc_lon",
                "air_beta",
                "NYC",
                "LON",
                points_price=95000,
                fees_cents=15000,
                window=OCT_NOV,
            ),
            flight(
                "beta_bus_lon_nyc",
                "air_beta",
                "LON",
                "NYC",
                points_price=95000,
                fees_cents=15000,
                window=OCT_NOV,
            ),
            flight(
                "alpha_bus_nyc_lon_soon",
                "air_alpha",
                "NYC",
                "LON",
                points_price=52000,
                fees_cents=20000,
                window=OCT_NOV,
                seats_available=2,
                bookable_until="2026-08-01",
            ),
        ],
        fares=fare_table(
            {("NYC", "LON", "business"): (200000, 380000)},
            ["2026-10", "2026-11"],
        ),
    )


# --------------------------------------------------------------------------
# small_c — fee caps and tier boundaries
# --------------------------------------------------------------------------


def small_c() -> dict[str, Any]:
    return assemble(
        programs=[
            program("mrx", "MRX Points", "bank"),
            program("typx", "TYPX Points", "bank"),
            program("dlx", "DLX Miles", "airline"),
            program("uax", "UAX Miles", "airline"),
            program("alx", "ALX Miles", "airline"),
            program("mbx", "MBX Hotel Points", "hotel"),
        ],
        valuations=[
            valuation("mrx", 2000),
            valuation("typx", 1800),
            valuation("dlx", 1000),
            valuation("uax", 1350),
            valuation("alx", 1400),
            valuation("mbx", 800),
        ],
        cards=[
            card("mrx_card", "mrx"),
            card("mrx_premium", "mrx"),
            card("typx_card", "typx"),
        ],
        edges=[
            edge("mrx__dlx", "mrx", "dlx", fee_mcpp=60, fee_cap_cents=4800),
            edge("mrx__uax", "mrx", "uax", fee_mcpp=60, fee_cap_cents=9900),
            edge("mrx__mbx", "mrx", "mbx"),
            edge("typx__uax", "typx", "uax", time_days=1),
            edge("typx__mbx", "typx", "mbx"),
            edge(
                "mbx__alx",
                "mbx",
                "alx",
                ratio_from=3,
                ratio_to=1,
                min_from=3000,
                increment_from=3000,
                time_days=2,
                bonus_per_from=60000,
                bonus_to=5000,
            ),
            edge(
                "mbx__dlx",
                "mbx",
                "dlx",
                ratio_from=3,
                ratio_to=1,
                min_from=3000,
                increment_from=3000,
                time_days=2,
                bonus_per_from=60000,
                bonus_to=5000,
            ),
        ],
        cashouts=[
            cashout("mrx_credit", "mrx", "statement_credit", 600),
            cashout("mrx_portal", "mrx", "portal_travel", 1538, requires_card="mrx_premium"),
            cashout("typx_credit", "typx", "statement_credit", 1000),
        ],
        offers=[
            flight("dlx_bus_nyc_mia", "dlx", "NYC", "MIA", points_price=40000, fees_cents=5600),
            flight("dlx_bus_mia_nyc", "dlx", "MIA", "NYC", points_price=40000, fees_cents=5600),
            flight("uax_bus_nyc_mia", "uax", "NYC", "MIA", points_price=95000, fees_cents=5600),
            flight("dlx_saver_nyc_mia", "dlx", "NYC", "MIA", points_price=20000, fees_cents=50000),
            flight("uax_bus_mia_nyc", "uax", "MIA", "NYC", points_price=95000, fees_cents=5600),
            flight("alx_bus_nyc_mia", "alx", "NYC", "MIA", points_price=55000, fees_cents=8000),
            flight("alx_bus_mia_nyc", "alx", "MIA", "NYC", points_price=55000, fees_cents=8000),
        ],
        fares=fare_table({("NYC", "MIA", "business"): (150000, 280000)}, ["2026-10"]),
    )


# --------------------------------------------------------------------------
# small_d — cash liquidity, card-gated portals, a 1:2 ratio
# --------------------------------------------------------------------------


def small_d() -> dict[str, Any]:
    # Valuations are deliberately dated 2026-01-05 so a scenario with a later
    # ``today`` exercises the FR-10 ``stale_world`` caveat (> 180 days old).
    return assemble(
        programs=[
            program("urd", "URD Points", "bank"),
            program("mrd", "MRD Points", "bank"),
            program("fbd", "FBD Miles", "airline"),
            program("hild", "HILD Points", "hotel"),
        ],
        valuations=[
            valuation("urd", 2050, "2026-01-05"),
            valuation("mrd", 2000, "2026-01-05"),
            valuation("fbd", 1300, "2026-01-05"),
            valuation("hild", 500, "2026-01-05"),
        ],
        cards=[
            card("csr", "urd"),
            card("csp", "urd"),
            card("gold", "mrd"),
        ],
        edges=[
            edge("urd__fbd", "urd", "fbd"),
            edge("mrd__fbd", "mrd", "fbd"),
            edge("mrd__hild", "mrd", "hild", ratio_from=1, ratio_to=2),
        ],
        cashouts=[
            cashout("urd_credit", "urd", "statement_credit", 1000),
            cashout("urd_portal_csr", "urd", "portal_travel", 1500, requires_card="csr"),
            cashout("urd_portal_csp", "urd", "portal_travel", 1250, requires_card="csp"),
            cashout("urd_gift", "urd", "gift_card", 1000, min_points=2500, increment=2500),
            cashout("mrd_credit", "mrd", "statement_credit", 600),
            cashout("mrd_deposit", "mrd", "bank_deposit", 650, min_points=1000, increment=1000),
            cashout("hild_credit", "hild", "statement_credit", 400, min_points=10000, increment=1000),
        ],
        offers=[
            flight("fbd_bus_nyc_par", "fbd", "NYC", "PAR", points_price=60000, fees_cents=25100),
            flight("fbd_bus_par_nyc", "fbd", "PAR", "NYC", points_price=60000, fees_cents=25100),
        ],
        fares=fare_table({("NYC", "PAR", "business"): (210000, 400000)}, ["2026-10"]),
    )


# --------------------------------------------------------------------------
# stress_a — 12 programs, ~30 edges (incl. live and dead promos), ~25 offers
# --------------------------------------------------------------------------


def stress_a() -> dict[str, Any]:
    banks = [("b1", 2050), ("b2", 2000), ("b3", 1800), ("b4", 1850)]
    airlines = [
        ("a1", 1500),
        ("a2", 1400),
        ("a3", 1300),
        ("a4", 1150),
        ("a5", 1350),
        ("a6", 1300),
    ]
    hotels = [("h1", 800), ("h2", 1700)]

    def hub(to_program: str, days: int = 2) -> dict[str, Any]:
        return edge(
            f"h1__{to_program}",
            "h1",
            to_program,
            ratio_from=3,
            ratio_to=1,
            min_from=3000,
            increment_from=3000,
            time_days=days,
            bonus_per_from=60000,
            bonus_to=5000,
        )

    return assemble(
        programs=(
            [program(pid, f"Bank {pid.upper()}", "bank") for pid, _ in banks]
            + [program(pid, f"Air {pid.upper()}", "airline") for pid, _ in airlines]
            + [program(pid, f"Hotel {pid.upper()}", "hotel") for pid, _ in hotels]
        ),
        valuations=[valuation(pid, cpp) for pid, cpp in banks + airlines + hotels],
        cards=[
            card("c1", "b1"),
            card("c1_basic", "b1", enables_transfer=False),
            card("c2", "b2"),
            card("c3", "b3"),
            card("c4", "b4"),
        ],
        edges=[
            edge("b1__a1", "b1", "a1"),
            edge("b1__a2", "b1", "a2", time_days=2),
            edge("b1__a3", "b1", "a3"),
            edge("b1__h1", "b1", "h1"),
            edge("b1__h2", "b1", "h2"),
            edge("b2__a1", "b2", "a1"),
            edge("b2__a3", "b2", "a3", fee_mcpp=60, fee_cap_cents=6000),
            edge("b2__a4", "b2", "a4", fee_mcpp=60, fee_cap_cents=6000),
            edge("b2__h1", "b2", "h1"),
            edge("b2__h2", "b2", "h2"),
            edge("b3__a2", "b3", "a2"),
            edge("b3__a5", "b3", "a5", time_days=1),
            edge("b3__a6", "b3", "a6"),
            edge("b3__h1", "b3", "h1"),
            edge("b4__a3", "b4", "a3"),
            edge("b4__a5", "b4", "a5"),
            edge("b4__h1", "b4", "h1"),
            edge("b4__h2", "b4", "h2"),
            hub("a1"),
            hub("a2"),
            hub("a3"),
            hub("a5", days=3),
            hub("a6"),
            # promo edges: one live at the scenario dates, one already expired,
            # one that has not opened yet (FR-3 window gating).
            edge(
                "b1__a4_promo",
                "b1",
                "a4",
                ratio_from=4,
                ratio_to=5,
                min_from=4000,
                increment_from=4000,
                valid_from="2026-07-01",
                valid_to="2026-08-20",
            ),
            edge(
                "b2__a5_promo",
                "b2",
                "a5",
                ratio_from=4,
                ratio_to=5,
                min_from=4000,
                increment_from=4000,
                valid_from="2026-05-01",
                valid_to="2026-06-30",
            ),
            edge(
                "b3__a1_promo",
                "b3",
                "a1",
                ratio_from=1,
                ratio_to=1,
                valid_from="2026-08-25",
                valid_to="2026-09-30",
            ),
        ],
        cashouts=[
            cashout("b1_credit", "b1", "statement_credit", 1000),
            cashout("b1_portal", "b1", "portal_travel", 1500, requires_card="c1"),
            cashout("b2_credit", "b2", "statement_credit", 600),
            cashout("b2_deposit", "b2", "bank_deposit", 600, min_points=1000, increment=1000),
            cashout("b3_credit", "b3", "statement_credit", 900),
            cashout("b4_credit", "b4", "statement_credit", 500),
            cashout("b4_portal", "b4", "portal_travel", 1000, requires_card="c4"),
            cashout("h2_credit", "h2", "statement_credit", 700, min_points=5000, increment=5000),
        ],
        offers=[
            flight("a1_bus_nyc_par", "a1", "NYC", "PAR", points_price=70000, fees_cents=12800),
            flight("a1_bus_par_nyc", "a1", "PAR", "NYC", points_price=70000, fees_cents=12800),
            flight("a2_bus_nyc_par", "a2", "NYC", "PAR", points_price=62000, fees_cents=30400),
            flight("a2_bus_par_nyc", "a2", "PAR", "NYC", points_price=62000, fees_cents=30400),
            flight(
                "a3_bus_nyc_par",
                "a3",
                "NYC",
                "PAR",
                points_price=60000,
                fees_cents=25100,
                seats_available=2,
            ),
            flight(
                "a3_bus_par_nyc",
                "a3",
                "PAR",
                "NYC",
                points_price=60000,
                fees_cents=25100,
                seats_available=2,
            ),
            flight(
                "a1_rt_nyc_par",
                "a1",
                "NYC",
                "PAR",
                points_price=132000,
                fees_cents=25600,
                round_trip=True,
            ),
            flight(
                "a4_bus_nyc_par",
                "a4",
                "NYC",
                "PAR",
                points_price=60000,
                fees_cents=9000,
                seats_available=3,
            ),
            flight(
                "a4_bus_par_nyc",
                "a4",
                "PAR",
                "NYC",
                points_price=60000,
                fees_cents=9000,
                seats_available=3,
            ),
            flight(
                "a5_bus_sfo_tyo",
                "a5",
                "SFO",
                "TYO",
                points_price=75000,
                fees_cents=8500,
                window=OCT_NOV,
                seats_available=2,
            ),
            flight(
                "a5_bus_tyo_sfo",
                "a5",
                "TYO",
                "SFO",
                points_price=75000,
                fees_cents=8500,
                window=OCT_NOV,
                seats_available=2,
            ),
            flight(
                "a6_bus_sfo_tyo",
                "a6",
                "SFO",
                "TYO",
                points_price=88000,
                fees_cents=6200,
                window=OCT_NOV,
            ),
            flight(
                "a6_bus_tyo_sfo",
                "a6",
                "TYO",
                "SFO",
                points_price=88000,
                fees_cents=6200,
                window=OCT_NOV,
            ),
            flight(
                "a2_eco_nyc_par",
                "a2",
                "NYC",
                "PAR",
                cabin="economy",
                points_price=25000,
                fees_cents=13500,
                seats_available=6,
            ),
            flight(
                "a2_eco_par_nyc",
                "a2",
                "PAR",
                "NYC",
                cabin="economy",
                points_price=25000,
                fees_cents=13500,
                seats_available=6,
            ),
            flight(
                "a3_bus_chi_rom",
                "a3",
                "CHI",
                "ROM",
                points_price=53000,
                fees_cents=18000,
                seats_available=2,
            ),
            flight(
                "a3_bus_rom_chi",
                "a3",
                "ROM",
                "CHI",
                points_price=53000,
                fees_cents=18000,
                seats_available=2,
            ),
            flight(
                "a6_bus_chi_rom",
                "a6",
                "CHI",
                "ROM",
                points_price=61000,
                fees_cents=8900,
                seats_available=4,
            ),
            flight(
                "a6_bus_rom_chi",
                "a6",
                "ROM",
                "CHI",
                points_price=61000,
                fees_cents=8900,
                seats_available=4,
            ),
            flight(
                "a4_bus_nyc_par_soon",
                "a4",
                "NYC",
                "PAR",
                points_price=72000,
                fees_cents=9000,
                seats_available=2,
                bookable_until="2026-08-02",
            ),
            stay("h2_stay_par", "h2", "PAR", points_price=25000),
            stay("h1_stay_par", "h1", "PAR", points_price=62000),
            stay("h2_stay_tyo", "h2", "TYO", points_price=21000, window=OCT_NOV),
        ],
        fares=fare_table(
            {
                ("NYC", "PAR", "business"): (210000, 400000),
                ("NYC", "PAR", "economy"): (62000, 115000),
                ("SFO", "TYO", "business"): (280000, 530000),
                ("CHI", "ROM", "business"): (190000, 360000),
            },
            ["2026-10", "2026-11"],
            stays={"PAR": 30000, "TYO": 26000},
        ),
    )


WORLDS = {
    "small_a": small_a,
    "small_b": small_b,
    "small_c": small_c,
    "small_d": small_d,
    "stress_a": stress_a,
}


def build_all() -> dict[str, dict[str, Any]]:
    return {name: builder() for name, builder in sorted(WORLDS.items())}


def main() -> int:
    WORLDS_DIR.mkdir(parents=True, exist_ok=True)
    for name, files in build_all().items():
        path = WORLDS_DIR / f"{name}.json"
        path.write_text(json.dumps(files, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(WORLDS_DIR.parents[2])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

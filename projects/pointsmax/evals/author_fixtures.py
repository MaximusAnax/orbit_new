"""Hand-authored fixtures: accounting cases, parser cases, M6 goals, exec scripts.

Everything here is **ground truth by construction**.  The accounting numbers are
hand-computed (each case's ``rationale`` shows the working, and
``generate_search_cases.py`` asserts the independent oracle reproduces every one
of them); the parser truths are the specifications the utterances were written
against; the M6 goals and D0 execution scripts are inputs.

Run ``python evals/author_fixtures.py`` to (re)write the JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scenarios import cash as cash_goal
from scenarios import flight as flight_goal
from scenarios import month_window
from scenarios import stay as stay_goal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _award(program: str, offer: str, points: int, fees: int, value: int) -> dict[str, Any]:
    return {
        "kind": "book_award",
        "program_id": program,
        "offer_id": offer,
        "cashout_id": None,
        "points": points,
        "fees_cents": fees,
        "value_cents": value,
    }


def _portal(program: str, option: str, points: int, value: int) -> dict[str, Any]:
    return {
        "kind": "book_portal",
        "program_id": program,
        "offer_id": None,
        "cashout_id": option,
        "points": points,
        "fees_cents": 0,
        "value_cents": value,
    }


def _cash(program: str, option: str, points: int, value: int) -> dict[str, Any]:
    return {
        "kind": "redeem_cash",
        "program_id": program,
        "offer_id": None,
        "cashout_id": option,
        "points": points,
        "fees_cents": 0,
        "value_cents": value,
    }


def A(
    case_id: str,
    group: str,
    world: str,
    opening: dict[str, int],
    transfers: list[dict[str, Any]],
    bookings: list[dict[str, Any]],
    expected: dict[str, Any],
    rationale: str,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "group": group,
        "world": world,
        "opening": opening,
        "transfers": transfers,
        "bookings": bookings,
        "expected": expected,
        "rationale": rationale,
    }


def T(edge_id: str, sent: int, delivered: int, fee: int) -> dict[str, Any]:
    return {"edge_id": edge_id, "sent": sent, "delivered": delivered, "fee_cents": fee}


def E(
    gross: int,
    outlay: int,
    points_cost: int,
    net: int,
    cpp: int | None,
    transfers: list[dict[str, Any]],
    *,
    cash_received: int | None = None,
    stranded: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "gross_value_cents": gross,
        "cash_outlay_cents": outlay,
        "points_cost_cents": points_cost,
        "net_value_cents": net,
        "realized_cpp_milli": cpp,
        "cash_received_cents": cash_received,
        "transfers": transfers,
        "stranded": stranded or [],
    }


FB_OUT = _award("fb", "fb_bus_nyc_par", 60000, 25100, 210000)
FB_BACK = _award("fb", "fb_bus_par_nyc", 60000, 25100, 210000)

# --------------------------------------------------------------------------
# 30 accounting cases (M4)
# --------------------------------------------------------------------------

ACCOUNTING_CASES: list[dict[str, Any]] = [
    # -- 8 pure-formula ----------------------------------------------------
    A(
        "acc_01", "pure_formula", "small_a", {"mr": 130000},
        [{"edge_id": "mr__fb", "sent": 120000}], [FB_OUT, FB_BACK],
        E(420000, 50200, 240000, 129800, 3081, [T("mr__fb", 120000, 120000, 0)]),
        "One merged 1:1 transfer funds both one-ways. gross = 2 x 210000 = 420000c; "
        "outlay = 2 x 25100 = 50200c (no edge fee); V(H0) = 130000*2000//1000 = 260000c; "
        "H1 = 10,000 MR = 20000c and FB 0; points_cost = 240000c; "
        "net = 420000-50200-240000 = 129800c; pooled cpp = (420000-50200)*1000//120000 = 3081.",
    ),
    A(
        "acc_02", "pure_formula", "small_a", {"ur": 100000},
        [{"edge_id": "ur__fb", "sent": 60000}], [FB_OUT],
        E(210000, 25100, 123000, 61900, 3081, [T("ur__fb", 60000, 60000, 0)]),
        "Single award. V(H0) = 100000*2050//1000 = 205000c; H1 = 40,000 UR = 82000c; "
        "points_cost = 123000c; net = 210000-25100-123000 = 61900c; "
        "cpp = (210000-25100)*1000//60000 = 3081.",
    ),
    A(
        "acc_03", "pure_formula", "small_a", {"ur": 200000}, [],
        [_portal("ur", "ur_portal", 140000, 210000)],
        E(210000, 0, 287000, -77000, 1500, []),
        "Portal point price = ceil(210000*1000/1500) = 140000 (increment 1). "
        "V(H0) = 410000c; H1 = 60,000 UR = 123000c; points_cost = 287000c; "
        "net = 210000-287000 = -77000c; cpp = 210000*1000//140000 = 1500 - exactly the "
        "portal rate, which is why the portal is a floor and not a maximiser.",
    ),
    A(
        "acc_04", "pure_formula", "small_c", {"mrx": 150000}, [],
        [_portal("mrx", "mrx_portal", 97530, 150000)],
        E(150000, 0, 195060, -45060, 1537, []),
        "Ceiling rounding: 150000*1000/1538 = 97529.26..., so the portal charges 97530 "
        "points. V(H0) = 300000c; H1 = 52,470 MRX = 104940c; points_cost = 195060c; "
        "net = -45060c; cpp = 150000*1000//97530 = 1537 (floor - one milli-cent below "
        "the 1538 headline rate, because the point price rounded up).",
    ),
    A(
        "acc_05", "pure_formula", "small_a", {"ur": 200000},
        [{"edge_id": "ur__fb", "sent": 60000}],
        [FB_OUT, _portal("ur", "ur_portal", 140000, 210000)],
        E(420000, 25100, 410000, -15100, 1974, [T("ur__fb", 60000, 60000, 0)]),
        "Award out (cpp 3081) plus portal back (cpp 1500). The pooled plan cpp is "
        "(420000-25100)*1000//200000 = 1974, not the average of 3081 and 1500 (2290) - "
        "FR-8 pools numerator and denominator. All 200,000 UR are consumed, so "
        "points_cost = V(H0) = 410000c and net = 420000-25100-410000 = -15100c.",
    ),
    A(
        "acc_06", "pure_formula", "small_a", {"ur": 40000, "mr": 40000},
        [{"edge_id": "ur__fb", "sent": 40000}, {"edge_id": "mr__fb", "sent": 20000}], [FB_OUT],
        E(210000, 25100, 122000, 62900, 3081,
          [T("ur__fb", 40000, 40000, 0), T("mr__fb", 20000, 20000, 0)]),
        "Two currencies fund one award. V(H0) = 82000 + 80000 = 162000c; H1 = 20,000 MR "
        "= 40000c; points_cost = 122000c; net = 210000-25100-122000 = 62900c. Spending "
        "the cheaper MR last leaves the more valuable UR exhausted first - the split is "
        "priced by the portfolio delta, not by any per-currency rule.",
    ),
    A(
        "acc_07", "pure_formula", "small_a", {"ur": 100000},
        [{"edge_id": "ur__mb", "sent": 60000}],
        [_award("mb", "mb_stay_par", 60000, 0, 90000)],
        E(90000, 0, 123000, -33000, 1500, [T("ur__mb", 60000, 60000, 0)]),
        "Stay: 3 nights x 20,000 points and 3 x 30000c nightly reference. "
        "V(H0) = 205000c; H1 = 40,000 UR = 82000c; points_cost = 123000c; "
        "net = 90000-123000 = -33000c; cpp = 90000*1000//60000 = 1500.",
    ),
    A(
        "acc_08", "pure_formula", "small_d", {"urd": 100000}, [],
        [_cash("urd", "urd_credit", 100000, 100000)],
        E(100000, 0, 205000, -105000, 1000, [], cash_received=100000),
        "Straight liquidation at 1,000 mcpp: 100000*1000//1000 = 100000c received while "
        "the portfolio gives up 100000*2050//1000 = 205000c of baseline value, so "
        "net = -105000c. This is the arithmetic behind the below_baseline warning.",
    ),
    # -- 8 fee cases -------------------------------------------------------
    A(
        "acc_09", "fee", "small_a", {"mr": 200000},
        [{"edge_id": "mr__dl", "sent": 165000}], [],
        E(0, 9900, 140250, -150150, None, [T("mr__dl", 165000, 165000, 9900)],
          stranded=[{"program": "dl", "points": 165000, "value_cents": 189750}]),
        "The documented cap boundary: 165000 x 60 mcpp = 9900c = the $99 cap exactly, so "
        "ceil and cap agree. V(H0) = 400000c; H1 = 35,000 MR (70000c) + 165,000 DL "
        "(165000*1150//1000 = 189750c) = 259750c; points_cost = 140250c; "
        "net = -9900-140250 = -150150c. No booking, so realized cpp is null.",
    ),
    A(
        "acc_10", "fee", "small_a", {"mr": 200000},
        [{"edge_id": "mr__dl", "sent": 166000}], [],
        E(0, 9900, 141100, -151000, None, [T("mr__dl", 166000, 166000, 9900)],
          stranded=[{"program": "dl", "points": 166000, "value_cents": 190900}]),
        "One increment above the boundary: raw fee = ceil(166000*60/1000) = 9960c, capped "
        "back to 9900c. H1 = 34,000 MR (68000c) + 166,000 DL (190900c) = 258900c; "
        "points_cost = 141100c; net = -151000c.",
    ),
    A(
        "acc_11", "fee", "small_a", {"mr": 200000},
        [{"edge_id": "mr__dl", "sent": 164000}], [],
        E(0, 9840, 139400, -149240, None, [T("mr__dl", 164000, 164000, 9840)],
          stranded=[{"program": "dl", "points": 164000, "value_cents": 188600}]),
        "One increment below the boundary: fee = ceil(164000*60/1000) = 9840c, under the "
        "cap. H1 = 36,000 MR (72000c) + 164,000 DL (188600c) = 260600c; "
        "points_cost = 139400c; net = -149240c.",
    ),
    A(
        "acc_12", "fee", "small_a", {"mr": 5000},
        [{"edge_id": "mr__dl", "sent": 2001}], [],
        E(0, 121, 1701, -1822, None, [T("mr__dl", 2001, 2001, 121)],
          stranded=[{"program": "dl", "points": 2001, "value_cents": 2301}]),
        "Ceiling rounding on the fee: 2001*60/1000 = 120.06c rounds up to 121c. "
        "H1 = 2,999 MR (5998c) + 2,001 DL (2001*1150//1000 = 2301c) = 8299c; "
        "V(H0) = 10000c; points_cost = 1701c; net = -121-1701 = -1822c.",
    ),
    A(
        "acc_13", "fee", "small_c", {"mrx": 200000},
        [{"edge_id": "mrx__uax", "sent": 190000}],
        [
            _award("uax", "uax_bus_nyc_mia", 95000, 5600, 150000),
            _award("uax", "uax_bus_mia_nyc", 95000, 5600, 150000),
        ],
        E(300000, 21100, 380000, -101100, 1520, [T("mrx__uax", 190000, 190000, 9900)]),
        "FR-7b merging: both one-ways are paid from UAX, so one transfer of 190,000 pays "
        "the cap once - ceil(190000*60/1000) = 11400c capped to 9900c. "
        "outlay = 9900 + 2 x 5600 = 21100c; V(H0) = 400000c; H1 = 10,000 MRX = 20000c; "
        "points_cost = 380000c; net = -101100c; pooled cpp = (300000-11200)*1000//190000 "
        "= 1520.",
    ),
    A(
        "acc_14", "fee", "small_c", {"mrx": 200000},
        [{"edge_id": "mrx__uax", "sent": 95000}, {"edge_id": "mrx__uax", "sent": 95000}],
        [
            _award("uax", "uax_bus_nyc_mia", 95000, 5600, 150000),
            _award("uax", "uax_bus_mia_nyc", 95000, 5600, 150000),
        ],
        E(300000, 22600, 380000, -102600, 1520,
          [T("mrx__uax", 95000, 95000, 5700), T("mrx__uax", 95000, 95000, 5700)]),
        "The counterfactual acc_13 exists to rule out: two unmerged transfers each pay "
        "ceil(95000*60/1000) = 5700c, i.e. 11400c total, 1500c worse than the merged "
        "9900c. Everything else is identical, so net = -102600c. The engine must never "
        "emit this shape (one transfer step per edge), but the arithmetic must be right.",
    ),
    A(
        "acc_15", "fee", "small_a", {"ur": 62000},
        [{"edge_id": "ur__fb", "sent": 22000}],
        [_award("fb", "fb_eco_nyc_par", 22000, 22400, 60000)],
        E(60000, 22400, 45100, -7500, 1709, [T("ur__fb", 22000, 22000, 0)]),
        "A zero-fee edge with heavy carrier charges: cpp = (60000-22400)*1000//22000 = "
        "1709 looks respectable, but V(H0) = 127100c and H1 = 40,000 UR = 82000c give "
        "points_cost = 45100c, so net = 60000-22400-45100 = -7500c. High cpp, negative "
        "net - the conflict the ranking rule exists to resolve.",
    ),
    A(
        "acc_16", "fee", "small_a", {"mr": 1000},
        [{"edge_id": "mr__dl", "sent": 1}], [],
        E(0, 1, 1, -2, None, [T("mr__dl", 1, 1, 1)],
          stranded=[{"program": "dl", "points": 1, "value_cents": 1}]),
        "The smallest possible fee: 1 x 60 mcpp = 0.06c, which ceils to 1c. "
        "H1 = 999 MR (1998c) + 1 DL (1150//1000 = 1c) = 1999c against V(H0) = 2000c, so "
        "points_cost = 1c and net = -2c.",
    ),
    # -- 6 tier cases ------------------------------------------------------
    A(
        "acc_17", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 57000}], [],
        E(0, 0, 20900, -20900, None, [T("mb__fb", 57000, 19000, 0)],
          stranded=[{"program": "fb", "points": 19000, "value_cents": 24700}]),
        "Below the first tier: delivered(57000) = 57000//3 + 5000*(57000//60000) = "
        "19000 + 0 = 19000. H1 = 143,000 MB (114400c) + 19,000 FB (24700c) = 139100c "
        "against V(H0) = 160000c, so points_cost = 20900c.",
    ),
    A(
        "acc_18", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 60000}], [],
        E(0, 0, 15500, -15500, None, [T("mb__fb", 60000, 25000, 0)],
          stranded=[{"program": "fb", "points": 25000, "value_cents": 32500}]),
        "Exactly one tier: delivered(60000) = 20000 + 5000 = 25000. Sending 3,000 more "
        "points than acc_17 delivers 6,000 more miles, which is why tier boundaries are "
        "explicit lattice candidates. points_cost = 160000 - (112000 + 32500) = 15500c.",
    ),
    A(
        "acc_19", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 120000}], [],
        E(0, 0, 31000, -31000, None, [T("mb__fb", 120000, 50000, 0)],
          stranded=[{"program": "fb", "points": 50000, "value_cents": 65000}]),
        "Two tiers: delivered(120000) = 40000 + 10000 = 50000. "
        "H1 = 80,000 MB (64000c) + 50,000 FB (65000c) = 129000c; points_cost = 31000c.",
    ),
    A(
        "acc_20", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 63000}], [],
        E(0, 0, 16600, -16600, None, [T("mb__fb", 63000, 26000, 0)],
          stranded=[{"program": "fb", "points": 26000, "value_cents": 33800}]),
        "Just past a boundary: delivered(63000) = 21000 + 5000 = 26000. The bonus is a "
        "step function, so the extra 3,000 points buy only 1,000 miles here.",
    ),
    A(
        "acc_21", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 180000}], [FB_OUT],
        E(210000, 25100, 124500, 60400, 3081, [T("mb__fb", 180000, 75000, 0)],
          stranded=[{"program": "fb", "points": 15000, "value_cents": 19500}]),
        "Three tiers: delivered(180000) = 60000 + 15000 = 75000, of which 60,000 pay the "
        "award and 15,000 strand at FB's 1,300 mcpp = 19500c. "
        "H1 = 20,000 MB (16000c) + 15,000 FB (19500c) = 35500c; points_cost = 124500c; "
        "net = 210000-25100-124500 = 60400c.",
    ),
    A(
        "acc_22", "tier", "small_a", {"mb": 200000},
        [{"edge_id": "mb__fb", "sent": 117000}], [],
        E(0, 0, 36400, -36400, None, [T("mb__fb", 117000, 44000, 0)],
          stranded=[{"program": "fb", "points": 44000, "value_cents": 57200}]),
        "One increment below the second boundary: delivered(117000) = 39000 + 5000 = "
        "44000, six thousand miles short of what 120,000 delivers (acc_19).",
    ),
    # -- 8 stranding / increment cases -------------------------------------
    A(
        "acc_23", "stranding", "small_a", {"ur": 100000},
        [{"edge_id": "ur__fb", "sent": 62000}], [FB_OUT],
        E(210000, 25100, 124500, 60400, 3081, [T("ur__fb", 62000, 62000, 0)],
          stranded=[{"program": "fb", "points": 2000, "value_cents": 2600}]),
        "Over-transferring by one 2,000 increment strands 2,000 FB miles worth "
        "2000*1300//1000 = 2600c. net drops from acc_02's 61900c to 60400c - exactly the "
        "1500c the 2,000 points lost by moving from 2,050 to 1,300 mcpp.",
    ),
    A(
        "acc_24", "stranding", "small_a", {"mr": 100000},
        [{"edge_id": "mr__fb", "sent": 64000}], [FB_OUT],
        E(210000, 25100, 122800, 62100, 3081, [T("mr__fb", 64000, 64000, 0)],
          stranded=[{"program": "fb", "points": 4000, "value_cents": 5200}]),
        "Two increments of waste from a 2,000 mcpp source: 4,000 FB miles = 5200c "
        "stranded. H1 = 36,000 MR (72000c) + 4,000 FB (5200c) = 77200c; "
        "points_cost = 122800c; net = 62100c.",
    ),
    A(
        "acc_25", "stranding", "small_d", {"mrd": 50000},
        [{"edge_id": "mrd__hild", "sent": 20000}], [],
        E(0, 0, 20000, -20000, None, [T("mrd__hild", 20000, 40000, 0)],
          stranded=[{"program": "hild", "points": 40000, "value_cents": 20000}]),
        "A 1:2 ratio doubles the point count and halves the value: 20,000 MRD (40000c of "
        "baseline) become 40,000 HILD worth 40000*500//1000 = 20000c. The stranded "
        "caveat's value is the *destination* valuation, so it reads 20000c, not the "
        "40000c the points cost.",
    ),
    A(
        "acc_26", "stranding", "small_d", {"mrd": 50000},
        [{"edge_id": "mrd__hild", "sent": 30000}],
        [_cash("hild", "hild_credit", 50000, 20000)],
        E(20000, 0, 55000, -35000, 400, [T("mrd__hild", 30000, 60000, 0)],
          cash_received=20000,
          stranded=[{"program": "hild", "points": 10000, "value_cents": 5000}]),
        "hild_credit redeems in 1,000-point increments from 10,000, so 60,000 delivered "
        "HILD can all be redeemed - here only 50,000 are, leaving 10,000 stranded "
        "(5000c). cash received = 50000*400//1000 = 20000c; "
        "V(H0) = 100000c; H1 = 20,000 MRD (40000c) + 10,000 HILD (5000c) = 45000c.",
    ),
    A(
        "acc_27", "stranding", "small_a", {"mb": 130000},
        [{"edge_id": "mb__fb", "sent": 129000}], [],
        E(0, 0, 34300, -34300, None, [T("mb__fb", 129000, 53000, 0)],
          stranded=[{"program": "fb", "points": 53000, "value_cents": 68900}]),
        "Everything strands: delivered(129000) = 43000 + 10000 = 53000 miles with no "
        "booking to spend them on, worth 68900c against the 103200c of MB value given "
        "up (129000*800//1000). H1 = 1,000 MB (800c) + 53,000 FB (68900c) = 69700c.",
    ),
    A(
        "acc_28", "stranding", "small_a", {"ur": 200000},
        [{"edge_id": "ur__fb", "sent": 62000}, {"edge_id": "ur__ac", "sent": 72000}],
        [FB_OUT, _award("ac", "ac_bus_par_nyc", 70000, 12800, 210000)],
        E(420000, 37900, 269100, 113000, 2939,
          [T("ur__fb", 62000, 62000, 0), T("ur__ac", 72000, 72000, 0)],
          stranded=[
              {"program": "ac", "points": 2000, "value_cents": 3000},
              {"program": "fb", "points": 2000, "value_cents": 2600},
          ]),
        "A cross-program round trip strands in two places: 2,000 FB (2600c) and 2,000 AC "
        "(3000c) - the same point count at different valuations. "
        "H1 = 66,000 UR (135300c) + 2,000 FB (2600c) + 2,000 AC (3000c) = 140900c; "
        "points_cost = 269100c; outlay = 25100 + 12800 = 37900c; net = 113000c; "
        "pooled cpp = (420000-37900)*1000//130000 = 2939.",
    ),
    A(
        "acc_29", "stranding", "small_d", {"mrd": 30000},
        [{"edge_id": "mrd__hild", "sent": 30000}],
        [_cash("hild", "hild_credit", 54000, 21600)],
        E(21600, 0, 57000, -35400, 400, [T("mrd__hild", 30000, 60000, 0)],
          cash_received=21600,
          stranded=[{"program": "hild", "points": 6000, "value_cents": 3000}]),
        "Redeeming 54,000 of the 60,000 delivered HILD yields 21600c and strands 6,000 "
        "points (3000c). V(H0) = 60000c; H1 = 6,000 HILD = 3000c; points_cost = 57000c; "
        "net = -35400c; cpp = 21600*1000//54000 = 400, the option's own rate.",
    ),
    A(
        "acc_30", "stranding", "small_c", {"mrx": 200000},
        [{"edge_id": "mrx__mbx", "sent": 141000}, {"edge_id": "mbx__alx", "sent": 141000}],
        [_award("alx", "alx_bus_nyc_mia", 55000, 8000, 150000)],
        E(150000, 8000, 279200, -137200, 2581,
          [T("mrx__mbx", 141000, 141000, 0), T("mbx__alx", 141000, 57000, 0)],
          stranded=[{"program": "alx", "points": 2000, "value_cents": 2800}]),
        "A two-hop chain: 141,000 MRX -> 141,000 MBX -> delivered(141000) = 47000 + "
        "5000*2 = 57000 ALX, of which 55,000 pay the award and 2,000 strand at 1,400 "
        "mcpp = 2800c. MBX ends at 0 so it raises no stranding caveat even though the "
        "plan transferred into it. V(H0) = 400000c; "
        "H1 = 59,000 MRX (118000c) + 2,000 ALX (2800c) = 120800c; "
        "points_cost = 279200c; net = 150000-8000-279200 = -137200c.",
    ),
]


# --------------------------------------------------------------------------
# 60 parser cases (M5)
# --------------------------------------------------------------------------


def _spec(
    origin: str,
    dest: str,
    month: str,
    cabin: str | None,
    rt: bool,
    pax: int = 1,
) -> dict[str, Any]:
    goal = flight_goal(origin, dest, month, cabin=cabin, rt=rt, pax=pax)
    goal.pop("book_by")
    return goal


def _stay_spec(city: str, nights: int, month: str) -> dict[str, Any]:
    goal = stay_goal(city, nights, month)
    goal.pop("book_by")
    return goal


def P(
    case_id: str, group: str, text: str, today: str, truth: dict[str, Any], **ctx: Any
) -> dict[str, Any]:
    case = {"id": case_id, "group": group, "text": text, "today": today, "truth": truth}
    case.update(ctx)
    return case


def _ok(spec: dict[str, Any]) -> dict[str, Any]:
    return {"outcome": "goal", "spec": spec}


def _err(missing: list[str], ambiguous: list[str] | None = None) -> dict[str, Any]:
    return {"outcome": "error", "missing": missing, "ambiguous": ambiguous or []}


D0 = "2026-07-31"

PARSER_CASES: list[dict[str, Any]] = [
    # -- 25 plain-form trip requests ---------------------------------------
    P("p_01", "plain", "business class NYC to Paris in October", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_02", "plain", "round-trip business NYC to Paris in October", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", True))),
    P("p_03", "plain", "one-way economy New York to London in November", D0,
      _ok(_spec("NYC", "LON", "2026-11", "economy", False))),
    P("p_04", "plain", "first class Chicago to Rome in December", D0,
      _ok(_spec("CHI", "ROM", "2026-12", "first", False))),
    P("p_05", "plain", "premium economy San Francisco to Tokyo in November", D0,
      _ok(_spec("SFO", "TYO", "2026-11", "premium_economy", False))),
    P("p_06", "plain", "round trip first class Los Angeles to Sydney in December for 2", D0,
      _ok(_spec("LAX", "SYD", "2026-12", "first", True, 2))),
    P("p_07", "plain", "economy Miami to Madrid in November", D0,
      _ok(_spec("MIA", "MAD", "2026-11", "economy", False))),
    P("p_08", "plain", "business Lisbon to New York in October", D0,
      _ok(_spec("LIS", "NYC", "2026-10", "business", False))),
    P("p_09", "plain", "coach Chicago to Rome in October", D0,
      _ok(_spec("CHI", "ROM", "2026-10", "economy", False))),
    P("p_10", "plain", "biz NYC to Tokyo in November", D0,
      _ok(_spec("NYC", "TYO", "2026-11", "business", False))),
    P("p_11", "plain", "PE Paris to New York in October", D0,
      _ok(_spec("PAR", "NYC", "2026-10", "premium_economy", False))),
    P("p_12", "plain", "return business Rome to Chicago in October", D0,
      _ok(_spec("ROM", "CHI", "2026-10", "business", True))),
    P("p_13", "plain", "roundtrip economy Madrid to Miami in November", D0,
      _ok(_spec("MAD", "MIA", "2026-11", "economy", True))),
    P("p_14", "plain", "Singapore to Hong Kong in October in business", D0,
      _ok(_spec("SIN", "HKG", "2026-10", "business", False))),
    P("p_15", "plain", "one way business Sydney to Los Angeles in December", D0,
      _ok(_spec("SYD", "LAX", "2026-12", "business", False))),
    P("p_16", "plain", "business class New York to Paris in October for 3 people", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False, 3))),
    P("p_17", "plain", "economy New York to London in October for two", D0,
      _ok(_spec("NYC", "LON", "2026-10", "economy", False, 2))),
    P("p_18", "plain", "business Chicago to Rome in October party of 4", D0,
      _ok(_spec("CHI", "ROM", "2026-10", "business", False, 4))),
    P("p_19", "plain", "round-trip premium economy Tokyo to San Francisco in November", D0,
      _ok(_spec("TYO", "SFO", "2026-11", "premium_economy", True))),
    P("p_20", "plain", "first Paris to New York in October", D0,
      _ok(_spec("PAR", "NYC", "2026-10", "first", False))),
    P("p_21", "plain", "business class from New York to Lisbon in October", D0,
      _ok(_spec("NYC", "LIS", "2026-10", "business", False))),
    P("p_22", "plain", "economy to Paris from New York in November", D0,
      _ok(_spec("NYC", "PAR", "2026-11", "economy", False))),
    P("p_23", "plain", "business NYC to PAR in October", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_24", "plain", "economy JFK to CDG in October", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "economy", False))),
    P("p_25", "plain", "round-trip business class New York City to Paris in October", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", True))),
    # -- 15 alias / gazetteer forms ----------------------------------------
    P("p_26", "alias", "business nyc to paris in october", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_27", "alias", "business new york to paris in october", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_28", "alias", "business new york city to paris in october", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_29", "alias", "business EWR to CDG in october", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_30", "alias", "business LGA to ORY in october", D0,
      _ok(_spec("NYC", "PAR", "2026-10", "business", False))),
    P("p_31", "alias", "business ORD to FCO in october", D0,
      _ok(_spec("CHI", "ROM", "2026-10", "business", False))),
    P("p_32", "alias", "business MDW to FCO in october", D0,
      _ok(_spec("CHI", "ROM", "2026-10", "business", False))),
    P("p_33", "alias", "business HND to SFO in november", D0,
      _ok(_spec("TYO", "SFO", "2026-11", "business", False))),
    P("p_34", "alias", "business NRT to SFO in november", D0,
      _ok(_spec("TYO", "SFO", "2026-11", "business", False))),
    P("p_35", "alias", "business LHR to JFK in october", D0,
      _ok(_spec("LON", "NYC", "2026-10", "business", False))),
    P("p_36", "alias", "business LGW to EWR in october", D0,
      _ok(_spec("LON", "NYC", "2026-10", "business", False))),
    P("p_37", "alias", "business hong kong to singapore in october", D0,
      _ok(_spec("HKG", "SIN", "2026-10", "business", False))),
    P("p_38", "alias", "business los angeles to sydney in december", D0,
      _ok(_spec("LAX", "SYD", "2026-12", "business", False))),
    P("p_39", "alias", "business san francisco to tokyo in november", D0,
      _ok(_spec("SFO", "TYO", "2026-11", "business", False))),
    P("p_40", "alias", "business lisbon to madrid in october", D0,
      _ok(_spec("LIS", "MAD", "2026-10", "business", False))),
    # -- 10 relative-month cases -------------------------------------------
    P("p_41", "month", "business NYC to London in October", "2026-07-31",
      _ok(_spec("NYC", "LON", "2026-10", "business", False))),
    P("p_42", "month", "business NYC to London in October", "2026-11-15",
      _ok(_spec("NYC", "LON", "2027-10", "business", False))),
    P("p_43", "month", "business NYC to London in October", "2026-10-05",
      _ok(_spec("NYC", "LON", "2026-10", "business", False))),
    P("p_44", "month", "business NYC to London in March", "2026-07-31",
      _ok(_spec("NYC", "LON", "2027-03", "business", False))),
    P("p_45", "month", "business NYC to London in March", "2027-02-01",
      _ok(_spec("NYC", "LON", "2027-03", "business", False))),
    P("p_46", "month", "business NYC to London in January", "2026-12-20",
      _ok(_spec("NYC", "LON", "2027-01", "business", False))),
    P("p_47", "month", "business NYC to London in December", "2026-01-10",
      _ok(_spec("NYC", "LON", "2026-12", "business", False))),
    P("p_48", "month", "business NYC to London in 2027-05", "2026-07-31",
      _ok(_spec("NYC", "LON", "2027-05", "business", False))),
    P("p_49", "month", "business NYC to London in October 2028", "2026-07-31",
      _ok(_spec("NYC", "LON", "2028-10", "business", False))),
    P("p_50", "month", "business NYC to London in June", "2026-06-30",
      _ok(_spec("NYC", "LON", "2026-06", "business", False))),
    # -- 5 cash / stay intents ---------------------------------------------
    P("p_51", "intent", "turn everything into cash", D0, _ok(cash_goal())),
    P("p_52", "intent", "cash out my points", D0, _ok(cash_goal())),
    P("p_53", "intent", "a week at a hotel in Paris in December", D0,
      _ok(_stay_spec("PAR", 7, "2026-12"))),
    P("p_54", "intent", "5 nights in Tokyo in November", D0,
      _ok(_stay_spec("TYO", 5, "2026-11"))),
    P("p_55", "intent", "statement credit please", D0, _ok(cash_goal())),
    # -- 5 negative cases --------------------------------------------------
    P("p_56", "negative", "business class somewhere warm", D0,
      _err(["dest_city", "origin_city", "travel_month"])),
    P("p_57", "negative", "next spring", D0,
      _err(["dest_city", "origin_city", "travel_month"])),
    P("p_58", "negative", "fly to Paris", D0, _err(["origin_city", "travel_month"])),
    P("p_59", "negative", "hotel in Paris", D0, _err(["nights", "travel_month"])),
    P("p_60", "negative", "business New York to Paris to London in October", D0,
      _err([], ["cities"])),
]


# --------------------------------------------------------------------------
# 12 shipped-world goals (M6) and the D0 execution scripts
# --------------------------------------------------------------------------


def G(
    goal_id: str,
    note: str,
    cards: list[str],
    balances: dict[str, int],
    goal: dict[str, Any],
    today: str = D0,
    params: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "id": goal_id,
        "note": note,
        "cards": cards,
        "balances": balances,
        "goal": goal,
        "today": today,
        "params": params or {},
    }


SHIPPED_WORLD_GOALS: list[dict[str, Any]] = [
    G("sw_01", "flagship round trip on a single transferable currency",
      ["chase_sapphire_reserve"], {"chase_ur": 210000},
      flight_goal("NYC", "PAR", "2026-10", rt=True)),
    G("sw_02", "single one-way award from Amex",
      ["amex_platinum"], {"amex_mr": 130000}, flight_goal("NYC", "PAR", "2026-10")),
    G("sw_03", "2-passenger one-way that needs Marriott-hub funding",
      ["amex_platinum"], {"amex_mr": 100000, "marriott_bonvoy": 400000},
      flight_goal("NYC", "PAR", "2026-10", pax=2)),
    G("sw_04", "multi-source split: neither balance covers the award alone",
      ["chase_sapphire_reserve", "amex_platinum"], {"chase_ur": 40000, "amex_mr": 40000},
      flight_goal("NYC", "PAR", "2026-10")),
    G("sw_05", "mixed award/portal round trip",
      ["chase_sapphire_reserve"], {"chase_ur": 350000},
      flight_goal("NYC", "PAR", "2026-10", rt=True)),
    G("sw_06", "hotel stay funded by a bank transfer",
      ["chase_sapphire_reserve"], {"chase_ur": 100000}, stay_goal("PAR", 3, "2026-10")),
    G("sw_07", "cash liquidation across two currencies",
      ["chase_sapphire_reserve", "amex_platinum"],
      {"chase_ur": 210000, "amex_mr": 130000}, cash_goal()),
    G("sw_08", "Citi to Turkish on a secondary route",
      ["citi_premier"], {"citi_typ": 120000}, flight_goal("CHI", "ROM", "2026-10")),
    G("sw_09", "Capital One to Avianca, tight booking window",
      ["capital_one_venture_x"], {"capital_one": 150000},
      flight_goal("NYC", "LIS", "2026-10")),
    G("sw_10", "Bilt funding a United round trip in November",
      ["bilt_mastercard_reserve"], {"bilt_rewards": 200000},
      flight_goal("SFO", "TYO", "2026-11", rt=True)),
    G("sw_11", "Amex to ANA: a two-day transfer against an open deadline",
      ["amex_business_platinum"], {"amex_mr": 200000},
      flight_goal("SFO", "TYO", "2026-11")),
    G("sw_12", "economy award with a portal comparator",
      ["chase_sapphire_preferred"], {"chase_ur": 90000},
      flight_goal("NYC", "LON", "2026-10", cabin="economy")),
]

EXEC_SCRIPTS: list[dict[str, Any]] = [
    {
        "id": "exec_01",
        "scenario": "hd_01",
        "note": "one transfer then the award booking; balances must replay exactly",
        "steps": [
            {"seq": 1, "at": "2026-05-02T09:00:00Z", "confirm_irreversible": True},
            {"seq": 2, "at": "2026-05-02T09:05:00Z", "confirm_irreversible": True},
        ],
    },
    {
        "id": "exec_02",
        "scenario": "hd_06",
        "note": "a cash plan whose steps write cash_redeem entries",
        "steps": [
            {"seq": 1, "at": "2026-05-03T10:00:00Z", "confirm_irreversible": True},
            {"seq": 2, "at": "2026-05-03T10:01:00Z", "confirm_irreversible": False},
        ],
    },
]


def write(name: str, payload: Any) -> None:
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path.name} ({len(payload) if isinstance(payload, list) else 1} rows)")


def main() -> int:
    assert len(ACCOUNTING_CASES) == 30, f"expected 30 accounting cases, got {len(ACCOUNTING_CASES)}"
    assert len(PARSER_CASES) == 60, f"expected 60 parser cases, got {len(PARSER_CASES)}"
    assert len(SHIPPED_WORLD_GOALS) == 12, "expected 12 shipped-world goals"
    for case in ACCOUNTING_CASES:
        exp = case["expected"]
        assert (
            exp["net_value_cents"]
            == exp["gross_value_cents"] - exp["cash_outlay_cents"] - exp["points_cost_cents"]
        ), f"{case['id']}: net does not equal gross - outlay - points_cost"
    write("accounting_cases.json", ACCOUNTING_CASES)
    write("parser_cases.json", PARSER_CASES)
    write("shipped_world_goals.json", SHIPPED_WORLD_GOALS)
    write("exec_scripts.json", EXEC_SCRIPTS)
    return 0


if __name__ == "__main__":
    _ = month_window  # re-exported for fixture authors
    raise SystemExit(main())

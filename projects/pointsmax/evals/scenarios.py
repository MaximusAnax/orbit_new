"""The 64 hand-composed search scenarios (inputs only) and the M6 goal list.

Nothing here is ground truth: every scenario is a *question*
(world + cards + balances + goal + today + params).  The answers come from
``oracle.py`` when ``generate_search_cases.py`` runs, except for the six
``hand_derived`` scenarios, whose ``rationale`` states the answer worked out by
hand — the generator asserts the oracle reproduces it (EVALS self-check 2).

Composition (EVALS "Files" table): 52 small + 12 stress —
6 straightforward, 8 multi-hop, 8 multi-source, 6 fee/tier, 6 round-trip,
6 cash, 2 deadline-binding, 3 negative-verdict, 1 stale-world, 6 hand-derived.
"""

from __future__ import annotations

import calendar
from typing import Any


def month_window(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:7])
    return f"{month}-01", f"{month}-{calendar.monthrange(year, mon)[1]:02d}"


def flight(
    origin: str,
    dest: str,
    month: str,
    *,
    cabin: str | None = "business",
    rt: bool = False,
    pax: int = 1,
    book_by: str | None = None,
) -> dict[str, Any]:
    start, end = month_window(month)
    return {
        "kind": "flight",
        "origin_city": origin,
        "dest_city": dest,
        "cabin": cabin,
        "round_trip": rt,
        "passengers": pax,
        "travel_window_start": start,
        "travel_window_end": end,
        "book_by": book_by,
    }


def stay(city: str, nights: int, month: str) -> dict[str, Any]:
    start, end = month_window(month)
    return {
        "kind": "stay",
        "city": city,
        "nights": nights,
        "travel_window_start": start,
        "travel_window_end": end,
        "book_by": None,
    }


def cash(
    programs: list[str] | None = None, max_points: dict[str, int] | None = None
) -> dict[str, Any]:
    return {"kind": "cash", "cash_programs": programs, "cash_max_points": max_points}


def S(
    case_id: str,
    family: str,
    world: str,
    cards: list[str],
    balances: dict[str, int],
    goal: dict[str, Any],
    today: str,
    *,
    params: dict[str, int] | None = None,
    rationale: str | None = None,
    hand: dict[str, Any] | None = None,
    tier: str = "small",
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": case_id,
        "family": family,
        "tier": tier,
        "world": world,
        "cards": cards,
        "balances": balances,
        "goal": goal,
        "today": today,
        "params": params or {},
    }
    if rationale:
        case["rationale"] = rationale
    if hand is not None:
        case["hand"] = hand
    return case


D = "2026-07-31"

# --------------------------------------------------------------------------
# 6 straightforward single-source direct transfers
# --------------------------------------------------------------------------

STRAIGHTFORWARD = [
    S("sd_01", "straightforward", "small_a", ["ur_premium"], {"ur": 120000},
      flight("NYC", "PAR", "2026-10"), D),
    S("sd_02", "straightforward", "small_a", ["mr_premium"], {"mr": 100000},
      flight("NYC", "PAR", "2026-10"), D),
    S("sd_03", "straightforward", "small_d", ["csr"], {"urd": 100000},
      flight("NYC", "PAR", "2026-10"), "2026-05-01"),
    S("sd_04", "straightforward", "small_a", ["ur_premium"], {"ur": 60000},
      flight("NYC", "PAR", "2026-10", cabin="economy"), D),
    S("sd_05", "straightforward", "small_a", ["ur_premium"], {"ur": 80000, "fb": 20000},
      flight("NYC", "PAR", "2026-10"), D),
    S("sd_06", "straightforward", "small_c", ["mrx_card", "mrx_premium"], {"mrx": 120000},
      flight("NYC", "MIA", "2026-10"), D),
]

# --------------------------------------------------------------------------
# 8 multi-hop-required (the target is reachable, or cheapest, only via the hub)
# --------------------------------------------------------------------------

MULTI_HOP = [
    S("mh_01", "multi_hop", "small_b", ["card_p"], {"bank_p": 40000, "hub": 160000},
      flight("NYC", "LON", "2026-10"), D),
    S("mh_02", "multi_hop", "small_b", ["card_p"], {"bank_p": 100000, "hub": 60000},
      flight("NYC", "LON", "2026-10"), D),
    S("mh_03", "multi_hop", "small_b", ["card_q"], {"bank_q": 80000, "hub": 100000},
      flight("NYC", "LON", "2026-10"), D),
    S("mh_04", "multi_hop", "small_c", ["mrx_card"], {"mbx": 180000},
      flight("NYC", "MIA", "2026-10"), D),
    S("mh_05", "multi_hop", "small_c", ["mrx_card"], {"mrx": 60000, "mbx": 100000},
      flight("NYC", "MIA", "2026-10"), D),
    S("mh_06", "multi_hop", "small_b", ["card_p"], {"bank_p": 70000, "hub": 96000},
      flight("NYC", "LON", "2026-10"), D,
      rationale=(
          "No direct need-sized transfer fits (alpha needs 180,000 hub-side, only "
          "96,000 held; beta needs 95,000 bank-side, only 70,000 held), so a greedy "
          "planner finds nothing; the optimum tops the hub up from bank_p and rides "
          "the 3:1 + 5k/60k tier into air_alpha."
      )),
    S("mh_07", "multi_hop", "small_c", ["typx_card"], {"typx": 60000, "mbx": 120000},
      flight("NYC", "MIA", "2026-10"), D),
    S("mh_08", "multi_hop", "small_b", ["card_p"], {"hub": 180000},
      flight("NYC", "LON", "2026-10"), D),
]

# --------------------------------------------------------------------------
# 8 multi-source splits (no single balance covers the deficit)
# --------------------------------------------------------------------------

MULTI_SOURCE = [
    S("ms_01", "multi_source", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 40000, "mr": 40000}, flight("NYC", "PAR", "2026-10"), D),
    S("ms_02", "multi_source", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 30000, "mr": 50000}, flight("NYC", "PAR", "2026-10"), D),
    S("ms_03", "multi_source", "small_c", ["mrx_card", "typx_card"],
      {"mrx": 60000, "typx": 60000}, flight("MIA", "NYC", "2026-10", pax=2), D,
      rationale=(
          "Two passengers need 80,000 DLX; no single source covers it (mrx 60,000 "
          "direct, typx only via the 3:1 mbx hub), so the optimum splits mrx direct "
          "plus a typx->mbx->dlx chain while a single-source planner finds nothing."
      )),
    S("ms_04", "multi_source", "small_b", ["card_p", "card_q"],
      {"bank_p": 50000, "bank_q": 50000}, flight("NYC", "LON", "2026-10"), D),
    S("ms_05", "multi_source", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 30000, "mr": 20000, "fb": 20000}, flight("NYC", "PAR", "2026-10"), D),
    S("ms_06", "multi_source", "small_c", ["mrx_card", "typx_card"],
      {"mrx": 40000, "typx": 40000, "mbx": 60000}, flight("NYC", "MIA", "2026-10"), D),
    S("ms_07", "multi_source", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 40000, "mr": 40000}, flight("NYC", "PAR", "2026-10", cabin="economy", rt=True),
      D, params={"max_hops": 1},
      rationale=(
          "44,000 FB across two legs exceeds either balance; joint funding maxes the "
          "cheaper MR balance (40,000) and tops up 4,000 from UR, while sequential "
          "per-booking funding sends 22,000 from each and loses 50 mcpp on the "
          "UR-funded excess."
      )),
    S("ms_08", "multi_source", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 30000, "mr": 30000, "mb": 60000}, flight("NYC", "PAR", "2026-10"), D),
]

# --------------------------------------------------------------------------
# 6 fee-cap / tier-boundary cases
# --------------------------------------------------------------------------

FEE_TIER = [
    S("ft_01", "fee_tier", "small_c", ["mrx_card"], {"mbx": 150000},
      flight("NYC", "MIA", "2026-10"), D,
      rationale=(
          "Marriott-style tier straddle: the optimal DLX funding sends 105,000 MBX "
          "(35 x 3,000, crossing one 60k tier for exactly 40,000 delivered); a "
          "need-sized 3:1 planner sends 120,000, crosses two tiers, and strands "
          "10,000 DLX."
      )),
    S("ft_02", "fee_tier", "small_c", ["mrx_card"], {"mbx": 105000},
      flight("MIA", "NYC", "2026-10"), D,
      rationale=(
          "Tier straddle where only bonus-aware sizing fits: 105,000 MBX delivers "
          "35,000 + 5,000 = 40,000 exactly; the need-sized amount (120,000) exceeds "
          "the balance, so a tier-blind planner finds nothing."
      )),
    S("ft_03", "fee_tier", "stress_a", ["c1", "c2"], {"b1": 70000, "b2": 70000},
      flight("CHI", "ROM", "2026-10"), D, params={"max_hops": 1},
      rationale=(
          "Fee-boundary source trap: b2 has the lower valuation (2000 vs 2050 mcpp) "
          "but its edge to a3 carries a 60 mcpp excise fee, so the free b1 edge is "
          "cheaper per delivered point; a cheapest-valuation planner pays the fee."
      )),
    S("ft_04", "fee_tier", "small_c", ["mrx_card"], {"mbx": 120000, "mrx": 20000},
      flight("NYC", "MIA", "2026-10"), D),
    S("ft_05", "fee_tier", "small_c", ["mrx_card"], {"mbx": 168000},
      flight("NYC", "MIA", "2026-10"), D),
    S("ft_06", "fee_tier", "small_a", ["mr_premium"], {"mb": 180000, "mr": 20000},
      flight("NYC", "PAR", "2026-10"), D),
]

# --------------------------------------------------------------------------
# 6 round-trip pairs with shared-source conflicts (incl. a mixed award+portal)
# --------------------------------------------------------------------------

ROUND_TRIP = [
    S("rt_01", "round_trip", "small_d", ["csr", "gold"],
      {"urd": 64000, "mrd": 64000},
      flight("NYC", "PAR", "2026-10", rt=True), D, params={"max_hops": 1},
      rationale=(
          "Shared-source conflict: both 60,000-mile FBD legs draw on URD + MRD "
          "jointly; the optimum exhausts the cheaper MRD balance (64,000) and covers "
          "56,000 from URD, while per-leg sequential funding sends 60,000 + 60,000 "
          "and pays 50 mcpp more on 4,000 points."
      )),
    S("rt_02", "round_trip", "small_a", ["mr_premium"], {"mr": 100000, "mb": 80000},
      flight("NYC", "PAR", "2026-10", rt=True), D),
    S("rt_03", "round_trip", "small_a", ["ur_premium", "mr_premium"],
      {"ur": 64000, "mr": 32000},
      flight("NYC", "PAR", "2026-10", cabin="economy", rt=True), D,
      params={"max_hops": 1},
      rationale=(
          "Economy round trip, 44,000 FB total: joint funding maxes MR at 32,000 and "
          "adds 12,000 UR; greedy funds the first leg fully from MR (22,000), leaves "
          "10,000 MR stranded below the second leg's need, and over-pays from UR."
      )),
    S("rt_04", "round_trip", "small_b", ["card_p"],
      {"bank_p": 120000, "hub": 150000},
      flight("NYC", "LON", "2026-10", rt=True), "2026-08-05", params={"max_hops": 1},
      rationale=(
          "The only fundable pairing books alpha out (hub sends exactly 150,000 "
          "= 3:1 across two 5k tiers for 60,000 miles) and beta back from bank_p "
          "(95,000).  A need-sized planner asks the hub for 180,000 it does not "
          "have, so it can fund no round trip at all."
      )),
    S("rt_05", "round_trip", "small_c", ["mrx_card"], {"mrx": 60000, "mbx": 105000},
      flight("NYC", "MIA", "2026-10", rt=True), D,
      rationale=(
          "Joint DLX funding must mix the fee-bearing 1:1 MRX edge with the 3:1 "
          "tier-bonus MBX edge; need-sized single-source transfers cannot fund the "
          "80,000 total, and greedy's fallback set (saver out) nets less."
      )),
    S("rt_06", "round_trip", "small_a", ["mr_premium"], {"mr": 64000, "mb": 150000},
      flight("NYC", "PAR", "2026-10", rt=True), D,
      rationale=(
          "150,000 MB delivers exactly 60,000 FB (3:1 plus two 5k tier bonuses) and "
          "MR covers the other leg; a need-sized 3:1 planner asks MB for 180,000 and "
          "cannot fund the pair without stranding."
      )),
]

# --------------------------------------------------------------------------
# 6 cash goals (liquid-method filter, gating, quantization, below-baseline)
# --------------------------------------------------------------------------

CASH = [
    S("cash_01", "cash", "small_d", ["csr"], {"urd": 210000, "mrd": 130000},
      cash(), "2026-05-01"),
    S("cash_02", "cash", "small_d", [], {"urd": 100000}, cash(), "2026-05-01"),
    S("cash_03", "cash", "small_d", ["gold"], {"mrd": 120000}, cash(), "2026-05-01"),
    S("cash_04", "cash", "small_a", ["ur_premium", "mr_premium"], {"ur": 50000, "mr": 33500},
      cash(), D),
    S("cash_05", "cash", "small_a", ["ur_premium"], {"ur": 40000, "mr": 60000},
      cash(programs=["ur"]), D),
    S("cash_06", "cash", "small_d", ["csr", "csp"], {"urd": 210000},
      cash(max_points={"urd": 100000}), "2026-05-01"),
]

# --------------------------------------------------------------------------
# 2 deadline-binding (a cheaper plan exists but its transfer lands too late)
# --------------------------------------------------------------------------

DEADLINE = [
    S("dl_01", "deadline", "small_b", ["card_p"], {"bank_p": 60000, "hub": 180000},
      flight("NYC", "LON", "2026-10"), D),
    S("dl_02", "deadline", "small_b", ["card_p", "card_q"],
      {"bank_p": 100000, "bank_q": 100000},
      flight("NYC", "LON", "2026-10", book_by="2026-07-31"), D,
      params={"max_hops": 1},
      rationale=(
          "Same-day booking deadline: the cheaper bank_q edge to air_beta posts in "
          "1 day and misses it, so the correct plan pays more from bank_p's instant "
          "edge; a deadline-blind planner emits the infeasible bank_q plan."
      )),
]

# --------------------------------------------------------------------------
# 3 negative-verdict scenarios
# --------------------------------------------------------------------------

NEGATIVE = [
    S("nv_01", "negative", "small_a", ["ur_premium"], {"ur": 6000},
      flight("NYC", "PAR", "2026-10"), D),
    S("nv_02", "negative", "small_b", ["card_p"], {"bank_p": 4000},
      flight("NYC", "LON", "2026-10"), D),
    S("nv_03", "negative", "small_a", ["ur_premium"], {"ur": 120000},
      flight("NYC", "PAR", "2026-10", cabin="first"), D),
]

# --------------------------------------------------------------------------
# 1 stale-world scenario (small_d's valuations are as of 2026-01-05)
# --------------------------------------------------------------------------

STALE = [
    S("stale_01", "stale", "small_d", ["csr"], {"urd": 150000},
      flight("NYC", "PAR", "2026-10"), "2026-08-01"),
]

# --------------------------------------------------------------------------
# 6 hand-derived scenarios: the answer is worked out by hand in `rationale`
# --------------------------------------------------------------------------

HAND_DERIVED = [
    S(
        "hd_01", "hand_derived", "small_d", ["csr"], {"urd": 100000},
        flight("NYC", "PAR", "2026-10"), "2026-05-01",
        hand={"objective_cents": 61900, "plan_count": 1, "verdict": "book_with_points"},
        rationale=(
            "Only fbd_bus_nyc_par matches (60,000 miles, $251.00 fees); the CSR portal "
            "would need ceil(210000*1000/1500)=140,000 URD but only 100,000 are held, so "
            "the portal booking set cannot be funded. Funding: urd->fbd is 1:1 in 2,000 "
            "increments, so send exactly 60,000. gross = $2,100.00 = 210000c. "
            "cash_outlay = 25100c. V(H0) = 100000*2050//1000 = 205000c; H1 = 40,000 URD "
            "= 40000*2050//1000 = 82000c, fbd 0. points_cost = 205000-82000 = 123000c. "
            "net = 210000 - 25100 - 123000 = 61900c. pooled cpp = "
            "(210000-25100)*1000//60000 = 3081 mcpp. Verdict book_with_points, 1 plan."
        ),
    ),
    S(
        "hd_02", "hand_derived", "small_d", ["csr"], {"urd": 150000},
        flight("NYC", "PAR", "2026-10"), "2026-05-01",
        hand={"objective_cents": 61900, "plan_count": 2, "verdict": "book_with_points"},
        rationale=(
            "Two booking sets are now fundable. (a) award: as in hd_01 but from 150,000 "
            "URD - V(H0)=307500c, H1 90,000 URD =184500c, points_cost=123000c, "
            "net = 210000-25100-123000 = 61900c. (b) portal: 140,000 URD buys the "
            "210000c fare; points_cost = 307500 - (10000*2050//1000=20500) = 287000c, "
            "no fees, net = 210000-287000 = -77000c. Award ranks first (61900 > -77000); "
            "the portal plan is inside top-K so it is not flagged as a comparator. "
            "Verdict book_with_points."
        ),
    ),
    S(
        "hd_03", "hand_derived", "small_a", ["ur_premium"], {"ur": 62000},
        flight("NYC", "PAR", "2026-10", cabin="economy"), D,
        hand={"objective_cents": -7500, "plan_count": 2, "verdict": "pay_cash_keep_points"},
        rationale=(
            "Economy: fb_eco_nyc_par is 22,000 miles + $224.00 against a $600.00 fare. "
            "Award: send 22,000 UR (1:1, multiple of 2,000). V(H0)=62000*2050//1000="
            "127100c; H1 = 40,000 UR = 82000c; points_cost = 45100c; "
            "net = 60000-22400-45100 = -7500c, cpp = (60000-22400)*1000//22000 = 1709. "
            "Portal: ceil(60000*1000/1500) = 40,000 UR; points_cost = 127100 - "
            "(22000*2050//1000 = 45100) = 82000c; net = 60000-82000 = -22000c, "
            "cpp 1500. Award ranks first, and because the best net is negative the "
            "verdict is pay_cash_keep_points."
        ),
    ),
    S(
        "hd_04", "hand_derived", "small_b", ["card_p"], {"bank_p": 20000, "hub": 160000},
        flight("NYC", "LON", "2026-10"), D,
        hand={"objective_cents": 45000, "plan_count": 1, "verdict": "book_with_points"},
        rationale=(
            "alpha_bus_nyc_lon needs 60,000 Alpha miles. hub->air_alpha is 3:1 with "
            "+5,000 per 60,000 in 3,000 increments: delivered(150000) = 50000 + 5000*2 "
            "= 60000, and no smaller multiple reaches 60,000 (147000 -> 49000+10000 = "
            "59000). alpha_bus_nyc_lon_soon (52,000 miles) is cheaper but its "
            "bookable_until is 2026-08-01 and the hub transfer takes 2 days, so "
            "2026-07-31+2 = 2026-08-02 misses the deadline - it is correctly dropped. "
            "beta needs 95,000 miles: hub would need delivered >= 95000, i.e. 240,000 "
            "hub points, more than the 160,000 held, and bank_p holds only 20,000. "
            "The p_portal booking needs ceil(200000*1000/1400) = 142,858 bank_p points. "
            "So exactly one plan: gross 200000c, outlay 20000c, "
            "V(H0) = 20000*2000//1000 + 160000*900//1000 = 40000 + 144000 = 184000c, "
            "V(H1) = 40000 + (10000*900//1000 = 9000) = 49000c, points_cost 135000c, "
            "net = 200000-20000-135000 = 45000c, feasible_in_days = 2."
        ),
    ),
    S(
        "hd_05", "hand_derived", "small_c", ["mrx_card"], {"mrx": 60000},
        flight("NYC", "MIA", "2026-10"), D,
        hand={"objective_cents": 62000, "plan_count": 2, "verdict": "book_with_points"},
        rationale=(
            "Four awards match NYC->MIA business and only the two DLX ones are "
            "reachable. uax needs 95,000 miles over a 1:1 edge, more than the 60,000 "
            "held. alx needs 55,000 miles through mbx (3:1, +5,000 per 60,000, 3,000 "
            "increments): delivered(135000) = 45000 + 10000 = 55000, so 135,000 MBX "
            "would be needed and MBX must itself be funded 1:1 from MRX. The MRX portal "
            "is gated behind mrx_premium, which is not held.\n"
            "Plan A (dlx_bus_nyc_mia, 40,000 miles + $56.00): send 40,000 over mrx__dlx, "
            "fee = ceil(40000*60/1000) = 2400c (under the 4800c cap). gross = 150000c; "
            "outlay = 5600 + 2400 = 8000c; V(H0) = 120000c; H1 = 20,000 MRX = 40000c; "
            "points_cost = 80000c; net = 62000c; cpp = (150000-5600)*1000//40000 = 3610.\n"
            "Plan B (dlx_saver_nyc_mia, 20,000 miles + $500.00 of surcharges): send "
            "20,000, fee = 1200c. outlay = 51200c; H1 = 40,000 MRX = 80000c; "
            "points_cost = 40000c; net = 58800c; cpp = (150000-50000)*1000//20000 = "
            "5000. B has the higher realized cents-per-point and the lower net value, "
            "so A must rank first: this is exactly the cpp-vs-net conflict FR-9's "
            "objective exists to resolve."
        ),
    ),
    S(
        "hd_06", "hand_derived", "small_d", ["gold"], {"urd": 50000, "mrd": 30000},
        cash(), "2026-05-01",
        hand={"objective_cents": 74000, "plan_count": 5, "verdict": "cash_plan"},
        rationale=(
            "Liquid options only: urd_credit 1,000 mcpp, mrd_credit 600, mrd_deposit "
            "650 (1,000-point increments); urd_portal_* are portal_travel and are "
            "excluded, and their cards are not held anyway. The gold card enables MRD "
            "transfers, and mrd->hild is 1:2 into a 400 mcpp "
            "statement credit, i.e. 800 mcpp per MRD point, which beats 650, so the "
            "chain is cash-improving: 30,000 MRD -> 60,000 HILD -> 60000*400//1000 = "
            "24000c, versus 30000*650//1000 = 19500c direct. Best plan = urd_credit on "
            "50,000 (50000c) + hild_credit on 60,000 (24000c) = 74000c received. "
            "Verdict cash_plan."
        ),
    ),
]

# --------------------------------------------------------------------------
# 12 stress scenarios (>= 1 cash chain, >= 1 mixed award/portal RT, >= 1 fee cap)
# --------------------------------------------------------------------------

STRESS = [
    S("st_01", "stress_direct", "stress_a", ["c1"], {"b1": 120000},
      flight("NYC", "PAR", "2026-10"), D, tier="stress",
      rationale="Control scenario: a plain direct transfer is optimal, so even the "
      "greedy baseline should tie here (its one expected stress hit)."),
    S("st_02", "stress_hub", "stress_a", ["c1"], {"b1": 40000, "h1": 120000},
      flight("NYC", "PAR", "2026-10"), D, tier="stress",
      rationale=(
          "Hub top-up with tier sizing: a3 needs h1 at 150,000 (120,000 held plus "
          "30,000 from b1) to deliver 60,000 through the 3:1 + 5k/60k edge; no "
          "direct need-sized transfer fits any offer."
      )),
    S("st_03", "stress_split", "stress_a", ["c1", "c2"], {"b1": 30000, "b2": 30000},
      flight("CHI", "ROM", "2026-10"), D, tier="stress"),
    S("st_04", "stress_promo", "stress_a", ["c1", "c2"], {"b1": 44000, "b2": 60000},
      flight("NYC", "PAR", "2026-10"), "2026-08-05", tier="stress",
      params={"max_hops": 1},
      rationale=(
          "Promo + fee + split: the 4:5 b1->a4 promo (active until 08-20) plus a "
          "6,000-point b2 top-up beats funding a4 entirely from b2's fee-bearing "
          "edge, which is what a cheapest-valuation planner does."
      )),
    S("st_05", "stress_fee_merge", "stress_a", ["c2"], {"b2": 120000},
      flight("CHI", "ROM", "2026-10", rt=True), D, tier="stress"),
    S("st_06", "stress_mixed_rt", "stress_a", ["c1", "c3"], {"b1": 42000, "b3": 40000},
      flight("NYC", "PAR", "2026-10", cabin="economy", rt=True), D, tier="stress",
      params={"max_hops": 1},
      rationale=(
          "Economy round trip over shared b1 + b3 balances (mixed award/portal sets "
          "are enumerated: b1's 1.5cpp portal is fundable): joint funding maxes the "
          "cheaper b3 balance; sequential per-leg funding strands 15,000 b3 points "
          "below the second leg's need."
      )),
    S("st_07", "stress_cash_chain", "stress_a", ["c4"], {"b4": 100000},
      cash(), D, tier="stress"),
    S("st_08", "stress_stay", "stress_a", ["c1", "c2"], {"b1": 50000, "b2": 40000},
      stay("PAR", 3, "2026-10"), D, tier="stress",
      rationale=(
          "Three h2 nights need 75,000 points, more than either bank balance, so "
          "the only funding is a b1 + b2 split; a single-source planner reports "
          "nothing bookable."
      )),
    S("st_09", "stress_seats", "stress_a", ["c1", "c4"], {"b1": 70000, "b4": 60000},
      flight("NYC", "PAR", "2026-10", pax=2), D, tier="stress",
      params={"max_hops": 1},
      rationale=(
          "Two passengers on the seats-capped a3 offer (seats_available = 2) need "
          "120,000 delivered, which only a b1 + b4 split can fund; the seats_limited "
          "caveat must fire on the winning plan."
      )),
    S("st_10", "stress_deadline", "stress_a", ["c3"], {"b3": 90000},
      flight("SFO", "TYO", "2026-10", book_by="2026-07-31"), D, tier="stress",
      rationale=(
          "Same-day deadline: the cheaper a5 award needs b3's 1-day edge and lands "
          "late; the correct answer is the pricier instant a6 booking, which a "
          "deadline-blind planner never prefers."
      )),
    S("st_11", "stress_far_route", "stress_a", ["c3"], {"b3": 60000, "h1": 120000},
      flight("CHI", "ROM", "2026-10"), D, tier="stress",
      rationale=(
          "b3 alone cannot fund a6 (61,000 > 60,000); the only feasible plan tops "
          "h1 up to 129,000 for exactly 53,000 delivered into a3 through the tiered "
          "3:1 edge — unreachable for a direct need-sized planner."
      )),
    S("st_12", "stress_expired_promo", "stress_a", ["c1", "c3"], {"b1": 40000, "b3": 60000},
      flight("NYC", "PAR", "2026-10"), "2026-08-25", tier="stress",
      params={"max_hops": 1},
      rationale=(
          "The b1->a4 promo has expired but the b3->a1 promo just opened: the only "
          "feasible funding splits a1's 70,000 across the b3 promo edge (60,000) "
          "and b1 (10,000); single-source need-sized transfers all fail."
      )),
]

SEARCH_SCENARIOS: list[dict[str, Any]] = (
    STRAIGHTFORWARD
    + MULTI_HOP
    + MULTI_SOURCE
    + FEE_TIER
    + ROUND_TRIP
    + CASH
    + DEADLINE
    + NEGATIVE
    + STALE
    + HAND_DERIVED
    + STRESS
)

EXPECTED_COMPOSITION = {
    "straightforward": 6,
    "multi_hop": 8,
    "multi_source": 8,
    "fee_tier": 6,
    "round_trip": 6,
    "cash": 6,
    "deadline": 2,
    "negative": 3,
    "stale": 1,
    "hand_derived": 6,
}


def check_composition() -> None:
    """Assert the fixture composition EVALS.md specifies (52 small + 12 stress)."""
    counts: dict[str, int] = {}
    for case in SEARCH_SCENARIOS:
        counts[case["family"]] = counts.get(case["family"], 0) + 1
    for family, expected in EXPECTED_COMPOSITION.items():
        actual = counts.get(family, 0)
        if actual != expected:
            raise AssertionError(f"family {family}: expected {expected} scenarios, got {actual}")
    small = [c for c in SEARCH_SCENARIOS if c["tier"] == "small"]
    stress = [c for c in SEARCH_SCENARIOS if c["tier"] == "stress"]
    if len(small) != 52 or len(stress) != 12:
        raise AssertionError(f"expected 52 small + 12 stress, got {len(small)} + {len(stress)}")
    if len({c["id"] for c in SEARCH_SCENARIOS}) != 64:
        raise AssertionError("scenario ids are not unique")

"""FR-7 booking sets, candidate lattice and funding search — hard part A.

These tests pin the behaviours the exactness argument rests on: need
aggregation, edge merging, the tier-boundary lattice, multi-source splits,
multi-hop chains, timing feasibility, and the explicit expansion budget.
"""

from __future__ import annotations

from datetime import date

import pytest
from conftest import make_world, world_files
from pointsmax.engine.goals import cash_goal, flight_goal, stay_goal
from pointsmax.engine.search import (
    FundingSearch,
    SearchBudgetExceeded,
    SearchStats,
    aggregate_needs,
    booking_deadlines,
    cash_drafts,
    enumerate_booking_sets,
    solve_booking_set,
)
from pointsmax.engine.value import Booking
from pointsmax.engine.world import active_subgraph
from pointsmax.models import Cabin, PlanParams, StepKind

TODAY = date(2026, 7, 31)


def search_for(world, cards, balances, today=TODAY, params=None, pruning=True):
    active = active_subgraph(world, cards, today)
    return FundingSearch(world, active, balances, today, params or PlanParams(), pruning=pruning)


def booking(program_id, points, *, offer_id="o", deadline=None, fees=0, value=0):
    return Booking(
        kind=StepKind.BOOK_AWARD,
        program_id=program_id,
        points=points,
        fees_cents=fees,
        value_cents=value,
        offer_id=offer_id,
        deadline=deadline,
    )


# -- FR-7a booking-set enumeration ----------------------------------------


def test_fr7a_one_way_goal_yields_single_awards_and_single_portal_bookings(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    assert all(len(s) == 1 for s in sets)
    offers = {s[0].offer_id for s in sets if s[0].offer_id}
    cashouts = {s[0].cashout_id for s in sets if s[0].cashout_id}
    assert offers == {"x_out", "x_soon"}
    assert cashouts == {"a_portal"}


def test_fr7a_round_trip_yields_rt_awards_portal_rt_and_every_leg_mix(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    singles = [s for s in sets if len(s) == 1]
    pairs = [s for s in sets if len(s) == 2]

    assert {s[0].offer_id for s in singles if s[0].offer_id} == {"x_rt"}
    assert {s[0].cashout_id for s in singles if s[0].cashout_id} == {"a_portal"}
    # 3 outbound candidates (x_out, x_soon, portal) x 3 inbound (x_back, portal, ...)
    assert len(pairs) == 3 * 2
    mixed = [s for s in pairs if {b.kind for b in s} == {StepKind.BOOK_AWARD, StepKind.BOOK_PORTAL}]
    assert mixed, "award + portal mixes must be enumerated (SCOPE decision 9)"


def test_fr7a_award_award_mixes_may_span_different_programs(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=None, round_trip=True
    )
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    cross = [
        s
        for s in sets
        if len(s) == 2
        and len({b.program_id for b in s}) == 2
        and all(b.kind is StepKind.BOOK_AWARD for b in s)
    ]
    assert cross, "round trips must be allowed to mix programs across legs"


def test_fr7a_null_cabin_prices_a_portal_booking_per_available_cabin(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=None, round_trip=False
    )
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    portal_values = {s[0].value_cents for s in sets if s[0].cashout_id}
    assert portal_values == {200000, 120000}  # business and premium-economy fares


def test_fr7a_no_portal_option_means_award_only_sets(world):
    active = active_subgraph(world, ["card_a_basic"], TODAY)  # no portal, no transfers
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=False
    )
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    assert all(s[0].cashout_id is None for s in sets)


def test_fr7a_stay_goal_yields_award_and_portal_bookings(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = stay_goal(city="AAA", nights=3, month="2026-10")
    sets = enumerate_booking_sets(goal, world, active, TODAY)
    assert {s[0].offer_id or s[0].cashout_id for s in sets} == {"h_stay", "a_portal"}
    award = next(s[0] for s in sets if s[0].offer_id == "h_stay")
    assert award.points == 60000  # 3 nights x 20,000


def test_fr7a_enumeration_is_deterministic(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )
    first = enumerate_booking_sets(goal, world, active, TODAY)
    second = enumerate_booking_sets(goal, world, active, TODAY)
    assert [[b.sort_key() for b in s] for s in first] == [[b.sort_key() for b in s] for s in second]


# -- FR-7b need aggregation and edge merging ------------------------------


def test_fr7b_needs_are_aggregated_per_paying_program():
    bookings = (booking("air_x", 60000), booking("air_x", 50000), booking("air_y", 20000))
    assert aggregate_needs(bookings) == {"air_x": 110000, "air_y": 20000}


def test_fr7b_deadline_per_program_is_the_earliest_booking_deadline():
    bookings = (
        booking("air_x", 1, deadline=date(2026, 10, 31)),
        booking("air_x", 1, deadline=date(2026, 8, 1)),
        booking("air_y", 1, deadline=None),
    )
    assert booking_deadlines(bookings) == {
        "air_x": date(2026, 8, 1),
        "air_y": None,
    }


def test_fr7b_two_bookings_from_one_program_produce_one_merged_transfer(world):
    search = search_for(world, ["card_a"], {"bank_a": 200000})
    solution = search.solve({"air_x": 110000}, {"air_x": None})
    assert solution is not None
    assert len(solution.transfers) == 1
    assert solution.transfers[0].sent == 110000


def test_fr7b_merging_pays_a_capped_fee_once(world):
    """bank_a -> air_y charges 60 mcpp capped at $99: merging beats splitting."""
    search = search_for(world, ["card_a"], {"bank_a": 500000})
    merged = search.solve({"air_y": 300000}, {"air_y": None})
    assert merged is not None
    assert len(merged.transfers) == 1
    assert merged.transfers[0].fee_cents == 9900
    assert merged.transfers[0].fee_cents < 2 * 9000


# -- FR-7c the candidate lattice ------------------------------------------


def test_fr7c_lattice_stops_at_the_covering_amount(world):
    """No plan sends more than it needs: extra sending is weakly worse (invariant 4)."""
    search = search_for(world, ["card_a"], {"bank_a": 500000})
    solution = search.solve({"air_x": 60000}, {"air_x": None})
    assert solution is not None
    assert [(t.edge_id, t.sent) for t in solution.transfers] == [("bank_a__air_x", 60000)]


def test_fr7c_tier_boundary_beats_the_bare_covering_amount(world):
    """hotel_h -> air_x at 3:1 with +5,000 per 60,000: 60,000 out delivers 25,000."""
    search = search_for(world, [], {"hotel_h": 150000})
    solution = search.solve({"air_x": 25000}, {"air_x": None})
    assert solution is not None
    assert [(t.edge_id, t.sent, t.delivered) for t in solution.transfers] == [
        ("hotel_h__air_x", 60000, 25000)
    ]
    # Covering 25,000 without the bonus would need 75,000 hotel points.
    assert solution.transfers[0].sent < 75000


def test_fr7c_prefers_the_cheapest_source_by_loss_per_delivered_point(world):
    """hotel_h loses 620 mcpp per delivered point vs bank_a's 700 and bank_b's 500."""
    search = search_for(
        world, ["card_a", "card_b"], {"bank_a": 100000, "bank_b": 100000, "hotel_h": 150000}
    )
    solution = search.solve({"air_x": 60000}, {"air_x": None})
    assert solution is not None
    assert {t.from_program for t in solution.transfers} == {"bank_b"}


def test_fr7c_splits_across_sources_when_no_single_balance_covers(world):
    search = search_for(world, ["card_a", "card_b"], {"bank_a": 40000, "bank_b": 30000})
    solution = search.solve({"air_x": 70000}, {"air_x": None})
    assert solution is not None
    assert {t.from_program: t.sent for t in solution.transfers} == {
        "bank_a": 40000,
        "bank_b": 30000,
    }


def test_fr7c_multi_hop_chain_through_the_hotel_hub(world):
    """bank_b has no direct air_y edge; bank_b -> hotel_h -> air_x is two hops."""
    files = world_files()
    files["transfers.json"] = [e for e in files["transfers.json"] if e["id"] != "bank_b__air_x"]
    hub_world = make_world(files)
    search = search_for(hub_world, ["card_b"], {"bank_b": 200000})
    solution = search.solve({"air_x": 25000}, {"air_x": None})
    assert solution is not None
    assert sorted(t.edge_id for t in solution.transfers) == ["bank_b__hotel_h", "hotel_h__air_x"]
    assert solution.hop_index == {"bank_b__hotel_h": 0, "hotel_h__air_x": 1}
    assert solution.arrival_days["air_x"] == 2
    assert solution.feasible_in_days == 2


def test_fr7c_hop_cap_blocks_chains_that_are_too_long(world):
    files = world_files()
    files["transfers.json"] = [e for e in files["transfers.json"] if e["id"] != "bank_b__air_x"]
    hub_world = make_world(files)
    one_hop = PlanParams(max_hops=1)
    search = search_for(hub_world, ["card_b"], {"bank_b": 200000}, params=one_hop)
    assert search.solve({"air_x": 25000}, {"air_x": None}) is None


def test_fr7c_returns_none_when_the_need_cannot_be_covered(world):
    search = search_for(world, ["card_a"], {"bank_a": 1000})
    assert search.solve({"air_x": 60000}, {"air_x": None}) is None


def test_fr7c_no_transfers_needed_when_the_balance_already_covers(world):
    search = search_for(world, ["card_a"], {"air_x": 100000})
    solution = search.solve({"air_x": 60000}, {"air_x": None})
    assert solution is not None
    assert solution.transfers == ()
    assert solution.feasible_in_days == 0


def test_fr7c_gating_removes_edges_the_wallet_cannot_use(world):
    """FR-3 feeds FR-7: without the premium card, bank_a cannot transfer at all."""
    search = search_for(world, ["card_a_basic"], {"bank_a": 200000})
    assert search.solve({"air_x": 60000}, {"air_x": None}) is None


def test_fr7c_pruning_switch_does_not_change_the_answer(world):
    balances = {"bank_a": 100000, "bank_b": 80000, "hotel_h": 150000}
    pruned = search_for(world, ["card_a", "card_b"], balances, pruning=True)
    unpruned = search_for(world, ["card_a", "card_b"], balances, pruning=False)
    needs, deadlines = {"air_x": 95000}, {"air_x": None}
    a = pruned.solve(needs, deadlines)
    b = unpruned.solve(needs, deadlines)
    assert a is not None and b is not None
    assert [(t.edge_id, t.sent) for t in a.transfers] == [(t.edge_id, t.sent) for t in b.transfers]
    assert pruned.stats.expansions < unpruned.stats.expansions


def test_fr7c_budget_exceeded_raises_instead_of_approximating(world):
    tiny = PlanParams(max_expansions=3)
    search = search_for(
        world,
        ["card_a", "card_b"],
        {"bank_a": 100000, "bank_b": 80000, "hotel_h": 150000},
        params=tiny,
    )
    with pytest.raises(SearchBudgetExceeded) as exc:
        search.solve({"air_x": 95000}, {"air_x": None})
    assert exc.value.budget == 3
    assert "max_expansions" in str(exc.value)


def test_fr7c_solutions_are_cached_per_need_and_deadline_profile(world):
    search = search_for(world, ["card_a"], {"bank_a": 200000})
    first = search.solve({"air_x": 60000}, {"air_x": None})
    used = search.stats.expansions
    second = search.solve({"air_x": 60000}, {"air_x": None})
    assert first is second
    assert search.stats.expansions == used


# -- FR-7d timing feasibility ---------------------------------------------


def test_fr7d_a_transfer_that_lands_after_the_deadline_is_infeasible(world):
    """bank_b -> air_x takes 2 days; a deadline 1 day out rules it out."""
    search = search_for(world, ["card_b"], {"bank_b": 100000})
    assert search.solve({"air_x": 60000}, {"air_x": date(2026, 8, 2)}) is not None
    assert search.solve({"air_x": 60000}, {"air_x": date(2026, 8, 1)}) is None


def test_fr7d_deadline_forces_the_more_expensive_instant_plan(world):
    """bank_b is cheaper per delivered point but slow; the deadline picks bank_a."""
    balances = {"bank_a": 100000, "bank_b": 100000}
    relaxed = search_for(world, ["card_a", "card_b"], balances)
    assert {
        t.from_program
        for t in relaxed.solve({"air_x": 60000}, {"air_x": date(2026, 10, 31)}).transfers
    } == {"bank_b"}

    tight = search_for(world, ["card_a", "card_b"], balances)
    solution = tight.solve({"air_x": 60000}, {"air_x": date(2026, 8, 1)})
    assert solution is not None
    assert {t.from_program for t in solution.transfers} == {"bank_a"}
    assert solution.feasible_in_days == 0


def test_fr7d_arrival_days_accumulate_along_a_chain(world):
    files = world_files()
    files["transfers.json"] = [e for e in files["transfers.json"] if e["id"] != "bank_b__air_x"]
    hub_world = make_world(files)
    search = search_for(hub_world, ["card_b"], {"bank_b": 200000})
    solution = search.solve({"air_x": 25000}, {"air_x": None})
    assert solution is not None
    assert solution.arrival_days == {"bank_b": 0, "hotel_h": 0, "air_x": 2}


def test_fr7_solve_booking_set_prices_the_plan(world):
    search = search_for(world, ["card_a"], {"bank_a": 200000})
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=25000,
            value_cents=200000,
            offer_id="x_out",
            deadline=date(2026, 10, 31),
        ),
    )
    draft = solve_booking_set(search, bookings)
    assert draft is not None
    assert draft.value.gross_value_cents == 200000
    assert draft.value.cash_outlay_cents == 25000
    assert draft.value.points_cost_cents == 120000  # 60,000 bank_a @ 2000 mcpp
    assert draft.value.net_value_cents == 55000
    assert draft.is_portal_only() is False


def test_fr7_solve_booking_set_returns_none_when_unfundable(world):
    search = search_for(world, ["card_a"], {"bank_a": 1000})
    bookings = (
        Booking(
            kind=StepKind.BOOK_AWARD,
            program_id="air_x",
            points=60000,
            fees_cents=0,
            value_cents=1,
            offer_id="x_out",
        ),
    )
    assert solve_booking_set(search, bookings) is None


# -- FR-12 cash goals ------------------------------------------------------


def test_fr12_cash_goal_uses_only_liquid_options(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    drafts = cash_drafts(
        world, active, {"bank_a": 100000}, cash_goal(), PlanParams(), SearchStats()
    )
    assert drafts
    used = {b.cashout_id for d in drafts for b in d.bookings}
    assert used == {"a_credit"}  # never a_portal (1.5 cpp) or a_gift
    assert all(b.kind is StepKind.REDEEM_CASH for d in drafts for b in d.bookings)


def test_fr12_top_cash_plan_covers_every_program_with_a_liquid_option(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    drafts = cash_drafts(
        world,
        active,
        {"bank_a": 100000, "bank_b": 50000, "air_x": 30000},
        cash_goal(),
        PlanParams(),
        SearchStats(),
    )
    best = max(drafts, key=lambda d: d.value.cash_received_cents or 0)
    assert {b.program_id for b in best.bookings} == {"bank_a", "bank_b"}
    assert best.value.cash_received_cents == 100000 + 40000


def test_fr12_program_filter_and_per_program_cap_are_honoured(world):
    active = active_subgraph(world, ["card_a"], TODAY)
    goal = cash_goal(programs=["bank_a"], max_points={"bank_a": 30000})
    drafts = cash_drafts(
        world,
        active,
        {"bank_a": 100000, "bank_b": 50000},
        goal,
        PlanParams(),
        SearchStats(),
    )
    best = max(drafts, key=lambda d: d.value.cash_received_cents or 0)
    assert {b.program_id for b in best.bookings} == {"bank_a"}
    assert best.value.cash_received_cents == 30000


def test_fr12_quantization_limits_the_redeemable_amount(world):
    active = active_subgraph(world, [], TODAY)
    drafts = cash_drafts(world, active, {"bank_b": 1500}, cash_goal(), PlanParams(), SearchStats())
    best = max(drafts, key=lambda d: d.value.cash_received_cents or 0)
    assert best.bookings[0].points == 1000
    assert best.value.cash_received_cents == 800


def test_fr12_no_eligible_program_yields_no_drafts(world):
    active = active_subgraph(world, [], TODAY)
    assert (
        cash_drafts(world, active, {"air_x": 50000}, cash_goal(), PlanParams(), SearchStats()) == []
    )


def test_fr12_transfer_chain_is_used_when_it_beats_every_direct_cashout(world):
    """A stress world where bank_a's own credit is worse than air_y's after a 1:2 transfer."""
    files = world_files()
    for edge in files["transfers.json"]:
        if edge["id"] == "bank_a__air_y":
            edge.update({"ratio_from": 1, "ratio_to": 2, "fee_mcpp": 0, "fee_cap_cents": None})
    files["cashouts.json"] = [c for c in files["cashouts.json"] if c["id"] not in {"a_gift"}]
    for option in files["cashouts.json"]:
        if option["id"] == "a_credit":
            option["cpp_milli"] = 600
    files["cashouts.json"].append(
        {
            "id": "y_credit",
            "program_id": "air_y",
            "method": "statement_credit",
            "cpp_milli": 400,
            "min_points": 1000,
            "increment": 1000,
            "requires_card": None,
            "valid_from": None,
            "valid_to": None,
            "source_note": "stress fixture",
        }
    )
    stress = make_world(files)
    active = active_subgraph(stress, ["card_a"], TODAY)
    drafts = cash_drafts(
        stress, active, {"bank_a": 100000}, cash_goal(), PlanParams(), SearchStats()
    )
    best = max(drafts, key=lambda d: d.value.cash_received_cents or 0)
    # Direct: 100,000 @ 0.6 = $600. Via air_y: 200,000 @ 0.4 = $800.
    assert best.value.cash_received_cents == 80000
    assert [t.edge_id for t in best.transfers] == ["bank_a__air_y"]
    assert {b.cashout_id for b in best.bookings} == {"y_credit"}

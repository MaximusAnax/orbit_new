"""FR-2 ledger semantics and repository behaviour, checked on both backends."""

from __future__ import annotations

from datetime import date

import pytest
from pointsmax.engine.advisor import compute_plan_set
from pointsmax.engine.execution import ledger_entries_for_step
from pointsmax.engine.goals import flight_goal
from pointsmax.models import (
    Cabin,
    Goal,
    GoalKind,
    GoalStatus,
    LedgerEntry,
    LedgerReason,
    Profile,
    Wallet,
)
from pointsmax.store import (
    InMemoryRepository,
    InvalidTransition,
    LedgerChainBroken,
    NegativeBalance,
    NotFound,
    Repository,
    SQLiteRepository,
)

AT = "2026-08-01T00:00:00Z"
TODAY = date(2026, 7, 31)


@pytest.fixture(params=["memory", "sqlite"])
def repo(request, tmp_path) -> Repository:
    if request.param == "memory":
        return InMemoryRepository()
    return SQLiteRepository(tmp_path / "pointsmax.db")


def flight():
    return flight_goal(
        origin_city="AAA", dest_city="BBB", month="2026-10", cabin=Cabin.BUSINESS, round_trip=True
    )


# -- profile ---------------------------------------------------------------


def test_profile_defaults_then_persists(repo):
    assert repo.get_profile().default_passengers == 1
    saved = repo.save_profile(
        Profile(
            display_name="owner",
            home_city="AAA",
            default_passengers=2,
            created_at=AT,
            updated_at=AT,
        )
    )
    assert saved.home_city == "AAA"
    assert repo.get_profile().display_name == "owner"


# -- wallet cards ----------------------------------------------------------


def test_cards_are_unique_per_product(repo):
    assert repo.add_card("card_a", AT) is True
    assert repo.add_card("card_a", AT) is False
    assert repo.add_card("card_b", AT) is True
    assert repo.list_cards() == ["card_a", "card_b"]
    assert repo.remove_card("card_a") is True
    assert repo.remove_card("card_a") is False
    assert repo.list_cards() == ["card_b"]


# -- FR-2 ledger -----------------------------------------------------------


def test_fr2_balance_is_the_ledger_sum(repo):
    repo.set_balance("bank_a", 100000, at=AT)
    repo.adjust_balance("bank_a", -25000, at=AT, note="spent")
    assert repo.balance("bank_a") == 75000
    assert repo.balances() == {"bank_a": 75000}


def test_fr2_set_records_the_delta_needed_to_reach_the_target(repo):
    repo.set_balance("bank_a", 100000, at=AT)
    entry = repo.set_balance("bank_a", 80000, at=AT)
    assert entry.delta_points == -20000
    assert entry.post_balance == 80000
    assert entry.reason is LedgerReason.SET


def test_fr2_balances_may_never_go_negative(repo):
    repo.set_balance("bank_a", 1000, at=AT)
    with pytest.raises(NegativeBalance):
        repo.adjust_balance("bank_a", -2000, at=AT)
    with pytest.raises(NegativeBalance):
        repo.set_balance("bank_a", -5, at=AT)
    assert repo.balance("bank_a") == 1000


def test_fr2_post_balance_must_chain(repo):
    repo.set_balance("bank_a", 1000, at=AT)
    broken = LedgerEntry(
        program_id="bank_a",
        delta_points=-500,
        post_balance=999,
        reason=LedgerReason.ADJUST,
        at=AT,
    )
    with pytest.raises(LedgerChainBroken) as exc:
        repo.append_entries([broken])
    assert exc.value.details["expected"] == 500
    assert repo.balance("bank_a") == 1000


def test_fr2_ledger_is_append_only_and_ordered(repo):
    repo.set_balance("bank_a", 100000, at=AT)
    repo.set_balance("bank_b", 50000, at=AT)
    repo.adjust_balance("bank_a", -1000, at=AT)
    ledger = repo.list_ledger()
    assert [(e.program_id, e.delta_points) for e in ledger] == [
        ("bank_a", 100000),
        ("bank_b", 50000),
        ("bank_a", -1000),
    ]
    assert [e.program_id for e in repo.list_ledger("bank_a")] == ["bank_a", "bank_a"]
    assert len(repo.list_ledger(limit=2)) == 2


def test_fr2_replaying_the_ledger_reproduces_every_post_balance(repo):
    repo.set_balance("bank_a", 130000, at=AT)
    repo.set_balance("air_x", 0, at=AT)
    repo.adjust_balance("bank_a", -30000, at=AT)
    repo.adjust_balance("air_x", 30000, at=AT)

    running: dict[str, int] = {}
    for entry in repo.list_ledger():
        running[entry.program_id] = running.get(entry.program_id, 0) + entry.delta_points
        assert running[entry.program_id] == entry.post_balance
    assert {p: n for p, n in running.items() if n} == repo.balances()


def test_fr2_wallet_view_combines_cards_and_balances(repo):
    repo.add_card("card_a", AT)
    repo.set_balance("bank_a", 100000, at=AT)
    wallet = repo.wallet()
    assert isinstance(wallet, Wallet)
    assert wallet.cards == ["card_a"]
    assert wallet.balances == {"bank_a": 100000}


# -- FR-4 goals ------------------------------------------------------------


def test_fr4_goal_round_trips_with_every_field(repo):
    stored = repo.add_goal(Goal(**flight().model_dump(), created_at=AT))
    assert stored.id is not None
    loaded = repo.get_goal(stored.id)
    assert loaded == stored
    assert loaded.cabin is Cabin.BUSINESS
    assert loaded.travel_window_start == date(2026, 10, 1)
    assert loaded.status is GoalStatus.ACTIVE
    assert [g.id for g in repo.list_goals()] == [stored.id]


def test_fr4_cash_goal_json_columns_round_trip(repo):
    goal = Goal(
        kind=GoalKind.CASH,
        cash_programs=["bank_a"],
        cash_max_points={"bank_a": 50000},
        created_at=AT,
    )
    loaded = repo.get_goal(repo.add_goal(goal).id)
    assert loaded.cash_programs == ["bank_a"]
    assert loaded.cash_max_points == {"bank_a": 50000}


def test_fr4_status_lifecycle_is_enforced(repo):
    stored = repo.add_goal(Goal(**flight().model_dump(), created_at=AT))
    planned = repo.set_goal_status(stored.id, GoalStatus.PLANNED)
    assert planned.status is GoalStatus.PLANNED
    with pytest.raises(InvalidTransition):
        repo.set_goal_status(stored.id, GoalStatus.ACTIVE)
    fulfilled = repo.set_goal_status(stored.id, GoalStatus.FULFILLED)
    assert fulfilled.status is GoalStatus.FULFILLED
    with pytest.raises(InvalidTransition):
        repo.set_goal_status(stored.id, GoalStatus.DROPPED)
    assert repo.set_goal_status(stored.id, GoalStatus.FULFILLED).status is GoalStatus.FULFILLED


def test_fr4_unknown_goal_is_a_structured_error(repo):
    with pytest.raises(NotFound):
        repo.set_goal_status(999, GoalStatus.PLANNED)
    assert repo.get_goal(999) is None


# -- FR-11 plan persistence ------------------------------------------------


def test_fr11_plan_set_round_trips_with_plans_steps_and_caveats(repo, world):
    wallet = Wallet(
        cards=["card_a", "card_b"], balances={"bank_a": 100000, "bank_b": 80000, "hotel_h": 150000}
    )
    computed = compute_plan_set(world=world, wallet=wallet, goal=flight(), today=TODAY)
    goal = repo.add_goal(Goal(**flight().model_dump(), created_at=AT))
    stored = repo.save_plan_set(computed.model_copy(update={"goal_id": goal.id}))

    assert stored.id is not None
    assert stored.recommended_plan_id == stored.plans[0].id
    assert len(stored.plans) == len(computed.plans)
    loaded = repo.get_plan_set(stored.id)
    assert loaded.world_hash == world.version.content_hash
    assert loaded.today == TODAY
    assert loaded.params == computed.params
    assert loaded.verdict is computed.verdict
    assert loaded.disclaimer == computed.disclaimer

    first, expected = loaded.plans[0], computed.plans[0]
    assert first.signature == expected.signature
    assert first.points_spent == expected.points_spent
    assert [c.code for c in first.caveats] == [c.code for c in expected.caveats]
    assert [c.params for c in first.caveats] == [c.params for c in expected.caveats]
    assert [s.canonical_tuple() for s in first.steps] == expected.canonical_form()
    assert [s.explanation for s in first.steps] == [s.explanation for s in expected.steps]
    assert [p.id for p in repo.list_plan_sets(goal.id)] == [stored.id]
    assert repo.latest_plan_set(goal.id).id == stored.id


def test_fr11_plan_sets_without_plans_persist_the_verdict_only(repo, world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 1000})
    computed = compute_plan_set(world=world, wallet=wallet, goal=flight(), today=TODAY)
    stored = repo.save_plan_set(computed)
    assert stored.plans == []
    assert stored.recommended_plan_id is None
    assert repo.get_plan_set(stored.id).verdict is computed.verdict


def test_fr11_step_execution_writes_the_ledger_and_stamps_the_step(repo, world):
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 400000})
    computed = compute_plan_set(world=world, wallet=wallet, goal=flight(), today=TODAY)
    stored = repo.save_plan_set(computed)
    repo.set_balance("bank_a", 400000, at=AT)

    plan = repo.get_plan(stored.plans[0].id)
    executed_at = "2026-08-02T09:15:00Z"
    for step in plan.steps:
        entries = ledger_entries_for_step(
            step, world, repo.balances(), at=executed_at, plan_step_id=step.id
        )
        marked, written = repo.record_step_execution(step.id, entries, at=executed_at)
        assert marked.executed_at == executed_at
        assert all(e.id is not None and e.plan_step_id == step.id for e in written)

    assert all(s.executed_at == executed_at for s in repo.get_plan(plan.id).steps)
    replay: dict[str, int] = {}
    for entry in repo.list_ledger():
        replay[entry.program_id] = replay.get(entry.program_id, 0) + entry.delta_points
        assert replay[entry.program_id] == entry.post_balance
    assert {p: n for p, n in replay.items() if n} == repo.balances()


def test_fr11_recording_execution_for_an_unknown_step_is_refused(repo):
    with pytest.raises(NotFound):
        repo.record_step_execution(999, [], at=AT)


def test_fr11_a_rejected_execution_writes_nothing_at_all(repo, world):
    """The ledger entries and the executed_at stamp land together or not at all."""
    wallet = Wallet(cards=["card_a"], balances={"bank_a": 400000})
    computed = compute_plan_set(world=world, wallet=wallet, goal=flight(), today=TODAY)
    stored = repo.save_plan_set(computed)
    repo.set_balance("bank_a", 400000, at=AT)
    before = len(repo.list_ledger())

    step = repo.get_plan(stored.plans[0].id).steps[0]
    good = LedgerEntry(
        program_id="bank_a",
        delta_points=-1000,
        post_balance=399000,
        reason=LedgerReason.TRANSFER_OUT,
        plan_step_id=step.id,
        at=AT,
    )
    doomed = LedgerEntry(
        program_id="bank_b",
        delta_points=-1,
        post_balance=0,
        reason=LedgerReason.TRANSFER_IN,
        plan_step_id=step.id,
        at=AT,
    )
    with pytest.raises(NegativeBalance):
        repo.record_step_execution(step.id, [good, doomed], at=AT)

    assert len(repo.list_ledger()) == before
    assert repo.balance("bank_a") == 400000
    assert repo.get_step(step.id).executed_at is None


def test_fr11_a_rejected_ledger_batch_leaves_no_partial_writes(repo):
    repo.set_balance("bank_a", 1000, at=AT)
    good = LedgerEntry(
        program_id="bank_a", delta_points=-500, post_balance=500, reason=LedgerReason.ADJUST, at=AT
    )
    bad = LedgerEntry(
        program_id="bank_b", delta_points=-1, post_balance=0, reason=LedgerReason.ADJUST, at=AT
    )
    with pytest.raises(NegativeBalance):
        repo.append_entries([good, bad])
    assert repo.balance("bank_a") == 1000
    assert len(repo.list_ledger()) == 1


def test_sqlite_backend_persists_across_connections(tmp_path, world):
    path = tmp_path / "pointsmax.db"
    with SQLiteRepository(path) as first:
        first.add_card("card_a", AT)
        first.set_balance("bank_a", 100000, at=AT)
    with SQLiteRepository(path) as second:
        assert second.list_cards() == ["card_a"]
        assert second.balances() == {"bank_a": 100000}


def test_backends_agree_on_the_same_script(world):
    scripts = []
    for repo in (InMemoryRepository(), SQLiteRepository()):
        repo.add_card("card_a", AT)
        repo.set_balance("bank_a", 100000, at=AT)
        repo.adjust_balance("bank_a", -1500, at=AT)
        goal = repo.add_goal(Goal(**flight().model_dump(), created_at=AT))
        repo.set_goal_status(goal.id, GoalStatus.PLANNED)
        scripts.append(
            (
                repo.list_cards(),
                repo.balances(),
                [(e.program_id, e.delta_points, e.post_balance) for e in repo.list_ledger()],
                [(g.id, str(g.status)) for g in repo.list_goals()],
            )
        )
    assert scripts[0] == scripts[1]

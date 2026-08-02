"""Typer CLI (FR-15) — thin: parse arguments, call the service, render text.

Irreversible steps are confirmation-gated (FR-16b): ``apply`` refuses to execute
a transfer or award booking without ``--yes-irreversible`` or an interactive
"yes".  Every domain failure prints its structured code and exits non-zero.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import typer

from ..adapters.world_provider import CommittedWorldProvider
from ..api.app import default_db_path, utc_now
from ..engine.execution import ExecutionError
from ..engine.goals import cash_goal, flight_goal, stay_goal
from ..engine.plan import format_cents, format_cpp, format_points
from ..engine.world import WorldValidationError
from ..models import Cabin, GoalKind, GoalSpec, GoalStatus, PlanParams, PlanSet, StepKind
from ..service import PointsMaxService, ServiceError
from ..store import SQLiteRepository, StoreError

app = typer.Typer(
    name="pointsmax",
    help=(
        "Find the highest-value redemption path for your credit-card points. "
        "Deterministic search over a committed, versioned rewards dataset."
    ),
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
world_app = typer.Typer(help="Inspect and validate the committed rewards dataset.")
profile_app = typer.Typer(help="Show or update the local profile.")
cards_app = typer.Typer(help="Browse the card-product catalog.")
wallet_app = typer.Typer(help="Cards held, balances and the append-only ledger.")
goal_app = typer.Typer(help="Create and manage goals.")
app.add_typer(world_app, name="world")
app.add_typer(profile_app, name="profile")
app.add_typer(cards_app, name="cards")
app.add_typer(wallet_app, name="wallet")
app.add_typer(goal_app, name="goal")


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


class CliState:
    """Lazily-built service so ``--help`` never touches the database."""

    def __init__(self, db: Path | None, world_dir: Path | None) -> None:
        self.db = db or default_db_path()
        self.world_dir = world_dir
        self._service: PointsMaxService | None = None

    def service(self) -> PointsMaxService:
        if self._service is None:
            provider = CommittedWorldProvider(self.world_dir)
            if str(self.db) != ":memory:":
                self.db.parent.mkdir(parents=True, exist_ok=True)
            repo = SQLiteRepository(self.db)
            self._service = PointsMaxService(repo, provider.load(), provider=provider)
        return self._service


def _state(ctx: typer.Context) -> CliState:
    return ctx.ensure_object(CliState)


def _service(ctx: typer.Context) -> PointsMaxService:
    return _state(ctx).service()


@app.callback()
def main_callback(
    ctx: typer.Context,
    db: Path = typer.Option(
        None, "--db", help="SQLite database path (default ~/.pointsmax/pointsmax.db)."
    ),
    world: Path = typer.Option(
        None, "--world", help="Directory holding the committed world files."
    ),
) -> None:
    """Shared options for every command."""
    ctx.obj = CliState(db, world)


def _today(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _fail(code: str, message: str) -> None:
    typer.echo(f"error [{code}]: {message}", err=True)
    raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# init / world
# --------------------------------------------------------------------------


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the database and load + validate the rewards world (FR-1)."""
    state = _state(ctx)
    service = state.service()
    issues = service.validate()
    if issues:
        for issue in issues:
            typer.echo(f"  [{issue.code}] {issue.message}", err=True)
        _fail("world_validation_failed", f"{len(issues)} world validation issue(s)")
    info = service.world_info(date.today())
    typer.echo(f"database: {state.db}")
    typer.echo(f"world:    v{info.version} (as of {info.as_of}), hash {info.content_hash[:12]}")
    typer.echo(
        "loaded:   "
        + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in info.counts.items())
    )
    typer.echo("world validation: OK")


@world_app.command("info")
def world_info(ctx: typer.Context, today: str = typer.Option(None, "--today")) -> None:
    """Version, entity counts and staleness of the loaded dataset."""
    info = _service(ctx).world_info(_today(today))
    typer.echo(f"version      {info.version}")
    typer.echo(f"as_of        {info.as_of}")
    typer.echo(f"content_hash {info.content_hash}")
    typer.echo(f"valuations   as of {info.valuations_as_of} ({info.days_old} days old)")
    typer.echo(f"stale        {'yes' if info.stale else 'no'} (threshold {info.stale_days} days)")
    for name, count in info.counts.items():
        typer.echo(f"  {name:<18} {count}")


@world_app.command("validate")
def world_validate(ctx: typer.Context) -> None:
    """Run every FR-1 invariant; exits non-zero when any fails."""
    issues = _service(ctx).validate()
    if not issues:
        typer.echo("world validation: OK (7/7 invariants)")
        return
    for issue in issues:
        typer.echo(f"[{issue.code}] {issue.message}", err=True)
    _fail("world_validation_failed", f"{len(issues)} issue(s)")


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


@profile_app.command("show")
def profile_show(ctx: typer.Context) -> None:
    """Print the local profile."""
    profile = _service(ctx).get_profile()
    typer.echo(f"display_name       {profile.display_name}")
    typer.echo(f"home_city          {profile.home_city or '-'}")
    typer.echo(f"default_passengers {profile.default_passengers}")


@profile_app.command("set")
def profile_set(
    ctx: typer.Context,
    home_city: str = typer.Option(None, "--home-city", help="Gazetteer city code, e.g. NYC."),
    default_pax: int = typer.Option(None, "--default-pax", min=1, max=8),
    name: str = typer.Option(None, "--name"),
) -> None:
    """Update the profile fields the parser and planner default from."""
    profile = _service(ctx).set_profile(
        display_name=name,
        home_city=home_city,
        default_passengers=default_pax,
        at=utc_now(),
    )
    typer.echo(
        f"profile updated: {profile.display_name}, home {profile.home_city or '-'}, "
        f"{profile.default_passengers} pax"
    )


# --------------------------------------------------------------------------
# cards
# --------------------------------------------------------------------------


@cards_app.command("list")
def cards_list(
    ctx: typer.Context,
    issuer: str = typer.Option(None, "--issuer", help="Filter by issuer, e.g. chase."),
) -> None:
    """The committed card-product catalog."""
    cards = _service(ctx).list_card_products(issuer)
    if not cards:
        typer.echo("no matching card products")
        return
    typer.echo(f"{'id':<32} {'issuer':<14} {'program':<16} transfers  annual fee")
    for card in cards:
        typer.echo(
            f"{card.id:<32} {card.issuer:<14} {card.program_id:<16} "
            f"{'yes' if card.enables_transfer else 'no':<10} "
            f"{format_cents(card.annual_fee_cents)}"
        )


# --------------------------------------------------------------------------
# wallet
# --------------------------------------------------------------------------


@wallet_app.command("show")
def wallet_show(ctx: typer.Context, today: str = typer.Option(None, "--today")) -> None:
    """Cards held plus every balance's baseline, cash floor and travel floor (FR-13)."""
    view = _service(ctx).wallet_view(_today(today))
    typer.echo("cards held:")
    if not view.cards:
        typer.echo("  (none)")
    for card in view.cards:
        typer.echo(f"  {card.id:<32} {card.name} ({card.issuer})")
    typer.echo("")
    typer.echo(
        f"{'program':<20} {'points':>12} {'baseline':>12} {'cash floor':>12} {'travel floor':>13}"
    )
    for row in view.balances:
        typer.echo(
            f"{row.program_id:<20} {format_points(row.points):>12} "
            f"{format_cents(row.value.baseline_value_cents):>12} "
            f"{format_cents(row.value.cash_floor_cents):>12} "
            f"{format_cents(row.value.travel_floor_cents):>13}"
        )
    typer.echo(
        f"{'TOTAL':<20} {'':>12} {format_cents(view.baseline_total_cents):>12} "
        f"{format_cents(view.cash_floor_total_cents):>12} "
        f"{format_cents(view.travel_floor_total_cents):>13}"
    )


@wallet_app.command("add-card")
def wallet_add_card(ctx: typer.Context, card_id: str) -> None:
    """Add a card product from the catalog (US-1)."""
    card = _service(ctx).add_card(card_id, at=utc_now())
    typer.echo(f"added {card.id} ({card.name})")


@wallet_app.command("remove-card")
def wallet_remove_card(ctx: typer.Context, card_id: str) -> None:
    """Remove a held card product."""
    _service(ctx).remove_card(card_id)
    typer.echo(f"removed {card_id}")


@wallet_app.command("set")
def wallet_set(
    ctx: typer.Context,
    program: str,
    points: int = typer.Argument(..., min=0),
    at: str = typer.Option(None, "--at", help="ISO timestamp for the ledger entry."),
) -> None:
    """Record an absolute balance (a `set` ledger entry)."""
    entry = _service(ctx).set_balance(program, points, at=at or utc_now())
    typer.echo(
        f"{entry.program_id}: {format_points(entry.post_balance)} points "
        f"(delta {entry.delta_points:+,})"
    )


# ``ignore_unknown_options`` lets a negative delta (``adjust chase_ur -10000``)
# reach the argument parser instead of being read as an option named ``-10000``.
@wallet_app.command("adjust", context_settings={"ignore_unknown_options": True})
def wallet_adjust(
    ctx: typer.Context,
    program: str,
    delta: int = typer.Argument(..., help="Signed correction, e.g. -10000."),
    reason: str = typer.Option(None, "--reason"),
    at: str = typer.Option(None, "--at"),
) -> None:
    """Record a relative correction (an `adjust` ledger entry)."""
    entry = _service(ctx).adjust_balance(program, delta, at=at or utc_now(), note=reason)
    typer.echo(f"{entry.program_id}: {format_points(entry.post_balance)} points")


@wallet_app.command("ledger")
def wallet_ledger(
    ctx: typer.Context,
    program: str = typer.Option(None, "--program"),
    limit: int = typer.Option(None, "--limit", min=1),
) -> None:
    """The append-only ledger, oldest first (FR-2)."""
    entries = _service(ctx).ledger(program, limit)
    if not entries:
        typer.echo("ledger is empty")
        return
    typer.echo(f"{'id':>5} {'program':<20} {'delta':>12} {'post':>12} {'reason':<16} at")
    for entry in entries:
        typer.echo(
            f"{entry.id or 0:>5} {entry.program_id:<20} {entry.delta_points:>12,} "
            f"{entry.post_balance:>12,} {entry.reason!s:<16} {entry.at}"
        )


# --------------------------------------------------------------------------
# value
# --------------------------------------------------------------------------


@app.command()
def value(
    ctx: typer.Context,
    program: str,
    points: int = typer.Argument(..., min=0),
    today: str = typer.Option(None, "--today"),
) -> None:
    """Baseline value, cash floor and travel floor for a balance (FR-13, US-2)."""
    breakdown = _service(ctx).value(program, points, _today(today))
    typer.echo(f"{program}: {format_points(points)} points  (valuation as of {breakdown.as_of})")
    typer.echo(
        f"  baseline     {format_cents(breakdown.baseline_value_cents):>12}"
        f"   @ {format_cpp(breakdown.baseline_cpp_milli)} cpp"
    )
    typer.echo(
        f"  cash floor   {format_cents(breakdown.cash_floor_cents):>12}"
        f"   via {breakdown.cash_floor_option_id or '(no liquid option)'}"
    )
    typer.echo(
        f"  travel floor {format_cents(breakdown.travel_floor_cents):>12}"
        f"   via {breakdown.travel_floor_option_id or '(no active option)'}"
    )


# --------------------------------------------------------------------------
# goals
# --------------------------------------------------------------------------


def _spec_from_options(
    kind: str | None,
    origin: str | None,
    dest: str | None,
    cabin: str | None,
    rt: bool,
    month: str | None,
    pax: int,
    nights: int | None,
    city: str | None,
    book_by: str | None,
    programs: list[str] | None,
) -> GoalSpec:
    goal_kind = GoalKind(kind or "flight")
    deadline = date.fromisoformat(book_by) if book_by else None
    if goal_kind is GoalKind.CASH:
        return cash_goal(programs=list(programs) if programs else None)
    if month is None:
        raise typer.BadParameter("--month YYYY-MM is required for flight and stay goals")
    if goal_kind is GoalKind.STAY:
        if not city or not nights:
            raise typer.BadParameter("stay goals need --city and --nights")
        return stay_goal(city=city, nights=nights, month=month, book_by=deadline)
    if not origin or not dest:
        raise typer.BadParameter("flight goals need --from and --to")
    return flight_goal(
        origin_city=origin,
        dest_city=dest,
        month=month,
        cabin=Cabin(cabin) if cabin else None,
        round_trip=rt,
        passengers=pax,
        book_by=deadline,
    )


@goal_app.command("add")
def goal_add(
    ctx: typer.Context,
    text: str = typer.Argument(
        None, help='Free text, e.g. "round-trip business NYC to Paris in October".'
    ),
    kind: str = typer.Option(None, "--kind", help="flight | stay | cash (structured form)."),
    origin: str = typer.Option(None, "--from", help="Origin city code."),
    dest: str = typer.Option(None, "--to", help="Destination city code."),
    cabin: str = typer.Option(
        None, "--cabin", help="economy | premium_economy | business | first."
    ),
    rt: bool = typer.Option(False, "--rt", help="Round trip."),
    month: str = typer.Option(None, "--month", help="Travel month, YYYY-MM."),
    pax: int = typer.Option(1, "--pax", min=1, max=8),
    nights: int = typer.Option(None, "--nights", min=1, max=30),
    city: str = typer.Option(None, "--city", help="Stay city code."),
    book_by: str = typer.Option(None, "--book-by", help="Booking deadline, YYYY-MM-DD."),
    program: list[str] = typer.Option(None, "--program", help="Cash goal: restrict to a program."),
    today: str = typer.Option(None, "--today"),
) -> None:
    """Create a goal from free text (FR-5) or from explicit options (FR-4)."""
    service = _service(ctx)
    if text:
        goal = service.create_goal_from_text(text, today=_today(today), at=utc_now())
    else:
        spec = _spec_from_options(
            kind, origin, dest, cabin, rt, month, pax, nights, city, book_by, program
        )
        goal = service.create_goal(spec, at=utc_now())
    typer.echo(f"goal {goal.id} created: {_goal_line(goal)}")


def _goal_line(goal: Any) -> str:
    if goal.kind is GoalKind.CASH:
        scope = ", ".join(goal.cash_programs) if goal.cash_programs else "all programs"
        return f"cash out ({scope})"
    if goal.kind is GoalKind.STAY:
        return f"{goal.nights} nights in {goal.city} ({goal.travel_window_start:%Y-%m})"
    trip = "round trip" if goal.round_trip else "one way"
    cabin = str(goal.cabin) if goal.cabin else "any cabin"
    return (
        f"{trip} {cabin} {goal.origin_city}->{goal.dest_city} "
        f"({goal.travel_window_start:%Y-%m}, {goal.passengers} pax)"
    )


@goal_app.command("list")
def goal_list(ctx: typer.Context) -> None:
    """All goals with their status."""
    goals = _service(ctx).list_goals()
    if not goals:
        typer.echo("no goals yet")
        return
    for goal in goals:
        typer.echo(f"{goal.id:>4}  {goal.status!s:<10} {_goal_line(goal)}")


@goal_app.command("show")
def goal_show(ctx: typer.Context, goal_id: int) -> None:
    """One goal, with its raw text and window."""
    goal = _service(ctx).get_goal(goal_id)
    typer.echo(f"goal {goal.id}: {_goal_line(goal)}")
    typer.echo(f"  status      {goal.status}")
    if goal.raw_text:
        typer.echo(f"  raw text    {goal.raw_text!r}")
    if goal.travel_window_start:
        typer.echo(f"  window      {goal.travel_window_start} .. {goal.travel_window_end}")
    if goal.book_by:
        typer.echo(f"  book by     {goal.book_by}")


@goal_app.command("drop")
def goal_drop(ctx: typer.Context, goal_id: int) -> None:
    """Move a goal to the terminal `dropped` status."""
    goal = _service(ctx).set_goal_status(goal_id, GoalStatus.DROPPED)
    typer.echo(f"goal {goal.id} dropped")


# --------------------------------------------------------------------------
# plan / show / apply
# --------------------------------------------------------------------------


def _render_plan_set(plan_set: PlanSet, *, verbose: bool = False) -> None:
    typer.echo(f"plan set {plan_set.id} for goal {plan_set.goal_id} — verdict: {plan_set.verdict}")
    typer.echo(
        f"  world v{plan_set.world_version} ({plan_set.world_hash[:12]}), "
        f"today {plan_set.today}, {plan_set.expansions} search expansions"
    )
    if not plan_set.plans:
        typer.echo("  no executable plan was found for this goal.")
    for plan in plan_set.plans:
        tag = " [comparator]" if plan.is_comparator else ""
        cpp = f"{format_cpp(plan.realized_cpp_milli)} cpp" if plan.realized_cpp_milli else "-"
        headline = (
            f"cash {format_cents(plan.cash_received_cents)}"
            if plan.cash_received_cents is not None
            else f"net {format_cents(plan.net_value_cents)}"
        )
        typer.echo(f"\n  #{plan.rank}{tag}  {headline}   {cpp}   plan {plan.id}")
        typer.echo(
            f"     gross {format_cents(plan.gross_value_cents)}"
            f" - outlay {format_cents(plan.cash_outlay_cents)}"
            f" - points cost {format_cents(plan.points_cost_cents)}"
            f" = {format_cents(plan.net_value_cents)}"
        )
        if plan.feasible_in_days:
            typer.echo(f"     transfers land in {plan.feasible_in_days} day(s)")
        for step in plan.steps:
            typer.echo(f"     {step.seq}. {step.explanation}")
        for caveat in plan.caveats:
            typer.echo(f"     ! {caveat.code}: {caveat.text}")
        if verbose:
            typer.echo(f"     signature {plan.signature}")
    typer.echo(f"\n  {plan_set.disclaimer}")


@app.command()
def plan(
    ctx: typer.Context,
    goal_id: int,
    top: int = typer.Option(5, "--top", min=1, max=25, help="How many ranked plans to keep."),
    today: str = typer.Option(None, "--today", help="Planning date, YYYY-MM-DD."),
    max_hops: int = typer.Option(2, "--max-hops", min=0, max=4),
) -> None:
    """Compute and store ranked plans for a goal (FR-7 .. FR-10)."""
    service = _service(ctx)
    base = service.default_params
    params = PlanParams(
        top_k=top,
        max_hops=max_hops,
        max_expansions=base.max_expansions,
        slack_days=base.slack_days,
        promo_days=base.promo_days,
        stale_days=base.stale_days,
    )
    plan_set = service.compute_plans(goal_id, today=_today(today), at=utc_now(), params=params)
    _render_plan_set(plan_set)


@app.command("show")
def show_plan(ctx: typer.Context, plan_id: int) -> None:
    """Full steps, math breakdown and caveats for one plan."""
    service = _service(ctx)
    stored = service.get_plan(plan_id)
    assert stored.plan_set_id is not None
    plan_set = service.get_plan_set(stored.plan_set_id)
    typer.echo(f"plan {stored.id} (rank {stored.rank} of plan set {plan_set.id})")
    typer.echo(f"  world       v{plan_set.world_version} ({plan_set.world_hash[:12]})")
    typer.echo(f"  gross       {format_cents(stored.gross_value_cents)}")
    typer.echo(f"  cash outlay {format_cents(stored.cash_outlay_cents)}")
    typer.echo(f"  points cost {format_cents(stored.points_cost_cents)}")
    typer.echo(f"  net value   {format_cents(stored.net_value_cents)}")
    if stored.cash_received_cents is not None:
        typer.echo(f"  cash back   {format_cents(stored.cash_received_cents)}")
    if stored.realized_cpp_milli is not None:
        typer.echo(f"  realized    {format_cpp(stored.realized_cpp_milli)} cents per point")
    typer.echo(
        "  points spent "
        + (
            ", ".join(f"{p} {format_points(n)}" for p, n in sorted(stored.points_spent.items()))
            or "none"
        )
    )
    typer.echo("  steps:")
    for step in stored.steps:
        mark = "!" if step.irreversible else " "
        done = f"  (executed {step.executed_at})" if step.executed_at else ""
        typer.echo(f"   {mark}{step.seq}. [{step.kind}] {step.explanation}{done}")
    if stored.caveats:
        typer.echo("  caveats:")
        for caveat in stored.caveats:
            typer.echo(f"    - {caveat.code}: {caveat.text}")
    typer.echo(f"\n  {plan_set.disclaimer}")


@app.command()
def apply(
    ctx: typer.Context,
    plan_id: int,
    step: int = typer.Option(..., "--step", min=1, help="1-based seq in canonical order."),
    yes_irreversible: bool = typer.Option(
        False, "--yes-irreversible", help="Confirm an irreversible transfer or award booking."
    ),
    at: str = typer.Option(None, "--at", help="ISO timestamp recorded on the ledger entries."),
) -> None:
    """Record that you performed a step; writes the ledger entries (FR-11, US-6)."""
    service = _service(ctx)
    stored = service.get_plan(plan_id)
    target = next((s for s in stored.steps if s.seq == step), None)
    confirmed = yes_irreversible
    if target is not None and target.irreversible and not confirmed:
        typer.echo(f"step {step}: {target.explanation}")
        typer.echo("This action is IRREVERSIBLE — points cannot be moved back.")
        confirmed = typer.confirm("Record it as performed?", default=False)
        if not confirmed:
            _fail("confirmation_required", "aborted; nothing was written")
    applied, entries = service.execute_step(
        plan_id, step, at=at or utc_now(), confirm_irreversible=confirmed
    )
    typer.echo(f"step {applied.seq} recorded at {applied.executed_at}")
    for entry in entries:
        typer.echo(
            f"  {entry.program_id:<20} {entry.delta_points:>+12,} -> "
            f"{entry.post_balance:>12,}  ({entry.reason})"
        )
    if applied.kind is StepKind.TRANSFER:
        typer.echo("  transfers are final; verify the posting in the program's account.")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main() -> int:
    """Console entry point: translate domain errors into exit codes.

    ``1`` = a domain refusal (unknown entity, precondition, declined
    confirmation), ``2`` = a usage error, ``0`` = success.
    """
    import click

    # Typer vendors its own click fork (``typer._click``), so exceptions raised
    # by its parser are NOT instances of the standalone ``click`` classes.  The
    # fork's base classes are recovered from public attributes' MROs rather than
    # importing the private module: ``typer.BadParameter`` subclasses the fork's
    # UsageError and ClickException, ``typer.Abort`` is the fork's Abort.
    fork_bases = tuple(
        base
        for base in typer.BadParameter.__mro__
        if base.__name__ in ("UsageError", "ClickException")
    )
    aborts: tuple[type[BaseException], ...] = (typer.Abort, click.Abort)
    usage: tuple[type[BaseException], ...] = (
        typer.BadParameter,
        click.UsageError,
        click.ClickException,
        *fork_bases,
    )
    try:
        app(standalone_mode=False)
    except (ServiceError, StoreError, ExecutionError) as exc:
        payload = exc.as_dict()
        print(f"error [{payload['code']}]: {payload['message']}", file=sys.stderr)
        return 1
    except typer.Exit as exc:
        return int(exc.exit_code)
    except WorldValidationError as exc:
        print(f"error [world_validation_failed]: {exc}", file=sys.stderr)
        return 1
    except aborts:
        print("error [confirmation_required]: aborted; nothing was written", file=sys.stderr)
        return 1
    except usage as exc:
        message = exc.format_message() if hasattr(exc, "format_message") else str(exc)
        print(f"error [usage]: {message}", file=sys.stderr)
        return int(getattr(exc, "exit_code", 2))
    return 0


__all__ = ["app", "main"]

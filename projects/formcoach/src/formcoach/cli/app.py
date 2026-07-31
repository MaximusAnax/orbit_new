"""FR-12 — the FormCoach Typer CLI.

Thin, like the API: parse arguments, call one
:class:`~formcoach.services.FormCoachService` method, print.  Every service
error prints ``error <code>: <message>`` on stderr and exits non-zero.

The clock is read here and nowhere deeper: every command takes an explicit
``--at`` / ``--as-of`` and only falls back to :func:`datetime.now` at this
boundary, so the engine stays hermetic (FR-14).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer

from formcoach.engine.volume import iso_week_of
from formcoach.models import (
    AnalysisStatus,
    DeclaredView,
    Equipment,
    Experience,
    FindingStatus,
    Goal,
    MovementPattern,
    Muscle,
    Phase,
    Unit,
    UserProfile,
    VolumeBand,
    kg_to_lb,
)
from formcoach.services import FormCoachService, ServiceError, open_repository

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "FormCoach — science-based training programs plus pose-keypoint form analysis.\n\n"
        "Not a medical device: 'formcoach profile ack-disclaimer' is required before a "
        "program can be generated, and form reports describe movement faults only."
    ),
)
profile_app = typer.Typer(no_args_is_help=True, help="Read and edit the single local profile.")
exercises_app = typer.Typer(no_args_is_help=True, help="Browse the exercise library.")
program_app = typer.Typer(no_args_is_help=True, help="Generate and read training programs.")
form_app = typer.Typer(no_args_is_help=True, help="Analyze a clip or a photo of a lift.")
app.add_typer(profile_app, name="profile")
app.add_typer(exercises_app, name="exercises")
app.add_typer(program_app, name="program")
app.add_typer(form_app, name="form")

DEFAULT_SEED = 20260731


class Context:
    """Per-invocation wiring: which database, and the service built on it."""

    def __init__(self, db: str | None, data_dir: str | None) -> None:
        self.db = db
        self.data_dir = data_dir
        self._service: FormCoachService | None = None

    @property
    def service(self) -> FormCoachService:
        if self._service is None:
            self._service = FormCoachService(open_repository(self.db), data_dir=self.data_dir)
        return self._service


def _ctx(ctx: typer.Context) -> Context:
    return ctx.obj


def _now(value: str | None) -> str:
    return value or datetime.now(UTC).isoformat(timespec="seconds")


def _die(exc: ServiceError) -> None:
    typer.secho(f"error {exc.code}: {exc.message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _emit(payload, as_json: bool, lines: list[str]) -> None:
    if as_json:
        typer.echo(json.dumps(payload, indent=2, default=str))
    else:
        for line in lines:
            typer.echo(line)


@app.callback()
def main_callback(
    ctx: typer.Context,
    db: str = typer.Option(
        None, "--db", help="SQLite path (default ~/.formcoach/formcoach.db, or $FORMCOACH_DB)."
    ),
    data_dir: str = typer.Option(
        None, "--data-dir", help="Committed dataset directory (default the packaged data/)."
    ),
) -> None:
    ctx.obj = Context(db, data_dir)


# --------------------------------------------------------------------------- init


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the database, load the committed datasets and run the FR-2 integrity check."""
    try:
        count = _ctx(ctx).service.initialize()
    except ServiceError as exc:
        _die(exc)
    typer.echo(f"initialized {_ctx(ctx).db or 'the default database'} with {count} exercises")


# ------------------------------------------------------------------------ profile


def _profile_lines(profile: UserProfile) -> list[str]:
    return [
        f"goal              {profile.goal.value}",
        f"experience        {profile.experience.value}",
        f"days per week     {profile.days_per_week}",
        f"equipment         {', '.join(e.value for e in profile.equipment)}",
        f"emphasis          {', '.join(m.value for m in profile.emphasized_muscles) or '-'}",
        f"unit              {profile.unit.value}",
        f"disclaimer        {profile.disclaimer_acknowledged_at or 'NOT ACKNOWLEDGED'}",
        f"pain flags        {', '.join(profile.pain_flags) or '-'}",
        f"updated at        {profile.updated_at}",
    ]


@profile_app.command("show")
def profile_show(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """Print the stored profile."""
    try:
        profile = _ctx(ctx).service.get_profile()
    except ServiceError as exc:
        _die(exc)
    _emit(profile.model_dump(), as_json, _profile_lines(profile))


@profile_app.command("set")
def profile_set(
    ctx: typer.Context,
    goal: Goal = typer.Option(..., help="hypertrophy | strength | general"),
    experience: Experience = typer.Option(..., help="beginner | intermediate | advanced"),
    days: int = typer.Option(..., "--days", min=2, max=6, help="Training days per week."),
    equipment: list[Equipment] = typer.Option(
        ..., "--equipment", "-e", help="Repeatable; at least one."
    ),
    emphasis: list[Muscle] = typer.Option(
        None, "--emphasis", "-m", help="Repeatable, up to three muscles."
    ),
    unit: Unit = typer.Option(Unit.KG, help="Display and input unit; storage is always kg."),
    at: str = typer.Option(None, "--at", help="ISO timestamp for updated_at."),
) -> None:
    """Create or replace the profile."""
    service = _ctx(ctx).service
    existing = service.profile_or_none()
    stamp = _now(at)
    profile = UserProfile(
        goal=goal,
        experience=experience,
        days_per_week=days,
        equipment=list(equipment),
        emphasized_muscles=list(emphasis or []),
        unit=unit,
        disclaimer_acknowledged_at=existing.disclaimer_acknowledged_at if existing else None,
        pain_flags=list(existing.pain_flags) if existing else [],
        updated_at=stamp,
    )
    try:
        saved = service.save_profile(profile)
    except ServiceError as exc:
        _die(exc)
    for line in _profile_lines(saved):
        typer.echo(line)


@profile_app.command("ack-disclaimer")
def profile_ack(
    ctx: typer.Context, at: str = typer.Option(None, "--at", help="ISO timestamp.")
) -> None:
    """Acknowledge the not-medical-advice notice (FR-13a gate on program generation)."""
    try:
        saved = _ctx(ctx).service.acknowledge_disclaimer(_now(at))
    except ServiceError as exc:
        _die(exc)
    typer.echo(f"acknowledged at {saved.disclaimer_acknowledged_at}")


@profile_app.command("clear-pain")
def profile_clear_pain(
    ctx: typer.Context,
    exercise_id: str = typer.Argument(..., help="Exercise to un-flag."),
    at: str = typer.Option(None, "--at", help="ISO timestamp."),
) -> None:
    """Clear a pain flag (FR-13b — flags are never cleared automatically)."""
    try:
        saved = _ctx(ctx).service.clear_pain_flag(exercise_id, _now(at))
    except ServiceError as exc:
        _die(exc)
    typer.echo(f"cleared {exercise_id}; remaining: {', '.join(saved.pain_flags) or '-'}")


# ------------------------------------------------------------------------ library


@exercises_app.command("list")
def exercises_list(
    ctx: typer.Context,
    muscle: Muscle = typer.Option(None, "--muscle", help="Filter by primary muscle."),
    equipment: Equipment = typer.Option(None, "--equipment", help="Filter by equipment."),
    pattern: MovementPattern = typer.Option(None, "--pattern", help="Filter by movement pattern."),
    analyzable: bool = typer.Option(
        None, "--analyzable/--not-analyzable", help="Only exercises with a form profile."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Search the library by muscle, equipment, pattern or form-analyzability."""
    rows = _ctx(ctx).service.list_exercises(
        muscle=muscle, equipment=equipment, pattern=pattern, analyzable=analyzable
    )
    _emit(
        [e.model_dump() for e in rows],
        as_json,
        [
            f"{e.id:<32} {e.pattern.value:<16} {'analyzable' if e.is_analyzable else '':<11}"
            f" {', '.join(m.value for m in e.primary_muscles)}"
            for e in rows
        ]
        + [f"{len(rows)} exercise(s)"],
    )


@exercises_app.command("show")
def exercises_show(
    ctx: typer.Context,
    exercise_id: str = typer.Argument(..., help="Exercise id, name or alias."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show one exercise with its instructions, cues and resolved media references."""
    try:
        exercise, media = _ctx(ctx).service.get_exercise(exercise_id)
    except ServiceError as exc:
        _die(exc)
    lines = [
        f"{exercise.name} ({exercise.id})",
        f"  muscles     {', '.join(m.value for m in exercise.primary_muscles)}"
        f" (secondary: {', '.join(m.value for m in exercise.secondary_muscles) or '-'})",
        f"  equipment   {', '.join(e.value for e in exercise.equipment)}",
        f"  pattern     {exercise.pattern.value} / {exercise.mechanics.value}"
        f" / difficulty {exercise.difficulty}",
        f"  analyzable  {'yes (' + exercise.form_profile_id + ')' if exercise.is_analyzable else 'no'}",
        "  steps:",
        *[f"    {i}. {step}" for i, step in enumerate(exercise.instructions, start=1)],
        "  cues:",
        *[f"    - {cue}" for cue in exercise.cues],
        "  media:",
        *[f"    {a.kind.value:<6} {a.source.value:<6} {a.ref} [{a.license}]" for a in media],
    ]
    _emit(
        {"exercise": exercise.model_dump(), "media": [a.model_dump() for a in media]},
        as_json,
        lines,
    )


# ----------------------------------------------------------------------- programs


@program_app.command("new")
def program_new(
    ctx: typer.Context,
    seed: int = typer.Option(DEFAULT_SEED, "--seed", help="Reproducibility seed."),
    as_of: str = typer.Option(None, "--as-of", help="ISO date the program is created for."),
    goal: Goal = typer.Option(None, "--goal", help="Override the profile's goal."),
    days: int = typer.Option(None, "--days", min=2, max=6, help="Override days per week."),
    experience: Experience = typer.Option(None, "--experience", help="Override experience."),
) -> None:
    """Generate a 5-week mesocycle (4 accumulation weeks + a deload)."""
    overrides = {
        key: value
        for key, value in (
            ("goal", goal),
            ("days_per_week", days),
            ("experience", experience),
        )
        if value is not None
    }
    try:
        plan = _ctx(ctx).service.create_program(as_of=_now(as_of), seed=seed, overrides=overrides)
    except ServiceError as exc:
        _die(exc)
    program = plan.program
    typer.echo(
        f"program {program.id}: {program.split.value}, {program.days_per_week} days/week, "
        f"{program.weeks} weeks, seed {program.seed}"
    )
    typer.echo(f"targets: {', '.join(m.value for m in program.target_muscles)}")
    for muscle in program.target_muscles:
        weekly = program.weekly_set_targets.get(muscle, [])
        typer.echo(f"  {muscle.value:<12} weekly effective sets {weekly}")
    typer.echo(f"sessions: {len(plan.sessions)} (use 'formcoach program show {program.id}')")


@program_app.command("show")
def program_show(
    ctx: typer.Context,
    program_id: int = typer.Argument(None, help="Program id; defaults to the active program."),
    week: int = typer.Option(1, "--week", min=1, max=5),
) -> None:
    """Print one week of a program, session by session."""
    service = _ctx(ctx).service
    try:
        program = service.get_program(program_id) if program_id else service.active_program()
        assert program.id is not None
        typer.echo(f"program {program.id} — week {week} of {program.weeks} ({program.split.value})")
        for day in range(program.days_per_week):
            session, prescriptions = service.get_session(program.id, week, day)
            typer.echo(f"  day {day}: {session.name}")
            for p in prescriptions:
                typer.echo(
                    f"    {p.position + 1}. {p.exercise_id:<30} {p.sets} x "
                    f"{p.rep_low}-{p.rep_high} @ RIR {p.target_rir:g}, rest {p.rest_s}s"
                    + (f"  [{p.load_note}]" if p.load_note else "")
                )
    except ServiceError as exc:
        _die(exc)


@program_app.command("next")
def program_next(
    ctx: typer.Context,
    program_id: int = typer.Argument(None, help="Program id; defaults to the active program."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """The next unlogged session, with FR-6 load suggestions and FR-13(b) substitutions."""
    service = _ctx(ctx).service
    try:
        program = service.get_program(program_id) if program_id else service.active_program()
        assert program.id is not None
        result = service.next_session(program.id)
    except ServiceError as exc:
        _die(exc)
    lines = [f"week {result.session.week}, day {result.session.day_index}: {result.session.name}"]
    for p in result.prescriptions:
        if p.suggested_load_kg is not None:
            load = f"{p.suggested_load_kg:g} kg"
        elif p.clause.startswith("B"):
            load = "bodyweight"
        else:
            load = "pick a starting load"
        sub = f" (substituted for {p.substituted_for})" if p.substituted_for else ""
        lines.append(
            f"  {p.exercise_id:<30} {p.sets} x {p.target_reps} @ RIR {p.target_rir:g} "
            f"-> {load}  [{p.action.value}/{p.clause}]{sub}"
        )
        lines.append(f"      {p.rationale}")
    for dropped in result.dropped:
        lines.append(f"  DROPPED {dropped['exercise_id']}: {dropped['reason']}")
    _emit(
        {
            "week": result.session.week,
            "day_index": result.session.day_index,
            "prescriptions": [p.model_dump() for p in result.prescriptions],
            "dropped": result.dropped,
        },
        as_json,
        lines,
    )


# --------------------------------------------------------------------------- logs


def parse_sets(spec: str) -> list[tuple[float, int, float | None]]:
    """Parse ``"100x8@2,100x8@2,bwx12"`` into ``(weight, reps, rir)`` triples.

    ``bw`` (or a zero weight) marks a bodyweight set; the ``@rir`` part is
    optional and an unrated set stores a null RIR, which FR-5 requires so its
    e1RM stays null.
    """
    out: list[tuple[float, int, float | None]] = []
    for chunk in spec.split(","):
        token = chunk.strip()
        if not token:
            continue
        weight_part, sep, rest = token.partition("x")
        if not sep:
            raise ValueError(f"expected <weight>x<reps>[@<rir>], got {token!r}")
        reps_part, _, rir_part = rest.partition("@")
        weight_text = weight_part.strip().lower()
        weight = 0.0 if weight_text in {"bw", "bodyweight", ""} else float(weight_text)
        out.append((weight, int(reps_part.strip()), float(rir_part) if rir_part.strip() else None))
    if not out:
        raise ValueError("no sets parsed")
    return out


@app.command("log")
def log_workout(
    ctx: typer.Context,
    exercise: str = typer.Option(..., "--exercise", help="Exercise id."),
    sets: str = typer.Option(
        ..., "--sets", help='Comma separated, e.g. "100x8@2,100x8@2,100x7@1" or "bwx12".'
    ),
    pain: bool = typer.Option(False, "--pain", help="Flag the last set as painful (FR-13b)."),
    session: int = typer.Option(None, "--session", help="Program session id to link to."),
    notes: str = typer.Option(None, "--notes"),
    at: str = typer.Option(None, "--at", help="ISO timestamp the workout was performed."),
) -> None:
    """Log a set block, append-only. Weights are entered in the profile's unit."""
    service = _ctx(ctx).service
    try:
        parsed = parse_sets(sets)
    except ValueError as exc:
        typer.secho(f"error invalid_sets: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    payload = [
        {
            "exercise_id": exercise,
            "weight": weight,
            "reps": reps,
            "rir": rir,
            "pain_flag": pain and index == len(parsed) - 1,
        }
        for index, (weight, reps, rir) in enumerate(parsed)
    ]
    try:
        result = service.log_workout(
            performed_at=_now(at), sets=payload, program_session_id=session, notes=notes
        )
    except ServiceError as exc:
        _die(exc)
    profile = service.profile_or_none()
    unit = profile.unit if profile else Unit.KG
    typer.echo(f"logged workout {result.session.workout.id} ({len(result.session.sets)} sets)")
    for row in result.session.sets:
        shown = row.weight_kg if unit is Unit.KG else kg_to_lb(row.weight_kg)
        e1rm = f", e1RM {row.e1rm_kg:.1f} kg" if row.e1rm_kg is not None else ""
        typer.echo(
            f"  set {row.set_index + 1}: {shown:g} {unit.value} x {row.reps}"
            + (f" @ RIR {row.rir:g}" if row.rir is not None else " (unrated)")
            + e1rm
        )
    if result.recommendation:
        typer.secho(
            f"pain flagged: {', '.join(result.flagged_exercise_ids)}",
            fg=typer.colors.YELLOW,
        )
        typer.secho(result.recommendation, fg=typer.colors.YELLOW)


@app.command("volume")
def volume(
    ctx: typer.Context,
    iso_week: str = typer.Option(None, "--iso-week", help='ISO week, e.g. "2026-W31".'),
    at: str = typer.Option(None, "--at", help="ISO timestamp; its ISO week is used."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Weekly effective sets per muscle against the MEV/MAV/MRV bands (FR-4)."""
    service = _ctx(ctx).service
    week = iso_week or iso_week_of(_now(at))
    try:
        report = service.volume_report(week)
    except ServiceError as exc:
        _die(exc)
    labels = {
        VolumeBand.BELOW_MEV: "below MEV",
        VolumeBand.MEV_MAV: "MEV-MAV",
        VolumeBand.MAV_MRV: "MAV-MRV",
        VolumeBand.ABOVE_MRV: "ABOVE MRV",
    }
    lines = [
        f"week {report.iso_week}",
        f"{'muscle':<13}{'eff':>6}{'direct':>8}  band        target",
    ]
    for row in report.rows:
        lines.append(
            f"{row.muscle.value:<13}{row.effective_sets:>6.1f}{row.direct_sets:>8}"
            f"  {labels[row.band]:<11} {'yes' if row.is_target else '-'}"
            f"   (MEV {row.mev}, MAV {row.mav}, MRV {row.mrv})"
        )
    _emit(report.model_dump(), as_json, lines)


# ----------------------------------------------------------------------- form


def _analysis_lines(result) -> list[str]:
    analysis = result.analysis
    if analysis.status is AnalysisStatus.REJECTED:
        return [
            f"analysis {analysis.id}: REJECTED ({analysis.reject_reason})",
            f"  view {analysis.view.value}"
            f"{' (inferred)' if analysis.view_inferred else ''}, "
            f"{analysis.frames_valid}/{analysis.frames_total} frames usable",
        ]
    lines = [
        f"analysis {analysis.id}: {analysis.exercise_id} "
        f"({analysis.analysis_kind.value}, profile {analysis.form_profile_id})",
        f"  view {analysis.view.value}{' (inferred)' if analysis.view_inferred else ''}, "
        f"reps {analysis.rep_count}, score {analysis.clip_score:.1f}/100",
    ]
    for rep in result.reps:
        faults = [f for f in rep.findings if f.status is FindingStatus.FAULT]
        lines.append(f"  rep {rep.rep_index + 1}: score {rep.score:.0f}")
        for finding in faults:
            lines.append(
                f"    {finding.severity.value:<9} {finding.fault_id:<22}"
                f" measured {finding.measured:.3f} vs threshold {finding.threshold:g}"
                f" (frame {finding.frame})"
            )
        for finding in rep.findings:
            if finding.status is FindingStatus.NOT_ASSESSED:
                lines.append(
                    f"    not assessed {finding.fault_id:<22} ({finding.not_assessed_reason.value})"
                )
    if result.corrections:
        lines.append("  corrections, most important first:")
        for correction in result.corrections:
            lines.append(
                f"    [{correction.severity.value}] {correction.fault_id} "
                f"x{correction.occurrences}: {correction.cue}"
            )
    return lines


def _source_payload(source: str) -> tuple[str | None, dict | None]:
    """A ``.keypoints.json`` argument is read inline; anything else is a media path."""
    path = Path(source)
    if path.suffix == ".json" and path.is_file():
        return str(path), json.loads(path.read_text(encoding="utf-8"))
    return source, None


@form_app.command("analyze")
def form_analyze(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Clip path, or a *.keypoints.json sidecar."),
    exercise: str = typer.Option(..., "--exercise", help="Exercise id, name or alias."),
    view: DeclaredView = typer.Option(
        None, "--view", help="side | side_left | side_right | front; omit to infer."
    ),
    at: str = typer.Option(None, "--as-of", help="ISO timestamp for the analysis record."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Analyze a clip: rep count, per-rep metrics, faults and correction cues."""
    ref, keypoints = _source_payload(source)
    try:
        result = _ctx(ctx).service.analyze(
            exercise_id=exercise,
            created_at=_now(at),
            source=ref,
            keypoints=keypoints,
            view=view,
        )
    except ServiceError as exc:
        _die(exc)
    _emit(result.model_dump(), as_json, _analysis_lines(result))


@form_app.command("photo")
def form_photo(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Image path, or a *.keypoints.json sidecar."),
    exercise: str = typer.Option(..., "--exercise", help="Exercise id, name or alias."),
    view: DeclaredView = typer.Option(None, "--view", help="Camera view; omit to infer."),
    phase: Phase = typer.Option(..., "--phase", help="bottom | top — what the frame shows."),
    at: str = typer.Option(None, "--as-of", help="ISO timestamp for the analysis record."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Analyze a single frame (FR-15): only rules sampled at the declared phase run."""
    ref, keypoints = _source_payload(source)
    try:
        result = _ctx(ctx).service.analyze_photo(
            exercise_id=exercise,
            created_at=_now(at),
            phase=phase,
            source=ref,
            keypoints=keypoints,
            view=view,
        )
    except ServiceError as exc:
        _die(exc)
    _emit(result.model_dump(), as_json, _analysis_lines(result))


@form_app.command("show")
def form_show(
    ctx: typer.Context,
    analysis_id: int = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Print a stored analysis."""
    try:
        result = _ctx(ctx).service.get_analysis(analysis_id)
    except ServiceError as exc:
        _die(exc)
    _emit(result.model_dump(), as_json, _analysis_lines(result))


@form_app.command("list")
def form_list(
    ctx: typer.Context,
    exercise: str = typer.Option(None, "--exercise", help="Filter by exercise id."),
) -> None:
    """List stored analyses, newest last."""
    rows = _ctx(ctx).service.list_analyses(exercise)
    for analysis in rows:
        state = (
            f"{analysis.rep_count} reps, score {analysis.clip_score:.1f}"
            if analysis.status is AnalysisStatus.COMPLETED
            else f"rejected ({analysis.reject_reason})"
        )
        typer.echo(
            f"{analysis.id:<5} {analysis.created_at:<26} {analysis.exercise_id:<22}"
            f" {analysis.analysis_kind.value:<6} {state}"
        )
    typer.echo(f"{len(rows)} analysis record(s)")


def main() -> None:
    """Console-script entry point (``formcoach``)."""
    app()

"""SQLite implementation of :class:`~formcoach.store.repository.Repository`.

Stdlib ``sqlite3`` only.  The schema mirrors DATA_MODEL.md table for table;
invariants that SQLite can express (the singleton profile row, the ordering of
volume landmarks, uniqueness of ``(program_id, week, day_index)`` and of
``(workout_id, exercise_id, set_index)``, the at-most-one active program) are
CHECK constraints and indexes rather than Python-side promises.

Append-only tables are enforced with BEFORE UPDATE/DELETE triggers, so the
"corrections are new workouts" rule survives someone opening the database with
a SQL client.

Passing ``":memory:"`` gives the ephemeral backend DATA_MODEL names for tests.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from formcoach.models import (
    AnalysisResult,
    Datasets,
    Equipment,
    Exercise,
    FaultFinding,
    FormAnalysis,
    LoggedSession,
    MediaAsset,
    MovementPattern,
    Muscle,
    Prescription,
    Program,
    ProgramPlan,
    ProgramSession,
    ProgramStatus,
    RepAnalysis,
    SessionPlan,
    SetLog,
    UserProfile,
    VolumeLandmark,
    WorkoutLog,
)
from formcoach.store.repository import RepositoryError, matches_exercise_filter

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_profile (
    id                         INTEGER PRIMARY KEY CHECK (id = 1),
    goal                       TEXT    NOT NULL,
    experience                 TEXT    NOT NULL,
    days_per_week              INTEGER NOT NULL CHECK (days_per_week BETWEEN 2 AND 6),
    equipment                  TEXT    NOT NULL,
    emphasized_muscles         TEXT    NOT NULL,
    unit                       TEXT    NOT NULL,
    disclaimer_acknowledged_at TEXT,
    pain_flags                 TEXT    NOT NULL,
    updated_at                 TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS exercise (
    id                TEXT PRIMARY KEY,
    name              TEXT    NOT NULL,
    aliases           TEXT    NOT NULL,
    primary_muscles   TEXT    NOT NULL,
    secondary_muscles TEXT    NOT NULL,
    equipment         TEXT    NOT NULL,
    pattern           TEXT    NOT NULL,
    mechanics         TEXT    NOT NULL,
    difficulty        INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 3),
    rest_s            INTEGER NOT NULL,
    harder_variant_id TEXT,
    instructions      TEXT    NOT NULL,
    cues              TEXT    NOT NULL,
    form_profile_id   TEXT
);

CREATE TABLE IF NOT EXISTS media_asset (
    id          TEXT PRIMARY KEY,
    exercise_id TEXT NOT NULL REFERENCES exercise(id),
    kind        TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    source      TEXT NOT NULL CHECK (source IN ('local', 'url')),
    ref         TEXT NOT NULL,
    license     TEXT NOT NULL,
    attribution TEXT
);
CREATE INDEX IF NOT EXISTS media_asset_exercise ON media_asset(exercise_id);

CREATE TABLE IF NOT EXISTS volume_landmark (
    muscle TEXT PRIMARY KEY,
    mv     INTEGER NOT NULL,
    mev    INTEGER NOT NULL,
    mav    INTEGER NOT NULL,
    mrv    INTEGER NOT NULL,
    CHECK (mv <= mev AND mev < mav AND mav <= mrv)
);

CREATE TABLE IF NOT EXISTS program (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT    NOT NULL,
    seed               INTEGER NOT NULL,
    goal               TEXT    NOT NULL,
    experience         TEXT    NOT NULL,
    days_per_week      INTEGER NOT NULL,
    emphasized_muscles TEXT    NOT NULL,
    equipment          TEXT    NOT NULL,
    target_muscles     TEXT    NOT NULL,
    weekly_set_targets TEXT    NOT NULL,
    weeks              INTEGER NOT NULL,
    split              TEXT    NOT NULL,
    status             TEXT    NOT NULL CHECK (status IN ('active', 'archived'))
);
CREATE UNIQUE INDEX IF NOT EXISTS program_one_active
    ON program(status) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS program_session (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES program(id),
    week       INTEGER NOT NULL CHECK (week BETWEEN 1 AND 5),
    day_index  INTEGER NOT NULL CHECK (day_index >= 0),
    name       TEXT    NOT NULL,
    UNIQUE (program_id, week, day_index)
);

CREATE TABLE IF NOT EXISTS prescription (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES program_session(id),
    position    INTEGER NOT NULL,
    exercise_id TEXT    NOT NULL REFERENCES exercise(id),
    sets        INTEGER NOT NULL CHECK (sets >= 1),
    rep_low     INTEGER NOT NULL,
    rep_high    INTEGER NOT NULL,
    target_rir  REAL    NOT NULL,
    rest_s      INTEGER NOT NULL,
    load_note   TEXT,
    UNIQUE (session_id, position),
    CHECK (rep_low <= rep_high)
);

CREATE TABLE IF NOT EXISTS workout_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    performed_at       TEXT NOT NULL,
    program_session_id INTEGER REFERENCES program_session(id),
    notes              TEXT
);

CREATE TABLE IF NOT EXISTS set_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id  INTEGER NOT NULL REFERENCES workout_log(id),
    exercise_id TEXT    NOT NULL REFERENCES exercise(id),
    set_index   INTEGER NOT NULL CHECK (set_index >= 0),
    weight_kg   REAL    NOT NULL CHECK (weight_kg >= 0),
    reps        INTEGER NOT NULL CHECK (reps >= 1),
    rir         REAL,
    pain_flag   INTEGER NOT NULL CHECK (pain_flag IN (0, 1)),
    e1rm_kg     REAL,
    UNIQUE (workout_id, exercise_id, set_index)
);

CREATE TABLE IF NOT EXISTS form_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    analysis_kind   TEXT    NOT NULL CHECK (analysis_kind IN ('clip', 'photo')),
    exercise_id     TEXT    NOT NULL REFERENCES exercise(id),
    form_profile_id TEXT    NOT NULL,
    source_ref      TEXT    NOT NULL,
    pose_source     TEXT    NOT NULL,
    view            TEXT    NOT NULL CHECK (view IN ('side_left', 'side_right', 'front')),
    view_inferred   INTEGER NOT NULL CHECK (view_inferred IN (0, 1)),
    declared_phase  TEXT,
    fps             REAL,
    frames_total    INTEGER NOT NULL,
    frames_valid    INTEGER NOT NULL,
    status          TEXT    NOT NULL CHECK (status IN ('completed', 'rejected')),
    reject_reason   TEXT,
    rep_count       INTEGER,
    clip_score      REAL,
    CHECK ((status = 'rejected') = (reject_reason IS NOT NULL)),
    CHECK ((status = 'rejected') = (rep_count IS NULL)),
    CHECK ((status = 'rejected') = (clip_score IS NULL)),
    CHECK ((analysis_kind = 'photo') = (declared_phase IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS rep_analysis (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id    INTEGER NOT NULL REFERENCES form_analysis(id),
    rep_index      INTEGER NOT NULL,
    start_frame    INTEGER NOT NULL,
    extremum_frame INTEGER NOT NULL,
    end_frame      INTEGER NOT NULL,
    metrics        TEXT    NOT NULL,
    score          REAL    NOT NULL,
    UNIQUE (analysis_id, rep_index),
    CHECK (start_frame <= extremum_frame AND extremum_frame <= end_frame)
);

CREATE TABLE IF NOT EXISTS fault_finding (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    rep_analysis_id     INTEGER NOT NULL REFERENCES rep_analysis(id),
    fault_id            TEXT    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('ok', 'fault', 'not_assessed')),
    not_assessed_reason TEXT,
    measured            REAL,
    threshold           REAL    NOT NULL,
    severity            TEXT    NOT NULL,
    frame               INTEGER,
    cue                 TEXT,
    UNIQUE (rep_analysis_id, fault_id),
    CHECK ((status = 'not_assessed') = (not_assessed_reason IS NOT NULL)),
    CHECK ((status = 'not_assessed') = (measured IS NULL)),
    CHECK ((status = 'fault') = (cue IS NOT NULL))
);

CREATE TRIGGER IF NOT EXISTS workout_log_append_only_update
    BEFORE UPDATE ON workout_log
    BEGIN SELECT RAISE(ABORT, 'workout_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS workout_log_append_only_delete
    BEFORE DELETE ON workout_log
    BEGIN SELECT RAISE(ABORT, 'workout_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS set_log_append_only_update
    BEFORE UPDATE ON set_log
    BEGIN SELECT RAISE(ABORT, 'set_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS set_log_append_only_delete
    BEFORE DELETE ON set_log
    BEGIN SELECT RAISE(ABORT, 'set_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS form_analysis_append_only_update
    BEFORE UPDATE ON form_analysis
    BEGIN SELECT RAISE(ABORT, 'form_analysis is append-only'); END;
"""


def _dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=False)


class SQLiteRepository:
    """Default persistence backend; ``":memory:"`` is the ephemeral test backend."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False``: FastAPI runs synchronous endpoints in a
        # worker thread, so the connection outlives the thread that opened it.
        # FormCoach is single-user and every write below runs inside a
        # ``with self.connection`` transaction, so serialization is sqlite3's
        # own module-level lock rather than anything this class has to invent.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    # -- lifecycle ---------------------------------------------------------

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- committed library -------------------------------------------------

    def load_library(self, datasets: Datasets) -> None:
        with self.connection:
            self.connection.executemany(
                "INSERT OR REPLACE INTO exercise VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        e.id,
                        e.name,
                        _dumps(e.aliases),
                        _dumps([m.value for m in e.primary_muscles]),
                        _dumps([m.value for m in e.secondary_muscles]),
                        _dumps([q.value for q in e.equipment]),
                        e.pattern.value,
                        e.mechanics.value,
                        e.difficulty,
                        e.rest_s,
                        e.harder_variant_id,
                        _dumps(e.instructions),
                        _dumps(e.cues),
                        e.form_profile_id,
                    )
                    for e in datasets.exercises
                ],
            )
            self.connection.executemany(
                "INSERT OR REPLACE INTO media_asset VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        a.id,
                        a.exercise_id,
                        a.kind.value,
                        a.source.value,
                        a.ref,
                        a.license,
                        a.attribution,
                    )
                    for a in datasets.media_assets
                ],
            )
            self.connection.executemany(
                "INSERT OR REPLACE INTO volume_landmark VALUES (?,?,?,?,?)",
                [
                    (lm.muscle.value, lm.mv, lm.mev, lm.mav, lm.mrv)
                    for lm in datasets.landmarks.values()
                ],
            )

    def list_exercises(
        self,
        *,
        muscle: Muscle | None = None,
        equipment: Equipment | None = None,
        pattern: MovementPattern | None = None,
        analyzable: bool | None = None,
    ) -> list[Exercise]:
        rows = self.connection.execute("SELECT * FROM exercise ORDER BY id").fetchall()
        exercises = [_exercise_from_row(row) for row in rows]
        return [
            e
            for e in exercises
            if matches_exercise_filter(
                e,
                muscle=muscle,
                equipment=equipment,
                pattern=pattern,
                analyzable=analyzable,
            )
        ]

    def get_exercise(self, exercise_id: str) -> Exercise | None:
        row = self.connection.execute(
            "SELECT * FROM exercise WHERE id = ?", (exercise_id,)
        ).fetchone()
        return _exercise_from_row(row) if row else None

    def list_media(self, exercise_id: str) -> list[MediaAsset]:
        rows = self.connection.execute(
            "SELECT * FROM media_asset WHERE exercise_id = ? ORDER BY id", (exercise_id,)
        ).fetchall()
        return [MediaAsset(**dict(row)) for row in rows]

    def get_landmark(self, muscle: Muscle) -> VolumeLandmark | None:
        row = self.connection.execute(
            "SELECT * FROM volume_landmark WHERE muscle = ?", (muscle.value,)
        ).fetchone()
        return VolumeLandmark(**dict(row)) if row else None

    # -- profile -----------------------------------------------------------

    def get_profile(self) -> UserProfile | None:
        row = self.connection.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
        if row is None:
            return None
        data = dict(row)
        data["equipment"] = json.loads(data["equipment"])
        data["emphasized_muscles"] = json.loads(data["emphasized_muscles"])
        data["pain_flags"] = json.loads(data["pain_flags"])
        return UserProfile(**data)

    def save_profile(self, profile: UserProfile) -> UserProfile:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO user_profile VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    profile.goal.value,
                    profile.experience.value,
                    profile.days_per_week,
                    _dumps([e.value for e in profile.equipment]),
                    _dumps([m.value for m in profile.emphasized_muscles]),
                    profile.unit.value,
                    profile.disclaimer_acknowledged_at,
                    _dumps(profile.pain_flags),
                    profile.updated_at,
                ),
            )
        return profile

    # -- programs ----------------------------------------------------------

    def save_program(self, plan: ProgramPlan) -> ProgramPlan:
        program = plan.program
        with self.connection:
            self.connection.execute(
                "UPDATE program SET status = 'archived' WHERE status = 'active'"
            )
            cursor = self.connection.execute(
                "INSERT INTO program (created_at, seed, goal, experience, days_per_week,"
                " emphasized_muscles, equipment, target_muscles, weekly_set_targets,"
                " weeks, split, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    program.created_at,
                    program.seed,
                    program.goal.value,
                    program.experience.value,
                    program.days_per_week,
                    _dumps([m.value for m in program.emphasized_muscles]),
                    _dumps([e.value for e in program.equipment]),
                    _dumps([m.value for m in program.target_muscles]),
                    _dumps({m.value: v for m, v in program.weekly_set_targets.items()}),
                    program.weeks,
                    program.split.value,
                    program.status.value,
                ),
            )
            program_id = int(cursor.lastrowid or 0)
            stored_sessions: list[SessionPlan] = []
            for session in plan.sessions:
                session_cursor = self.connection.execute(
                    "INSERT INTO program_session (program_id, week, day_index, name)"
                    " VALUES (?,?,?,?)",
                    (program_id, session.week, session.day_index, session.name),
                )
                session_id = int(session_cursor.lastrowid or 0)
                stored: list[Prescription] = []
                for prescription in session.prescriptions:
                    presc_cursor = self.connection.execute(
                        "INSERT INTO prescription (session_id, position, exercise_id, sets,"
                        " rep_low, rep_high, target_rir, rest_s, load_note)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            session_id,
                            prescription.position,
                            prescription.exercise_id,
                            prescription.sets,
                            prescription.rep_low,
                            prescription.rep_high,
                            prescription.target_rir,
                            prescription.rest_s,
                            prescription.load_note,
                        ),
                    )
                    stored.append(
                        prescription.model_copy(
                            update={
                                "id": int(presc_cursor.lastrowid or 0),
                                "session_id": session_id,
                            }
                        )
                    )
                stored_sessions.append(session.model_copy(update={"prescriptions": stored}))
        return ProgramPlan(
            program=program.model_copy(update={"id": program_id}), sessions=stored_sessions
        )

    def get_program(self, program_id: int) -> Program | None:
        row = self.connection.execute(
            "SELECT * FROM program WHERE id = ?", (program_id,)
        ).fetchone()
        return _program_from_row(row) if row else None

    def list_programs(self) -> list[Program]:
        rows = self.connection.execute("SELECT * FROM program ORDER BY id").fetchall()
        return [_program_from_row(row) for row in rows]

    def active_program(self) -> Program | None:
        row = self.connection.execute(
            "SELECT * FROM program WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _program_from_row(row) if row else None

    def set_program_status(self, program_id: int, status: ProgramStatus) -> None:
        with self.connection:
            if status is ProgramStatus.ACTIVE:
                self.connection.execute(
                    "UPDATE program SET status = 'archived' WHERE status = 'active'"
                )
            self.connection.execute(
                "UPDATE program SET status = ? WHERE id = ?", (status.value, program_id)
            )

    def list_sessions(self, program_id: int) -> list[ProgramSession]:
        rows = self.connection.execute(
            "SELECT * FROM program_session WHERE program_id = ? ORDER BY week, day_index",
            (program_id,),
        ).fetchall()
        return [ProgramSession(**dict(row)) for row in rows]

    def get_session(self, program_id: int, week: int, day_index: int) -> ProgramSession | None:
        row = self.connection.execute(
            "SELECT * FROM program_session WHERE program_id = ? AND week = ? AND day_index = ?",
            (program_id, week, day_index),
        ).fetchone()
        return ProgramSession(**dict(row)) if row else None

    def list_prescriptions(self, session_id: int) -> list[Prescription]:
        rows = self.connection.execute(
            "SELECT * FROM prescription WHERE session_id = ? ORDER BY position", (session_id,)
        ).fetchall()
        return [Prescription(**dict(row)) for row in rows]

    # -- logs --------------------------------------------------------------

    def add_workout(self, workout: WorkoutLog, sets: Sequence[SetLog]) -> LoggedSession:
        if not sets:
            raise RepositoryError("a workout must carry at least one set")
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO workout_log (performed_at, program_session_id, notes) VALUES (?,?,?)",
                (workout.performed_at, workout.program_session_id, workout.notes),
            )
            workout_id = int(cursor.lastrowid or 0)
            stored: list[SetLog] = []
            for set_log in sets:
                set_cursor = self.connection.execute(
                    "INSERT INTO set_log (workout_id, exercise_id, set_index, weight_kg,"
                    " reps, rir, pain_flag, e1rm_kg) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        workout_id,
                        set_log.exercise_id,
                        set_log.set_index,
                        set_log.weight_kg,
                        set_log.reps,
                        set_log.rir,
                        int(set_log.pain_flag),
                        set_log.e1rm_kg,
                    ),
                )
                stored.append(
                    set_log.model_copy(
                        update={
                            "id": int(set_cursor.lastrowid or 0),
                            "workout_id": workout_id,
                        }
                    )
                )
        return LoggedSession(workout=workout.model_copy(update={"id": workout_id}), sets=stored)

    def _sessions_from_rows(self, rows: Iterable[sqlite3.Row]) -> list[LoggedSession]:
        sessions: list[LoggedSession] = []
        for row in rows:
            workout = WorkoutLog(**dict(row))
            set_rows = self.connection.execute(
                "SELECT * FROM set_log WHERE workout_id = ? ORDER BY exercise_id, set_index",
                (workout.id,),
            ).fetchall()
            sets = [_set_from_row(set_row) for set_row in set_rows]
            sessions.append(LoggedSession(workout=workout, sets=sets))
        return sessions

    def list_workouts(self, since: str | None = None) -> list[LoggedSession]:
        if since is None:
            rows = self.connection.execute(
                "SELECT * FROM workout_log ORDER BY performed_at, id"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM workout_log WHERE performed_at >= ? ORDER BY performed_at, id",
                (since,),
            ).fetchall()
        return self._sessions_from_rows(rows)

    def workouts_for_session(self, session_id: int) -> list[LoggedSession]:
        rows = self.connection.execute(
            "SELECT * FROM workout_log WHERE program_session_id = ? ORDER BY performed_at, id",
            (session_id,),
        ).fetchall()
        return self._sessions_from_rows(rows)

    # -- form analyses -----------------------------------------------------

    def save_analysis(self, result: AnalysisResult) -> AnalysisResult:
        analysis = result.analysis
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO form_analysis (created_at, analysis_kind, exercise_id,"
                " form_profile_id, source_ref, pose_source, view, view_inferred,"
                " declared_phase, fps, frames_total, frames_valid, status, reject_reason,"
                " rep_count, clip_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    analysis.created_at,
                    analysis.analysis_kind.value,
                    analysis.exercise_id,
                    analysis.form_profile_id,
                    analysis.source_ref,
                    analysis.pose_source.value,
                    analysis.view.value,
                    int(analysis.view_inferred),
                    analysis.declared_phase.value if analysis.declared_phase else None,
                    analysis.fps,
                    analysis.frames_total,
                    analysis.frames_valid,
                    analysis.status.value,
                    analysis.reject_reason,
                    analysis.rep_count,
                    analysis.clip_score,
                ),
            )
            analysis_id = int(cursor.lastrowid or 0)
            stored_reps: list[RepAnalysis] = []
            for rep in result.reps:
                rep_cursor = self.connection.execute(
                    "INSERT INTO rep_analysis (analysis_id, rep_index, start_frame,"
                    " extremum_frame, end_frame, metrics, score) VALUES (?,?,?,?,?,?,?)",
                    (
                        analysis_id,
                        rep.rep_index,
                        rep.start_frame,
                        rep.extremum_frame,
                        rep.end_frame,
                        _dumps(rep.metrics),
                        rep.score,
                    ),
                )
                rep_id = int(rep_cursor.lastrowid or 0)
                stored_findings: list[FaultFinding] = []
                for finding in rep.findings:
                    finding_cursor = self.connection.execute(
                        "INSERT INTO fault_finding (rep_analysis_id, fault_id, status,"
                        " not_assessed_reason, measured, threshold, severity, frame, cue)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            rep_id,
                            finding.fault_id,
                            finding.status.value,
                            finding.not_assessed_reason.value
                            if finding.not_assessed_reason
                            else None,
                            finding.measured,
                            finding.threshold,
                            finding.severity.value,
                            finding.frame,
                            finding.cue,
                        ),
                    )
                    stored_findings.append(
                        finding.model_copy(
                            update={
                                "id": int(finding_cursor.lastrowid or 0),
                                "rep_analysis_id": rep_id,
                            }
                        )
                    )
                stored_reps.append(
                    rep.model_copy(
                        update={
                            "id": rep_id,
                            "analysis_id": analysis_id,
                            "findings": stored_findings,
                        }
                    )
                )
        return AnalysisResult(
            analysis=analysis.model_copy(update={"id": analysis_id}),
            reps=stored_reps,
            corrections=result.corrections,
        )

    def get_analysis(self, analysis_id: int) -> AnalysisResult | None:
        row = self.connection.execute(
            "SELECT * FROM form_analysis WHERE id = ?", (analysis_id,)
        ).fetchone()
        if row is None:
            return None
        analysis = FormAnalysis(**dict(row))
        rep_rows = self.connection.execute(
            "SELECT * FROM rep_analysis WHERE analysis_id = ? ORDER BY rep_index",
            (analysis_id,),
        ).fetchall()
        reps: list[RepAnalysis] = []
        for rep_row in rep_rows:
            data = dict(rep_row)
            data["metrics"] = json.loads(data["metrics"])
            finding_rows = self.connection.execute(
                "SELECT * FROM fault_finding WHERE rep_analysis_id = ? ORDER BY fault_id",
                (rep_row["id"],),
            ).fetchall()
            data["findings"] = [FaultFinding(**dict(f)) for f in finding_rows]
            reps.append(RepAnalysis(**data))
        return AnalysisResult(analysis=analysis, reps=reps, corrections=[])

    def list_analyses(self, exercise_id: str | None = None) -> list[FormAnalysis]:
        if exercise_id is None:
            rows = self.connection.execute("SELECT * FROM form_analysis ORDER BY id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM form_analysis WHERE exercise_id = ? ORDER BY id",
                (exercise_id,),
            ).fetchall()
        return [FormAnalysis(**dict(row)) for row in rows]


# ------------------------------------------------------------------- row mapping


def _exercise_from_row(row: sqlite3.Row) -> Exercise:
    data = dict(row)
    for field in (
        "aliases",
        "primary_muscles",
        "secondary_muscles",
        "equipment",
        "instructions",
        "cues",
    ):
        data[field] = json.loads(data[field])
    return Exercise(**data)


def _program_from_row(row: sqlite3.Row) -> Program:
    data = dict(row)
    data["emphasized_muscles"] = json.loads(data["emphasized_muscles"])
    data["equipment"] = json.loads(data["equipment"])
    data["target_muscles"] = json.loads(data["target_muscles"])
    data["weekly_set_targets"] = json.loads(data["weekly_set_targets"])
    return Program(**data)


def _set_from_row(row: sqlite3.Row) -> SetLog:
    data = dict(row)
    data["pain_flag"] = bool(data["pain_flag"])
    data.pop("e1rm_kg", None)  # derived on the model, never trusted from storage
    return SetLog(**data)

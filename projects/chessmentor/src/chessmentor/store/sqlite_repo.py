"""SQLite backend (stdlib ``sqlite3``) — the default persistence.

Schema DDL follows DATA_MODEL.md table-for-table.  Structural invariants live in
CHECK / UNIQUE constraints where SQLite can express them; the cross-row ones
(single in-progress game, immutability, append-only ordering) are enforced by
:mod:`chessmentor.store._guards`, which the in-memory backend shares.

**Threading.**  The served path (FR-14) runs sync handlers in Starlette's
thread pool, and FastAPI resolves a sync generator dependency's setup and its
teardown as *separate* pool submissions — so one request's connection is
routinely opened on one worker thread and closed on another.  With sqlite3's
default ``check_same_thread=True`` that raises ``ProgrammingError`` under
concurrency.  The connection is therefore opened with ``check_same_thread=
False``, which is safe because CPython's sqlite3 is built in serialized mode
(``sqlite3.threadsafety == 3``) and every connection has its own mutex.  What
that mutex does *not* protect is a multi-statement transaction: ``with
self._connection`` shares one implicit transaction across the whole process, so
a second thread entering it would have its partial work committed (or rolled
back) by the first.  Every write batch and every read-modify-write composite
below therefore runs under :attr:`_lock`, a re-entrant lock so the guard reads
those methods perform can nest inside it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..models import (
    AnalystKind,
    CategoryTotal,
    CoachingReport,
    CpuMeta,
    EvidenceItem,
    Game,
    GameAnalysis,
    GameSource,
    GameStatus,
    KeyMoment,
    Level,
    MistakeCategory,
    MoveAnalysis,
    MoveRecord,
    Phase,
    PhaseStats,
    PlayerProfile,
    RatingEvent,
    RatingState,
    Suggestion,
    WindowEntry,
)
from ._guards import (
    guard_game_mutable,
    guard_move_append,
    guard_rating_event,
    guard_single_in_progress,
)
from .repository import ConflictError, NotFoundError

__all__ = ["SCHEMA", "SQLiteRepository"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS level (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL,
    max_depth            INTEGER NOT NULL CHECK (max_depth BETWEEN 1 AND 5),
    node_budget          INTEGER NOT NULL CHECK (node_budget > 0),
    noise_sigma_cp       REAL    NOT NULL CHECK (noise_sigma_cp >= 0),
    blunder_prob         REAL    NOT NULL CHECK (blunder_prob BETWEEN 0 AND 0.35),
    blunder_margin_lo_cp INTEGER,
    blunder_margin_hi_cp INTEGER,
    book_plies           INTEGER NOT NULL CHECK (book_plies BETWEEN 0 AND 12),
    elo_internal         REAL    NOT NULL,
    acpl_mean            REAL    NOT NULL,
    acpl_std             REAL    NOT NULL,
    calibration_seed     INTEGER NOT NULL,
    calibrated_at        TEXT    NOT NULL,
    engine_version       TEXT    NOT NULL,
    CHECK ((blunder_prob = 0) = (blunder_margin_lo_cp IS NULL))
);

CREATE TABLE IF NOT EXISTS player_profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    display_name    TEXT NOT NULL,
    challenge_mode  TEXT NOT NULL,
    preferred_color TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rating_state (
    id                       INTEGER PRIMARY KEY CHECK (id = 1),
    glicko_rating            REAL    NOT NULL,
    glicko_rd                REAL    NOT NULL CHECK (glicko_rd BETWEEN 60 AND 350),
    perf_ewma                REAL,
    judged_games             INTEGER NOT NULL,
    rated_games              INTEGER NOT NULL,
    surprise_window          TEXT    NOT NULL,
    games_since_rd_inflation INTEGER NOT NULL,
    divergence_streak        INTEGER NOT NULL,
    calibration_warning      INTEGER NOT NULL,
    current_level_id         INTEGER NOT NULL REFERENCES level(id),
    last_game_id             INTEGER REFERENCES game(id),
    updated_at               TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS game (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source               TEXT    NOT NULL,
    created_at           TEXT    NOT NULL,
    seed                 INTEGER,
    player_color         TEXT    NOT NULL,
    level_id             INTEGER REFERENCES level(id),
    level_elo            REAL,
    recommended_level_id INTEGER,
    level_overridden     INTEGER NOT NULL,
    status               TEXT    NOT NULL,
    termination          TEXT,
    result_score         REAL,
    ply_count            INTEGER NOT NULL,
    final_fen            TEXT,
    eco                  TEXT,
    opening_name         TEXT,
    book_depth           INTEGER NOT NULL,
    rated                INTEGER NOT NULL,
    imported_tags        TEXT,
    CHECK ((source = 'played') = (seed IS NOT NULL)),
    CHECK ((source = 'played') = (level_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS move_record (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    INTEGER NOT NULL REFERENCES game(id),
    ply        INTEGER NOT NULL CHECK (ply >= 1),
    color      TEXT    NOT NULL,
    san        TEXT    NOT NULL,
    uci        TEXT    NOT NULL,
    fen_after  TEXT    NOT NULL,
    is_book    INTEGER NOT NULL,
    cpu_meta   TEXT,
    UNIQUE (game_id, ply)
);

CREATE TABLE IF NOT EXISTS game_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES game(id),
    analyst         TEXT    NOT NULL,
    analyst_version TEXT    NOT NULL,
    node_budget     INTEGER NOT NULL,
    created_at      TEXT    NOT NULL,
    book_depth      INTEGER NOT NULL,
    is_rating_basis INTEGER NOT NULL,
    acpl            REAL    NOT NULL,
    accuracy        REAL    NOT NULL,
    perf_rating     REAL    NOT NULL,
    n_blunders      INTEGER NOT NULL,
    n_mistakes      INTEGER NOT NULL,
    n_inaccuracies  INTEGER NOT NULL,
    mg_start_ply    INTEGER,
    eg_start_ply    INTEGER,
    per_phase       TEXT    NOT NULL,
    key_moments     TEXT    NOT NULL,
    UNIQUE (game_id, analyst, analyst_version, node_budget)
);

CREATE TABLE IF NOT EXISTS move_analysis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id   INTEGER NOT NULL REFERENCES game_analysis(id),
    ply           INTEGER NOT NULL,
    in_acpl       INTEGER NOT NULL,
    cp_best       INTEGER NOT NULL,
    cp_played     INTEGER NOT NULL,
    cp_loss       INTEGER NOT NULL,
    w_before      REAL    NOT NULL,
    w_after       REAL    NOT NULL,
    delta_w       REAL    NOT NULL,
    severity      TEXT    NOT NULL,
    best_uci      TEXT    NOT NULL,
    best_line_san TEXT    NOT NULL,
    phase         TEXT    NOT NULL,
    category      TEXT,
    motif         TEXT    NOT NULL,
    evidence      TEXT,
    UNIQUE (analysis_id, ply)
);

CREATE TABLE IF NOT EXISTS rating_event (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          INTEGER NOT NULL UNIQUE REFERENCES game(id),
    created_at       TEXT    NOT NULL,
    result_score     REAL    NOT NULL,
    opponent_elo     REAL    NOT NULL,
    expected_score   REAL    NOT NULL,
    surprise_after   REAL    NOT NULL,
    rd_inflated      INTEGER NOT NULL,
    glicko_r_before  REAL    NOT NULL,
    glicko_rd_before REAL    NOT NULL,
    glicko_r_after   REAL    NOT NULL,
    glicko_rd_after  REAL    NOT NULL,
    perf_game        REAL    NOT NULL,
    perf_ewma_after  REAL    NOT NULL,
    lambda_used      REAL    NOT NULL,
    r_hat_after      REAL    NOT NULL,
    level_played     INTEGER NOT NULL,
    level_next       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS coaching_report (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    window              TEXT    NOT NULL,
    skipped_game_ids    TEXT    NOT NULL,
    include_imported    INTEGER NOT NULL,
    totals_by_category  TEXT    NOT NULL,
    totals_by_phase     TEXT    NOT NULL,
    prev_report_id      INTEGER REFERENCES coaching_report(id)
);

CREATE TABLE IF NOT EXISTS suggestion (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      INTEGER NOT NULL REFERENCES coaching_report(id),
    rank           INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 3),
    category       TEXT    NOT NULL,
    priority_score REAL    NOT NULL,
    advice_id      TEXT    NOT NULL,
    advice_title   TEXT    NOT NULL,
    advice_body    TEXT    NOT NULL,
    advice_drill   TEXT    NOT NULL,
    evidence       TEXT    NOT NULL,
    UNIQUE (report_id, rank)
);

CREATE INDEX IF NOT EXISTS idx_move_record_game ON move_record(game_id);
CREATE INDEX IF NOT EXISTS idx_move_analysis_analysis ON move_analysis(analysis_id);
CREATE INDEX IF NOT EXISTS idx_game_analysis_game ON game_analysis(game_id);
CREATE INDEX IF NOT EXISTS idx_game_status ON game(status);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SQLiteRepository:
    """Repository backed by a SQLite file (or ``:memory:``)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path not in (":memory:", ""):
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: see the module docstring.  Safe at
        # sqlite3.threadsafety == 3; transactions are serialised by ``_lock``.
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------- #

    def initialize(self, levels: Sequence[Level]) -> None:
        with self._lock, self._connection:
            self._connection.executescript(SCHEMA)
            for level in levels:
                self._connection.execute(
                    """
                    INSERT INTO level (id, name, max_depth, node_budget, noise_sigma_cp,
                                       blunder_prob, blunder_margin_lo_cp, blunder_margin_hi_cp,
                                       book_plies, elo_internal, acpl_mean, acpl_std,
                                       calibration_seed, calibrated_at, engine_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name, max_depth=excluded.max_depth,
                        node_budget=excluded.node_budget, noise_sigma_cp=excluded.noise_sigma_cp,
                        blunder_prob=excluded.blunder_prob,
                        blunder_margin_lo_cp=excluded.blunder_margin_lo_cp,
                        blunder_margin_hi_cp=excluded.blunder_margin_hi_cp,
                        book_plies=excluded.book_plies, elo_internal=excluded.elo_internal,
                        acpl_mean=excluded.acpl_mean, acpl_std=excluded.acpl_std,
                        calibration_seed=excluded.calibration_seed,
                        calibrated_at=excluded.calibrated_at,
                        engine_version=excluded.engine_version
                    """,
                    (
                        level.id,
                        level.name,
                        level.max_depth,
                        level.node_budget,
                        level.noise_sigma_cp,
                        level.blunder_prob,
                        level.blunder_margin_lo_cp,
                        level.blunder_margin_hi_cp,
                        level.book_plies,
                        level.elo_internal,
                        level.acpl_mean,
                        level.acpl_std,
                        level.calibration_seed,
                        level.calibrated_at,
                        level.engine_version,
                    ),
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # -- levels -------------------------------------------------------------- #

    def list_levels(self) -> list[Level]:
        rows = self._connection.execute("SELECT * FROM level ORDER BY id").fetchall()
        return [Level.model_validate(dict(row)) for row in rows]

    def get_level(self, level_id: int) -> Level:
        row = self._connection.execute("SELECT * FROM level WHERE id = ?", (level_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no such level: {level_id}")
        return Level.model_validate(dict(row))

    # -- profile -------------------------------------------------------------- #

    def get_profile(self) -> PlayerProfile | None:
        row = self._connection.execute("SELECT * FROM player_profile WHERE id = 1").fetchone()
        return PlayerProfile.model_validate(dict(row)) if row else None

    def save_profile(self, profile: PlayerProfile) -> PlayerProfile:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO player_profile (id, display_name, challenge_mode, preferred_color,
                                            created_at, updated_at)
                VALUES (1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name=excluded.display_name,
                    challenge_mode=excluded.challenge_mode,
                    preferred_color=excluded.preferred_color,
                    updated_at=excluded.updated_at
                """,
                (
                    profile.display_name,
                    str(profile.challenge_mode),
                    str(profile.preferred_color),
                    profile.created_at,
                    profile.updated_at,
                ),
            )
        return profile.model_copy(update={"id": 1})

    # -- rating state ---------------------------------------------------------- #

    def get_rating_state(self) -> RatingState | None:
        row = self._connection.execute("SELECT * FROM rating_state WHERE id = 1").fetchone()
        if row is None:
            return None
        data = dict(row)
        data["surprise_window"] = json.loads(data["surprise_window"])
        data["calibration_warning"] = bool(data["calibration_warning"])
        return RatingState.model_validate(data)

    def save_rating_state(self, state: RatingState) -> RatingState:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO rating_state (id, glicko_rating, glicko_rd, perf_ewma, judged_games,
                                          rated_games, surprise_window, games_since_rd_inflation,
                                          divergence_streak, calibration_warning,
                                          current_level_id, last_game_id, updated_at)
                VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    glicko_rating=excluded.glicko_rating, glicko_rd=excluded.glicko_rd,
                    perf_ewma=excluded.perf_ewma, judged_games=excluded.judged_games,
                    rated_games=excluded.rated_games, surprise_window=excluded.surprise_window,
                    games_since_rd_inflation=excluded.games_since_rd_inflation,
                    divergence_streak=excluded.divergence_streak,
                    calibration_warning=excluded.calibration_warning,
                    current_level_id=excluded.current_level_id,
                    last_game_id=excluded.last_game_id, updated_at=excluded.updated_at
                """,
                (
                    state.glicko_rating,
                    state.glicko_rd,
                    state.perf_ewma,
                    state.judged_games,
                    state.rated_games,
                    _dumps(state.surprise_window),
                    state.games_since_rd_inflation,
                    state.divergence_streak,
                    int(state.calibration_warning),
                    state.current_level_id,
                    state.last_game_id,
                    state.updated_at,
                ),
            )
        return state.model_copy(update={"id": 1})

    # -- games ------------------------------------------------------------------ #

    @staticmethod
    def _game_row(row: sqlite3.Row) -> Game:
        data = dict(row)
        data["level_overridden"] = bool(data["level_overridden"])
        data["rated"] = bool(data["rated"])
        data["imported_tags"] = json.loads(data["imported_tags"]) if data["imported_tags"] else None
        return Game.model_validate(data)

    def create_game(self, game: Game) -> Game:
        with self._lock, self._connection:  # guard read + insert: one composite
            guard_single_in_progress(self.get_in_progress_game(), game)
            cursor = self._connection.execute(
                """
                INSERT INTO game (source, created_at, seed, player_color, level_id, level_elo,
                                  recommended_level_id, level_overridden, status, termination,
                                  result_score, ply_count, final_fen, eco, opening_name,
                                  book_depth, rated, imported_tags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(game.source),
                    game.created_at,
                    game.seed,
                    str(game.player_color),
                    game.level_id,
                    game.level_elo,
                    game.recommended_level_id,
                    int(game.level_overridden),
                    str(game.status),
                    str(game.termination) if game.termination else None,
                    game.result_score,
                    game.ply_count,
                    game.final_fen,
                    game.eco,
                    game.opening_name,
                    game.book_depth,
                    int(game.rated),
                    _dumps(game.imported_tags) if game.imported_tags else None,
                ),
            )
        return game.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def update_game(self, game: Game) -> Game:
        if game.id is None:
            raise ConflictError("cannot update a game without an id")
        with self._lock, self._connection:  # guard reads + update: one composite
            stored = self.get_game(game.id)
            guard_game_mutable(stored)
            if game.status is GameStatus.IN_PROGRESS:
                other = self.get_in_progress_game()
                if other is not None and other.id != game.id:
                    guard_single_in_progress(other, game)
            self._connection.execute(
                """
                UPDATE game SET status=?, termination=?, result_score=?, ply_count=?,
                                final_fen=?, eco=?, opening_name=?, book_depth=?, rated=?,
                                imported_tags=?, level_id=?, level_elo=?,
                                recommended_level_id=?, level_overridden=?
                WHERE id=?
                """,
                (
                    str(game.status),
                    str(game.termination) if game.termination else None,
                    game.result_score,
                    game.ply_count,
                    game.final_fen,
                    game.eco,
                    game.opening_name,
                    game.book_depth,
                    int(game.rated),
                    _dumps(game.imported_tags) if game.imported_tags else None,
                    game.level_id,
                    game.level_elo,
                    game.recommended_level_id,
                    int(game.level_overridden),
                    game.id,
                ),
            )
        return game

    def get_game(self, game_id: int) -> Game:
        row = self._connection.execute("SELECT * FROM game WHERE id = ?", (game_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"no such game: {game_id}")
        return self._game_row(row)

    def list_games(
        self,
        *,
        status: GameStatus | None = None,
        source: GameSource | None = None,
        limit: int | None = None,
    ) -> list[Game]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(str(status))
        if source is not None:
            clauses.append("source = ?")
            params.append(str(source))
        sql = "SELECT * FROM game"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._game_row(row) for row in self._connection.execute(sql, params).fetchall()]

    def get_in_progress_game(self) -> Game | None:
        row = self._connection.execute(
            "SELECT * FROM game WHERE status = ? LIMIT 1", (str(GameStatus.IN_PROGRESS),)
        ).fetchone()
        return self._game_row(row) if row else None

    # -- moves --------------------------------------------------------------- #

    def append_move(self, game_id: int, record: MoveRecord) -> MoveRecord:
        with self._lock, self._connection:  # guard read + append: one composite
            guard_move_append(self.list_moves(game_id), record)
            cursor = self._connection.execute(
                """
                INSERT INTO move_record (game_id, ply, color, san, uci, fen_after, is_book, cpu_meta)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    game_id,
                    record.ply,
                    str(record.color),
                    record.san,
                    record.uci,
                    record.fen_after,
                    int(record.is_book),
                    _dumps(record.cpu_meta.model_dump()) if record.cpu_meta else None,
                ),
            )
        return record.model_copy(update={"id": int(cursor.lastrowid or 0), "game_id": game_id})

    def list_moves(self, game_id: int) -> list[MoveRecord]:
        rows = self._connection.execute(
            "SELECT * FROM move_record WHERE game_id = ? ORDER BY ply", (game_id,)
        ).fetchall()
        records: list[MoveRecord] = []
        for row in rows:
            data = dict(row)
            data["is_book"] = bool(data["is_book"])
            data["cpu_meta"] = (
                CpuMeta.model_validate(json.loads(data["cpu_meta"])) if data["cpu_meta"] else None
            )
            records.append(MoveRecord.model_validate(data))
        return records

    # -- analyses ------------------------------------------------------------- #

    def add_analysis(self, analysis: GameAnalysis) -> GameAnalysis:
        if analysis.game_id is None:
            raise ConflictError("an analysis must reference a game")
        with self._lock, self._connection:  # dedup read + multi-row insert: one composite
            existing = self._connection.execute(
                """
                SELECT id FROM game_analysis
                WHERE game_id = ? AND analyst = ? AND analyst_version = ? AND node_budget = ?
                """,
                (
                    analysis.game_id,
                    str(analysis.analyst),
                    analysis.analyst_version,
                    analysis.node_budget,
                ),
            ).fetchone()
            if existing is not None:
                # The tuple pins the code that produced it, so a repeat request is a
                # read (DATA_MODEL "Invariants": deterministic per FR-2/FR-16).
                return self.get_analysis(int(existing["id"]))

            cursor = self._connection.execute(
                """
                INSERT INTO game_analysis (game_id, analyst, analyst_version, node_budget,
                                           created_at, book_depth, is_rating_basis, acpl,
                                           accuracy, perf_rating, n_blunders, n_mistakes,
                                           n_inaccuracies, mg_start_ply, eg_start_ply,
                                           per_phase, key_moments)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    analysis.game_id,
                    str(analysis.analyst),
                    analysis.analyst_version,
                    analysis.node_budget,
                    analysis.created_at,
                    analysis.book_depth,
                    int(analysis.is_rating_basis),
                    analysis.acpl,
                    analysis.accuracy,
                    analysis.perf_rating,
                    analysis.n_blunders,
                    analysis.n_mistakes,
                    analysis.n_inaccuracies,
                    analysis.mg_start_ply,
                    analysis.eg_start_ply,
                    _dumps({str(k): v.model_dump() for k, v in analysis.per_phase.items()}),
                    _dumps([k.model_dump() for k in analysis.key_moments]),
                ),
            )
            analysis_id = int(cursor.lastrowid or 0)
            for move in analysis.moves:
                self._connection.execute(
                    """
                    INSERT INTO move_analysis (analysis_id, ply, in_acpl, cp_best, cp_played,
                                               cp_loss, w_before, w_after, delta_w, severity,
                                               best_uci, best_line_san, phase, category, motif,
                                               evidence)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        analysis_id,
                        move.ply,
                        int(move.in_acpl),
                        move.cp_best,
                        move.cp_played,
                        move.cp_loss,
                        move.w_before,
                        move.w_after,
                        move.delta_w,
                        str(move.severity),
                        move.best_uci,
                        _dumps(move.best_line_san),
                        str(move.phase),
                        str(move.category) if move.category else None,
                        str(move.motif),
                        _dumps(move.evidence) if move.evidence else None,
                    ),
                )
        return self.get_analysis(analysis_id)

    def get_analysis(self, analysis_id: int) -> GameAnalysis:
        row = self._connection.execute(
            "SELECT * FROM game_analysis WHERE id = ?", (analysis_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no such analysis: {analysis_id}")
        return self._analysis_row(row)

    def _analysis_row(self, row: sqlite3.Row) -> GameAnalysis:
        data = dict(row)
        data["is_rating_basis"] = bool(data["is_rating_basis"])
        data["analyst"] = AnalystKind(data["analyst"])
        data["per_phase"] = {
            Phase(key): PhaseStats.model_validate(value)
            for key, value in json.loads(data["per_phase"]).items()
        }
        data["key_moments"] = [
            KeyMoment.model_validate(item) for item in json.loads(data["key_moments"])
        ]
        move_rows = self._connection.execute(
            "SELECT * FROM move_analysis WHERE analysis_id = ? ORDER BY ply", (row["id"],)
        ).fetchall()
        moves: list[MoveAnalysis] = []
        for move_row in move_rows:
            move_data = dict(move_row)
            move_data["in_acpl"] = bool(move_data["in_acpl"])
            move_data["best_line_san"] = json.loads(move_data["best_line_san"])
            move_data["evidence"] = (
                json.loads(move_data["evidence"]) if move_data["evidence"] else None
            )
            if move_data["category"] is not None:
                move_data["category"] = MistakeCategory(move_data["category"])
            moves.append(MoveAnalysis.model_validate(move_data))
        data["moves"] = moves
        return GameAnalysis.model_validate(data)

    def list_analyses(self, game_id: int) -> list[GameAnalysis]:
        rows = self._connection.execute(
            "SELECT * FROM game_analysis WHERE game_id = ? ORDER BY id", (game_id,)
        ).fetchall()
        return [self._analysis_row(row) for row in rows]

    def latest_analysis(self, game_id: int) -> GameAnalysis | None:
        analyses = self.list_analyses(game_id)
        return analyses[-1] if analyses else None

    # -- rating events ---------------------------------------------------------- #

    def append_rating_event(self, event: RatingEvent) -> RatingEvent:
        with self._lock, self._connection:  # guard reads + append: one composite
            existing = [e.game_id for e in self.list_rating_events()]
            guard_rating_event(self.get_rating_state(), existing, event)
            cursor = self._connection.execute(
                """
                INSERT INTO rating_event (game_id, created_at, result_score, opponent_elo,
                                          expected_score, surprise_after, rd_inflated,
                                          glicko_r_before, glicko_rd_before, glicko_r_after,
                                          glicko_rd_after, perf_game, perf_ewma_after,
                                          lambda_used, r_hat_after, level_played, level_next)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.game_id,
                    event.created_at,
                    event.result_score,
                    event.opponent_elo,
                    event.expected_score,
                    event.surprise_after,
                    int(event.rd_inflated),
                    event.glicko_r_before,
                    event.glicko_rd_before,
                    event.glicko_r_after,
                    event.glicko_rd_after,
                    event.perf_game,
                    event.perf_ewma_after,
                    event.lambda_used,
                    event.r_hat_after,
                    event.level_played,
                    event.level_next,
                ),
            )
        return event.model_copy(update={"id": int(cursor.lastrowid or 0)})

    def list_rating_events(self, *, limit: int | None = None) -> list[RatingEvent]:
        sql = "SELECT * FROM rating_event ORDER BY id"
        params: list[Any] = []
        if limit is not None:
            sql = "SELECT * FROM (SELECT * FROM rating_event ORDER BY id DESC LIMIT ?) ORDER BY id"
            params.append(limit)
        rows = self._connection.execute(sql, params).fetchall()
        events: list[RatingEvent] = []
        for row in rows:
            data = dict(row)
            data["rd_inflated"] = bool(data["rd_inflated"])
            events.append(RatingEvent.model_validate(data))
        return events

    # -- reports ------------------------------------------------------------------ #

    def add_report(self, report: CoachingReport) -> CoachingReport:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO coaching_report (created_at, window, skipped_game_ids,
                                             include_imported, totals_by_category,
                                             totals_by_phase, prev_report_id)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    report.created_at,
                    _dumps([w.model_dump() for w in report.window]),
                    _dumps(report.skipped_game_ids),
                    int(report.include_imported),
                    _dumps({str(k): v.model_dump() for k, v in report.totals_by_category.items()}),
                    _dumps({str(k): v for k, v in report.totals_by_phase.items()}),
                    report.prev_report_id,
                ),
            )
            report_id = int(cursor.lastrowid or 0)
            for suggestion in report.suggestions:
                self._connection.execute(
                    """
                    INSERT INTO suggestion (report_id, rank, category, priority_score, advice_id,
                                            advice_title, advice_body, advice_drill, evidence)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        report_id,
                        suggestion.rank,
                        str(suggestion.category),
                        suggestion.priority_score,
                        suggestion.advice_id,
                        suggestion.advice_title,
                        suggestion.advice_body,
                        suggestion.advice_drill,
                        _dumps([e.model_dump() for e in suggestion.evidence]),
                    ),
                )
        return self.get_report(report_id)

    def get_report(self, report_id: int) -> CoachingReport:
        row = self._connection.execute(
            "SELECT * FROM coaching_report WHERE id = ?", (report_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"no such report: {report_id}")
        return self._report_row(row)

    def _report_row(self, row: sqlite3.Row) -> CoachingReport:
        data = dict(row)
        data["include_imported"] = bool(data["include_imported"])
        data["window"] = [WindowEntry.model_validate(w) for w in json.loads(data["window"])]
        data["skipped_game_ids"] = json.loads(data["skipped_game_ids"])
        data["totals_by_category"] = {
            MistakeCategory(k): CategoryTotal.model_validate(v)
            for k, v in json.loads(data["totals_by_category"]).items()
        }
        data["totals_by_phase"] = {
            Phase(k): float(v) for k, v in json.loads(data["totals_by_phase"]).items()
        }
        suggestion_rows = self._connection.execute(
            "SELECT * FROM suggestion WHERE report_id = ? ORDER BY rank", (row["id"],)
        ).fetchall()
        suggestions: list[Suggestion] = []
        for suggestion_row in suggestion_rows:
            suggestion_data = dict(suggestion_row)
            suggestion_data["category"] = MistakeCategory(suggestion_data["category"])
            suggestion_data["evidence"] = [
                EvidenceItem.model_validate(item)
                for item in json.loads(suggestion_data["evidence"])
            ]
            suggestions.append(Suggestion.model_validate(suggestion_data))
        data["suggestions"] = suggestions
        return CoachingReport.model_validate(data)

    def list_reports(self, *, limit: int | None = None) -> list[CoachingReport]:
        sql = "SELECT * FROM coaching_report ORDER BY id DESC"
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._report_row(row) for row in self._connection.execute(sql, params).fetchall()]

"""FR-15 — Typer CLI tests (``CliRunner``), against a temporary SQLite file.

Each test drives the real console-script surface: same services, same store, no
mocks beyond pointing ``--db`` at ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from chessmentor.cli.app import app
from typer.testing import CliRunner

runner = CliRunner()

PGN = """[Event "Test"]
[White "Owner"]
[Black "Rival"]
[Result "0-1"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. d3 d6 0-1
"""


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "chessmentor.db"


def _run(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def test_fr15_help_lists_every_documented_command() -> None:
    code, out = _run("--help")
    assert code == 0
    for command in ("init", "play", "games", "analyze", "import", "rating", "report", "levels"):
        assert command in out


def test_fr15_init_validates_datasets_and_reports_the_cold_start_level(db: Path) -> None:
    code, out = _run("init", "--db", str(db), "--name", "Owner", "--challenge", "balanced")
    assert code == 0
    assert "10 levels" in out
    assert "advice entries" in out
    assert "starting level" in out
    assert db.exists()


def test_fr15_commands_exit_non_zero_before_init(db: Path) -> None:
    code, out = _run("rating", "--db", str(db))
    assert code == 1
    assert "error" in out


def test_fr15_levels_marks_the_recommendation(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    code, out = _run("levels", "--db", str(db))
    assert code == 0
    assert out.count("->") == 1
    assert "L10" in out
    assert "internal rating" in out


def test_fr15_profile_show_and_set(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    code, out = _run("profile", "show", "--db", str(db))
    assert code == 0
    assert "Owner" in out
    code, out = _run("profile", "set", "--db", str(db), "--challenge", "stretch", "--name", "Ada")
    assert code == 0
    assert "Ada" in out and "stretch" in out
    _, out = _run("profile", "show", "--db", str(db))
    assert "stretch" in out


def test_fr15_play_accepts_san_rejects_illegal_and_stays_resumable(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner", "--color", "white")
    result = runner.invoke(
        app,
        ["play", "--db", str(db), "--seed", "4242", "--level", "2"],
        input="e5\ne4\nboard\nlegal\nmoves\nquit\n",
    )
    assert result.exit_code == 0
    assert "new game" in result.output
    assert "illegal move" in result.output  # e5 is not legal at ply 1
    assert "CPU plays" in result.output
    assert "resume it with" in result.output

    resumed = runner.invoke(app, ["play", "--db", str(db)], input="quit\n")
    assert resumed.exit_code == 0
    assert "resuming game #1" in resumed.output


def test_fr15_play_resign_finishes_rates_and_prints_the_judge_pass(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner", "--color", "white")
    result = runner.invoke(
        app,
        ["play", "--db", str(db), "--seed", "77", "--level", "1"],
        input="e4\nNf3\nBc4\nd3\nresign\n",
    )
    assert result.exit_code == 0
    assert "You lose (resignation)" in result.output
    assert "judge pass:" in result.output
    assert "internal rating" in result.output

    code, out = _run("rating", "--db", str(db), "--history")
    assert code == 0
    assert "results channel (Glicko)" in out
    assert "move-quality channel" in out
    assert "blend lambda" in out


def test_fr15_games_list_and_show(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner", "--color", "white")
    runner.invoke(
        app, ["play", "--db", str(db), "--seed", "5", "--level", "1"], input="e4\nresign\n"
    )
    code, out = _run("games", "list", "--db", str(db))
    assert code == 0
    assert "played" in out
    code, out = _run("games", "show", "1", "--db", str(db))
    assert code == 0
    assert "game #1" in out
    code, out = _run("games", "show", "1", "--db", str(db), "--pgn")
    assert code == 0
    assert '[White "Owner"]' in out
    code, out = _run("games", "show", "99", "--db", str(db))
    assert code == 1


def test_fr13_import_and_analyze_from_the_cli(db: Path, tmp_path: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(PGN, encoding="utf-8")

    code, out = _run("import", str(pgn_path), "--db", str(db), "--as", "auto")
    assert code == 0
    assert "imported game #1" in out
    assert "never affect your rating" in out

    code, out = _run("analyze", "1", "--db", str(db), "--nodes", "400")
    assert code == 0
    assert "ACPL" in out
    assert "accuracy" in out
    # A second identical request must reuse the stored analysis.
    code, out = _run("analyze", "1", "--db", str(db), "--nodes", "400")
    assert "(cached)" in out


def test_fr13_import_rejects_a_bad_side_flag(db: Path, tmp_path: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text(PGN, encoding="utf-8")
    code, out = _run("import", str(pgn_path), "--db", str(db), "--as", "sideways")
    assert code == 1
    assert "--as must be white, black or auto" in out


def test_fr15_import_missing_file_exits_non_zero(db: Path, tmp_path: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    code, out = _run("import", str(tmp_path / "nope.pgn"), "--db", str(db))
    assert code == 1
    assert "no such file" in out


def test_fr12_report_command_prints_suggestions_or_says_why_not(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner", "--color", "white")
    runner.invoke(
        app,
        ["play", "--db", str(db), "--seed", "31", "--level", "1"],
        input="e4\nQh5\nBc4\nNf3\nresign\n",
    )
    code, out = _run("report", "--db", str(db))
    assert code == 0
    assert "coaching report #1" in out
    assert "drill:" in out or "nothing to work on yet" in out


def test_fr15_analyze_unknown_game_exits_non_zero(db: Path) -> None:
    _run("init", "--db", str(db), "--name", "Owner")
    code, _ = _run("analyze", "42", "--db", str(db))
    assert code == 1

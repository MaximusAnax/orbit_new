"""Typer CLI (SCOPE.md FR-15) via CliRunner, plus the FR-13 single scan pass.

Exit codes are part of the contract: 0 on success, 1 on a datasweep error,
2 on a usage error.  Read commands must be scriptable through ``--json``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from datasweep.adapters.notifier import LogNotifier
from datasweep.cli.app import app as cli_app
from support_datasweep import SAMPLE_ROWS, write_sample
from typer.testing import CliRunner

from datasweep import __version__

runner = CliRunner()


@pytest.fixture
def db(tmp_path: Path) -> str:
    """The store lives outside the fixture directory, so `profile` can prove
    it wrote nothing next to the source file."""
    return str(tmp_path / "store" / "datasweep.db")


def invoke(*args: str) -> object:
    return runner.invoke(cli_app, list(args))


def _clean(tmp_path: Path, db: str, *extra: str):
    source = write_sample(tmp_path)
    return invoke("clean", source, "--out", str(tmp_path / "out"), "--db", db, *extra)


def _run_id(tmp_path: Path, db: str) -> str:
    listed = invoke("runs", "--db", db, "--json")
    return json.loads(listed.stdout)[0]["id"]


def test_fr15_help_lists_every_command() -> None:
    result = invoke("--help")
    assert result.exit_code == 0
    for command in ("run", "clean", "profile", "runs", "show", "review", "accept", "revert"):
        assert command in result.stdout


def test_fr15_version() -> None:
    result = invoke("--version")
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_fr15_clean_prints_a_summary(tmp_path: Path, db: str) -> None:
    result = _clean(tmp_path, db)
    assert result.exit_code == 0
    assert "review_pending" in result.stdout
    assert f"rows={SAMPLE_ROWS}" in result.stdout
    assert "artifacts:" in result.stdout


def test_fr15_clean_json_is_machine_readable(tmp_path: Path, db: str) -> None:
    result = _clean(tmp_path, db, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["n_rows"] == SAMPLE_ROWS
    assert Path(payload["artifact_dir"], "cleaned.csv").is_file()


def test_fr2_cli_second_clean_is_skipped(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    again = _clean(tmp_path, db)
    assert again.exit_code == 0
    assert "already processed" in again.stdout


def test_fr15_profile_writes_nothing(tmp_path: Path, db: str) -> None:
    data = tmp_path / "data"
    data.mkdir()
    source = write_sample(data)
    before = sorted(p.name for p in data.iterdir())
    result = invoke("profile", source, "--db", db)
    assert result.exit_code == 0
    assert "country" in result.stdout and "categorical" in result.stdout
    assert sorted(p.name for p in data.iterdir()) == before


def test_us2_show_changes_lists_every_audited_cell(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    run_id = _run_id(tmp_path, db)
    result = invoke("show", run_id, "--changes", "--issues", "--paths", "--db", db)
    assert result.exit_code == 0
    assert "audited change(s)" in result.stdout
    assert "fix.trim" in result.stdout
    assert "cleaned:" in result.stdout


def test_us2_show_accepts_a_run_id_prefix(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    run_id = _run_id(tmp_path, db)
    result = invoke("show", run_id[:8], "--db", db)
    assert result.exit_code == 0
    assert run_id in result.stdout


def test_fr11_review_accept_and_revert_round_trip(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    run_id = _run_id(tmp_path, db)

    review = invoke("review", run_id, "--db", db, "--json")
    items = json.loads(review.stdout)
    assert [item["rule"] for item in items] == ["fix.label_merge_nn"]

    accepted = invoke("accept", run_id, "--all", "--db", db, "--json")
    assert accepted.exit_code == 0
    revision = json.loads(accepted.stdout)
    assert revision["revision_no"] == 2
    assert Path(revision["cleaned_path"]).is_file()

    reverted = invoke("revert", run_id, "--db", db)
    assert reverted.exit_code == 0
    assert "match" in reverted.stdout


def test_fr11_reject_records_the_decision(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    run_id = _run_id(tmp_path, db)
    item = json.loads(invoke("review", run_id, "--db", db, "--json").stdout)[0]["id"]
    result = invoke("reject", run_id, "--item", item, "--db", db)
    assert result.exit_code == 0
    after = json.loads(invoke("review", run_id, "--db", db, "--json").stdout)
    assert after[0]["status"] == "rejected"


def test_fr13_run_once(tmp_path: Path, db: str) -> None:
    watched = tmp_path / "drop"
    watched.mkdir()
    source = write_sample(watched)
    # FR-1's settle check: a file is processed only once it has been quiet for
    # `settle_seconds`, so backdate it rather than sleeping in a test.
    quiet = time.time() - 600
    os.utime(source, (quiet, quiet))
    added = invoke("watch", "add", str(watched), "--db", db)
    assert added.exit_code == 0

    listed = invoke("watch", "ls", "--db", db)
    assert str(watched) in listed.stdout

    result = invoke("run", "--once", "--db", db, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["processed"]) == 1

    folder_id = json.loads(invoke("watch", "ls", "--db", db, "--json").stdout)[0]["id"]
    assert invoke("watch", "rm", folder_id, "--db", db).exit_code == 0
    # Deleting a folder keeps the run history (DATA_MODEL §2.1).
    assert len(json.loads(invoke("runs", "--db", db, "--json").stdout)) == 1


def test_fr13_log_notifier_writes_one_line_per_run(tmp_path: Path) -> None:
    from datasweep.engine.models import RunStatus, RunSummary

    log = tmp_path / "notify.log"
    notifier = LogNotifier(log)
    notifier.notify(
        RunSummary(
            run_id="abc",
            source_name="sales.csv",
            status=RunStatus.REVIEW_PENDING,
            issue_counts={"WS": 3},
            change_counts={"auto": 2, "review": 1},
        )
    )
    line = log.read_text(encoding="utf-8").strip()
    assert line.startswith("datasweep review_pending: sales.csv")
    assert "3 issues, 2 auto changes, 1 awaiting review" in line


# -- exit codes ------------------------------------------------------------


def test_fr15_missing_file_exits_1(tmp_path: Path, db: str) -> None:
    result = invoke("clean", str(tmp_path / "nope.csv"), "--db", db)
    assert result.exit_code == 1
    assert "error [" in result.stderr


def test_fr15_unknown_run_exits_1(tmp_path: Path, db: str) -> None:
    result = invoke("show", "nope", "--db", db)
    assert result.exit_code == 1
    assert "unknown_run" in result.stderr


def test_fr15_unsupported_format_exits_1(tmp_path: Path, db: str) -> None:
    path = tmp_path / "data.parquet"
    path.write_bytes(b"PAR1")
    result = invoke("clean", str(path), "--db", db)
    assert result.exit_code == 1
    assert "unsupported_format" in result.stderr


def test_fr15_usage_error_exits_2(db: str) -> None:
    assert invoke("clean", "--db", db).exit_code == 2
    assert invoke("nonsense").exit_code == 2


def test_fr15_accept_without_items_exits_1(tmp_path: Path, db: str) -> None:
    _clean(tmp_path, db)
    run_id = _run_id(tmp_path, db)
    result = invoke("accept", run_id, "--db", db)
    assert result.exit_code == 1
    assert "no_items" in result.stderr

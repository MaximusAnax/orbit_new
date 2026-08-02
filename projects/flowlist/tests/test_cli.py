"""FR-14: the Typer CLI — exit codes, ``--json``, and every documented command."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from flowlist.cli import ERROR_EXIT, app, parse_weights
from flowlist.errors import InvalidWeightsError
from typer.testing import CliRunner

CSV = """Track URI,Track Name,Artist Name(s),Album Name,Duration (ms),Tempo,Key,Mode,Energy,Danceability,Loudness
spotify:track:3n3Ppam7vgaVa1iaRUc9Lp,One More Hour,Synthetic Sun,Neon Hours,214000,124.0,9,0,0.71,0.80,-7.2
spotify:track:1n3Ppam7vgaVa1iaRUc9Lp,Night Drive,Vera Lux,Afterglow,231000,126.5,4,0,0.76,0.78,-6.4
spotify:track:2n3Ppam7vgaVa1iaRUc9Lp,Slow Burn,Marta Quiet,Embers,198000,86.0,7,1,0.42,0.65,-11.0
spotify:track:4n3Ppam7vgaVa1iaRUc9Lp,Breakline,Cyan Drift,Signal,205000,174.0,2,0,0.88,0.72,-5.1
spotify:track:5n3Ppam7vgaVa1iaRUc9Lp,Paper Moon,Halcyon Bay,Tide,222000,122.0,0,1,0.68,0.74,-7.9
spotify:track:6n3Ppam7vgaVa1iaRUc9Lp,No Key Here,Ghost Signal,Static,180000,0,-1,1,0.55,0.60,-9.0
"""

runner = CliRunner()


class Cli:
    """A CLI bound to one temporary database."""

    def __init__(self, db: Path, csv_path: Path) -> None:
        self.db = db
        self.csv = csv_path

    def run(self, *args: str, json_output: bool = False):
        argv = ["--db", str(self.db)]
        if json_output:
            argv.append("--json")
        return runner.invoke(app, [*argv, *args])

    def payload(self, *args: str) -> dict:
        result = self.run(*args, json_output=True)
        assert result.exit_code == 0, result.output
        return json.loads(result.output)


@pytest.fixture
def cli(tmp_path: Path) -> Iterator[Cli]:
    csv_path = tmp_path / "party.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    yield Cli(tmp_path / "flowlist.db", csv_path)


def _imported(cli: Cli) -> Cli:
    result = cli.run("import", str(cli.csv), "--name", "party")
    assert result.exit_code == 0, result.output
    return cli


def _run_id(cli: Cli, *extra: str) -> str:
    return cli.payload("reorder", "party", "--seed", "7", *extra)["run_id"]


# --------------------------------------------------------------------------- #
# Help and wiring
# --------------------------------------------------------------------------- #


def test_fr14_help_lists_every_documented_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "import",
        "ls",
        "show",
        "analyze",
        "features",
        "reorder",
        "explain",
        "compare",
        "apply",
        "export",
        "serve",
    ):
        assert command in result.output, command
    assert "--db" in result.output and "--json" in result.output


def test_fr14_subcommand_help_is_useful() -> None:
    result = runner.invoke(app, ["reorder", "--help"])
    assert result.exit_code == 0
    for option in ("--seed", "--profile", "--start-track", "--end-track", "--weights", "--apply"):
        assert option in result.output, option


# --------------------------------------------------------------------------- #
# FR-1/FR-2 import, US-1 listing
# --------------------------------------------------------------------------- #


def test_fr14_import_reports_sentinel_warnings(cli: Cli) -> None:
    result = cli.run("import", str(cli.csv), "--name", "party")
    assert result.exit_code == 0
    assert "imported party: 6 entries" in result.output
    assert "no key detected" in result.output
    assert "outside 40-260 BPM" in result.output


def test_fr14_import_json_output(cli: Cli) -> None:
    body = cli.payload("import", str(cli.csv), "--name", "party")
    assert body["entries"] == 6
    assert body["replaced"] is False
    assert {issue["code"] for issue in body["warnings"]} == {"bpm_sentinel", "key_sentinel"}


def test_fr1_import_refuses_a_duplicate_name_without_replace(cli: Cli) -> None:
    _imported(cli)
    result = cli.run("import", str(cli.csv), "--name", "party")
    assert result.exit_code == ERROR_EXIT
    assert "name_conflict" in result.output

    replaced = cli.payload("import", str(cli.csv), "--name", "party", "--replace")
    assert replaced["replaced"] is True


def test_fr1_replace_with_runs_needs_force(cli: Cli) -> None:
    _imported(cli)
    _run_id(cli)
    blocked = cli.run("import", str(cli.csv), "--name", "party", "--replace")
    assert blocked.exit_code == ERROR_EXIT
    assert "playlist_has_runs" in blocked.output

    forced = cli.run("import", str(cli.csv), "--name", "party", "--replace", "--force")
    assert forced.exit_code == 0


def test_fr14_import_rejects_an_unknown_format(cli: Cli) -> None:
    result = cli.run("import", str(cli.csv), "--name", "party", "--format", "xml")
    assert result.exit_code == ERROR_EXIT
    assert "unknown --format" in result.output


def test_fr14_import_of_a_missing_file_exits_non_zero(cli: Cli) -> None:
    result = cli.run("import", str(cli.csv.parent / "nope.csv"), "--name", "party")
    assert result.exit_code == ERROR_EXIT
    assert "import_failed" in result.output


def test_fr14_ls_and_show(cli: Cli) -> None:
    result = cli.run("ls")
    assert "no playlists yet" in result.output

    _imported(cli)
    assert "party" in cli.run("ls").output

    shown = cli.run("show", "party")
    assert shown.exit_code == 0
    assert "Synthetic Sun - One More Hour" in shown.output
    assert "124 BPM 8A" in shown.output
    assert "no BPM no key" in shown.output
    assert "stored order: n=6" in shown.output

    with_transitions = cli.run("show", "party", "--transitions")
    assert "total " in with_transitions.output

    body = cli.payload("show", "party")
    assert body["coverage"]["full"] == 5
    assert len(body["entries"]) == 6


def test_fr14_show_unknown_playlist_exits_non_zero(cli: Cli) -> None:
    result = cli.run("show", "ghost")
    assert result.exit_code == ERROR_EXIT
    assert "unknown_playlist" in result.output


# --------------------------------------------------------------------------- #
# FR-3/FR-4 features
# --------------------------------------------------------------------------- #


def test_fr14_analyze_reports_coverage(cli: Cli) -> None:
    _imported(cli)
    result = cli.run("analyze", "party")
    assert result.exit_code == 0
    assert "5/6 tracks fully featured" in result.output
    assert "missing bpm, key_pc" in result.output

    body = cli.payload("analyze", "party")
    assert body["missing"] == {"bpm": 1, "key_pc": 1}


def test_fr4_features_set_overrides_a_provider_value(cli: Cli) -> None:
    _imported(cli)
    track_id = cli.payload("show", "party")["entries"][-1]["track_id"]

    result = cli.run("features", "set", track_id, "--bpm", "128", "--key", "8A")
    assert result.exit_code == 0
    assert "manual override" in result.output

    entry = cli.payload("show", "party")["entries"][-1]
    assert entry["features"]["bpm"] == 128.0
    assert (entry["features"]["key_pc"], entry["features"]["mode"]) == (9, 0)
    assert cli.payload("analyze", "party")["full"] == 6


def test_fr14_features_set_rejects_a_bad_key(cli: Cli) -> None:
    _imported(cli)
    track_id = cli.payload("show", "party")["entries"][0]["track_id"]
    result = cli.run("features", "set", track_id, "--key", "Q7")
    assert result.exit_code == ERROR_EXIT


def test_fr14_features_set_unknown_track_exits_non_zero(cli: Cli) -> None:
    _imported(cli)
    result = cli.run("features", "set", "meta:nope", "--bpm", "120")
    assert result.exit_code == ERROR_EXIT
    assert "unknown_track" in result.output


# --------------------------------------------------------------------------- #
# FR-8/9/10 reorder, US-3/US-4
# --------------------------------------------------------------------------- #


def test_fr14_reorder_prints_a_scorecard_and_is_reproducible(cli: Cli) -> None:
    _imported(cli)
    result = cli.run("reorder", "party", "--seed", "7")
    assert result.exit_code == 0
    assert "before:" in result.output and "after :" in result.output
    assert "delta:" in result.output

    first = cli.payload("reorder", "party", "--seed", "7")
    second = cli.payload("reorder", "party", "--seed", "7")
    assert first["order"] == second["order"]
    assert first["run_id"] != second["run_id"]
    assert first["after"]["mean"] >= first["before"]["mean"]


def test_fr9_reorder_honours_pinned_endpoints(cli: Cli) -> None:
    _imported(cli)
    entries = cli.payload("show", "party")["entries"]
    start, end = entries[3]["entry_id"], entries[0]["entry_id"]
    body = cli.payload(
        "reorder", "party", "--seed", "7", "--start-track", start, "--end-track", end
    )
    assert body["order"][0] == start
    assert body["order"][-1] == end


def test_fr10_reorder_accepts_an_arc_profile(cli: Cli) -> None:
    _imported(cli)
    body = cli.payload("reorder", "party", "--seed", "7", "--profile", "build")
    assert body["params"]["profile"] == "build"


def test_fr6_reorder_rejects_invalid_weights(cli: Cli) -> None:
    _imported(cli)
    for spec in ("key=-1", "key=abc", "nope=1", "key"):
        result = cli.run("reorder", "party", "--weights", spec)
        assert result.exit_code == ERROR_EXIT, spec
        assert "invalid_weights" in result.output, spec


def test_fr6_weight_parsing_normalizes(cli: Cli) -> None:
    weights = parse_weights("key=3.5,bpm=3.5,energy=2,loudness=1")
    assert weights is not None
    normalized = weights.normalized()
    assert normalized.key == pytest.approx(0.35, abs=1e-9)
    assert parse_weights(None) is None
    with pytest.raises(InvalidWeightsError):
        parse_weights("bpm")


def test_fr14_reorder_apply_writes_the_order_back(cli: Cli) -> None:
    _imported(cli)
    body = cli.payload("reorder", "party", "--seed", "7", "--apply")
    assert body["applied"] is True
    stored = [entry["entry_id"] for entry in cli.payload("show", "party")["entries"]]
    assert stored == body["order"]


def test_us4_explain_shows_one_line_per_transition(cli: Cli) -> None:
    _imported(cli)
    run_id = _run_id(cli)
    result = cli.run("explain", run_id)
    assert result.exit_code == 0
    assert "->" in result.output
    assert "total" in result.output
    assert "no key on one side" in result.output

    body = cli.payload("explain", run_id)
    assert len(body["entries"]) == 6
    assert body["entries"][0]["transition"] is None
    assert body["entries"][1]["transition"]["key_relation"]


def test_us3_compare_reports_stored_and_recomputed_aggregates(cli: Cli) -> None:
    _imported(cli)
    run_id = _run_id(cli)
    result = cli.run("compare", run_id)
    assert result.exit_code == 0
    assert "coverage:" in result.output

    body = cli.payload("compare", run_id)
    assert body["after"]["mean"] == pytest.approx(body["recomputed_after"]["mean"], abs=1e-12)
    assert body["after"]["seamless"] == body["recomputed_after"]["seamless"]


def test_fr14_runs_listing(cli: Cli) -> None:
    _imported(cli)
    assert "no runs yet" in cli.run("runs").output
    run_id = _run_id(cli)
    assert run_id in cli.run("runs", "party").output
    assert cli.run("runs", "ghost").exit_code == ERROR_EXIT


def test_fr14_unknown_run_exits_non_zero(cli: Cli) -> None:
    _imported(cli)
    for command in ("explain", "compare", "apply"):
        result = cli.run(command, "nope")
        assert result.exit_code == ERROR_EXIT, command
        assert "unknown_run" in result.output, command


# --------------------------------------------------------------------------- #
# FR-12 apply and export, US-7
# --------------------------------------------------------------------------- #


def test_fr12_apply_command(cli: Cli) -> None:
    _imported(cli)
    body = cli.payload("reorder", "party", "--seed", "7")
    result = cli.run("apply", body["run_id"])
    assert result.exit_code == 0
    stored = [entry["entry_id"] for entry in cli.payload("show", "party")["entries"]]
    assert stored == body["order"]


def test_fr12_export_to_stdout_and_file(cli: Cli, tmp_path: Path) -> None:
    _imported(cli)
    run_id = _run_id(cli)

    printed = cli.run("export", run_id, "--format", "m3u")
    assert printed.exit_code == 0
    assert printed.output.startswith("#EXTM3U")
    assert "#EXTINF:" in printed.output

    out = tmp_path / "party.m3u8"
    written = cli.payload("export", run_id, "--format", "m3u", "--out", str(out))
    assert written["path"] == str(out)
    assert out.read_text(encoding="utf-8").startswith("#EXTM3U")

    csv_out = tmp_path / "party.csv"
    cli.run("export", run_id, "--format", "csv", "--out", str(csv_out))
    header = csv_out.read_text(encoding="utf-8").splitlines()[0]
    assert header.endswith("Position,Transition Score,Key Relation")

    json_out = tmp_path / "party.json"
    cli.run("export", run_id, "--format", "json", "--out", str(json_out))
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["playlist_name"] == "party"
    assert len(payload["entries"]) == 6


def test_fr14_export_rejects_an_unknown_format(cli: Cli) -> None:
    _imported(cli)
    run_id = _run_id(cli)
    result = cli.run("export", run_id, "--format", "wav")
    assert result.exit_code != 0


def test_fr14_delete_refuses_while_runs_exist(cli: Cli) -> None:
    _imported(cli)
    _run_id(cli)
    blocked = cli.run("delete", "party")
    assert blocked.exit_code == ERROR_EXIT
    assert "playlist_has_runs" in blocked.output

    forced = cli.run("delete", "party", "--force")
    assert forced.exit_code == 0
    assert "no playlists yet" in cli.run("ls").output


def test_fr14_db_flag_isolates_state(cli: Cli, tmp_path: Path) -> None:
    _imported(cli)
    other = Cli(tmp_path / "other.db", cli.csv)
    assert "no playlists yet" in other.run("ls").output
    assert "party" in cli.run("ls").output

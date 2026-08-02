"""Typer CLI tests (FR-15) — commands, rendering and non-zero exit codes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pointsmax.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()
TODAY = "2026-07-31"


@pytest.fixture
def cli(tmp_path: Path, world_dir: Path) -> Any:
    """Invoke the CLI against a temp database and the synthetic world."""
    db = tmp_path / "pointsmax.db"

    def invoke(*args: str) -> Any:
        return runner.invoke(app, ["--db", str(db), "--world", str(world_dir), *args])

    return invoke


@pytest.fixture
def stocked(cli: Any) -> Any:
    assert cli("init").exit_code == 0
    assert cli("wallet", "add-card", "card_a").exit_code == 0
    assert cli("wallet", "add-card", "card_b").exit_code == 0
    assert cli("wallet", "set", "bank_a", "100000").exit_code == 0
    assert cli("wallet", "set", "bank_b", "80000").exit_code == 0
    return cli


def test_help_lists_every_command_fr15() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "world", "profile", "cards", "wallet", "value", "plan", "show", "apply"):
        assert command in result.output


def test_init_validates_the_world_fr1(cli: Any) -> None:
    result = cli("init")
    assert result.exit_code == 0
    assert "world validation: OK" in result.output
    assert "5 programs" in result.output


def test_world_info_and_validate_fr1(cli: Any) -> None:
    cli("init")
    info = cli("world", "info", "--today", TODAY)
    assert info.exit_code == 0
    assert "content_hash" in info.output
    assert "stale        no" in info.output
    assert cli("world", "validate").exit_code == 0


def test_profile_set_and_show_fr5(cli: Any) -> None:
    cli("init")
    assert cli("profile", "set", "--home-city", "AAA", "--default-pax", "2").exit_code == 0
    shown = cli("profile", "show")
    assert "AAA" in shown.output and "2" in shown.output
    bad = cli("profile", "set", "--home-city", "ZZZ")
    assert bad.exit_code != 0


def test_cards_list_filters_by_issuer_fr3(cli: Any) -> None:
    cli("init")
    result = cli("cards", "list", "--issuer", "bank b")
    assert result.exit_code == 0
    assert "card_b" in result.output and "card_a" not in result.output


def test_wallet_show_prints_three_numbers_fr13(stocked: Any) -> None:
    result = stocked("wallet", "show", "--today", TODAY)
    assert result.exit_code == 0
    assert "cash floor" in result.output and "travel floor" in result.output
    assert "$2,000.00" in result.output  # bank_a baseline: 100,000 x 2000 mcpp
    assert "$1,000.00" in result.output  # bank_a cash floor via a_credit
    assert "$1,500.00" in result.output  # bank_a travel floor via a_portal


def test_value_command_fr13(stocked: Any) -> None:
    result = stocked("value", "bank_a", "100000", "--today", TODAY)
    assert result.exit_code == 0
    assert "baseline        $2,000.00   @ 2.00 cpp" in result.output
    assert "a_credit" in result.output and "a_portal" in result.output


def test_value_rejects_unknown_program_fr15(stocked: Any) -> None:
    result = stocked("value", "nope", "10")
    assert result.exit_code != 0


def test_ledger_lists_entries_fr2(stocked: Any) -> None:
    result = stocked("wallet", "ledger", "--program", "bank_a")
    assert result.exit_code == 0
    assert "set" in result.output and "100,000" in result.output


def test_goal_add_from_text_and_list_fr5(stocked: Any) -> None:
    created = stocked("goal", "add", "round-trip business Alfaville to Betatown in October",
                      "--today", TODAY)
    assert created.exit_code == 0, created.output
    assert "goal 1 created" in created.output
    listing = stocked("goal", "list")
    assert "round trip business AAA->BBB" in listing.output
    shown = stocked("goal", "show", "1")
    assert "2026-10-01 .. 2026-10-31" in shown.output


def test_goal_add_structured_and_drop_fr4(stocked: Any) -> None:
    created = stocked(
        "goal", "add", "--kind", "flight", "--from", "AAA", "--to", "BBB",
        "--cabin", "business", "--rt", "--month", "2026-10", "--pax", "2",
    )
    assert created.exit_code == 0, created.output
    assert stocked("goal", "drop", "1").exit_code == 0
    assert "dropped" in stocked("goal", "list").output


def test_goal_add_requires_month_fr15(stocked: Any) -> None:
    result = stocked("goal", "add", "--kind", "flight", "--from", "AAA", "--to", "BBB")
    assert result.exit_code != 0


def test_plan_show_and_apply_fr9_fr11(stocked: Any) -> None:
    stocked("goal", "add", "--kind", "flight", "--from", "AAA", "--to", "BBB",
            "--cabin", "business", "--month", "2026-10")
    planned = stocked("plan", "1", "--today", TODAY, "--top", "3")
    assert planned.exit_code == 0, planned.output
    assert "verdict:" in planned.output
    assert "estimates from a versioned dataset" in planned.output
    assert "irreversible_transfer" in planned.output

    shown = stocked("show", "1")
    assert shown.exit_code == 0
    assert "net value" in shown.output and "steps:" in shown.output

    refused = stocked("apply", "1", "--step", "1")
    assert refused.exit_code != 0, "an irreversible step must not apply without confirmation"

    applied = stocked("apply", "1", "--step", "1", "--yes-irreversible",
                      "--at", "2026-08-02T09:15:00Z")
    assert applied.exit_code == 0, applied.output
    assert "recorded at 2026-08-02T09:15:00Z" in applied.output
    assert "transfers are final" in applied.output

    ledger = stocked("wallet", "ledger")
    assert "transfer_out" in ledger.output and "transfer_in" in ledger.output

    repeat = stocked("apply", "1", "--step", "1", "--yes-irreversible")
    assert repeat.exit_code != 0


def test_plan_cash_goal_uses_liquid_options_only_fr12(stocked: Any) -> None:
    stocked("goal", "add", "--kind", "cash")
    result = stocked("plan", "1", "--today", TODAY)
    assert result.exit_code == 0, result.output
    assert "cash_plan" in result.output
    assert "a_portal" not in result.output


def test_main_maps_domain_errors_to_exit_codes_fr15(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world_dir: Path
) -> None:
    from pointsmax.cli.main import main

    db = tmp_path / "pointsmax.db"
    base = ["pointsmax", "--db", str(db), "--world", str(world_dir)]
    monkeypatch.setattr("sys.argv", [*base, "init"])
    assert main() == 0
    monkeypatch.setattr("sys.argv", [*base, "value", "nope", "10"])
    assert main() == 1
    monkeypatch.setattr("sys.argv", [*base, "goal", "show", "42"])
    assert main() == 1


def test_wallet_adjust_accepts_negative_delta_fr2(stocked: Any) -> None:
    """The documented ``wallet adjust <program> <delta>`` form with a signed delta."""
    result = stocked("wallet", "adjust", "bank_a", "-10000", "--reason", "correction")
    assert result.exit_code == 0, result.output
    assert "90,000" in result.output
    ledger = stocked("wallet", "ledger", "--program", "bank_a")
    assert "adjust" in ledger.output and "-10,000" in ledger.output


def test_main_maps_vendored_usage_errors_to_exit_2_fr15(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, world_dir: Path, capsys: Any
) -> None:
    """Unknown options are parsed by Typer's vendored click fork; ``main()`` must
    turn them into a clean ``error [usage]`` line and exit code 2, never a
    traceback (regression: ``wallet adjust p --bogus`` crashed)."""
    from pointsmax.cli.main import main

    db = tmp_path / "pointsmax.db"
    base = ["pointsmax", "--db", str(db), "--world", str(world_dir)]
    # NoSuchOption from the vendored parser (previously an unhandled traceback).
    monkeypatch.setattr("sys.argv", [*base, "wallet", "set", "bank_a", "--bogus"])
    assert main() == 2
    err = capsys.readouterr().err
    assert "error [usage]:" in err and "Traceback" not in err
    # BadParameter (bad int) still maps to a usage error too.
    monkeypatch.setattr("sys.argv", [*base, "wallet", "adjust", "bank_a", "not-a-number"])
    assert main() == 2
    err = capsys.readouterr().err
    assert "error [usage]:" in err

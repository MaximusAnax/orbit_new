"""FR-13: the Typer CLI — the whole walkthrough, --json escape hatches and exit codes.

Every invocation runs against a real SQLite database in ``tmp_path`` (via ``--db``),
so the CLI is exercised exactly as the owner runs it, persistence included.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from grailtrader.cli import app
from grailtrader.cli.app import set_service
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def run(tmp_path: Path) -> Callable[..., object]:
    db = tmp_path / "grailtrader.db"

    def invoke(*args: str, expect: int = 0):
        set_service(None)  # each invocation opens the database fresh, like a real shell
        result = runner.invoke(app, ["--db", str(db), *args])
        assert result.exit_code == expect, (
            f"`grailtrader {' '.join(args)}` exited {result.exit_code}\n{result.output}"
        )
        return result

    yield invoke
    set_service(None)


@pytest.fixture
def loaded(run: Callable[..., object], mini_feed: tuple[Path, Path], mini_weeks: list[str]):
    listings, events = mini_feed
    run("init")
    run("listings", "load", "--path", str(listings))
    run("events", "ingest", "--path", str(events))
    run("index", "build", "--as-of", mini_weeks[-1])
    return run


def test_fr13_help_lists_every_command_group() -> None:
    output = runner.invoke(app, ["--help"]).output
    for command in (
        "init",
        "listings",
        "index",
        "events",
        "brands",
        "portfolio",
        "advise",
        "advice",
        "backtest",
    ):
        assert command in output


def test_fr13_init_validates_and_materialises(run: Callable[..., object]) -> None:
    result = run("init", "--reset")
    assert "validated datasets" in result.output
    assert "impact priors" in result.output
    assert "database reset" in result.output


def test_fr13_listings_load_and_list(
    run: Callable[..., object], mini_feed: tuple[Path, Path]
) -> None:
    listings, _ = mini_feed
    run("init")
    first = run("listings", "load", "--path", str(listings))
    assert "ingested 1620" in first.output
    again = run("listings", "load", "--path", str(listings))
    assert "ingested 0" in again.output
    assert "duplicates 1620" in again.output

    rows = json.loads(
        run("listings", "list", "--stratum", "celine/philo", "--limit", "3", "--json").output
    )
    assert len(rows) == 3
    detail = run("listings", "show", rows[0]["id"])
    assert rows[0]["id"] in detail.output


def test_fr13_index_build_and_show(loaded: Callable[..., object]) -> None:
    built = loaded("index", "build")
    assert "built" in built.output and "weekly points" in built.output

    shown = loaded("index", "show", "helmut-lang/helmut/outerwear", "--weeks", "4")
    assert "week" in shown.output and "n_sales" in shown.output

    payload = json.loads(
        loaded("index", "show", "helmut-lang/helmut", "--weeks", "3", "--json").output
    )
    assert len(payload["points"]) == 3
    assert payload["points"][0]["level_usd"] is None  # parents carry no level (FR-4)

    strata = loaded("index", "strata", "--level", "leaf", "--json").output
    assert "helmut-lang/helmut/tailoring" in strata


def test_fr13_events_add_review_and_show(loaded: Callable[..., object]) -> None:
    listed = json.loads(loaded("events", "list", "--json").output)
    assert len(listed) == 1
    shown = loaded("events", "show", listed[0]["id"])
    assert "targets: helmut-lang/helmut (era)" in shown.output
    assert "designer_departure.resignation" in shown.output
    assert "retires at age 26.0 weeks" in shown.output

    added = loaded(
        "events",
        "add",
        "--type",
        "celebrity_cosign",
        "--brand",
        "celine",
        "--era",
        "philo",
        "--celebrity",
        "Juno Vasquez",
        "--tier",
        "a_list",
        "--source",
        "social",
        "--occurred-on",
        "2025-08-04",
    )
    assert "created" in added.output
    assert len(json.loads(loaded("events", "list", "--json").output)) == 2

    assert "no pending events" in loaded("events", "review").output


def test_fr13_unknown_brand_exits_non_zero_with_a_suggestion(
    run: Callable[..., object],
) -> None:
    run("init")
    result = run(
        "events",
        "add",
        "--type",
        "brand_scandal",
        "--brand",
        "celin",
        "--severity",
        "minor",
        "--occurred-on",
        "2025-08-04",
        expect=1,
    )
    assert "unknown brand" in result.output
    assert "did you mean" in result.output


def test_fr13_portfolio_lifecycle_and_valuation(
    loaded: Callable[..., object], mini_weeks: list[str]
) -> None:
    added = json.loads(
        loaded(
            "portfolio",
            "add",
            "--label",
            "HL astro moto",
            "--brand",
            "Helmut Lang",
            "--era",
            "helmut",
            "--category",
            "outerwear",
            "--condition",
            "excellent",
            "--price",
            "1200",
            "--date",
            mini_weeks[10],
            "--json",
        ).output
    )
    garment_id = added["id"]
    assert (added["brand_id"], added["era_id"], added["category"]) == (
        "helmut-lang",
        "helmut-lang:helmut",
        "outerwear",
    )

    listing = loaded("portfolio", "list")
    assert "HL astro moto" in listing.output

    value = loaded("portfolio", "value")
    assert "repeat_sales" in value.output
    assert "total fair value" in value.output

    shown = loaded("portfolio", "show", garment_id)
    assert "typical excellent-condition comp" in shown.output

    loaded("portfolio", "edit", garment_id, "--condition", "good")
    assert "good" in loaded("portfolio", "list").output

    removed = loaded("portfolio", "remove", garment_id)
    assert "soft delete" in removed.output
    assert "portfolio is empty" in loaded("portfolio", "list").output


def test_fr13_advise_advice_and_backtest(
    loaded: Callable[..., object], mini_weeks: list[str]
) -> None:
    loaded(
        "portfolio",
        "add",
        "--label",
        "HL astro moto",
        "--brand",
        "helmut-lang",
        "--era",
        "helmut",
        "--category",
        "outerwear",
        "--condition",
        "excellent",
        "--price",
        "1200",
        "--date",
        mini_weeks[10],
    )
    advised = loaded("advise", "--as-of", mini_weeks[47], "--text")
    assert "buy" in advised.output
    assert "Fees & liquidity:" in advised.output
    assert "not investment advice." in advised.output

    rows = json.loads(loaded("advice", "list", "--json").output)
    assert len(rows) == 1
    shown = loaded("advice", "show", rows[0]["id"])
    assert "rationale codes:" in shown.output
    assert "mod:z=" in shown.output

    backtest = loaded("backtest", "run", "--start", mini_weeks[20], "--end", mini_weeks[-1])
    assert "decisions" in backtest.output
    assert "baselines:" in backtest.output
    placebo = loaded("backtest", "placebo", "--seed", "20260731")
    assert "PLACEBO seed 20260731" in placebo.output
    assert len(json.loads(loaded("backtest", "list", "--json").output)) == 2


def test_fr13_advise_before_index_exits_non_zero(run: Callable[..., object]) -> None:
    run("init")
    result = run("advise", expect=1)
    assert "index build" in result.output


def test_fr13_brands(run: Callable[..., object]) -> None:
    run("init")
    assert "helmut-lang" in run("brands", "list").output
    shown = run("brands", "show", "helmut-lang")
    assert "Helmut Lang era" in shown.output
    assert "open" in shown.output  # the post-founder era has no end date

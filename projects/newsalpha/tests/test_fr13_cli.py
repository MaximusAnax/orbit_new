"""FR-13: the Typer CLI -- commands, rendering, `--json`, and exit codes.

Driven through Typer's `CliRunner`, so the tests exercise the same argument
parsing, the same service wiring and the same exit statuses a shell would see.
"""

from __future__ import annotations

import json

import pytest
from newsalpha.cli.main import EXIT_BY_CODE, app, state
from newsalpha_edge_kit import AS_OF, write_corpus, write_market
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_state():
    state.reset()
    yield
    state.reset()


def run(*args: str):
    return runner.invoke(app, ["--db", ":memory:", *args])


@pytest.fixture()
def corpus(tmp_path):
    return write_corpus(tmp_path)


@pytest.fixture()
def market(tmp_path):
    return write_market(tmp_path)


@pytest.fixture()
def ingested(corpus):
    result = run("init")
    assert result.exit_code == 0, result.output
    result = run("ingest", "--path", str(corpus), "--as-of", AS_OF)
    assert result.exit_code == 0, result.output
    return result


def _json(result):
    return json.loads(result.output)


def test_fr13_help_lists_every_documented_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "init",
        "ingest",
        "articles",
        "events",
        "signals",
        "brief",
        "digest",
        "assets",
        "watch",
        "prices",
        "backtest",
    ):
        assert command in result.output


def test_fr13_subcommand_help_is_useful():
    for args in (["ingest", "--help"], ["backtest", "--help"], ["signals", "list", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert result.output.strip()


def test_fr13_init_validates_and_loads_the_datasets():
    result = run("init")
    assert result.exit_code == 0
    assert "initialized" in result.output
    assert "assets" in result.output


def test_fr13_ingest_reports_counts_and_is_idempotent(corpus):
    run("init")
    first = run("ingest", "--path", str(corpus), "--as-of", AS_OF, "--json")
    assert first.exit_code == 0
    payload = _json(first)
    assert payload["articles"] == 5 and payload["signals_new"] >= 3

    second = run("ingest", "--path", str(corpus), "--as-of", AS_OF, "--json")
    again = _json(second)
    assert again["articles"] == 0 and again["signals_new"] == 0 and again["revisions_new"] == 0


def test_fr13_ingest_without_as_of_anchors_on_the_corpus(corpus):
    run("init")
    result = run("ingest", "--path", str(corpus), "--json")
    assert result.exit_code == 0
    assert _json(result)["as_of"] == "2026-03-19T10:00:00Z"


def test_fr13_ingest_with_a_missing_corpus_exits_seven(tmp_path):
    run("init")
    result = run("ingest", "--path", str(tmp_path / "nope.jsonl"))
    assert result.exit_code == EXIT_BY_CODE["feed_unavailable"]
    assert "feed_unavailable" in result.output


def test_fr13_articles_list_and_show(ingested):
    listing = run("articles", "list")
    assert listing.exit_code == 0
    assert "published" in listing.output and "reuters.example" in listing.output

    rows = _json(run("articles", "list", "--json"))
    detail = run("articles", "show", rows[0]["id"])
    assert detail.exit_code == 0
    assert rows[0]["title"] in detail.output


def test_fr13_articles_show_unknown_exits_four(ingested):
    result = run("articles", "show", "0000000000000000")
    assert result.exit_code == EXIT_BY_CODE["unknown_article"]


def test_fr13_events_show_prints_evidence_spans(ingested):
    events = _json(run("events", "list", "--json"))
    hack = next(e for e in events if e["event_type"] == "hack_exploit")
    detail = run("events", "show", hack["id"])
    assert detail.exit_code == 0
    assert "evidence" in detail.output and "links" in detail.output
    assert "subject" in detail.output
    assert run("events", "show", "0000000000000000").exit_code == EXIT_BY_CODE["unknown_event"]


def test_fr13_events_filters(ingested):
    assert len(_json(run("events", "list", "--type", "mna", "--json"))) == 1
    assert len(_json(run("events", "list", "--asset", "cx:SOL", "--json"))) == 1


def test_fr13_signals_list_show_and_revisions(ingested):
    signals = _json(run("signals", "list", "--json"))
    assert signals
    table = run("signals", "list")
    assert "direction" in table.output and "conf" in table.output

    detail = run("signals", "show", signals[0]["id"])
    assert detail.exit_code == 0
    assert "prior_key" in detail.output and "rationale" in detail.output

    chain = run("signals", "revisions", signals[0]["id"], "--json")
    assert len(_json(chain)) == 1
    assert run("signals", "show", "0" * 16).exit_code == EXIT_BY_CODE["unknown_signal"]


def test_fr13_brief_prints_the_rendered_text(ingested):
    signals = _json(run("signals", "list", "--json"))
    brief = run("brief", signals[0]["id"])
    assert brief.exit_code == 0
    assert "What happened" in brief.output and "Why it matters" in brief.output
    assert "not investment advice" in brief.output
    assert run("brief", "0" * 16).exit_code == EXIT_BY_CODE["unknown_brief"]


def test_fr13_digest_empty_state_and_ranking(ingested):
    empty = run("digest", "--date", "2026-03-19")
    assert empty.exit_code == 0
    assert "No notable events" in empty.output

    run("watch", "add", "cx:SOL")
    watched = run("digest", "--date", "2026-03-19")
    assert "cx:SOL" in watched.output

    everything = _json(run("digest", "--date", "2026-03-19", "--all-assets", "--json"))
    scores = [abs(entry["score"]) for entry in everything["entries"]]
    assert scores == sorted(scores, reverse=True)


def test_fr13_digest_defaults_to_the_latest_event_date(ingested):
    result = run("digest", "--all-assets", "--json")
    assert result.exit_code == 0
    assert _json(result)["entries"]


def test_fr13_assets_list_and_show():
    run("init")
    listing = run("assets", "list", "--kind", "crypto", "--query", "solana")
    assert listing.exit_code == 0 and "cx:SOL" in listing.output
    detail = run("assets", "show", "cx:SOL")
    assert detail.exit_code == 0 and "Solana" in detail.output
    missing = run("assets", "show", "cx:NOPE")
    assert missing.exit_code == EXIT_BY_CODE["unknown_asset"]
    assert "suggestion" in missing.output


def test_fr13_watchlist_add_list_remove():
    run("init")
    assert run("watch", "add", "eq:MSFT").exit_code == 0
    assert "already watching" in run("watch", "add", "eq:MSFT").output
    assert "eq:MSFT" in run("watch", "list").output
    assert run("watch", "remove", "eq:MSFT").exit_code == 0
    assert run("watch", "remove", "eq:MSFT").exit_code == EXIT_BY_CODE["unknown_asset"]
    assert "watchlist is empty" in run("watch", "list").output
    unknown = run("watch", "add", "eq:NOTREAL")
    assert unknown.exit_code == EXIT_BY_CODE["unknown_asset"]


def test_fr13_prices_load_and_backtest(ingested, market):
    loaded = run(
        "prices",
        "load",
        "--dir",
        str(market),
        "--start",
        "2026-03-01",
        "--end",
        "2026-04-30",
        "--assets",
        "cx:SOL,eq:MSFT,eq:ADBE,eq:OKTA,idx:US,idx:CX",
    )
    assert loaded.exit_code == 0 and "loaded" in loaded.output

    real = run("backtest", "run", "--start", "2026-03-01", "--end", "2026-03-31")
    assert real.exit_code == 0
    assert "hit_rate" in real.output and "bucket" in real.output

    payload = _json(
        run("backtest", "run", "--start", "2026-03-01", "--end", "2026-03-31", "--json")
    )
    assert payload["aggregates"]["n"] >= 1
    shown = run("backtest", "show", payload["id"])
    assert shown.exit_code == 0 and payload["id"] in shown.output
    assert run("backtest", "show", "0" * 16).exit_code == EXIT_BY_CODE["unknown_backtest"]


def test_fr13_backtest_placebo_is_labelled(ingested, market):
    run(
        "prices",
        "load",
        "--dir",
        str(market),
        "--start",
        "2026-03-01",
        "--end",
        "2026-04-30",
        "--assets",
        "cx:SOL,eq:MSFT,eq:ADBE,eq:OKTA,idx:US,idx:CX",
    )
    placebo = run(
        "backtest", "placebo", "--seed", "20260731", "--start", "2026-03-01", "--end", "2026-03-31"
    )
    assert placebo.exit_code == 0
    assert "(placebo)" in placebo.output


def test_fr13_prices_load_with_a_missing_directory_exits_seven(tmp_path):
    run("init")
    result = run("prices", "load", "--dir", str(tmp_path / "absent"), "--assets", "cx:SOL")
    assert result.exit_code == EXIT_BY_CODE["market_data_unavailable"]


def test_fr13_version_command():
    result = run("version")
    assert result.exit_code == 0 and result.output.strip()

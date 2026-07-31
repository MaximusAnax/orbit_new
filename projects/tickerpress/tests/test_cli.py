"""FR-15: the Typer CLI — exit codes, ``--json``, ``--now`` injection, init report."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from click.testing import Result
from tickerpress.cli import app
from tickerpress_testkit import rss_feed, rss_item
from typer.testing import CliRunner

WIRE_ONE = rss_feed(
    [
        rss_item(
            guid="wireone-1",
            link="https://wireone.example.com/apple-q2?utm_source=rss&amp;fbclid=xyz",
            title="Apple beats March-quarter estimates on services strength",
            description=(
                "Apple Inc. (NASDAQ: AAPL) reported quarterly revenue of $96.4 billion, ahead "
                "of analyst estimates, as services growth offset softer iPhone sales. Shares "
                "rose 3% in extended trading."
            ),
            pub_date="Sun, 01 Mar 2026 21:30:00 GMT",
        ),
        rss_item(
            guid="wireone-2",
            link="https://wireone.example.com/apple-growers",
            title="Apple growers brace for frost across the valley",
            description=(
                "Growers across the valley expect a difficult harvest after an early frost "
                "damaged the orchard blossom. The cooperative said the crop may fall by a fifth."
            ),
            pub_date="Sun, 01 Mar 2026 10:00:00 GMT",
        ),
    ]
)

Run = Callable[..., Result]


@pytest.fixture
def feed_path(tmp_path: Path) -> Path:
    path = tmp_path / "wire_one.xml"
    path.write_bytes(WIRE_ONE)
    return path


@pytest.fixture
def run(tmp_path: Path) -> Run:
    runner = CliRunner()
    db = tmp_path / "archive.db"
    outbox = tmp_path / "outbox"

    def _run(*args: str, expect: int = 0) -> Result:
        result = runner.invoke(app, ["--db", str(db), "--outbox", str(outbox), *args])
        assert result.exit_code == expect, (
            f"`tickerpress {' '.join(args)}` exited {result.exit_code} "
            f"(expected {expect}); output:\n{result.output}\n{result.exception!r}"
        )
        return result

    return _run


def _seed(run: Run, feed_path: Path) -> None:
    run(
        "company",
        "add",
        "AAPL",
        "--name",
        "Apple Inc.",
        "--context",
        "cupertino",
        "--context",
        "iphone",
        "--anti",
        "orchard",
        "--anti",
        "harvest",
    )
    run("feed", "add", f"file://{feed_path}", "--name", "Wire One")


def _json(result: Result) -> Sequence | dict:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# help / init
# ---------------------------------------------------------------------------


def test_fr15_help_lists_every_command_group(run: Run) -> None:
    output = run("--help").output
    for command in ("company", "alias", "term", "feed", "ingest", "explain", "digest"):
        assert command in output


def test_fr15_init_reports_lexicon_counts_and_hashes(run: Run) -> None:
    payload = _json(run("init", "--json"))
    assert isinstance(payload, dict)
    files = {entry["file"]: entry for entry in payload["lexicons"]}
    assert set(files) == {
        "abbreviations.txt",
        "common_words.txt",
        "cues_anti.txt",
        "cues_corporate.txt",
        "digest_template.md",
        "legal_suffixes.txt",
        "tracking_params.txt",
    }
    assert all(len(entry["sha256"]) == 64 for entry in files.values())
    assert files["common_words.txt"]["entries"] > 1000
    # idempotent: a second init on the same archive still succeeds
    run("init")


# ---------------------------------------------------------------------------
# watchlist (FR-1)
# ---------------------------------------------------------------------------


def test_fr15_company_add_generates_aliases_and_show_lists_them(run: Run) -> None:
    added = run("company", "add", "TSLA", "--name", "Tesla, Inc.")
    assert "added TSLA" in added.output
    payload = _json(run("company", "show", "TSLA", "--json"))
    assert isinstance(payload, dict)
    assert [(a["text"], a["kind"], a["strength"], a["prior"]) for a in payload["aliases"]] == [
        ("TSLA", "ticker_symbol", "strong", 0.0),
        ("$TSLA", "cashtag", "strong", 0.0),
        ("Tesla, Inc.", "legal_name", "strong", 0.0),
        ("Tesla", "short_name", "weak", 0.25),
    ]
    human = run("company", "show", "TSLA").output
    assert "short_name" in human and "prior=0.25" in human


def test_fr15_company_add_rejects_a_bad_ticker_with_exit_1(run: Run) -> None:
    result = run("company", "add", "toolongticker", "--name", "x", expect=1)
    assert "error:" in result.output


def test_fr15_company_list_set_and_remove(run: Run) -> None:
    run("company", "add", "TSLA", "--name", "Tesla, Inc.")
    assert "TSLA" in run("company", "list").output
    run("company", "set", "TSLA", "--mode", "both", "--min-relevance", "30")
    payload = _json(run("company", "show", "TSLA", "--json"))
    assert isinstance(payload, dict)
    assert (payload["mode"], payload["min_relevance"]) == ("both", 30)
    run("company", "remove", "TSLA")
    run("company", "show", "TSLA", expect=1)
    run("company", "remove", "TSLA", expect=1)


def test_fr15_alias_add_and_rm(run: Run) -> None:
    run("company", "add", "TSLA", "--name", "Tesla, Inc.")
    added = _json(run("alias", "add", "TSLA", "Tesla Motors", "--kind", "nickname", "--json"))
    assert isinstance(added, dict)
    assert (added["strength"], added["prior"]) == ("weak", 0.10)
    run("alias", "add", "TSLA", "tesla", "--kind", "nickname", expect=1)
    run("alias", "rm", "TSLA", str(added["id"]))
    run("alias", "rm", "TSLA", str(added["id"]), expect=1)


def test_fr15_term_add_appends_company_terms(run: Run) -> None:
    run("company", "add", "AAPL", "--name", "Apple Inc.")
    payload = _json(
        run("term", "add", "AAPL", "--context", "Cupertino", "--anti", "cider", "--json")
    )
    assert isinstance(payload, dict)
    assert payload["context_terms"] == ["cupertino"] and payload["anti_terms"] == ["cider"]
    # repeated flags all append — none are silently discarded
    repeated = _json(
        run(
            "term", "add", "AAPL",
            "--context", "iphone", "--context", "app store",
            "--anti", "orchard", "--json",
        )
    )
    assert isinstance(repeated, dict)
    assert repeated["context_terms"] == ["cupertino", "iphone", "app store"]
    assert repeated["anti_terms"] == ["cider", "orchard"]
    run("term", "add", "AAPL", expect=1)
    run("term", "add", "MSFT", "--context", "azure", expect=1)


# ---------------------------------------------------------------------------
# feeds (FR-2)
# ---------------------------------------------------------------------------


def test_fr15_feed_registry_commands(run: Run, feed_path: Path) -> None:
    run("feed", "add", f"file://{feed_path}", "--name", "Wire One")
    assert "Wire One" in run("feed", "list").output
    assert "disabled" in run("feed", "disable", "1").output
    assert "enabled" in run("feed", "enable", "1").output
    run("feed", "enable", "7", expect=1)
    run("feed", "rm", "1")
    run("feed", "rm", "1", expect=1)


def test_fr15_feed_rm_is_refused_once_articles_exist(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    result = run("feed", "rm", "1", expect=1)
    assert "disable it instead" in result.output


# ---------------------------------------------------------------------------
# ingest, archive, explain
# ---------------------------------------------------------------------------


def test_fr15_ingest_accepts_injected_now_and_reports_counts(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    payload = _json(run("ingest", "--now", "2026-03-02T13:00:00Z", "--json"))
    assert isinstance(payload, dict)
    assert payload["articles_new"] == 2
    assert payload["status"] == "succeeded"
    articles = _json(run("articles", "list", "--json"))
    assert isinstance(articles, list)
    assert {article["first_seen_at"] for article in articles} == {"2026-03-02T13:00:00Z"}
    # re-running is idempotent
    again = _json(run("ingest", "--now", "2026-03-02T13:05:00Z", "--json"))
    assert isinstance(again, dict)
    assert again["articles_new"] == 0


def test_fr15_ingest_rejects_a_bad_now_and_an_unknown_feed(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "yesterday", expect=1)
    run("ingest", "--feed", "Nope", expect=1)


def test_fr15_articles_and_stories_views(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    listed = _json(run("articles", "list", "--company", "AAPL", "--min-relevance", "90", "--json"))
    assert isinstance(listed, list) and len(listed) == 1
    article_id = listed[0]["id"]
    shown = run("articles", "show", str(article_id)).output
    assert "relevance 94" in shown
    assert "canonical:  https://wireone.example.com/apple-q2" in shown
    stories = _json(run("stories", "list", "--json"))
    assert isinstance(stories, list) and len(stories) == 2
    story = run("stories", "show", str(listed[0]["story_id"])).output
    assert "relevance AAPL: 94" in story
    run("articles", "show", "999", expect=1)
    run("stories", "show", "999", expect=1)


def test_fr15_explain_prints_features_for_accepted_and_rejected(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    accepted = run("explain", "1").output
    assert "[ACCEPT]" in accepted and "Apple Inc." in accepted
    rejected = run("explain", "2", "--company", "AAPL").output
    assert "[reject]" in rejected
    assert "anti_terms=2" in rejected
    payload = _json(run("explain", "2", "--json"))
    assert isinstance(payload, dict)
    assert payload["candidates"][0]["accepted"] is False
    run("explain", "999", expect=1)


# ---------------------------------------------------------------------------
# digests (FR-9, FR-11, FR-12)
# ---------------------------------------------------------------------------


def test_fr15_digest_dry_run_prints_body_and_persists_nothing(run: Run, feed_path: Path) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    result = run("digest", "run", "--channel", "file", "--dry-run", "--now", "2026-03-02T13:05:00Z")
    assert "# TickerPress digest — 2026-03-02" in result.output
    assert "Not investment advice." in result.output
    assert "[dry run] 1 item(s); nothing persisted" in result.output
    assert _json(run("digest", "list", "--json")) == []


def test_fr15_digest_run_writes_the_outbox_and_is_exactly_once(
    run: Run, feed_path: Path, tmp_path: Path
) -> None:
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    result = run("digest", "run", "--channel", "file", "--now", "2026-03-02T13:05:00Z")
    assert "delivery 1 sent on file" in result.output
    written = sorted((tmp_path / "outbox").glob("*.md"))
    assert [path.name for path in written] == ["20260302T130500Z-digest-1.md"]
    body = written[0].read_text(encoding="utf-8")
    assert "relevance 94" in body

    shown = run("digest", "show", "1").output
    assert body in shown
    assert "AAPL" in shown and "counted=1" in shown

    second = run("digest", "run", "--channel", "file", "--now", "2026-03-02T14:00:00Z")
    assert "nothing new to deliver" in second.output
    assert len(sorted((tmp_path / "outbox").glob("*.md"))) == 1
    run("digest", "show", "9", expect=1)


def test_fr15_digest_run_on_an_unconfigured_live_channel_exits_nonzero(
    run: Run, feed_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TICKERPRESS_WEBHOOK_URL", raising=False)
    _seed(run, feed_path)
    run("ingest", "--now", "2026-03-02T13:00:00Z")
    result = run("digest", "run", "--channel", "webhook", expect=1)
    assert "TICKERPRESS_WEBHOOK_URL" in result.output
    assert _json(run("digest", "list", "--json")) == []

"""FR-14: the Typer CLI — rendering, --json, exit codes, browse commands."""
from __future__ import annotations

import json

import pytest
from ethos.cli.app import app
from ethos.service import IntegrityError
from typer.testing import CliRunner

DIRECT_QUESTION = "Is lying always wrong, or are there exceptions?"


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHOS_DB_PATH", str(tmp_path / "ethos.db"))
    monkeypatch.delenv("ETHOS_LLM_API_KEY", raising=False)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    return runner


def test_fr14_init_reports_corpus_version(cli) -> None:
    result = cli.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "initialized: corpus" in result.stdout


def test_fr14_ask_render_matches_stored_text(cli) -> None:
    """What `ask` prints is the verified render that was persisted (US-7)."""
    asked = cli.invoke(app, ["ask", DIRECT_QUESTION])
    assert asked.exit_code == 0
    assert "=== Honesty and deception ===" in asked.stdout
    assert "Citations:" in asked.stdout
    history = cli.invoke(app, ["history", "--limit", "1"])
    question_id = history.stdout.strip().split()[0].lstrip("#")
    shown = cli.invoke(app, ["show", question_id])
    assert shown.exit_code == 0
    assert "Where traditions agree and differ:" in shown.stdout


def test_fr14_json_flag_emits_answerbody(cli) -> None:
    result = cli.invoke(app, ["ask", DIRECT_QUESTION, "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["routing"]["topic_id"] == "honesty_and_deception"
    assert body["citations"] and body["agreement_map"]
    assert "overall" not in body  # non-goal 2: no synthesized answer anywhere


def test_fr14_ask_forced_topic_and_filter(cli) -> None:
    result = cli.invoke(
        app, ["ask", "anything", "--topic", "divorce", "--tradition", "christianity"]
    )
    assert result.exit_code == 0
    assert "forced by request" in result.stdout
    assert "--- Christianity" in result.stdout
    assert "Excluded by your filter" in result.stdout


def test_fr14_refusal_lists_nearest_topics(cli) -> None:
    result = cli.invoke(
        app,
        [
            "ask",
            "Is CRISPR editing of an embryo's genome killing an unborn person, "
            "murder, or self-defense against inherited disease?",
        ],
    )
    assert result.exit_code == 0
    assert "cannot answer" in result.stdout
    assert "Nearest topics:" in result.stdout
    assert "ethos topics" in result.stdout


def test_fr14_exit_codes(cli, monkeypatch) -> None:
    """2 = polish unavailable, 3 = FR-8 integrity failure on the null path."""
    polish = cli.invoke(app, ["ask", DIRECT_QUESTION, "--polish"])
    assert polish.exit_code == 2
    assert "ETHOS_LLM_API_KEY" in polish.stderr

    from ethos.engine.verify import Failure
    from ethos.service import EthosService

    def boom(*_args, **_kwargs):
        raise IntegrityError([Failure("c", "locator mismatch on kjv-matthew-5-37")])

    monkeypatch.setattr(EthosService, "_compose_verified", boom)
    broken = cli.invoke(app, ["ask", DIRECT_QUESTION])
    assert broken.exit_code == 3
    assert "kjv-matthew-5-37" in broken.stderr


def test_fr14_browse_commands(cli) -> None:
    topics = cli.invoke(app, ["topics"])
    assert topics.exit_code == 0
    assert "honesty_and_deception" in topics.stdout
    assert "[safeguards]" in topics.stdout

    topic = cli.invoke(app, ["topic", "suicide_and_self_harm"])
    assert topic.exit_code == 0
    assert "Question forms:" in topic.stdout and "Further reading:" in topic.stdout

    traditions = cli.invoke(app, ["traditions"])
    assert traditions.exit_code == 0
    assert traditions.stdout.count("\n") == 10

    tradition = cli.invoke(app, ["tradition", "stoicism"])
    assert tradition.exit_code == 0
    assert "Key concepts:" in tradition.stdout

    reading = cli.invoke(app, ["reading", "honesty_and_deception"])
    assert reading.exit_code == 0
    assert reading.stdout.count("\n") >= 6

    unknown = cli.invoke(app, ["topic", "not_a_topic"])
    assert unknown.exit_code == 1


def test_fr14_corpus_stats_and_validate(cli) -> None:
    stats = cli.invoke(app, ["corpus", "stats"])
    assert stats.exit_code == 0
    assert "corpus_version" in stats.stdout
    assert "coverage matrix" in stats.stdout
    assert "complicating_share" in stats.stdout

    validate = cli.invoke(app, ["corpus", "validate"])
    assert validate.exit_code == 0
    assert "FAIL" not in validate.stdout

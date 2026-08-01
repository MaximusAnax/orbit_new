"""ethos CLI (FR-14). Exit codes: 2 polish unavailable, 3 integrity failure."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer

from ethos.corpus import default_data_dir, load_corpus
from ethos.models import CorpusMeta
from ethos.service import (
    EthosService,
    IntegrityError,
    PolishUnavailableError,
    UnknownTopicError,
    UnknownTraditionError,
)
from ethos.store.repository import StaleCorpusError
from ethos.store.sqlite_repo import SqliteRepository

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Comparative ethics reference")
corpus_app = typer.Typer(no_args_is_help=True, help="Corpus inspection and validation")
app.add_typer(corpus_app, name="corpus")


def _db_path() -> Path:
    return Path(os.environ.get("ETHOS_DB_PATH", str(Path.home() / ".ethos" / "ethos.db")))


def _service(polish_needed: bool = False) -> EthosService:
    corpus = load_corpus(default_data_dir())
    repo = SqliteRepository(_db_path())
    polisher = None
    if polish_needed and os.environ.get("ETHOS_LLM_API_KEY"):
        from ethos.adapters.polisher_llm import LLMPolisher

        polisher = LLMPolisher()
    return EthosService(corpus, repo, polisher=polisher)


def _fail(message: str, code: int = 1) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


@app.command()
def init() -> None:
    """Create the database and load + validate the corpus."""
    from ethos.validate import validate_corpus

    data_dir = default_data_dir()
    corpus = load_corpus(data_dir)
    results = validate_corpus(data_dir, corpus, _near_unanimous())
    failures = {gate: errs for gate, errs in results.items() if errs}
    if failures:
        for gate, errs in failures.items():
            for err in errs[:10]:
                typer.echo(f"  {gate}: {err}", err=True)
        _fail(f"corpus validation failed ({', '.join(failures)})")
    repo = SqliteRepository(_db_path())
    repo.set_corpus_meta(
        CorpusMeta(
            corpus_version=corpus.corpus_version, loaded_at=datetime.now(UTC).isoformat()
        )
    )
    typer.echo(f"initialized: corpus {corpus.corpus_version[:12]} at {_db_path()}")


def _near_unanimous() -> list[str]:
    path = (
        Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "gate_exceptions.json"
    )
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("near_unanimous_topics", [])
    return []


@app.command()
def ask(
    text: str,
    tradition: list[str] = typer.Option(None, "--tradition", help="Filter to tradition id(s)"),
    topic: str | None = typer.Option(None, "--topic", help="Bypass routing; force topic id"),
    polish: bool = typer.Option(False, "--polish", help="Use the LLM prose-polish adapter"),
    json_out: bool = typer.Option(False, "--json", help="Emit the raw AnswerBody JSON"),
) -> None:
    """Ask a moral question; renders every tradition's position side by side."""
    if polish and not os.environ.get("ETHOS_LLM_API_KEY"):
        typer.echo(
            "error: polish requested but ETHOS_LLM_API_KEY is not configured", err=True
        )
        raise typer.Exit(2)
    service = _service(polish_needed=polish)
    traditions = list(tradition) if tradition else None
    try:
        result = service.ask(
            text=text,
            asked_at=datetime.now(UTC).isoformat(),
            traditions=traditions,
            topic_id=topic,
            polish=polish,
        )
    except PolishUnavailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except IntegrityError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(3) from exc
    except (UnknownTopicError, UnknownTraditionError) as exc:
        _fail(f"unknown id: {exc}")
        return
    except StaleCorpusError as exc:
        _fail(str(exc))
        return
    if result.refusal is not None:
        refusal = result.refusal
        typer.echo("This corpus cannot answer that question.")
        typer.echo(refusal.note)
        if refusal.nearest_topics:
            typer.echo("Nearest topics:")
            for ts in refusal.nearest_topics:
                typer.echo(f"  {ts.topic_id}  (score {ts.score:.2f})")
        typer.echo(refusal.browse_hint)
        return
    assert result.answer is not None
    if json_out:
        typer.echo(result.answer.body.model_dump_json(indent=2))
    else:
        typer.echo(result.answer.rendered_text, nl=False)
        if result.answer.polish_fell_back:
            typer.echo("(polish fell back to the deterministic render)")


@app.command()
def topics(
    tradition: str | None = typer.Option(None, "--tradition", help="Only topics this tradition covers")
) -> None:
    """List topics (optionally filtered to one tradition)."""
    service = _service()
    try:
        for topic in service.topics_for_tradition(tradition):
            marks = " [safeguards]" if topic.safeguard_ids else ""
            typer.echo(f"{topic.id:<32} {topic.title}{marks}")
    except UnknownTraditionError as exc:
        _fail(f"unknown tradition: {exc}")


@app.command()
def topic(topic_id: str) -> None:
    """Topic detail: description, question forms, coverage, reading list."""
    service = _service()
    try:
        detail = service.topic_detail(topic_id)
    except UnknownTopicError as exc:
        _fail(f"unknown topic: {exc}")
        return
    t = detail["topic"]
    typer.echo(f"{t.title} ({t.id})\n\n{t.description}\n")
    typer.echo("Question forms:")
    for form in t.question_forms:
        typer.echo(f"  - {form}")
    typer.echo("\nCovered traditions:")
    for cell in detail["covered"]:
        typer.echo(f"  {cell['tradition_id']:<22} {cell['stance'].value}")
    typer.echo("\nFurther reading:")
    for row in detail["reading"]:
        entry = row["entry"]
        year = f" ({entry.year})" if entry.year else ""
        typer.echo(f"  - {entry.author}, {entry.title}{year} [{entry.kind.value}]")


@app.command()
def traditions() -> None:
    """List the ten traditions with summaries."""
    for tr in _service().corpus.traditions:
        typer.echo(f"{tr.order:>2}. {tr.id:<22} {tr.name} — {tr.era}")


@app.command()
def tradition(tradition_id: str) -> None:
    """Tradition detail: summary and key concepts."""
    service = _service()
    tr = service.corpus.tradition_by_id.get(tradition_id)
    if tr is None:
        _fail(f"unknown tradition: {tradition_id}")
        return
    typer.echo(f"{tr.name} ({tr.id}) — {tr.era}\n\n{tr.summary}\n\nKey concepts:")
    for concept in tr.key_concepts:
        typer.echo(f"  {concept.term}: {concept.gloss}")
    typer.echo("\nTopics covered:")
    for topic in service.topics_for_tradition(tradition_id):
        typer.echo(f"  {topic.id}")


@app.command()
def reading(
    topic_id: str,
    tradition: str | None = typer.Option(None, "--tradition"),
) -> None:
    """Aggregated, de-duplicated further reading for a topic."""
    service = _service()
    try:
        rows = service.reading_list(topic_id, tradition)
    except (UnknownTopicError, UnknownTraditionError) as exc:
        _fail(f"unknown id: {exc}")
        return
    for row in rows:
        entry = row["entry"]
        year = f" ({entry.year})" if entry.year else ""
        url = f" {entry.url}" if entry.url else ""
        typer.echo(f"- {entry.author}, {entry.title}{year} [{entry.kind.value}]{url}")


@app.command()
def history(limit: int = typer.Option(20, "--limit")) -> None:
    """Past questions, newest first."""
    service = _service()
    for question in service.repo.list_questions(limit, 0):
        outcome = question.outcome.value
        typer.echo(f"#{question.id}  {question.asked_at}  [{outcome}]  {question.text}")


@app.command()
def show(
    question_id: int,
    tradition: str | None = typer.Option(None, "--tradition"),
) -> None:
    """Re-display a stored answer exactly as it was verified and shown."""
    service = _service()
    question = service.repo.get_question(question_id)
    if question is None:
        _fail(f"unknown question: {question_id}")
        return
    typer.echo(f"Q: {question.text}  ({question.asked_at})")
    answer = service.repo.get_answer_for_question(question_id)
    if answer is None:
        typer.echo("(refused as out of scope — no stored answer)")
        return
    text, outdated = service.render_stored(answer)
    if outdated:
        typer.echo("note: rendered with an older composer version; shown as originally verified")
    if tradition:
        typer.echo(f"(full stored render; filter --tradition={tradition} applies to new asks)")
    typer.echo(text, nl=False)


@corpus_app.command("stats")
def corpus_stats() -> None:
    """Counts, coverage matrix, substance statistics, corpus_version."""
    service = _service()
    stats = service.substance_stats()
    for key, value in stats.items():
        if isinstance(value, float):
            typer.echo(f"{key:<28} {value:.3f}")
        else:
            typer.echo(f"{key:<28} {value}")
    typer.echo("\ncoverage matrix (topic x tradition, stance or -):")
    matrix = service.coverage_matrix()
    tradition_ids = [t.id for t in service.corpus.traditions]
    header = " ".join(t[:4] for t in tradition_ids)
    typer.echo(f"{'':<32} {header}")
    for topic_id, row in matrix.items():
        cells = " ".join((row[t][:4] if row[t] else "-").ljust(4) for t in tradition_ids)
        typer.echo(f"{topic_id:<32} {cells}")


@corpus_app.command("validate")
def corpus_validate() -> None:
    """Run the corpus gates (C1-C16) against data/."""
    from ethos.validate import validate_corpus

    data_dir = default_data_dir()
    corpus = load_corpus(data_dir)
    results = validate_corpus(data_dir, corpus, _near_unanimous())
    bad = False
    for gate, errors in results.items():
        status = "PASS" if not errors else "FAIL"
        typer.echo(f"{gate:<5} {status}")
        for err in errors[:20]:
            bad = True
            typer.echo(f"      {err}")
    if bad:
        raise typer.Exit(1)


def main() -> None:
    app()

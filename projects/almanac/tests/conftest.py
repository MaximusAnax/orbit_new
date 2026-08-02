"""Shared fixtures. Everything here is hermetic: offline adapters, memory store."""

from __future__ import annotations

import datetime as dt

import pytest
from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.adapters.clock import FixedClock
from almanac.adapters.ids import SequenceIdFactory
from almanac.adapters.personalizer_null import TemplatePersonalizer
from almanac.datasets import load_datasets, load_starter_quotes
from almanac.engine.scheduler import SchedulerEntry
from almanac.models import DatasetBundle, EntryStatus, SchedulerState
from almanac.service import AlmanacService
from almanac.store.memory_repo import MemoryRepository

START = dt.date(2026, 1, 1)


@pytest.fixture(scope="session")
def datasets() -> DatasetBundle:
    return load_datasets()


@pytest.fixture(scope="session")
def starter_quotes():
    return load_starter_quotes()


@pytest.fixture
def params(datasets):
    return datasets.params


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def repo() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def service(repo, datasets, clock) -> AlmanacService:
    svc = AlmanacService(
        repo=repo,
        datasets=datasets,
        clock=clock,
        ids=SequenceIdFactory(),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=7,
    )
    svc.initialize()
    return svc


def at(day_offset: int = 0, hour: int = 8) -> dt.datetime:
    """A deterministic timestamp ``day_offset`` days after the fixture start."""
    return dt.datetime.combine(START + dt.timedelta(days=day_offset), dt.time(hour), dt.UTC)


def on(day_offset: int = 0) -> dt.date:
    return START + dt.timedelta(days=day_offset)


def make_entry(
    entry_id: str,
    params,
    captured_offset: int = 0,
    exposures: int = 0,
    last_offset: int | None = None,
    interval: int | None = None,
    pinned: bool = False,
    flat_streak: int = 0,
    status: EntryStatus = EntryStatus.ACTIVE,
) -> SchedulerEntry:
    """Build a ``SchedulerEntry`` directly, without going through the store."""
    state = SchedulerState(
        entry_id=entry_id,
        exposure_count=exposures,
        last_surfaced_on=on(last_offset) if last_offset is not None else None,
        interval_days=interval if interval is not None else params.I0,
        flat_streak=flat_streak,
    )
    return SchedulerEntry(
        entry_id=entry_id,
        captured_on=on(captured_offset),
        pinned=pinned,
        status=status,
        state=state,
    )


# Exposed as fixtures rather than imported directly: test modules across the
# workspace share a flat module namespace, so `from tests.conftest import ...`
# is not portable.
@pytest.fixture
def on_day():
    return on


@pytest.fixture
def at_time():
    return at


@pytest.fixture
def entry_factory(params):
    def build(entry_id: str, **kwargs) -> SchedulerEntry:
        return make_entry(entry_id, params, **kwargs)

    return build


@pytest.fixture
def api_client(service):
    """FastAPI TestClient wired to the in-memory, offline service."""
    from almanac.api.routes import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(lambda: service)) as client:
        yield client


@pytest.fixture
def cli(tmp_path):
    """Typer CliRunner bound to a throwaway SQLite database.

    Returns ``run(*args, input=None)`` which prepends ``--db <tmp>`` so every
    invocation hits the same temporary file, as separate processes would.
    """
    from almanac.cli.main import app as cli_app
    from typer.testing import CliRunner

    runner = CliRunner()
    db = tmp_path / "almanac.db"

    def run(*args: str, input: str | None = None):
        return runner.invoke(cli_app, ["--db", str(db), *args], input=input)

    run.db = db  # type: ignore[attr-defined]
    return run

"""Composition root: wire a Repository and the four adapters into a service.

Both edges (`api/` and `cli/`) build their service here so that adapter
selection — offline default vs live, env-gated — happens in exactly one place.
Live adapters are imported *lazily inside the branch that activates them*, so
the offline path never touches their modules (CONVENTIONS.md).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from almanac.adapters.attribution import AttributionChecker
from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.adapters.clock import Clock, SystemClock
from almanac.adapters.ids import IdFactory, UlidFactory
from almanac.adapters.personalizer import PromptPersonalizer
from almanac.adapters.personalizer_null import TemplatePersonalizer
from almanac.datasets import default_datasets, load_starter_quotes
from almanac.models import DatasetBundle, StarterQuote
from almanac.service import AlmanacService
from almanac.store.memory_repo import MemoryRepository
from almanac.store.repository import Repository
from almanac.store.sqlite_repo import default_db_path, open_repository

#: Env var that switches the personalizer to the live LLM adapter (FR-10).
LLM_KEY_ENV = "ALMANAC_LLM_API_KEY"
#: Env var that adds the live Wikiquote attribution checker (FR-2).
WIKIQUOTE_ENV = "ALMANAC_WIKIQUOTE"
#: Env var that relocates the SQLite database (SCOPE.md D18).
DB_PATH_ENV = "ALMANAC_DB_PATH"


def db_path() -> Path:
    """Where the SQLite database lives: ``$ALMANAC_DB_PATH`` or the default."""
    override = os.environ.get(DB_PATH_ENV)
    return Path(override).expanduser() if override else default_db_path()


def build_personalizer() -> PromptPersonalizer:
    """The live LLM personalizer when credentials exist, else the template one."""
    if os.environ.get(LLM_KEY_ENV):
        from almanac.adapters.personalizer_llm import LLMPersonalizer

        return LLMPersonalizer()
    return TemplatePersonalizer()


def build_attribution_checker(datasets: DatasetBundle) -> AttributionChecker:
    """The curated dataset, optionally chained with the live Wikiquote lookup."""
    local = LocalAttributionChecker(datasets.misattributions)
    if os.environ.get(WIKIQUOTE_ENV) == "1":
        from almanac.adapters.attribution import ChainedAttributionChecker
        from almanac.adapters.attribution_wikiquote import WikiquoteAttributionChecker

        return ChainedAttributionChecker([local, WikiquoteAttributionChecker()])
    return local


def build_service(
    repo: Repository,
    *,
    datasets: DatasetBundle | None = None,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    personalizer: PromptPersonalizer | None = None,
    attribution: AttributionChecker | None = None,
    seed: int = 0,
) -> AlmanacService:
    """Assemble a service around an already-open repository."""
    bundle = datasets or default_datasets()
    return AlmanacService(
        repo=repo,
        datasets=bundle,
        clock=clock or SystemClock(),
        ids=ids or UlidFactory(),
        personalizer=personalizer or build_personalizer(),
        attribution=attribution or build_attribution_checker(bundle),
        seed=seed,
    )


def open_service(path: str | Path | None = None, **kwargs: object) -> AlmanacService:
    """Open the SQLite-backed service the API and CLI use by default."""
    repo = open_repository(path if path is not None else db_path())
    return build_service(repo, **kwargs)  # type: ignore[arg-type]


def memory_service(
    *,
    seed: int = 0,
    today: dt.date | None = None,
    ids: IdFactory | None = None,
    initialize: bool = True,
) -> AlmanacService:
    """An in-memory service for tests, evals and ``--dry-run`` style callers."""
    from almanac.adapters.clock import FixedClock
    from almanac.adapters.ids import SequenceIdFactory

    datasets = default_datasets()
    service = build_service(
        MemoryRepository(),
        datasets=datasets,
        clock=FixedClock(today or dt.date(2026, 1, 1)),
        ids=ids or SequenceIdFactory(),
        personalizer=TemplatePersonalizer(),
        attribution=LocalAttributionChecker(datasets.misattributions),
        seed=seed,
    )
    if initialize:
        service.initialize()
    return service


def starter_pack() -> list[StarterQuote]:
    """The committed public-domain starter entries (FR-5 ``--starter``)."""
    return load_starter_quotes()

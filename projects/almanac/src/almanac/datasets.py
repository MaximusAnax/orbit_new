"""Loading the committed datasets from ``data/``.

This module sits *outside* ``engine/`` because it touches the filesystem: the
engine receives already-parsed, already-validated data as plain arguments
(SCOPE.md D11).  Validation floors (16 themes, ≥ 6 templates per theme, the
``I0 == W`` and ``lo >= W`` scheduler constraints) are enforced by the Pydantic
models at load time, so a malformed data edit fails loudly and immediately.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from almanac.models import (
    DatasetBundle,
    MisattributionRecord,
    PromptTemplate,
    SchedulerParams,
    StarterQuote,
    Theme,
)

#: Number of themes in the fixed taxonomy (SCOPE.md D8).
THEME_COUNT = 16

THEMES_FILE = "themes.json"
PROMPTS_FILE = "prompts.json"
SCHEDULER_FILE = "scheduler.json"
MISATTRIBUTIONS_FILE = "misattributions.json"
STARTER_FILE = "starter_quotes.json"


def data_dir() -> Path:
    """Resolve the committed data directory.

    ``ALMANAC_DATA_DIR`` overrides; otherwise the ``data/`` folder that sits
    next to ``src/`` in the project checkout.
    """
    override = os.environ.get("ALMANAC_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data"


def _read_json(directory: Path, name: str) -> object:
    path = directory / name
    if not path.exists():
        raise FileNotFoundError(f"missing committed dataset: {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_datasets(directory: Path | None = None) -> DatasetBundle:
    """Load and validate themes, prompt templates, misattributions and params."""
    base = directory or data_dir()
    themes = [Theme.model_validate(row) for row in _read_json(base, THEMES_FILE)]
    if len(themes) != THEME_COUNT:
        raise ValueError(f"the taxonomy is fixed at {THEME_COUNT} themes, found {len(themes)}")
    templates = [PromptTemplate.model_validate(row) for row in _read_json(base, PROMPTS_FILE)]
    records = [
        MisattributionRecord.model_validate(row) for row in _read_json(base, MISATTRIBUTIONS_FILE)
    ]
    params = SchedulerParams.model_validate(_read_json(base, SCHEDULER_FILE))
    return DatasetBundle(themes=themes, templates=templates, misattributions=records, params=params)


def load_starter_quotes(directory: Path | None = None) -> list[StarterQuote]:
    """Load the committed starter pack (FR-5 ``import --starter``)."""
    base = directory or data_dir()
    return [StarterQuote.model_validate(row) for row in _read_json(base, STARTER_FILE)]


@lru_cache(maxsize=1)
def default_datasets() -> DatasetBundle:
    """Cached load of the committed data for the default data directory."""
    return load_datasets()

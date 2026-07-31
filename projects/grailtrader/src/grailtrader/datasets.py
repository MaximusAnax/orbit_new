"""Loading and caching the five committed datasets (DATA_MODEL "Committed datasets").

This is the only module that reads ``data/`` from disk. ``brands.json`` is the
one dataset ``init`` materialises into SQLite; the other four are validated at
init and then read from file at process start and cached in memory, so editing a
prior or a config value takes effect immediately without re-running ``init``.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from .engine.context import EngineContext
from .engine.validate import DatasetValidationError, validate_datasets
from .models import (
    AdviceTemplateCatalog,
    AdvisorConfig,
    Brand,
    ConditionTable,
    ImpactPrior,
)

__all__ = [
    "DATA_DIR_ENV",
    "DEFAULT_DATA_DIR",
    "load_brands",
    "load_conditions",
    "load_config",
    "load_context",
    "load_priors",
    "load_templates",
]

#: Environment variable overriding the committed dataset directory.
DATA_DIR_ENV = "GRAILTRADER_DATA_DIR"
#: The committed dataset directory shipped with the package.
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _data_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return DEFAULT_DATA_DIR


def _read(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise DatasetValidationError(path.name, "committed dataset is missing") from None
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(path.name, f"invalid JSON: {exc}") from None


def load_brands(data_dir: str | os.PathLike[str] | None = None) -> tuple[Brand, ...]:
    payload = _read(_data_dir(data_dir) / "brands.json")
    return tuple(Brand.model_validate(row) for row in payload["brands"])


def load_priors(data_dir: str | os.PathLike[str] | None = None) -> tuple[ImpactPrior, ...]:
    payload = _read(_data_dir(data_dir) / "impact_priors.json")
    return tuple(ImpactPrior.model_validate(row) for row in payload["priors"])


def load_conditions(data_dir: str | os.PathLike[str] | None = None) -> ConditionTable:
    return ConditionTable.model_validate(_read(_data_dir(data_dir) / "conditions.json"))


def load_templates(
    data_dir: str | os.PathLike[str] | None = None,
) -> AdviceTemplateCatalog:
    return AdviceTemplateCatalog.model_validate(
        _read(_data_dir(data_dir) / "advice_templates.json")
    )


def load_config(data_dir: str | os.PathLike[str] | None = None) -> AdvisorConfig:
    return AdvisorConfig.model_validate(_read(_data_dir(data_dir) / "advisor_config.json"))


def build_context(data_dir: str | os.PathLike[str] | None = None) -> EngineContext:
    """Load and validate every committed dataset, returning the engine's context (FR-1)."""
    brands = load_brands(data_dir)
    priors = load_priors(data_dir)
    conditions = load_conditions(data_dir)
    templates = load_templates(data_dir)
    config = load_config(data_dir)
    validate_datasets(
        brands=brands,
        priors=priors,
        conditions=conditions,
        templates=templates,
        config=config,
    )
    return EngineContext.build(
        brands=brands,
        priors=priors,
        conditions=conditions,
        templates=templates,
        config=config,
    )


@lru_cache(maxsize=8)
def _cached_context(resolved: str) -> EngineContext:
    return build_context(resolved)


def load_context(data_dir: str | os.PathLike[str] | None = None) -> EngineContext:
    """Cached :func:`build_context` — the datasets are read once per process."""
    return _cached_context(str(_data_dir(data_dir).resolve()))

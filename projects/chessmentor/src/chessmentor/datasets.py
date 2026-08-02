"""Loading and validating the committed datasets (DATA_MODEL.md).

Three read-only JSON files ship with the project: the difficulty ladder, the
opening book and the advice catalog.  All three are validated at
``chessmentor init``; only ``levels.json`` is materialised as a SQLite table
because game rows take a foreign key to it.

The loader is deliberately outside ``engine/`` — it touches the filesystem.
Everything it returns is a plain Pydantic model the engine can consume.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from . import __version__
from .adapters.book_committed import CommittedBook
from .models import AdviceEntry, Level, MistakeCategory, OpeningLine, Phase

__all__ = [
    "DATA_DIR_ENV",
    "DatasetError",
    "Datasets",
    "data_dir",
    "load_advice",
    "load_datasets",
    "load_levels",
    "load_openings",
    "sha256_of",
    "validate_ladder",
]

DATA_DIR_ENV = "CHESSMENTOR_DATA_DIR"

#: FR-5 / M1b: adjacent ``elo_internal`` gaps must sit inside this window.
LADDER_GAP_MIN = 100.0
LADDER_GAP_MAX = 170.0


class DatasetError(ValueError):
    """A committed dataset is missing, malformed or violates an invariant."""


def data_dir() -> Path:
    """Where the committed datasets live."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data"


def _read_json(path: Path) -> object:
    if not path.is_file():
        raise DatasetError(f"missing committed dataset: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path} is not valid JSON: {exc}") from exc


def sha256_of(path: Path) -> str:
    """SHA-256 of a dataset file — the integrity anchor M1b checks."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_levels(directory: Path | None = None) -> list[Level]:
    """Load and validate ``levels.json`` (FR-4 configs + FR-5 calibrated fields)."""
    path = (directory or data_dir()) / "levels.json"
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise DatasetError(f"{path} must contain a list of levels")
    try:
        levels = [Level.model_validate(item) for item in raw]
    except Exception as exc:  # pydantic ValidationError
        raise DatasetError(f"{path}: {exc}") from exc
    validate_ladder(levels)
    return levels


def validate_ladder(levels: list[Level]) -> None:
    """Assert every ladder invariant DATA_MODEL.md states (checked at ``init``)."""
    if not levels:
        raise DatasetError("the ladder must have at least one level")
    ids = [level.id for level in levels]
    if ids != list(range(1, len(ids) + 1)):
        raise DatasetError("level ids must be 1..N contiguous and ordered")
    for prev, nxt in pairwise(levels):
        gap = nxt.elo_internal - prev.elo_internal
        if gap <= 0:
            raise DatasetError(
                f"elo_internal must strictly increase (L{prev.id} -> L{nxt.id}: {gap:+.1f})"
            )
        if not LADDER_GAP_MIN <= gap <= LADDER_GAP_MAX:
            raise DatasetError(
                f"adjacent gap L{prev.id}->L{nxt.id} is {gap:.1f} Elo, outside "
                f"[{LADDER_GAP_MIN:.0f}, {LADDER_GAP_MAX:.0f}] (FR-5)"
            )
        if nxt.acpl_mean >= prev.acpl_mean:
            raise DatasetError(
                f"acpl_mean must strictly decrease (L{prev.id} -> L{nxt.id}) so the "
                "FR-7b interpolation stays single-valued"
            )
    for level in levels:
        if level.engine_version != __version__:
            raise DatasetError(
                f"L{level.id} was calibrated for engine {level.engine_version}, "
                f"running {__version__} — re-run FR-5 calibration"
            )


def load_openings(directory: Path | None = None) -> list[OpeningLine]:
    """Load and validate ``openings.json`` (legality is checked by ``CommittedBook``)."""
    path = (directory or data_dir()) / "openings.json"
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise DatasetError(f"{path} must contain a list of opening lines")
    try:
        return [OpeningLine.model_validate(item) for item in raw]
    except Exception as exc:
        raise DatasetError(f"{path}: {exc}") from exc


def load_advice(directory: Path | None = None) -> list[AdviceEntry]:
    """Load and validate ``advice.json``.

    Invariant: every :class:`MistakeCategory` has at least one entry with
    ``phase = null`` so suggestion selection is total.
    """
    path = (directory or data_dir()) / "advice.json"
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise DatasetError(f"{path} must contain a list of advice entries")
    try:
        entries = [AdviceEntry.model_validate(item) for item in raw]
    except Exception as exc:
        raise DatasetError(f"{path}: {exc}") from exc

    ids = [entry.id for entry in entries]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise DatasetError(f"{path}: duplicate advice ids {sorted(duplicates)}")
    generic = {entry.category for entry in entries if entry.phase is None}
    missing = sorted(c for c in MistakeCategory if c not in generic)
    if missing:
        raise DatasetError(f"{path}: every category needs a phase-null entry; missing {missing}")
    keyed: set[tuple[MistakeCategory, object]] = set()
    for entry in entries:
        key = (entry.category, entry.phase)
        if key in keyed:
            raise DatasetError(f"{path}: two entries share the key {key}")
        keyed.add(key)
    return entries


@dataclass(frozen=True)
class Datasets:
    """The three committed datasets, loaded and cross-validated."""

    levels: list[Level]
    book: CommittedBook
    advice: list[AdviceEntry]
    levels_sha256: str
    openings_sha256: str
    advice_sha256: str

    def level_by_id(self, level_id: int) -> Level:
        for level in self.levels:
            if level.id == level_id:
                return level
        raise KeyError(f"no such level: {level_id}")

    def advice_for(self, category: MistakeCategory, phase: Phase | None = None) -> AdviceEntry:
        """Exact ``(category, phase)`` beats ``(category, null)`` (DATA_MODEL.md)."""
        fallback: AdviceEntry | None = None
        for entry in self.advice:
            if entry.category is not category:
                continue
            if phase is not None and entry.phase == phase:
                return entry
            if entry.phase is None:
                fallback = entry
        if fallback is None:  # pragma: no cover - load_advice guarantees totality
            raise KeyError(f"no advice for {category}")
        return fallback


def load_datasets(directory: Path | None = None) -> Datasets:
    """Load and validate all three committed datasets."""
    base = directory or data_dir()
    levels = load_levels(base)
    lines = load_openings(base)
    book = CommittedBook(lines)
    advice = load_advice(base)
    return Datasets(
        levels=levels,
        book=book,
        advice=advice,
        levels_sha256=sha256_of(base / "levels.json"),
        openings_sha256=sha256_of(base / "openings.json"),
        advice_sha256=sha256_of(base / "advice.json"),
    )


@lru_cache(maxsize=4)
def _cached_datasets(key: str) -> Datasets:
    return load_datasets(Path(key))


def default_datasets() -> Datasets:
    """Process-wide cached load of the committed datasets."""
    return _cached_datasets(str(data_dir()))

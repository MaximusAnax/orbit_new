"""``WorldProvider``: load the committed rewards world (FR-1).

The offline provider reads ``data/world/`` and runs the full FR-1 validation.
The live provider (Non-goal 2 — deferred feature, real code path) lives in
``world_provider_live.py`` so this module never touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..engine.world import HASHED_WORLD_FILES, assert_world_valid, compute_content_hash
from ..models import (
    AwardOffer,
    CardProduct,
    CashoutOption,
    GazetteerEntry,
    Program,
    ReferenceFare,
    TransferEdge,
    Valuation,
    World,
    WorldVersion,
)

#: ``projects/pointsmax/data/world``
DEFAULT_WORLD_DIR = Path(__file__).resolve().parents[3] / "data" / "world"

#: file name -> (World field, row model)
WORLD_FILE_MODELS: dict[str, tuple[str, type]] = {
    "programs.json": ("programs", Program),
    "cards.json": ("cards", CardProduct),
    "transfers.json": ("edges", TransferEdge),
    "cashouts.json": ("cashouts", CashoutOption),
    "valuations.json": ("valuations", Valuation),
    "awards.json": ("offers", AwardOffer),
    "reference_fares.json": ("reference_fares", ReferenceFare),
    "gazetteer.json": ("gazetteer", GazetteerEntry),
}


@runtime_checkable
class WorldProvider(Protocol):
    """Loads a fully validated :class:`World`."""

    def load(self) -> World:
        """Return the rewards world, raising ``WorldValidationError`` when invalid."""
        ...


def build_world(raw: dict[str, Any], *, validate: bool = True) -> World:
    """Build a :class:`World` from parsed world files keyed by file name.

    ``raw`` must contain every entry of :data:`WORLD_FILE_MODELS` plus
    ``version.json``.  When ``validate`` is set, the FR-1 invariants — including
    the recomputed content hash — must all hold.
    """
    missing = sorted(set(WORLD_FILE_MODELS) | {"version.json"} - set(raw))
    missing = [name for name in [*WORLD_FILE_MODELS, "version.json"] if name not in raw]
    if missing:
        raise ValueError(f"world data is missing {', '.join(missing)}")
    fields: dict[str, Any] = {"version": WorldVersion.model_validate(raw["version.json"])}
    for file_name, (field_name, model) in WORLD_FILE_MODELS.items():
        rows = raw[file_name]
        if not isinstance(rows, list):
            raise ValueError(f"{file_name} must contain a JSON array")
        fields[field_name] = [model.model_validate(row) for row in rows]
    world = World(**fields)
    if validate:
        hashed = {name: raw[name] for name in HASHED_WORLD_FILES if name in raw}
        assert_world_valid(world, computed_hash=compute_content_hash(hashed))
    return world


class CommittedWorldProvider:
    """Offline default: read ``data/world/`` and validate it (FR-1)."""

    def __init__(self, root: Path | str | None = None, *, validate: bool = True) -> None:
        self.root = Path(root) if root is not None else DEFAULT_WORLD_DIR
        self.validate = validate

    def raw_files(self) -> dict[str, Any]:
        """Parsed contents of every world file, keyed by file name."""
        raw: dict[str, Any] = {}
        for file_name in [*WORLD_FILE_MODELS, "version.json"]:
            path = self.root / file_name
            if not path.exists():
                raise FileNotFoundError(f"world file not found: {path}")
            raw[file_name] = json.loads(path.read_text(encoding="utf-8"))
        return raw

    def content_hash(self) -> str:
        """Recompute the content hash over every file except ``version.json``."""
        raw = self.raw_files()
        return compute_content_hash({name: raw[name] for name in HASHED_WORLD_FILES})

    def load(self) -> World:
        return build_world(self.raw_files(), validate=self.validate)

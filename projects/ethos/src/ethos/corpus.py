"""Corpus loading (byte-preserving) and corpus_version computation (FR-1).

The committed JSON files under ``data/`` are the source of truth. Loading uses
stdlib ``json`` only: string values pass through untouched (no NFC/NFKC, no
strip, no dash or quote folding) into the Pydantic models, which are likewise
non-normalizing. ``corpus_version`` is the SHA-256 described in
DATA_MODEL.md § corpus_version.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ethos.models import (
    Passage,
    Position,
    RouterConfig,
    Safeguard,
    Source,
    Topic,
    Tradition,
)


@dataclass
class Corpus:
    traditions: list[Tradition]
    topics: list[Topic]
    sources: list[Source]
    passages: list[Passage]
    positions: list[Position]
    safeguards: list[Safeguard]
    router_config: RouterConfig
    stopwords: frozenset[str]
    corpus_version: str
    tradition_by_id: dict[str, Tradition] = field(init=False)
    topic_by_id: dict[str, Topic] = field(init=False)
    source_by_id: dict[str, Source] = field(init=False)
    passage_by_id: dict[str, Passage] = field(init=False)
    safeguard_by_id: dict[str, Safeguard] = field(init=False)
    position_by_cell: dict[tuple[str, str], Position] = field(init=False)

    def __post_init__(self) -> None:
        self.tradition_by_id = {t.id: t for t in self.traditions}
        self.topic_by_id = {t.id: t for t in self.topics}
        self.source_by_id = {s.id: s for s in self.sources}
        self.passage_by_id = {p.id: p for p in self.passages}
        self.safeguard_by_id = {s.id: s for s in self.safeguards}
        self.position_by_cell = {(p.topic_id, p.tradition_id): p for p in self.positions}

    def positions_for_topic(self, topic_id: str) -> list[Position]:
        return [p for p in self.positions if p.topic_id == topic_id]


def canonical_bytes(path: Path) -> bytes:
    """Canonical byte serialization of one committed file (DATA_MODEL)."""
    if path.suffix == ".json":
        with path.open(encoding="utf-8") as fh:
            obj = json.load(fh)
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    return path.read_bytes()


def compute_corpus_version(data_dir: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*") if p.is_file()),
    )
    for rel in files:
        digest.update(rel.encode("utf-8") + b"\n")
        digest.update(canonical_bytes(data_dir / rel) + b"\n")
    return digest.hexdigest()


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_corpus(data_dir: Path) -> Corpus:
    corpus_dir = data_dir / "corpus"
    traditions = [Tradition(**r) for r in _load_json(corpus_dir / "traditions.json")]
    topics = [Topic(**r) for r in _load_json(corpus_dir / "topics.json")]
    sources = [Source(**r) for r in _load_json(corpus_dir / "sources.json")]
    passages: list[Passage] = []
    for path in sorted((corpus_dir / "passages").glob("*.json")):
        passages.extend(Passage(**r) for r in _load_json(path))
    positions: list[Position] = []
    for path in sorted((corpus_dir / "positions").glob("*.json")):
        positions.extend(Position(**r) for r in _load_json(path))
    safeguards = [Safeguard(**r) for r in _load_json(data_dir / "safeguards.json")]
    router_config = RouterConfig(**_load_json(data_dir / "router.json"))
    stopwords = frozenset(
        line.strip()
        for line in (data_dir / "stopwords.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return Corpus(
        traditions=sorted(traditions, key=lambda t: t.order),
        topics=sorted(topics, key=lambda t: t.id),
        sources=sources,
        passages=passages,
        positions=positions,
        safeguards=safeguards,
        router_config=router_config,
        stopwords=stopwords,
        corpus_version=compute_corpus_version(data_dir),
    )


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"

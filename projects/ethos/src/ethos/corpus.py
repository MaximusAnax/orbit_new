"""Reading `data/` from disk and computing `corpus_version` (FR-1).

This is the only module that touches the corpus filesystem. It reads the
committed JSON with stdlib `json` and hands the untouched objects to the pure
parser in `ethos.engine.corpus` — so the byte-preserving contract (FR-1 layer
2) is testable without any I/O, and `engine/` stays free of filesystem imports.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ethos.engine.corpus import Corpus, RawCorpus, parse_corpus

__all__ = [
    "Corpus",
    "RawCorpus",
    "canonical_bytes",
    "compute_corpus_version",
    "default_data_dir",
    "load_corpus",
    "read_raw",
]


def canonical_bytes(path: Path) -> bytes:
    """Canonical byte serialization of one committed file (DATA_MODEL)."""
    if path.suffix == ".json":
        with path.open(encoding="utf-8") as fh:
            obj = json.load(fh)
        return json.dumps(
            obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    return path.read_bytes()


def compute_corpus_version(data_dir: Path) -> str:
    """SHA-256 over every answer-determining committed file under data/."""
    digest = hashlib.sha256()
    files = sorted(p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*") if p.is_file())
    for rel in files:
        digest.update(rel.encode("utf-8") + b"\n")
        digest.update(canonical_bytes(data_dir / rel) + b"\n")
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def read_raw(data_dir: Path) -> RawCorpus:
    """Read every committed file into plain json objects, unmodified."""
    corpus_dir = data_dir / "corpus"
    return RawCorpus(
        traditions=_read_json(corpus_dir / "traditions.json"),
        topics=_read_json(corpus_dir / "topics.json"),
        sources=_read_json(corpus_dir / "sources.json"),
        passage_files={
            path.name: _read_json(path) for path in sorted((corpus_dir / "passages").glob("*.json"))
        },
        position_files={
            path.name: _read_json(path)
            for path in sorted((corpus_dir / "positions").glob("*.json"))
        },
        safeguards=_read_json(data_dir / "safeguards.json"),
        router=_read_json(data_dir / "router.json"),
        stopword_lines=(data_dir / "stopwords.txt").read_text(encoding="utf-8").splitlines(),
        corpus_version=compute_corpus_version(data_dir),
    )


def load_corpus(data_dir: Path) -> Corpus:
    """Read and parse the committed corpus (byte-preserving end to end)."""
    return parse_corpus(read_raw(data_dir))


def default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"

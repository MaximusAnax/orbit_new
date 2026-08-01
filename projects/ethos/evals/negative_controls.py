"""R2 — negative controls: every exact gate must be observed rejecting something.

Each artefact under `fixtures/negative_controls/` is a tiny JSON patch against
the committed corpus (or a fixture), applied here to raw json before parsing, so
the artefacts stay minimal and the gate under test runs on a real `Corpus`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ethos.corpus import RawCorpus, compute_corpus_version, read_raw
from ethos.engine.corpus import parse_corpus

CONTROLS = Path(__file__).resolve().parent / "fixtures" / "negative_controls"


def load_control(name: str) -> dict[str, Any]:
    return json.loads((CONTROLS / f"{name}.json").read_text(encoding="utf-8"))


def _records(raw_tree: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    if collection in ("traditions", "topics", "sources"):
        return raw_tree[collection]
    key = "passage_files" if collection == "passages" else "position_files"
    return [record for records in raw_tree[key].values() for record in records]


def _apply(raw_tree: dict[str, Any], op: dict[str, Any]) -> None:
    kind = op["op"]
    collection = op.get("collection", "")
    records = _records(raw_tree, collection) if collection else []
    if kind == "set_field":
        matched = [r for r in records if r["id"] == op["id"]]
        if not matched:
            raise KeyError(f"{collection}:{op['id']} not found")
        for record in matched:
            record[op["field"]] = op["value"]
    elif kind == "set_where":
        for record in records:
            if all(record.get(k) == v for k, v in op["where"].items()):
                record[op["field"]] = op["value"]
    elif kind == "delete_where":
        keep = op.get("keep", 0)
        seen = 0
        for key, group in _groups(raw_tree, collection):
            remaining = []
            for record in group:
                if all(record.get(k) == v for k, v in op["where"].items()):
                    seen += 1
                    if seen <= keep:
                        remaining.append(record)
                    continue
                remaining.append(record)
            _replace_group(raw_tree, collection, key, remaining)
    elif kind == "append":
        key, group = next(iter(_groups(raw_tree, collection)))
        _replace_group(raw_tree, collection, key, [*group, copy.deepcopy(op["record"])])
    else:  # pragma: no cover - authoring error
        raise ValueError(f"unknown control op {kind!r}")


def _groups(raw_tree: dict[str, Any], collection: str):
    if collection in ("traditions", "topics", "sources"):
        return [(collection, raw_tree[collection])]
    key = "passage_files" if collection == "passages" else "position_files"
    return list(raw_tree[key].items())


def _replace_group(raw_tree: dict[str, Any], collection: str, key: str, records: list) -> None:
    if collection in ("traditions", "topics", "sources"):
        raw_tree[collection] = records
        return
    bucket = "passage_files" if collection == "passages" else "position_files"
    raw_tree[bucket][key] = records


def raw_tree(data_dir: Path) -> dict[str, Any]:
    raw = read_raw(data_dir)
    return {
        "traditions": copy.deepcopy(raw.traditions),
        "topics": copy.deepcopy(raw.topics),
        "sources": copy.deepcopy(raw.sources),
        "passage_files": copy.deepcopy(raw.passage_files),
        "position_files": copy.deepcopy(raw.position_files),
        "safeguards": copy.deepcopy(raw.safeguards),
        "router": copy.deepcopy(raw.router),
        "stopword_lines": list(raw.stopword_lines),
        "corpus_version": raw.corpus_version,
    }


def broken_corpus(data_dir: Path, control: dict[str, Any]):
    """Apply a control's patch and parse it, so the gate runs on a real Corpus."""
    tree = raw_tree(data_dir)
    for op in control["ops"]:
        _apply(tree, op)
    return parse_corpus(RawCorpus(**tree))


def broken_data_dir(data_dir: Path, control: dict[str, Any], destination: Path) -> Path:
    """Materialise a patched `data/` tree on disk for the independent M3 checker."""
    tree = raw_tree(data_dir)
    for op in control["ops"]:
        _apply(tree, op)
    corpus_dir = destination / "corpus"
    (corpus_dir / "passages").mkdir(parents=True, exist_ok=True)
    (corpus_dir / "positions").mkdir(parents=True, exist_ok=True)
    for name in ("traditions", "topics", "sources"):
        (corpus_dir / f"{name}.json").write_text(
            json.dumps(tree[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    for bucket, folder in (("passage_files", "passages"), ("position_files", "positions")):
        for filename, records in tree[bucket].items():
            (corpus_dir / folder / filename).write_text(
                json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    (destination / "safeguards.json").write_text(
        json.dumps(tree["safeguards"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "router.json").write_text(
        json.dumps(tree["router"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "stopwords.txt").write_text(
        "\n".join(tree["stopword_lines"]) + "\n", encoding="utf-8"
    )
    compute_corpus_version(destination)
    return destination


__all__ = ["CONTROLS", "broken_corpus", "broken_data_dir", "load_control", "raw_tree"]

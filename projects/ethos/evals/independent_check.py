"""M3 — citation integrity, measured independently of the code it grades (EVALS R1).

This module imports **only** `json`, `pathlib`, `re` and `sys`. It never imports
`ethos.*`: it re-parses the printed plain-text answer per the grammar in
DATA_MODEL § Plain-text render and resolves every printed citation against the
raw JSON files under `data/corpus/` with raw string equality — no NFC, no strip,
no case folding, no Pydantic, no loader. A shared-mode failure (a loader that
NFC-folded a quote, a composer and verifier that agreed on a corrupted locator)
would leave FR-8 happy and move only this number.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PARAPHRASE_LABEL = "[paraphrase — no public-domain translation quoted]"
QUOTE_RE = re.compile(r"^ {6}“(.*)”$")
CITATION_RE = re.compile(r"^ {6}— (.*?) — (.*)$")
TABLE_RE = re.compile(r"^ {2}\[(C[0-9]+)\] (\S+) — (.*?) — (\S+)$")
MARKER_RE = re.compile(r"\[(C[0-9]+)\]")
PERSPECTIVE_RE = re.compile(r"^--- (.*) — ([a-z_]+) ---$")


def load_raw_corpus(data_dir: Path) -> dict:
    """Every committed corpus record as plain dicts, keyed by id."""
    corpus_dir = data_dir / "corpus"
    passages: dict[str, dict] = {}
    for path in sorted((corpus_dir / "passages").glob("*.json")):
        for record in json.loads(path.read_text(encoding="utf-8")):
            passages[record["id"]] = record
    positions: dict[str, dict] = {}
    for path in sorted((corpus_dir / "positions").glob("*.json")):
        for record in json.loads(path.read_text(encoding="utf-8")):
            positions[record["id"]] = record
    sources = {
        record["id"]: record
        for record in json.loads((corpus_dir / "sources.json").read_text(encoding="utf-8"))
    }
    traditions = {
        record["id"]: record
        for record in json.loads(
            (corpus_dir / "traditions.json").read_text(encoding="utf-8")
        )
    }
    return {
        "passages": passages,
        "positions": positions,
        "sources": sources,
        "traditions": traditions,
    }


def source_line(source: dict) -> str:
    """The exact string the render must print (DATA_MODEL § Source, derived).

    Rebuilt here from the raw record rather than imported, which is the whole
    point of this module. English originals (Mill, Bentham) carry no translator,
    so their line is `title, author (year)`.
    """
    if source["license"] == "reference_only":
        return f"{source['title']} — {source['edition_note']}"
    if source.get("translator") is None:
        return f"{source['title']}, {source['author']} ({source['translation_year']})"
    return f"{source['title']}, trans. {source['translator']} ({source['translation_year']})"


def parse_render(text: str) -> dict:
    """Re-parse a printed answer into citations, the citation table, and headers."""
    lines = text.split("\n")
    citations: list[dict] = []
    table: dict[str, dict] = {}
    headers: list[tuple[str, str]] = []
    markers_in_body: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        header = PERSPECTIVE_RE.match(line)
        if header:
            headers.append((header.group(1), header.group(2)))
        if line.startswith("  • ") or line.startswith("  Note: "):
            markers_in_body.extend(MARKER_RE.findall(line))
        entry = TABLE_RE.match(line)
        if entry:
            table[entry.group(1)] = {
                "passage_id": entry.group(2),
                "locator": entry.group(3),
                "source_id": entry.group(4),
            }
        quote = QUOTE_RE.match(line)
        if quote is not None:
            citations.append(
                {
                    "kind": "quote",
                    "text": quote.group(1),
                    "marker": _preceding_marker(lines, index),
                    **_citation_line(lines, index + 1),
                }
            )
        elif line == f"      {PARAPHRASE_LABEL}":
            citations.append(
                {
                    "kind": "paraphrase",
                    "text": lines[index + 1][6:] if index + 1 < len(lines) else "",
                    "marker": _preceding_marker(lines, index),
                    **_citation_line(lines, index + 2),
                }
            )
            index += 1
        index += 1
    return {
        "citations": citations,
        "table": table,
        "headers": headers,
        "markers_in_body": markers_in_body,
    }


def _preceding_marker(lines: list[str], index: int) -> str | None:
    """A quote block is bound to its marker by the bullet immediately above it."""
    for probe in range(index - 1, max(index - 6, -1), -1):
        found = MARKER_RE.findall(lines[probe])
        if found:
            return found[-1]
    return None


def _citation_line(lines: list[str], index: int) -> dict:
    if index >= len(lines):
        return {"locator": None, "source_line": None}
    match = CITATION_RE.match(lines[index])
    if match is None:
        return {"locator": None, "source_line": None}
    return {"locator": match.group(1), "source_line": match.group(2)}


def check_citation(citation: dict, raw: dict) -> list[str]:
    """The four checks of EVALS M3, with raw string equality throughout."""
    failures: list[str] = []
    marker = citation["marker"]
    passages = raw["passages"]
    candidates = [
        pid
        for pid, passage in passages.items()
        if (passage.get("paraphrase") if citation["kind"] == "paraphrase" else passage.get("text"))
        == citation["text"]
    ]
    if len(candidates) != 1:
        return [f"{marker}: printed text matches {len(candidates)} committed passages"]
    passage = passages[candidates[0]]
    if citation["kind"] == "paraphrase" and passage.get("text") is not None:
        failures.append(f"{marker}: paraphrase block for a passage that has quotable text")
    if citation["locator"] != passage["locator"]:
        failures.append(
            f"{marker}: printed locator {citation['locator']!r} != {passage['locator']!r}"
        )
    source = raw["sources"].get(passage["source_id"])
    if source is None:
        failures.append(f"{marker}: passage names unknown source {passage['source_id']!r}")
    elif citation["source_line"] != source_line(source):
        failures.append(
            f"{marker}: printed source line {citation['source_line']!r}"
            f" != {source_line(source)!r}"
        )
    entry = raw["table"].get(marker) if "table" in raw else None
    if entry is not None:
        if entry["passage_id"] != passage["id"]:
            failures.append(f"{marker}: citation table names {entry['passage_id']!r}")
        if entry["source_id"] != passage["source_id"]:
            failures.append(f"{marker}: citation table names source {entry['source_id']!r}")
    return failures


def expected_citation_count(parsed: dict, raw: dict) -> int:
    """How many citations the rendered perspectives should have produced.

    Recomputed from the raw corpus: for each rendered tradition, the position on
    the rendered topic contributes one citation per distinct passage it cites.
    """
    names = {t["name"]: t["id"] for t in raw["traditions"].values()}
    total = 0
    for header, _stance in parsed["headers"]:
        tradition_id = names.get(header)
        if tradition_id is None:
            continue
        for position in raw["positions"].values():
            if position["tradition_id"] != tradition_id:
                continue
            if position["topic_id"] != raw.get("topic_id"):
                continue
            passage_ids = {ref["passage_id"] for ref in position["passages"]}
            passage_ids |= {
                point["passage_id"]
                for point in position.get("reasoning", [])
                if point.get("passage_id")
            }
            total += len(passage_ids)
    return total


def score_render(text: str, raw: dict, topic_id: str) -> tuple[int, int]:
    """Returns (passing citations, denominator) for one printed answer."""
    parsed = parse_render(text)
    scoped = dict(raw)
    scoped["table"] = parsed["table"]
    scoped["topic_id"] = topic_id
    passing = 0
    for citation in parsed["citations"]:
        if not check_citation(citation, scoped):
            passing += 1
    expected = expected_citation_count(parsed, scoped)
    return passing, max(len(parsed["citations"]), expected)


def main(argv: list[str]) -> int:
    """`python evals/independent_check.py <data_dir> <render.txt> <topic_id>`."""
    if len(argv) != 4:
        print(__doc__)
        return 2
    raw = load_raw_corpus(Path(argv[1]))
    passing, total = score_render(Path(argv[2]).read_text(encoding="utf-8"), raw, argv[3])
    print(f"{passing}/{total}")
    return 0 if total and passing == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

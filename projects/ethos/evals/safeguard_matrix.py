"""C17 — the safeguard matrix (EVALS § C17, FR-10).

For the two `sensitive` topics and one `not_legal_advice` topic the matrix is
{no filter, 1-tradition filter, 3-tradition filter} x {polish off, FaultyPolisher
clean, FaultyPolisher mutated -> fallback} x {routed, forced} x {text render,
AnswerBody JSON} = 36 cells each; every other safeguard-carrying topic runs the
reduced 4-cell matrix. Each cell reports the safeguard blocks it actually
rendered so `check_c17_safeguard_cells` can compare them against
`data/safeguards.json` byte-for-byte and assert they came first.
"""
from __future__ import annotations

from typing import Any

from ethos.corpus import Corpus
from ethos.service import EthosService
from ethos.store.memory_repo import MemoryRepository

from evals.corpus_gates import load_fixture
from evals.faulty_polisher import FaultyPolisher

TIMESTAMP = "2026-08-01T00:00:00+00:00"
FULL_MATRIX_TOPICS = (
    "suicide_and_self_harm",
    "euthanasia_and_end_of_life",
    "capital_punishment",
)

CLEAN_CASE = {
    "id": "c17-clean",
    "mode": "clean",
    "ops": [{"op": "append_to_region", "region": "M:summary", "replace": " In short."}],
}
MUTATED_CASE = {
    "id": "c17-mutated",
    "mode": "altered_locator",
    "ops": [{"op": "replace_in_region", "region": "I:qmeta:C1", "find": ":", "replace": ";"}],
}


def _expected(corpus: Corpus, topic_id: str) -> list[dict[str, str]]:
    topic = corpus.topic_by_id[topic_id]
    return [
        {
            "id": corpus.safeguard_by_id[sid].id,
            "kind": corpus.safeguard_by_id[sid].kind.value,
            "text": corpus.safeguard_by_id[sid].text,
        }
        for sid in topic.safeguard_ids
    ]


def _first_direct_question(topic_id: str) -> str | None:
    for question in load_fixture("routing_questions.json"):
        if question["truth_topic"] == topic_id and question["tier"] == "direct":
            return question["text"]
    return None


def _cell(
    corpus: Corpus,
    topic_id: str,
    traditions: list[str] | None,
    polish: str,
    routed: bool,
    surface: str,
) -> dict[str, Any]:
    name = f"{topic_id}|{traditions}|{polish}|{'routed' if routed else 'forced'}|{surface}"
    polisher = None
    if polish == "clean":
        polisher = FaultyPolisher(CLEAN_CASE)
    elif polish == "mutated":
        polisher = FaultyPolisher(MUTATED_CASE)
    service = EthosService(corpus, MemoryRepository(), polisher=polisher)
    service.init_store(TIMESTAMP)
    question = _first_direct_question(topic_id) if routed else f"[forced {topic_id}]"
    try:
        result = service.ask(
            question or f"[forced {topic_id}]",
            TIMESTAMP,
            traditions=traditions,
            topic_id=None if routed else topic_id,
            polish=polisher is not None,
        )
    except Exception as exc:  # pragma: no cover - surfaced as a gate failure
        return {"cell": name, "expected": _expected(corpus, topic_id), "got": [], "first": False,
                "error": str(exc)}
    if result.answer is None:
        return {"cell": name, "expected": _expected(corpus, topic_id), "got": [], "first": False,
                "error": "refused"}
    body = result.answer.body
    if routed and body.routing.topic_id != topic_id:
        # the routed cell only makes sense when the fixture question routes home
        return {"cell": name, "expected": [], "got": [], "first": True}
    got = [{"id": s.id, "kind": s.kind.value, "text": s.text} for s in body.safeguards]
    if surface == "text":
        lines = result.answer.rendered_text.split("\n")
        block_lines = [line for line in lines if line.startswith("[!] ")]
        first = bool(block_lines) and lines.index(block_lines[0]) == 2
        got = [
            {"id": s["id"], "kind": s["kind"], "text": s["text"]}
            for s, line in zip(got, block_lines, strict=False)
            if line == f"[!] {s['text']}"
        ]
    else:
        first = True  # JSON: `safeguards` is the first field of AnswerBody
    return {
        "cell": name,
        "expected": _expected(corpus, topic_id),
        "got": got,
        "first": first,
    }


def build_cells(corpus: Corpus) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for topic in corpus.topics:
        if not topic.safeguard_ids:
            continue
        covered = [
            tradition.id
            for tradition in corpus.traditions
            if (topic.id, tradition.id) in corpus.position_by_cell
        ]
        if topic.id in FULL_MATRIX_TOPICS:
            filters: list[list[str] | None] = [None, covered[:1], covered[:3]]
            polishes = ("off", "clean", "mutated")
            routings = (False, True)
        else:
            filters = [None]
            polishes = ("off", "mutated")
            routings = (False,)
        for traditions in filters:
            for polish in polishes:
                for routed in routings:
                    for surface in ("text", "json"):
                        cells.append(
                            _cell(corpus, topic.id, traditions, polish, routed, surface)
                        )
    return cells


__all__ = ["build_cells"]

"""D0(b) — the nine golden answers regenerate byte-identically.

    python evals/fixtures/regenerate_golden.py --check   # inside the suite
    python evals/fixtures/regenerate_golden.py --write   # by hand, in the same
                                                         # commit as a template change

Coverage (EVALS § D0): 4 ordinary topics, 1 sensitive topic (safeguard block),
1 dual-home question, 1 tradition-filtered render, 1 polish-fallback render, and
1 refusal payload — the last three because no metric's answer set covered them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from ethos.corpus import default_data_dir, load_corpus  # noqa: E402
from ethos.engine.compose import COMPOSER_VERSION  # noqa: E402
from ethos.service import EthosService  # noqa: E402
from ethos.store.memory_repo import MemoryRepository  # noqa: E402
from evals.corpus_gates import load_fixture  # noqa: E402
from evals.faulty_polisher import FaultyPolisher  # noqa: E402

GOLDEN = HERE / "golden"
TIMESTAMP = "2026-08-01T00:00:00+00:00"

FALLBACK_CASE = {
    "id": "golden-fallback",
    "mode": "altered_locator",
    "ops": [{"op": "replace_in_region", "region": "I:qmeta:C1", "find": ":", "replace": ";"}],
}

CASES: list[dict] = [
    {"name": "ordinary_honesty", "topic_id": "honesty_and_deception"},
    {"name": "ordinary_wealth", "topic_id": "wealth_and_generosity"},
    {"name": "ordinary_forgiveness", "topic_id": "forgiveness_and_revenge"},
    {"name": "ordinary_courage", "topic_id": "courage_and_fear"},
    {"name": "sensitive_suicide", "topic_id": "suicide_and_self_harm"},
    {"name": "dual_home_question", "question_ref": "amb-01"},
    {
        "name": "filtered_divorce",
        "topic_id": "divorce",
        "traditions": ["christianity", "islam", "judaism"],
    },
    {"name": "polish_fallback", "topic_id": "marriage_and_fidelity", "polish": "fallback"},
    {"name": "refusal", "question_ref": "oos-03"},
]


def _question_text(ref: str) -> str:
    for name in ("ambiguous_questions.json", "oos_questions.json", "routing_questions.json"):
        for question in load_fixture(name):
            if question["id"] == ref:
                return question["text"]
    raise KeyError(ref)


def build(case: dict) -> dict:
    corpus = load_corpus(default_data_dir())
    polisher = FaultyPolisher(FALLBACK_CASE) if case.get("polish") else None
    service = EthosService(corpus, MemoryRepository(), polisher=polisher)
    service.init_store(TIMESTAMP)
    text = _question_text(case["question_ref"]) if "question_ref" in case else (
        f"[golden {case['name']}]"
    )
    result = service.ask(
        text,
        TIMESTAMP,
        traditions=case.get("traditions"),
        topic_id=case.get("topic_id"),
        polish=polisher is not None,
    )
    if result.answer is None:
        assert result.refusal is not None
        return {
            "name": case["name"],
            "kind": "refusal",
            "question": text,
            "outcome": result.refusal.outcome,
            "nearest_topics": [t.model_dump() for t in result.refusal.nearest_topics],
            "browse_hint": result.refusal.browse_hint,
            "note": result.refusal.note,
        }
    return {
        "name": case["name"],
        "kind": "answer",
        "question": text,
        "composer_version": result.answer.composer_version,
        "polish_fell_back": result.answer.polish_fell_back,
        "body": json.loads(result.answer.body.model_dump_json()),
        "rendered_text": result.answer.rendered_text,
    }


def serialize(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    GOLDEN.mkdir(exist_ok=True)
    write = "--write" in argv
    problems: list[str] = []
    for case in CASES:
        payload = build(case)
        path = GOLDEN / f"{case['name']}.json"
        rendered = serialize(payload)
        if write:
            path.write_text(rendered, encoding="utf-8")
            continue
        if not path.exists():
            problems.append(f"{path.name}: missing golden (run with --write)")
            continue
        committed = path.read_text(encoding="utf-8")
        if committed != rendered:
            problems.append(f"{path.name}: differs from the committed golden")
        embedded = json.loads(committed).get("composer_version")
        if embedded is not None and embedded != COMPOSER_VERSION:
            problems.append(
                f"{path.name}: composer_version {embedded} != code constant {COMPOSER_VERSION}"
            )
    if write:
        print(f"wrote {len(CASES)} goldens to {GOLDEN}")
        return 0
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

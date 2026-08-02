"""C1-C20 gate predicates over the committed corpus and fixtures (EVALS).

The predicates themselves live in `ethos.engine.corpus` so that `ethos corpus
validate`, `evals/run.py` and `evals/test_gates.py` cannot drift apart; this
module is the fixture-aware wrapper that feeds them the committed fixture data
and returns `{gate: [failures]}` with empty lists meaning pass.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ethos.corpus import Corpus, default_data_dir, load_corpus
from ethos.engine.corpus import (
    check_c9_fixture_questions,
    check_c17_safeguard_cells,
    check_c18_stemmer,
    check_c19_baselines,
    check_c20_oos_composition,
    validate_corpus,
)
from ethos.engine.normalize import normalize, porter_stem

FIXTURES = Path(__file__).resolve().parent / "fixtures"

QUESTION_FILES = (
    "routing_questions.json",
    "oblique_holdout.json",
    "ambiguous_questions.json",
    "oos_questions.json",
    "polish_cases.json",
)
FROZEN_FILES = ("oblique_holdout.json", "oos_questions.json")


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_sha(name: str) -> str:
    return hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def near_unanimous_topics() -> list[str]:
    return list(load_fixture("gate_exceptions.json").get("near_unanimous_topics", []))


def stemmer_pairs() -> list[tuple[str, str]]:
    def words(name: str) -> list[str]:
        lines = (FIXTURES / "stemmer" / name).read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.startswith("#")]

    voc, out = words("voc.txt"), words("output.txt")
    if len(voc) != len(out):
        raise ValueError(f"stemmer fixture length mismatch: {len(voc)} vs {len(out)}")
    return list(zip(voc, out, strict=True))


def check_c9(corpus: Corpus) -> list[str]:
    """C9: fixture lexical integrity across every committed question set."""
    errors: list[str] = []
    routing = load_fixture("routing_questions.json")
    holdout = load_fixture("oblique_holdout.json")
    errors += check_c9_fixture_questions(corpus, routing)
    errors += check_c9_fixture_questions(
        corpus, holdout, oblique_tiers=("oblique_holdout",)
    )
    for question in load_fixture("ambiguous_questions.json"):
        for topic_id in question["truth_topics"]:
            if topic_id not in corpus.topic_by_id:
                errors.append(f"C9: {question['id']} names unknown topic {topic_id!r}")
    observed = sum(1 for q in holdout if q.get("origin") == "observed")
    if observed < 0.30 * len(holdout):
        errors.append(
            f"C9: only {observed}/{len(holdout)} holdout entries carry origin='observed'"
            " (>= 30% required)"
        )
    for question in holdout:
        if not question.get("rationale"):
            errors.append(f"C9: holdout {question['id']} records no rationale")
    tiers = {"direct": 96, "colloquial": 72, "oblique": 72}
    for tier, expected in tiers.items():
        got = sum(1 for q in routing if q["tier"] == tier)
        if got != expected:
            errors.append(f"C9: {tier} tier has {got} questions, expected {expected}")
    if len(holdout) != 48:
        errors.append(f"C9: holdout has {len(holdout)} questions, expected 48")
    return errors


def check_c18(corpus: Corpus) -> list[str]:
    """C18: Porter conformance plus the committed NFKC/casefold cases."""
    errors = check_c18_stemmer(stemmer_pairs(), porter_stem)
    for case in load_fixture("normalization_cases.json"):
        got = normalize(case["text"], corpus.stopwords)
        if got != case["tokens"]:
            errors.append(
                f"C18: normalize({case['text']!r}) == {got}, expected {case['tokens']}"
            )
    return errors


def check_c20(corpus: Corpus, oos_s1: list[float], direct_s1: list[float]) -> list[str]:
    """C20: composition of the out-of-scope set (>= 25 lexically hard items)."""
    errors = check_c20_oos_composition(oos_s1, direct_s1)
    oos = load_fixture("oos_questions.json")
    near = sum(1 for q in oos if q["kind"] == "moral_out_of_taxonomy")
    if len(oos) != 40:
        errors.append(f"C20: out-of-scope set has {len(oos)} questions, expected 40")
    if near < 25:
        errors.append(f"C20: only {near} near-miss moral questions, >= 25 required")
    return errors


def check_c19(measured: dict[str, Any]) -> list[str]:
    """C19: committed hashes and measured baselines are still current."""
    path = FIXTURES.parent / "baselines.json"
    if not path.exists():
        return ["C19: evals/baselines.json is missing (run.py --write-baselines)"]
    recorded = json.loads(path.read_text(encoding="utf-8"))
    return check_c19_baselines(recorded, measured)


def freeze_record(corpus: Corpus) -> dict[str, Any]:
    """Everything C19 pins: corpus version, config hashes, fixture hashes, tau/kappa."""
    data = default_data_dir()
    record: dict[str, Any] = {
        "corpus_version": corpus.corpus_version,
        "sha256:data/router.json": file_sha(data / "router.json"),
        "sha256:data/stopwords.txt": file_sha(data / "stopwords.txt"),
        "tau": corpus.router_config.tau,
        "kappa": corpus.router_config.kappa,
        "tuning_note": corpus.router_config.tuning_note,
    }
    for name in QUESTION_FILES:
        record[f"sha256:{name}"] = fixture_sha(name)
        if name in FROZEN_FILES:
            record[f"frozen:{name}"] = True
    return record


def run_corpus_gates(corpus: Corpus | None = None) -> dict[str, list[str]]:
    """C1-C18 minus the ones needing routing scores (C19/C20 are added by run.py)."""
    corpus = corpus or load_corpus(default_data_dir())
    results = validate_corpus(corpus, near_unanimous_topics())
    results["C9"] = check_c9(corpus)
    results["C18"] = check_c18(corpus)
    return dict(sorted(results.items(), key=lambda kv: (len(kv[0]), kv[0])))


__all__ = [
    "FIXTURES",
    "check_c9",
    "check_c17_safeguard_cells",
    "check_c18",
    "check_c19",
    "check_c20",
    "freeze_record",
    "load_fixture",
    "near_unanimous_topics",
    "run_corpus_gates",
    "stemmer_pairs",
]

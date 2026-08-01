"""FR-1: corpus schema, byte-preserving loading, validation, corpus_version."""
from __future__ import annotations

import copy
import json

import pytest
from ethos.corpus import compute_corpus_version, default_data_dir, read_raw
from ethos.engine import corpus as gates
from ethos.models import Passage
from pydantic import ValidationError


def test_fr1_all_corpus_gates_pass(corpus):
    results = gates.validate_corpus(corpus, [])
    assert {g: e for g, e in results.items() if e} == {}


def test_fr1_loader_byte_preserving(corpus):
    """Every loaded string byte-equals the raw JSON string, per record."""
    raw = corpus.raw
    by_id = {p["id"]: p for p in raw.passages}
    for passage in corpus.passages:
        source = by_id[passage.id]
        assert passage.locator == source["locator"]
        assert passage.text == source["text"]
        assert passage.paraphrase == source["paraphrase"]
        assert passage.context_note == source["context_note"]
    assert gates.check_c1_byte_preserving(corpus) == []


def test_fr1_c1_catches_a_folding_loader(corpus):
    """A loader that folded an en-dash in a locator is caught by C1 alone."""
    tampered = copy.deepcopy(corpus)
    tampered.raw = corpus.raw
    victim = next(p for p in tampered.passages if "–" in p.locator)  # noqa: RUF001
    folded = victim.model_copy(update={"locator": victim.locator.replace("–", "-")})  # noqa: RUF001
    tampered.passages = [folded if p.id == victim.id else p for p in tampered.passages]
    tampered.__post_init__()
    failures = gates.check_c1_byte_preserving(tampered)
    assert any("loader altered" in f for f in failures)


def test_fr1_c1_rejects_envelope_sentinels(corpus):
    raw = copy.deepcopy(corpus.raw)
    raw.topics[0]["title"] = "Honesty [[I:meta]]"
    parsed = gates.parse_corpus(raw)
    assert any("envelope sentinel" in f for f in gates.check_c1_byte_preserving(parsed))


def test_fr1_c1_rejects_render_delimiters_in_passages(corpus):
    raw = copy.deepcopy(corpus.raw)
    name = sorted(raw.passage_files)[0]
    quoted = next(p for p in raw.passage_files[name] if p["text"])
    quoted["text"] = "“" + quoted["text"] + "”"
    parsed = gates.parse_corpus(raw)
    assert any("curly double quote" in f for f in gates.check_c1_byte_preserving(parsed))


@pytest.mark.parametrize(
    ("gate", "mutate"),
    [
        ("C3", lambda raw: raw.position_files[sorted(raw.position_files)[0]][0].update(
            {"further_reading": []})),
        ("C7", lambda raw: raw.traditions.pop()),
        ("C11", lambda raw: [
            p["passages"].append({"passage_id": "kjv-matthew-5-37", "role": "core", "note": None})
            for p in raw.positions
        ]),
        ("C16", lambda raw: [
            p.update({"further_reading": [{"author": "A", "title": "T", "year": 2000,
                                           "kind": "book", "url": None, "note": None}]})
            for p in raw.positions
        ]),
    ],
)
def test_fr1_gates_are_falsifiable(corpus, gate, mutate):
    """Every structural gate rejects a deliberately broken corpus (EVALS R2)."""
    raw = copy.deepcopy(corpus.raw)
    mutate(raw)
    broken = gates.parse_corpus(raw)
    results = gates.validate_corpus(broken, [])
    assert results[gate], f"{gate} accepted a broken corpus"


def test_fr1_corpus_version_covers_data_dir(tmp_path, corpus):
    """Editing router.json, safeguards.json or stopwords.txt moves the digest."""
    data_dir = default_data_dir()
    baseline = compute_corpus_version(data_dir)
    assert baseline == corpus.corpus_version
    for name, edit in (
        ("router.json", lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "tau": 7.0}))),
        ("stopwords.txt", lambda p: p.write_text(p.read_text() + "zzz\n")),
    ):
        mirror = tmp_path / name.replace(".", "_")
        mirror.mkdir()
        for item in data_dir.rglob("*"):
            if item.is_file():
                target = mirror / item.relative_to(data_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item.read_bytes())
        edit(mirror / name)
        assert compute_corpus_version(mirror) != baseline


def test_fr1_model_rejects_passage_with_two_bodies():
    with pytest.raises(ValidationError):
        Passage(
            id="x", source_id="s", locator="Analects XIII.18",
            text="a", paraphrase="b", context_note="c", transcription_checked=True,
        )


def test_fr1_read_raw_is_pure_json(corpus):
    raw = read_raw(default_data_dir())
    assert isinstance(raw.passages[0], dict)
    assert raw.corpus_version == corpus.corpus_version

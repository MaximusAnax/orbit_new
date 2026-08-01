"""FR-10: sensitive-topic safeguards as implemented behavior — present, byte
identical, first, and suppressible by nothing."""
from __future__ import annotations

import itertools

import pytest
from ethos.engine import envelope as env_mod
from ethos.engine.corpus import check_c17_safeguard_cells
from ethos.engine.verify import check_body, parse_render
from ethos.service import EthosService
from ethos.store.memory_repo import MemoryRepository

TS = "2026-08-01T12:00:00Z"
SENSITIVE = ("suicide_and_self_harm", "euthanasia_and_end_of_life")
LEGAL = ("capital_punishment", "obedience_and_civil_authority", "theft_and_property",
         "usury_and_lending")


class ClearingPolisher:
    """A polisher that tries to delete the safeguard block."""

    def polish(self, envelope: str) -> str:
        out = []
        for line in envelope.split("\n"):
            if not line.startswith("[[I:safeguard:"):
                out.append(line)
        return "\n".join(out)


def test_fr10_sensitive_topics_declare_crisis_resources(corpus):
    for topic_id in SENSITIVE:
        topic = corpus.topic_by_id[topic_id]
        assert topic.sensitive is True
        assert "crisis_resources" in topic.safeguard_ids
    assert "not_medical_advice" in corpus.topic_by_id[
        "euthanasia_and_end_of_life"
    ].safeguard_ids
    for topic_id in LEGAL:
        assert "not_legal_advice" in corpus.topic_by_id[topic_id].safeguard_ids


def test_fr10_safeguard_first_in_render_order(compose, corpus):
    for topic_id in SENSITIVE + LEGAL:
        body, rendered = compose(topic_id)
        texts = [corpus.safeguard_by_id[s].text for s in corpus.topic_by_id[topic_id].safeguard_ids]
        head = f"=== {corpus.topic_by_id[topic_id].title} ===\n\n"
        head += "".join(f"[!] {t}\n" for t in texts)
        assert rendered.startswith(head)
        assert [s.text for s in body.safeguards] == texts


@pytest.mark.parametrize("topic_id", (*SENSITIVE, "capital_punishment"))
def test_fr10_safeguard_matrix(corpus, compose, topic_id):
    """C17 in miniature: filters x forced/routed x text/JSON, every cell."""
    traditions = [t.id for t in corpus.traditions if (topic_id, t.id) in corpus.position_by_cell]
    filters = [None, traditions[:1], traditions[:3]]
    expected = [
        {"id": s, "kind": corpus.safeguard_by_id[s].kind.value,
         "text": corpus.safeguard_by_id[s].text}
        for s in corpus.topic_by_id[topic_id].safeguard_ids
    ]
    cells = []
    for tradition_filter, forced in itertools.product(filters, (True, False)):
        body, rendered = compose(topic_id, tradition_filter, forced)
        got_json = [
            {"id": s.id, "kind": s.kind.value, "text": s.text} for s in body.safeguards
        ]
        printed = parse_render(rendered)["safeguards"]
        cells.append({
            "cell": f"{topic_id}/{tradition_filter}/{forced}",
            "expected": expected,
            "got": got_json,
            "first": printed == [s["text"] for s in expected],
        })
    assert check_c17_safeguard_cells(cells) == []


def test_fr10_polish_cannot_suppress_the_block(corpus):
    service = EthosService(corpus, MemoryRepository(), polisher=ClearingPolisher())
    service.init_store(TS)
    result = service.ask("x", TS, topic_id="suicide_and_self_harm", polish=True)
    assert result.answer is not None
    assert result.answer.polish_fell_back is True
    assert result.answer.body.safeguards
    assert result.answer.rendered_text.split("\n")[2].startswith("[!] ")


def test_fr10_a_body_without_its_safeguards_fails_verification(corpus, compose):
    body, _rendered = compose("suicide_and_self_harm")
    envelope = env_mod.serialize(body, None)
    stripped = body.model_copy(deep=True)
    stripped.safeguards = []
    assert any(f.check == "d" for f in check_body(stripped, corpus, None, envelope))


def test_fr10_crisis_text_names_real_resources(corpus):
    text = corpus.safeguard_by_id["crisis_resources"].text
    for needle in ("988", "befrienders.org", "findahelpline.com"):
        assert needle in text
    assert "not counseling" in text

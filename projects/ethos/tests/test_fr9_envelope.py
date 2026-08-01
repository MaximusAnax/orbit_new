"""FR-9: the envelope, the polish adapter contract, and the fallback path."""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from ethos.adapters.polisher import NullPolisher, ProsePolisher
from ethos.engine import envelope as env_mod
from ethos.service import EthosService
from ethos.store.memory_repo import MemoryRepository

TS = "2026-08-01T12:00:00Z"


class CleanPolisher:
    """Rewrites only mutable regions, preserving every immutable one."""

    def polish(self, envelope: str) -> str:
        out = []
        for line in envelope.split("\n"):
            if line.startswith("[[M:summary:"):
                tag = line[2 : line.index("]]")]
                out.append(f"[[{tag}]]Rephrased, but the same claim.[[/{tag}]]")
            else:
                out.append(line)
        return "\n".join(out)


class QuoteTamperingPolisher:
    def polish(self, envelope: str) -> str:
        start = envelope.index("[[I:quote:C1]]") + len("[[I:quote:C1]]")
        end = envelope.index("[[/I:quote:C1]]")
        return envelope[:start] + "Fabricated scripture." + envelope[end:]


class BrokenEnvelopePolisher:
    def polish(self, envelope: str) -> str:
        return envelope.replace("[[/I:citations]]", "")


def test_fr9_envelope_roundtrip(corpus, compose):
    body, _rendered = compose("honesty_and_deception")
    envelope = env_mod.serialize(body, None)
    position_ids = {p.tradition_id: p.position_id for p in body.perspectives}
    parsed = env_mod.parse(envelope, position_ids)
    assert parsed.model_dump_json() == body.model_dump_json()
    assert env_mod.serialize(parsed, None) == envelope


def test_fr9_null_polisher_is_the_identity():
    assert NullPolisher().polish("[[ETHOS:1]]\n") == "[[ETHOS:1]]\n"
    assert isinstance(NullPolisher(), ProsePolisher)


def test_fr9_regions_are_marked_mutable_or_immutable(corpus, compose):
    body, _rendered = compose("divorce")
    regions = env_mod.regions_of(env_mod.serialize(body, None))
    kinds = {tag.split(":")[0] for tag, _payload, _mutable in regions}
    assert kinds == {"I", "M"}
    mutable = {tag.split(":")[1] for tag, _p, m in regions if m}
    assert mutable <= {"summary", "reason", "intra"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.replace("[[/I:routing]]", ""),  # missing close
        lambda e: e.replace("[[I:agreement]]", "[[I:agreement]]not json"),
        lambda e: "prose before the envelope\n" + e,
        lambda e: e + "\n[[I:extra]]x[[/I:extra]]\n",
    ],
)
def test_fr9_strict_parse_back_rejects_structural_damage(corpus, compose, mutate):
    body, _rendered = compose("divorce")
    envelope = env_mod.serialize(body, None)
    with pytest.raises(env_mod.EnvelopeError):
        env_mod.parse(mutate(envelope), {p.tradition_id: p.position_id for p in body.perspectives})


def test_fr9_clean_polish_is_accepted_and_used(corpus):
    service = EthosService(corpus, MemoryRepository(), polisher=CleanPolisher())
    service.init_store(TS)
    result = service.ask("is lying wrong?", TS, topic_id="honesty_and_deception", polish=True)
    assert result.answer is not None
    assert result.answer.polish_used is True
    assert result.answer.polish_fell_back is False
    assert result.answer.verified is True
    assert "Rephrased, but the same claim." in result.answer.rendered_text


def test_fr9_fallback_sets_flag_and_serves_the_deterministic_render(corpus):
    plain = EthosService(corpus, MemoryRepository())
    plain.init_store(TS)
    baseline = plain.ask("x", TS, topic_id="honesty_and_deception").answer

    for polisher in (QuoteTamperingPolisher(), BrokenEnvelopePolisher()):
        service = EthosService(corpus, MemoryRepository(), polisher=polisher)
        service.init_store(TS)
        result = service.ask("x", TS, topic_id="honesty_and_deception", polish=True)
        assert result.answer is not None
        assert result.answer.polish_fell_back is True
        assert result.answer.polish_used is False
        assert result.answer.verified is True
        assert result.answer.rendered_text == baseline.rendered_text


def test_fr9_llm_adapter_not_imported_on_the_offline_path(corpus):
    assert "ethos.adapters.polisher_llm" not in sys.modules
    service = EthosService(corpus, MemoryRepository())
    service.init_store(TS)
    service.ask("is lying wrong?", TS, topic_id="honesty_and_deception")
    assert "ethos.adapters.polisher_llm" not in sys.modules


def test_fr9_live_adapter_is_env_gated():
    source = pathlib.Path(__file__).resolve().parents[1] / "src/ethos/adapters/polisher_llm.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    env_names = {
        node.slice.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
    }
    assert "ETHOS_LLM_API_KEY" in env_names

"""FR-15: the engine is pure — no I/O, no clock, no randomness — and the whole
pipeline is byte-deterministic across processes, hash seeds and locales."""
from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

import pytest

ENGINE = pathlib.Path(__file__).resolve().parents[1] / "src/ethos/engine"
BANNED_MODULES = {
    "os", "io", "pathlib", "socket", "requests", "urllib", "http", "sqlite3",
    "random", "secrets", "time", "datetime", "subprocess", "shutil", "tempfile",
    "threading", "asyncio", "uuid",
}
BANNED_CALLS = {"open", "input", "print", "eval", "exec", "compile"}


@pytest.mark.parametrize("module", sorted(p.name for p in ENGINE.glob("*.py")))
def test_fr15_engine_has_no_io_imports(module):
    tree = ast.parse((ENGINE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in BANNED_MODULES, f"{module}: {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in BANNED_MODULES, f"{module}: {node.module}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in BANNED_CALLS, f"{module}: calls {node.func.id}()"


def test_fr15_engine_never_reads_the_clock():
    for path in ENGINE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for banned in ("now(", "utcnow(", "time()", "monotonic("):
            assert banned not in source, f"{path.name} reads a clock"


def test_fr15_no_seeds_exist_anywhere():
    """There is no randomness, so there is no seed plumbing to get wrong."""
    src = ENGINE.parent
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "random." not in text
        assert "seed=" not in text


_SCRIPT = """
import json, sys
sys.path.insert(0, {src!r})
from ethos.corpus import load_corpus
from pathlib import Path
from ethos.engine.compose import compose_answer, render_text
from ethos.engine.retrieve import retrieve
from ethos.engine.router import build_index, index_to_json, route
from ethos.models import RoutingEcho
corpus = load_corpus(Path({data!r}))
index = build_index(corpus.topics, corpus.router_config, corpus.stopwords)
routing = route("is it wrong to lie to spare feelings?", index).model_dump()
out = []
for topic in ("honesty_and_deception", "suicide_and_self_harm", "divorce"):
    echo = RoutingEcho(topic_id=topic, confidence=None, alternates=[], forced=True)
    body = compose_answer(corpus, topic, retrieve(corpus, topic, None), echo)
    out.append(body.model_dump_json())
    out.append(render_text(body, {{t.id: t.title for t in corpus.topics}},
                           {{t.id: t.name for t in corpus.traditions}}))
print(json.dumps({{"routing": routing, "answers": out,
                   "index": index_to_json(index), "version": corpus.corpus_version}}))
"""


def _run(env_extra: dict[str, str]) -> str:
    root = pathlib.Path(__file__).resolve().parents[1]
    script = _SCRIPT.format(src=str(root / "src"), data=str(root / "data"))
    env = {**os.environ, **env_extra}
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout


def test_fr15_determinism_across_hash_seeds_and_locales():
    """D0(a): three fresh subprocesses, byte-identical output."""
    first = _run({"PYTHONHASHSEED": "0"})
    second = _run({"PYTHONHASHSEED": "1"})
    third = _run({"PYTHONHASHSEED": "0", "LC_ALL": "C"})
    assert first == second == third
    payload = json.loads(first)
    assert payload["routing"]["ranked"]
    assert len(payload["answers"]) == 6


def test_fr15_index_rebuild_is_byte_identical():
    """D0(c): the serialized BM25 index is stable across processes."""
    assert json.loads(_run({"PYTHONHASHSEED": "3"}))["index"] == json.loads(
        _run({"PYTHONHASHSEED": "4"})
    )["index"]

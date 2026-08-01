"""FR-6: deterministic composition. The composer assembles curated data and
has no path that produces a verdict (SCOPE non-goal 2)."""
from __future__ import annotations

import ast
import pathlib

from ethos.engine.compose import COMPOSER_VERSION, compose_answer
from ethos.engine.retrieve import retrieve
from ethos.models import Stance


def test_fr6_marker_numbering_first_appearance(compose):
    body, rendered = compose("honesty_and_deception")
    order: list[str] = []
    for perspective in body.perspectives:
        for point in perspective.reasoning:
            if point.marker and point.marker not in order:
                order.append(point.marker)
        for quote in perspective.quotes:
            if quote.marker not in order:
                order.append(quote.marker)
    assert order == [f"C{i}" for i in range(1, len(order) + 1)]
    assert list(body.citations) == order
    positions = [rendered.index(f"[{m}]") for m in order]
    assert positions == sorted(positions)


def test_fr6_every_citation_key_has_exactly_one_quote(compose):
    body, _ = compose("wealth_and_generosity")
    counts: dict[str, int] = {}
    for perspective in body.perspectives:
        for quote in perspective.quotes:
            counts[quote.marker] = counts.get(quote.marker, 0) + 1
    assert counts == {marker: 1 for marker in body.citations}


def test_fr6_agreement_map_partitions_rendered_traditions(compose, corpus):
    for topic in corpus.topics:
        body, _ = compose(topic.id)
        rendered = [p.tradition_id for p in body.perspectives]
        flat = [t for members in body.agreement_map.values() for t in members]
        assert sorted(flat) == sorted(rendered)
        assert len(flat) == len(set(flat))
        assert all(k in {s.value for s in Stance} for k in body.agreement_map)


def test_fr6_agreement_map_follows_enum_order(compose):
    body, _ = compose("killing_and_self_defense")
    order = [s.value for s in Stance]
    keys = list(body.agreement_map)
    assert keys == sorted(keys, key=order.index)


def test_fr6_filtered_answer_maps_only_the_rendered_subset(compose, corpus):
    body, _ = compose("honesty_and_deception", ["christianity"])
    assert [p.tradition_id for p in body.perspectives] == ["christianity"]
    flat = [t for members in body.agreement_map.values() for t in members]
    assert flat == ["christianity"]
    assert "judaism" not in body.not_covered  # judaism has a position: it was filtered


def test_fr6_identical_inputs_give_identical_output(corpus, compose):
    first, first_text = compose("divorce")
    second, second_text = compose("divorce")
    assert first.model_dump_json() == second.model_dump_json()
    assert first_text == second_text


def test_fr6_composition_is_order_independent(corpus):
    """Reversing the corpus record order cannot change the answer."""
    body_a = compose_answer(
        corpus, "anger_and_hatred", retrieve(corpus, "anger_and_hatred", None),
        _echo("anger_and_hatred"),
    )
    shuffled = corpus
    shuffled.positions = list(reversed(corpus.positions))
    shuffled.__post_init__()
    body_b = compose_answer(
        shuffled, "anger_and_hatred", retrieve(shuffled, "anger_and_hatred", None),
        _echo("anger_and_hatred"),
    )
    corpus.positions = list(reversed(shuffled.positions))
    corpus.__post_init__()
    assert body_a.model_dump_json() == body_b.model_dump_json()


def _echo(topic_id: str):
    from ethos.models import RoutingEcho

    return RoutingEcho(topic_id=topic_id, confidence=None, alternates=[], forced=True)


def test_fr6_no_overall_answer_path():
    """Non-goal 2, asserted structurally: the composer has no verdict field and
    no code that ranks or merges traditions."""
    source = pathlib.Path(compose_answer.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for banned in ("verdict", "overall", "consensus", "conclusion", "recommend", "best_answer"):
        assert banned not in names
    composer = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "compose_answer"
    )
    body_names = {n.id for n in ast.walk(composer) if isinstance(n, ast.Name)}
    for banned in ("rank", "ranked", "score", "weight", "majority", "sorted", "max", "min"):
        assert banned not in body_names, f"compose_answer ranks traditions via {banned}"


def test_fr6_a_traditions_perspective_does_not_depend_on_the_others(compose):
    """No cross-tradition synthesis: filtering changes who is shown, never
    what any shown tradition says."""
    full, _ = compose("wealth_and_generosity")
    alone, _ = compose("wealth_and_generosity", ["buddhism"])
    from_full = next(p for p in full.perspectives if p.tradition_id == "buddhism")
    only = alone.perspectives[0]
    assert only.summary == from_full.summary
    assert only.stance == from_full.stance
    assert [q.passage_id for q in only.quotes] == [q.passage_id for q in from_full.quotes]


def test_fr6_composer_version_is_stamped(compose):
    body, rendered = compose("divorce")
    assert body.composer_version == COMPOSER_VERSION
    assert rendered.rstrip().endswith(f"composer {COMPOSER_VERSION}")

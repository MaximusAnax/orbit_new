"""FR-7: citation and reading rendering — the printed grammar the independent
checker parses (DATA_MODEL § Plain-text render)."""
from __future__ import annotations

from ethos.engine.compose import PARAPHRASE_LABEL
from ethos.engine.verify import parse_render
from ethos.models import License


def _first_paraphrase_topic(corpus) -> tuple[str, str]:
    for position in corpus.positions:
        for ref in position.passages:
            if corpus.passage_by_id[ref.passage_id].text is None:
                return position.topic_id, position.tradition_id
    raise AssertionError("corpus has no reference_only citation to exercise")


def test_fr7_quote_block_is_verbatim_inside_typographic_quotes(compose, corpus):
    body, rendered = compose("honesty_and_deception")
    for perspective in body.perspectives:
        for quote in perspective.quotes:
            if quote.is_paraphrase:
                continue
            passage = corpus.passage_by_id[quote.passage_id]
            assert f"      “{passage.text}”" in rendered
            assert f"      — {passage.locator} — " in rendered


def test_fr7_source_line_forms(corpus):
    translated = next(
        s for s in corpus.sources if s.translator and s.license != License.reference_only
    )
    assert translated.source_line == (
        f"{translated.title}, trans. {translated.translator} ({translated.translation_year})"
    )
    english_original = next(
        (s for s in corpus.sources if s.translator is None and s.license != License.reference_only),
        None,
    )
    if english_original is not None:
        assert english_original.source_line == (
            f"{english_original.title}, {english_original.author}"
            f" ({english_original.translation_year})"
        )
    reference_only = next(s for s in corpus.sources if s.license == License.reference_only)
    assert reference_only.source_line == (
        f"{reference_only.title} — {reference_only.edition_note}"
    )


def test_fr7_paraphrase_label_rendered_and_never_quoted(compose, corpus):
    topic_id, tradition_id = _first_paraphrase_topic(corpus)
    body, rendered = compose(topic_id, [tradition_id])
    paraphrases = [q for p in body.perspectives for q in p.quotes if q.is_paraphrase]
    assert paraphrases, "expected a reference_only citation in this cell"
    for quote in paraphrases:
        assert quote.label == PARAPHRASE_LABEL
        assert f"      {PARAPHRASE_LABEL}\n      {quote.text}\n" in rendered
        assert f"“{quote.text}”" not in rendered


def test_fr7_context_note_follows_every_citation(compose, corpus):
    body, rendered = compose("wealth_and_generosity")
    for perspective in body.perspectives:
        for quote in perspective.quotes:
            assert f"      Context: {quote.context_note}" in rendered


def test_fr7_further_reading_renders_author_title_year_kind(compose):
    body, rendered = compose("divorce")
    for perspective in body.perspectives:
        assert "  Further reading:" in rendered
        for entry in perspective.further_reading:
            line = f"      - {entry.author}, “{entry.title}”"
            assert line in rendered
            if entry.year is not None:
                assert f"{line} ({entry.year}) [{entry.kind.value}]" in rendered
            if entry.url:
                assert entry.url in rendered


def test_fr7_render_is_parseable_by_the_grammar(compose, corpus):
    body, rendered = compose("theft_and_property")
    parsed = parse_render(rendered)
    assert parsed["topic_id"] == "theft_and_property"
    assert len(parsed["citations"]) == len(body.citations)
    assert set(parsed["table"]) == set(body.citations)
    assert parsed["traditions"] == [p.tradition_name for p in body.perspectives]


def test_fr7_citations_section_lists_every_marker_once(compose):
    body, rendered = compose("courage_and_fear")
    section = rendered.split("Citations:\n")[1]
    for marker, entry in body.citations.items():
        line = f"  [{marker}] {entry.passage_id} — {entry.locator} — {entry.source_id}\n"
        assert section.count(line) == 1

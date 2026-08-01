"""FR-6/FR-7: deterministic composition — AnswerBody assembly and the
plain-text render. The composer assembles curated data; it never writes moral
content and has no code path that produces an overall answer (non-goal 2).
"""
from __future__ import annotations

from collections.abc import Mapping

from ethos.corpus import Corpus
from ethos.engine.retrieve import Retrieval
from ethos.models import (
    AnswerBody,
    CitationEntry,
    Perspective,
    Quote,
    RenderedReasoning,
    RoutingEcho,
    Stance,
)

COMPOSER_VERSION = "1"

PARAPHRASE_LABEL = "[paraphrase — no public-domain translation quoted]"


def compose_answer(
    corpus: Corpus,
    topic_id: str,
    retrieval: Retrieval,
    routing: RoutingEcho,
) -> AnswerBody:
    topic = corpus.topic_by_id[topic_id]
    safeguards = [corpus.safeguard_by_id[sid] for sid in topic.safeguard_ids]
    perspectives: list[Perspective] = []
    citations: dict[str, CitationEntry] = {}
    marker_of: dict[tuple[str, str], str] = {}  # (position_id, passage_id) -> marker

    def assign_marker(position_id: str, passage_id: str) -> str:
        key = (position_id, passage_id)
        if key not in marker_of:
            marker = f"C{len(citations) + 1}"
            passage = corpus.passage_by_id[passage_id]
            marker_of[key] = marker
            citations[marker] = CitationEntry(
                passage_id=passage_id, locator=passage.locator, source_id=passage.source_id
            )
        return marker_of[key]

    for retrieved in retrieval.rendered:
        position = retrieved.position
        tradition = corpus.tradition_by_id[position.tradition_id]
        reasoning: list[RenderedReasoning] = []
        perspective_markers: list[str] = []
        for point in position.reasoning:
            marker = None
            if point.passage_id is not None:
                marker = assign_marker(position.id, point.passage_id)
                if marker not in perspective_markers:
                    perspective_markers.append(marker)
            reasoning.append(RenderedReasoning(text=point.text, marker=marker))
        for ref in retrieved.ordered_refs:
            marker = assign_marker(position.id, ref.passage_id)
            if marker not in perspective_markers:
                perspective_markers.append(marker)
        quotes: list[Quote] = []
        for marker in perspective_markers:
            entry = citations[marker]
            passage = corpus.passage_by_id[entry.passage_id]
            source = corpus.source_by_id[passage.source_id]
            is_paraphrase = passage.text is None
            quotes.append(
                Quote(
                    marker=marker,
                    passage_id=passage.id,
                    text=passage.paraphrase if is_paraphrase else passage.text or "",
                    is_paraphrase=is_paraphrase,
                    label=PARAPHRASE_LABEL if is_paraphrase else None,
                    locator=passage.locator,
                    source_line=source.source_line,
                    context_note=passage.context_note,
                )
            )
        perspectives.append(
            Perspective(
                tradition_id=tradition.id,
                tradition_name=tradition.name,
                position_id=position.id,
                stance=position.stance,
                summary=position.summary,
                reasoning=reasoning,
                quotes=quotes,
                intra_tradition_note=position.intra_tradition_note,
                further_reading=position.further_reading,
            )
        )

    agreement: dict[str, list[str]] = {}
    for stance in Stance:
        members = [p.tradition_id for p in perspectives if p.stance == stance]
        if members:
            agreement[stance.value] = members

    return AnswerBody(
        safeguards=safeguards,
        routing=routing,
        perspectives=perspectives,
        not_covered=list(retrieval.not_covered),
        filtered_out=list(retrieval.filtered_out),
        agreement_map=agreement,
        citations=citations,
        corpus_version=corpus.corpus_version,
        composer_version=COMPOSER_VERSION,
    )


# --- Plain-text render (grammar in DATA_MODEL § Plain-text render) ----------


def _quote_block(lines: list[str], quote: Quote) -> None:
    if quote.is_paraphrase:
        lines.append(f"      {PARAPHRASE_LABEL}")
        lines.append(f"      {quote.text}")
    else:
        lines.append(f"      “{quote.text}”")
    lines.append(f"      — {quote.locator} — {quote.source_line}")
    lines.append(f"      Context: {quote.context_note}")


def _reading_line(entry) -> str:
    parts = [f"      - {entry.author}, “{entry.title}”"]
    if entry.year is not None:
        parts.append(f" ({entry.year})")
    parts.append(f" [{entry.kind.value}]")
    if entry.url:
        parts.append(f" {entry.url}")
    return "".join(parts)


def render_text(
    body: AnswerBody,
    topic_titles: Mapping[str, str],
    tradition_names: Mapping[str, str] | None = None,
) -> str:
    """Deterministic plain-text render of an AnswerBody (FR-7)."""
    lines: list[str] = []
    title = topic_titles.get(body.routing.topic_id, body.routing.topic_id)
    lines.append(f"=== {title} ===")
    lines.append("")
    for safeguard in body.safeguards:
        lines.append(f"[!] {safeguard.text}")
    if body.safeguards:
        lines.append("")
    if body.routing.forced:
        lines.append(f"Topic: {title} ({body.routing.topic_id}) — forced by request")
    else:
        lines.append(
            f"Matched topic: {title} ({body.routing.topic_id})"
            f" — confidence {body.routing.confidence:.2f}"
        )
        if body.routing.alternates:
            alts = "; ".join(
                f"{topic_titles.get(a.topic_id, a.topic_id)} ({a.topic_id}) {a.score:.2f}"
                for a in body.routing.alternates
            )
            lines.append(f"Other topics considered: {alts}")
    lines.append("")
    name_of = dict(tradition_names or {})
    name_of.update({p.tradition_id: p.tradition_name for p in body.perspectives})
    for perspective in body.perspectives:
        lines.append(f"--- {perspective.tradition_name} — {perspective.stance.value} ---")
        lines.append(perspective.summary)
        quotes_by_marker = {q.marker: q for q in perspective.quotes}
        rendered_markers: list[str] = []
        for point in perspective.reasoning:
            suffix = f" [{point.marker}]" if point.marker else ""
            lines.append(f"  • {point.text}{suffix}")
            if point.marker and point.marker not in rendered_markers:
                rendered_markers.append(point.marker)
                _quote_block(lines, quotes_by_marker[point.marker])
        remaining = [q for q in perspective.quotes if q.marker not in rendered_markers]
        if remaining:
            lines.append("  Also cited:")
            for quote in remaining:
                _quote_block(lines, quote)
        if perspective.intra_tradition_note:
            lines.append(f"  Note: {perspective.intra_tradition_note}")
        lines.append("  Further reading:")
        for entry in perspective.further_reading:
            lines.append(_reading_line(entry))
        lines.append("")
    lines.append("Where traditions agree and differ:")
    for stance_value, members in body.agreement_map.items():
        names = ", ".join(name_of.get(t, t) for t in members)
        lines.append(f"  {stance_value}: {names}")
    if body.not_covered:
        covered_names = ", ".join(name_of.get(t, t) for t in body.not_covered)
        lines.append(f"No curated position on this topic: {covered_names}")
    if body.filtered_out:
        filtered_names = ", ".join(name_of.get(t, t) for t in body.filtered_out)
        lines.append(
            f"Excluded by your filter (these traditions do have positions): {filtered_names}"
        )
    lines.append("")
    lines.append("Citations:")
    for marker, entry in body.citations.items():
        lines.append(f"  [{marker}] {entry.passage_id} — {entry.locator} — {entry.source_id}")
    lines.append("")
    lines.append(f"Corpus {body.corpus_version[:12]} · composer {body.composer_version}")
    return "\n".join(lines) + "\n"

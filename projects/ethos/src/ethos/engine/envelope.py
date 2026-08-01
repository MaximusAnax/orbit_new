"""FR-9 / DATA_MODEL § Envelope: AnswerBody <-> envelope serialization.

The envelope is the only serialization a polisher ever sees. `[[I:...]]`
regions are immutable, `[[M:...]]` regions mutable. Serialization is a pure
function; parse-back is strict — any structural deviation raises
EnvelopeError, which callers treat as an FR-8 violation (fallback), never as
a crash.
"""
from __future__ import annotations

import json
import re

from ethos.models import (
    AnswerBody,
    CitationEntry,
    FurtherReading,
    Perspective,
    Quote,
    RenderedReasoning,
    RoutingEcho,
    Safeguard,
    Stance,
    TopicScore,
)

_SENTINEL = re.compile(r"\[\[(/?)([A-Za-z0-9_:.\-]+)\]\]")


class EnvelopeError(ValueError):
    """Strict parse-back failure — an FR-8 violation, not an exception path."""


def _cj(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _region(tag: str, payload: str) -> str:
    return f"[[{tag}]]{payload}[[/{tag}]]\n"


def serialize(body: AnswerBody, requested_traditions: list[str] | None) -> str:
    out: list[str] = ["[[ETHOS:1]]\n"]
    meta = {
        "topic_id": body.routing.topic_id,
        "corpus_version": body.corpus_version,
        "composer_version": body.composer_version,
        "requested_traditions": requested_traditions,
        "forced": body.routing.forced,
    }
    out.append(_region("I:meta", _cj(meta)))
    for sg in body.safeguards:
        out.append(_region(f"I:safeguard:{sg.id}", _cj({"kind": sg.kind.value, "text": sg.text})))
    routing = {
        "topic_id": body.routing.topic_id,
        "confidence": body.routing.confidence,
        "alternates": [{"topic_id": a.topic_id, "score": a.score} for a in body.routing.alternates],
        "forced": body.routing.forced,
    }
    out.append(_region("I:routing", _cj(routing)))
    for p in body.perspectives:
        tid = p.tradition_id
        out.append(f"[[P:{tid}]]\n")
        out.append(_region(f"I:header:{tid}", p.tradition_name))
        out.append(_region(f"I:stance:{tid}", p.stance.value))
        out.append(_region(f"M:summary:{tid}", p.summary))
        for i, point in enumerate(p.reasoning, start=1):
            out.append(_region(f"M:reason:{tid}:{i}", point.text))
            if point.marker is not None:
                out.append(_region(f"I:rmark:{tid}:{i}", point.marker))
        for q in p.quotes:
            out.append(_region(f"I:quote:{q.marker}", q.text))
            qmeta = {
                "passage_id": q.passage_id,
                "is_paraphrase": q.is_paraphrase,
                "locator": q.locator,
                "source_line": q.source_line,
                "label": q.label,
            }
            out.append(_region(f"I:qmeta:{q.marker}", _cj(qmeta)))
            out.append(_region(f"I:context:{q.marker}", q.context_note))
        if p.intra_tradition_note is not None:
            out.append(_region(f"M:intra:{tid}", p.intra_tradition_note))
        reading = [
            {
                "author": r.author,
                "title": r.title,
                "year": r.year,
                "kind": r.kind.value,
                "url": r.url,
                "note": r.note,
            }
            for r in p.further_reading
        ]
        out.append(_region(f"I:reading:{tid}", _cj(reading)))
        out.append(f"[[/P:{tid}]]\n")
    out.append(_region("I:agreement", _cj(body.agreement_map)))
    out.append(_region("I:not_covered", _cj(body.not_covered)))
    out.append(_region("I:filtered_out", _cj(body.filtered_out)))
    citations = {
        m: {"passage_id": c.passage_id, "locator": c.locator, "source_id": c.source_id}
        for m, c in body.citations.items()
    }
    out.append(_region("I:citations", _cj(citations)))
    out.append("[[/ETHOS:1]]\n")
    return "".join(out)


# --- Strict parse-back -------------------------------------------------------

Region = tuple[str, str, bool]  # (tag, payload, mutable)


def _scan(env: str) -> list[tuple[str, str, str | None]]:
    """Tokenize into ('block_open'|'block_close', tag, None) and
    ('region', tag, payload) items; any structural noise is an error."""
    items: list[tuple[str, str, str | None]] = []
    pos = 0
    while pos < len(env):
        m = _SENTINEL.search(env, pos)
        if m is None:
            if env[pos:].strip():
                raise EnvelopeError("trailing content outside sentinels")
            break
        if env[pos : m.start()].strip():
            raise EnvelopeError("content outside regions")
        closing, tag = m.group(1), m.group(2)
        if closing:
            items.append(("block_close", tag, None))
            pos = m.end()
            continue
        if tag.startswith(("I:", "M:")):
            close = _SENTINEL.search(env, m.end())
            if close is None or close.group(1) != "/" or close.group(2) != tag:
                raise EnvelopeError(f"unterminated region {tag}")
            items.append(("region", tag, env[m.end() : close.start()]))
            pos = close.end()
        else:
            items.append(("block_open", tag, None))
            pos = m.end()
    return items


class _Reader:
    def __init__(self, items: list[tuple[str, str, str | None]]):
        self.items = items
        self.i = 0

    def peek(self) -> tuple[str, str, str | None] | None:
        return self.items[self.i] if self.i < len(self.items) else None

    def next(self) -> tuple[str, str, str | None]:
        if self.i >= len(self.items):
            raise EnvelopeError("unexpected end of envelope")
        item = self.items[self.i]
        self.i += 1
        return item

    def expect_region(self, tag: str) -> str:
        kind, got, payload = self.next()
        if kind != "region" or got != tag:
            raise EnvelopeError(f"expected region {tag}, got {kind} {got}")
        return payload or ""

    def expect_block(self, kind: str, tag: str) -> None:
        got_kind, got_tag, _ = self.next()
        if got_kind != kind or got_tag != tag:
            raise EnvelopeError(f"expected {kind} {tag}, got {got_kind} {got_tag}")


def _json(payload: str, tag: str) -> object:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"unparseable JSON in {tag}") from exc


def _stance_order(agreement: dict[str, list[str]]) -> dict[str, list[str]]:
    known = [s.value for s in Stance]
    if any(k not in known for k in agreement):
        raise EnvelopeError("unknown stance in agreement map")
    return {s: agreement[s] for s in known if s in agreement}


def _marker_order(citations: dict[str, dict]) -> dict[str, dict]:
    def key(marker: str) -> int:
        if not re.fullmatch(r"C[1-9][0-9]*", marker):
            raise EnvelopeError(f"bad marker {marker}")
        return int(marker[1:])

    return {m: citations[m] for m in sorted(citations, key=key)}


def _parse_perspective(reader: _Reader, tid: str) -> Perspective:
    name = reader.expect_region(f"I:header:{tid}")
    stance_raw = reader.expect_region(f"I:stance:{tid}")
    try:
        stance = Stance(stance_raw)
    except ValueError as exc:
        raise EnvelopeError(f"bad stance {stance_raw!r}") from exc
    summary = reader.expect_region(f"M:summary:{tid}")
    reasoning: list[RenderedReasoning] = []
    idx = 1
    while True:
        item = reader.peek()
        if item is None or item[0] != "region" or item[1] != f"M:reason:{tid}:{idx}":
            break
        reader.next()
        text = item[2] or ""
        marker = None
        nxt = reader.peek()
        if nxt is not None and nxt[0] == "region" and nxt[1] == f"I:rmark:{tid}:{idx}":
            reader.next()
            marker = nxt[2] or ""
        reasoning.append(RenderedReasoning(text=text, marker=marker))
        idx += 1
    quotes: list[Quote] = []
    while True:
        item = reader.peek()
        if item is None or item[0] != "region" or not item[1].startswith("I:quote:"):
            break
        reader.next()
        marker = item[1].removeprefix("I:quote:")
        text = item[2] or ""
        qmeta = _json(reader.expect_region(f"I:qmeta:{marker}"), "qmeta")
        if not isinstance(qmeta, dict):
            raise EnvelopeError("qmeta must be an object")
        context = reader.expect_region(f"I:context:{marker}")
        try:
            quotes.append(
                Quote(
                    marker=marker,
                    passage_id=qmeta["passage_id"],
                    text=text,
                    is_paraphrase=qmeta["is_paraphrase"],
                    label=qmeta["label"],
                    locator=qmeta["locator"],
                    source_line=qmeta["source_line"],
                    context_note=context,
                )
            )
        except (KeyError, ValueError) as exc:
            raise EnvelopeError(f"bad qmeta for {marker}") from exc
    intra = None
    item = reader.peek()
    if item is not None and item[0] == "region" and item[1] == f"M:intra:{tid}":
        reader.next()
        intra = item[2] or ""
    reading_raw = _json(reader.expect_region(f"I:reading:{tid}"), "reading")
    if not isinstance(reading_raw, list):
        raise EnvelopeError("reading must be a list")
    try:
        reading = [FurtherReading(**r) for r in reading_raw]
    except (TypeError, ValueError) as exc:
        raise EnvelopeError("bad further_reading entry") from exc
    reader.expect_block("block_close", f"P:{tid}")
    return Perspective(
        tradition_id=tid,
        tradition_name=name,
        position_id="",  # filled by caller from pre-polish body (immutable pairing)
        stance=stance,
        summary=summary,
        reasoning=reasoning,
        quotes=quotes,
        intra_tradition_note=intra,
        further_reading=reading,
    )


def regions_of(env: str) -> list[Region]:
    """Ordered (tag, payload, mutable) list — the FR-8(e) diff basis."""
    return [
        (tag, payload or "", tag.startswith("M:"))
        for kind, tag, payload in _scan(env)
        if kind == "region"
    ]


def parse(env: str, position_ids: dict[str, str] | None = None) -> AnswerBody:
    """Strict envelope -> AnswerBody. `position_ids` maps tradition_id ->
    position_id from the pre-polish body (the envelope does not carry it)."""
    reader = _Reader(_scan(env))
    reader.expect_block("block_open", "ETHOS:1")
    meta = _json(reader.expect_region("I:meta"), "meta")
    if not isinstance(meta, dict):
        raise EnvelopeError("meta must be an object")
    safeguards: list[Safeguard] = []
    while True:
        item = reader.peek()
        if item is None or item[0] != "region" or not item[1].startswith("I:safeguard:"):
            break
        reader.next()
        sid = item[1].removeprefix("I:safeguard:")
        payload = _json(item[2] or "", "safeguard")
        if not isinstance(payload, dict):
            raise EnvelopeError("safeguard payload must be an object")
        try:
            safeguards.append(Safeguard(id=sid, kind=payload["kind"], text=payload["text"]))
        except (KeyError, ValueError) as exc:
            raise EnvelopeError(f"bad safeguard {sid}") from exc
    routing_raw = _json(reader.expect_region("I:routing"), "routing")
    if not isinstance(routing_raw, dict):
        raise EnvelopeError("routing must be an object")
    try:
        routing = RoutingEcho(
            topic_id=routing_raw["topic_id"],
            confidence=routing_raw["confidence"],
            alternates=[TopicScore(**a) for a in routing_raw["alternates"]],
            forced=routing_raw["forced"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvelopeError("bad routing region") from exc
    perspectives: list[Perspective] = []
    while True:
        item = reader.peek()
        if item is None or item[0] != "block_open" or not item[1].startswith("P:"):
            break
        reader.next()
        tid = item[1].removeprefix("P:")
        perspective = _parse_perspective(reader, tid)
        if position_ids is not None:
            if tid not in position_ids:
                raise EnvelopeError(f"perspective {tid} not in pre-polish body")
            perspective = perspective.model_copy(update={"position_id": position_ids[tid]})
        perspectives.append(perspective)
    agreement_raw = _json(reader.expect_region("I:agreement"), "agreement")
    not_covered = _json(reader.expect_region("I:not_covered"), "not_covered")
    filtered_out = _json(reader.expect_region("I:filtered_out"), "filtered_out")
    citations_raw = _json(reader.expect_region("I:citations"), "citations")
    reader.expect_block("block_close", "ETHOS:1")
    if reader.peek() is not None:
        raise EnvelopeError("content after envelope close")
    if not isinstance(agreement_raw, dict) or not isinstance(citations_raw, dict):
        raise EnvelopeError("agreement/citations must be objects")
    if not isinstance(not_covered, list) or not isinstance(filtered_out, list):
        raise EnvelopeError("not_covered/filtered_out must be lists")
    try:
        citations = {
            m: CitationEntry(**c) for m, c in _marker_order(citations_raw).items()
        }
        return AnswerBody(
            safeguards=safeguards,
            routing=routing,
            perspectives=perspectives,
            not_covered=not_covered,
            filtered_out=filtered_out,
            agreement_map=_stance_order(agreement_raw),
            citations=citations,
            corpus_version=meta.get("corpus_version", ""),
            composer_version=meta.get("composer_version", ""),
        )
    except (TypeError, ValueError) as exc:
        raise EnvelopeError("body reconstruction failed") from exc

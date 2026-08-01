"""FR-8: the citation-integrity gate — checks (a)-(i) plus an independent
re-read of the printed answer.

Two deliberately separate instruments live here:

1. :func:`check_body` — the FR-8 checks over the parsed-back ``AnswerBody``,
   resolving every quote, locator, source line, safeguard, stance, reading
   list and structural immutable against the loaded corpus, and diffing the
   polished envelope against the pre-polish one.
2. :func:`check_render_independently` — a **stdlib-only** reader that re-parses
   the *printed plain text* per DATA_MODEL § Plain-text render and resolves it
   against the **raw json dicts** of the corpus files. It imports nothing from
   the composer, the envelope, or the Pydantic models: it never sees the object
   the composer built, only the characters the user will read. A composer bug,
   a model bug, or a loader that folded an en-dash cannot hide from it, because
   it agrees with the composer about nothing except the render grammar.

This module imports no I/O and no clock (engine purity, FR-15); the raw dicts
are supplied by the caller.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from ethos.engine import envelope as env_mod
from ethos.engine.corpus import Corpus
from ethos.models import AnswerBody

# Deliberately narrower than the anchored C5 validation regexes, and applied
# only to polish-introduced spans (DATA_MODEL § Locator regexes, set 2).
PROSE_LOCATOR_SCAN = [
    r"\b\d+:\d+(?:[–-]\d+)?\b",  # noqa: RUF001 - en-dash ranges are real locators
    r"\b[IVXLC]{1,5}\.\d+(?:[–-]\d+)?\b",  # noqa: RUF001
    r"\bQ\.\d+,\s*art\.\d+\b",
    r"\b\d{3,4}[ab]\d{1,2}\b",
    r"\b(?:DN|MN|SN|AN|Dhp|Snp)\s\d+(?:\.\d+)?\b",
    r"\b\d{1,3}[ab]\b(?=\s|,|\.|$)",
]
_SCAN_RES = [re.compile(p) for p in PROSE_LOCATOR_SCAN]

PARAPHRASE_LABEL = "[paraphrase — no public-domain translation quoted]"
MAX_REGION_GROWTH = 1.4
MIN_REGION_SHRINK = 0.6


@dataclass(frozen=True)
class Failure:
    check: str  # 'a'..'i', 'render', or 'parse'
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"({self.check}) {self.detail}"


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")


# --- (a) marker correspondence ----------------------------------------------


def _check_markers(body: AnswerBody, failures: list[Failure]) -> None:
    table_keys = list(body.citations.keys())
    appearance: list[str] = []
    quote_count: dict[str, int] = {}
    for perspective in body.perspectives:
        for point in perspective.reasoning:
            if point.marker is None:
                continue
            if point.marker not in body.citations:
                failures.append(Failure("a", f"reasoning marker {point.marker} not in table"))
            if point.marker not in appearance:
                appearance.append(point.marker)
        for quote in perspective.quotes:
            quote_count[quote.marker] = quote_count.get(quote.marker, 0) + 1
            if quote.marker not in appearance:
                appearance.append(quote.marker)
    if set(appearance) != set(table_keys):
        failures.append(Failure("a", "marker set != citation table keys"))
        return
    for key in table_keys:
        count = quote_count.get(key, 0)
        if count != 1:
            failures.append(Failure("a", f"marker {key} has {count} quote blocks, need exactly 1"))
    expected = [f"C{i}" for i in range(1, len(appearance) + 1)]
    if appearance != expected or table_keys != expected:
        failures.append(Failure("a", "markers are not C1..Cn in first-appearance order"))


# --- (b) quote fidelity, (c) locator + source line, (f) triple consistency ---


def _check_quotes(
    body: AnswerBody, corpus: Corpus, null_path: bool, failures: list[Failure]
) -> None:
    for perspective in body.perspectives:
        for quote in perspective.quotes:
            passage = corpus.passage_by_id.get(quote.passage_id)
            if passage is None:
                failures.append(Failure("f", f"{quote.marker}: unknown passage {quote.passage_id}"))
                continue
            source = corpus.source_by_id.get(passage.source_id)
            if source is None:
                failures.append(Failure("f", f"{quote.marker}: unknown source"))
                continue
            committed_is_para = passage.text is None
            committed = passage.paraphrase if committed_is_para else passage.text
            ok = (
                quote.text == committed
                if null_path
                else _nfc(quote.text) == _nfc(committed or "")
            )
            if not ok:
                failures.append(Failure("b", f"{quote.marker}: text != passage {passage.id}"))
            if quote.locator != passage.locator:
                failures.append(Failure("c", f"{quote.marker}: locator mismatch"))
            if quote.source_line != source.source_line:
                failures.append(Failure("c", f"{quote.marker}: source line mismatch"))
            if quote.is_paraphrase != committed_is_para:
                failures.append(Failure("f", f"{quote.marker}: is_paraphrase flipped"))
            expected_label = PARAPHRASE_LABEL if committed_is_para else None
            if quote.label != expected_label:
                failures.append(Failure("f", f"{quote.marker}: paraphrase label wrong"))
            if quote.context_note != passage.context_note:
                failures.append(Failure("i", f"{quote.marker}: context note altered"))
            entry = body.citations.get(quote.marker)
            if entry is not None and (
                entry.passage_id != passage.id
                or entry.locator != passage.locator
                or entry.source_id != passage.source_id
            ):
                failures.append(Failure("f", f"{quote.marker}: citation table disagrees"))


# --- (d) safeguards, (g) reading, (h) identity/order, (i) immutables ---------


def _check_safeguards(body: AnswerBody, corpus: Corpus, failures: list[Failure]) -> None:
    topic = corpus.topic_by_id.get(body.routing.topic_id)
    if topic is None:
        failures.append(Failure("h", f"unknown topic {body.routing.topic_id}"))
        return
    want = [
        (sid, corpus.safeguard_by_id[sid].kind, corpus.safeguard_by_id[sid].text)
        for sid in topic.safeguard_ids
        if sid in corpus.safeguard_by_id
    ]
    got = [(s.id, s.kind, s.text) for s in body.safeguards]
    if got != want:
        failures.append(Failure("d", "safeguard block differs from data/safeguards.json"))


def _check_perspectives(
    body: AnswerBody, corpus: Corpus, requested: list[str] | None, failures: list[Failure]
) -> None:
    canonical = [t.id for t in corpus.traditions]
    requested_set = set(requested) if requested is not None else set(canonical)
    have = {t for t in canonical if (body.routing.topic_id, t) in corpus.position_by_cell}
    expected_rendered = [t for t in canonical if t in requested_set and t in have]
    if [p.tradition_id for p in body.perspectives] != expected_rendered:
        failures.append(Failure("h", "perspectives are not the canonical order over the rendered set"))
    for perspective in body.perspectives:
        tradition = corpus.tradition_by_id.get(perspective.tradition_id)
        if tradition is None or perspective.tradition_name != tradition.name:
            failures.append(Failure("h", f"header mismatch for {perspective.tradition_id}"))
        position = corpus.position_by_cell.get(
            (body.routing.topic_id, perspective.tradition_id)
        )
        if position is None:
            failures.append(Failure("h", f"no curated position for {perspective.tradition_id}"))
            continue
        if perspective.position_id != position.id:
            failures.append(Failure("h", f"position id mismatch for {perspective.tradition_id}"))
        if perspective.stance != position.stance:
            failures.append(Failure("i", f"stance altered for {perspective.tradition_id}"))
        got_reading = [r.model_dump(mode="json") for r in perspective.further_reading]
        want_reading = [r.model_dump(mode="json") for r in position.further_reading]
        if got_reading != want_reading:
            failures.append(Failure("g", f"further reading altered for {perspective.tradition_id}"))
    expected_not_covered = [t for t in canonical if t in requested_set and t not in have]
    expected_filtered = [t for t in canonical if t in have and t not in requested_set]
    if body.not_covered != expected_not_covered:
        failures.append(Failure("i", "not_covered altered"))
    if body.filtered_out != expected_filtered:
        failures.append(Failure("i", "filtered_out altered"))
    agreement: dict[str, list[str]] = {}
    for perspective in body.perspectives:
        agreement.setdefault(perspective.stance.value, []).append(perspective.tradition_id)
    if dict(body.agreement_map) != agreement:
        failures.append(Failure("i", "agreement map does not partition the rendered set"))


# --- (e) polish-introduced text ---------------------------------------------


def _changed_spans(pre: str, post: str) -> list[str]:
    """Deterministic token-level diff: the spans of `post` that are new."""
    pre_tokens = pre.split()
    post_tokens = post.split()
    matcher = SequenceMatcher(a=pre_tokens, b=post_tokens, autojunk=False)
    spans: list[str] = []
    for op, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if op in ("replace", "insert") and j2 > j1:
            spans.append(" ".join(post_tokens[j1:j2]))
    return spans


def _check_polish_diff(pre_env: str, post_env: str, failures: list[Failure]) -> None:
    pre_regions = env_mod.regions_of(pre_env)
    post_regions = env_mod.regions_of(post_env)
    if [tag for tag, _, _ in pre_regions] != [tag for tag, _, _ in post_regions]:
        failures.append(Failure("a", "envelope region structure altered by polish"))
        return
    for (tag, pre_payload, mutable), (_, post_payload, _) in zip(
        pre_regions, post_regions, strict=True
    ):
        if not mutable:
            if pre_payload != post_payload:
                failures.append(Failure("i", f"immutable region {tag} altered"))
            continue
        if pre_payload == post_payload:
            continue
        if pre_payload:
            ratio = len(post_payload) / len(pre_payload)
            if ratio > MAX_REGION_GROWTH or ratio < MIN_REGION_SHRINK:
                failures.append(Failure("e", f"mutable region {tag} length changed by > 40%"))
        for span in _changed_spans(pre_payload, post_payload):
            if any(pattern.search(span) for pattern in _SCAN_RES):
                failures.append(
                    Failure("e", f"locator-shaped text introduced in {tag}: {span[:60]!r}")
                )


def check_body(
    body: AnswerBody,
    corpus: Corpus,
    requested: list[str] | None,
    pre_polish_envelope: str,
    polished_envelope: str | None = None,
) -> list[Failure]:
    """FR-8 (a)-(i) over the parsed-back body. Empty list means verified."""
    failures: list[Failure] = []
    _check_markers(body, failures)
    _check_quotes(body, corpus, polished_envelope is None, failures)
    _check_safeguards(body, corpus, failures)
    _check_perspectives(body, corpus, requested, failures)
    if polished_envelope is not None:
        _check_polish_diff(pre_polish_envelope, polished_envelope, failures)
    return failures


# --- the independent reader --------------------------------------------------
# Everything below re-parses the printed answer and resolves it against the raw
# json dicts. It shares no code with the composer and no object with the body:
# a fabricated citation has to survive being read back off the page.

_QUOTE_LINE = re.compile(r"^ {6}“(?P<text>.*)”$")
_CITE_LINE = re.compile(r"^ {6}— (?P<rest>.+)$")
_BULLET = re.compile(r"^ {2}• .*?(?: \[(?P<marker>C[1-9][0-9]*)\])?$")
_MARKER_AT_END = re.compile(r" \[(C[1-9][0-9]*)\]$")
_HEADER = re.compile(r"^--- (?P<name>.+) — (?P<stance>[a-z_]+) ---$")
_TOPIC_LINE = re.compile(r"^(?:Matched topic|Topic): .+ \((?P<topic_id>[a-z0-9_]+)\)")
_TABLE_LINE = re.compile(r"^ {2}\[(?P<marker>C[1-9][0-9]*)\] (?P<rest>.+)$")


def _source_line_from_raw(source: dict[str, Any]) -> str:
    if source.get("license") == "reference_only":
        return f"{source['title']} — {source['edition_note']}"
    if source.get("translator") is None:
        return f"{source['title']}, {source['author']} ({source['translation_year']})"
    return f"{source['title']}, trans. {source['translator']} ({source['translation_year']})"


@dataclass(frozen=True)
class PrintedCitation:
    marker: str | None
    text: str
    is_paraphrase: bool
    locator: str
    source_line: str


def parse_render(rendered_text: str) -> dict[str, Any]:
    """Re-parse a printed answer per DATA_MODEL § Plain-text render."""
    lines = rendered_text.split("\n")
    citations: list[PrintedCitation] = []
    table: dict[str, tuple[str, str, str]] = {}
    traditions: list[str] = []
    safeguards: list[str] = []
    topic_id = ""
    marker: str | None = None
    in_table = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("[!] "):
            safeguards.append(line[4:])
        elif not topic_id and _TOPIC_LINE.match(line):
            topic_id = _TOPIC_LINE.match(line).group("topic_id")  # type: ignore[union-attr]
        elif _HEADER.match(line):
            traditions.append(_HEADER.match(line).group("name"))  # type: ignore[union-attr]
        elif line.startswith("  • "):
            found = _MARKER_AT_END.search(line)
            marker = found.group(1) if found else None
        elif line == "Citations:":
            in_table = True
        elif in_table and _TABLE_LINE.match(line):
            hit = _TABLE_LINE.match(line)
            parts = hit.group("rest").split(" — ")  # type: ignore[union-attr]
            if len(parts) >= 3:
                table[hit.group("marker")] = (  # type: ignore[union-attr]
                    parts[0],
                    " — ".join(parts[1:-1]),
                    parts[-1],
                )
        elif _QUOTE_LINE.match(line):
            text = _QUOTE_LINE.match(line).group("text")  # type: ignore[union-attr]
            locator, source_line = _read_citation_line(lines, i + 1)
            citations.append(PrintedCitation(marker, text, False, locator, source_line))
        elif line == f"      {PARAPHRASE_LABEL}" and i + 1 < len(lines):
            text = lines[i + 1][6:]
            locator, source_line = _read_citation_line(lines, i + 2)
            citations.append(PrintedCitation(marker, text, True, locator, source_line))
            i += 1
        i += 1
    return {
        "topic_id": topic_id,
        "safeguards": safeguards,
        "traditions": traditions,
        "citations": citations,
        "table": table,
    }


def _read_citation_line(lines: list[str], index: int) -> tuple[str, str]:
    if index >= len(lines):
        return "", ""
    hit = _CITE_LINE.match(lines[index])
    if hit is None:
        return "", ""
    rest = hit.group("rest").split(" — ", 1)
    return (rest[0], rest[1] if len(rest) > 1 else "")


def check_render_independently(rendered_text: str, raw: Any) -> list[Failure]:
    """Resolve every printed citation against the raw corpus files.

    `raw` is a :class:`~ethos.engine.corpus.RawCorpus` — plain json dicts, no
    models. Comparison is raw string equality: no NFC, no strip, no casefold.
    Shortfall counts too: if the corpus says a rendered position cites five
    passages and only four blocks were printed, that is a failure.
    """
    failures: list[Failure] = []
    parsed = parse_render(rendered_text)
    passages = {p["id"]: p for p in raw.passages}
    sources = {s["id"]: s for s in raw.sources}
    by_text: dict[tuple[bool, str], list[dict[str, Any]]] = {}
    for passage in passages.values():
        if passage.get("text") is not None:
            by_text.setdefault((False, passage["text"]), []).append(passage)
        if passage.get("paraphrase") is not None:
            by_text.setdefault((True, passage["paraphrase"]), []).append(passage)

    for printed in parsed["citations"]:
        matches = by_text.get((printed.is_paraphrase, printed.text), [])
        if len(matches) != 1:
            failures.append(
                Failure(
                    "render",
                    f"printed text matches {len(matches)} committed passages: "
                    f"{printed.text[:60]!r}",
                )
            )
            continue
        passage = matches[0]
        if printed.locator != passage["locator"]:
            failures.append(
                Failure("render", f"{passage['id']}: printed locator {printed.locator!r}")
            )
        source = sources.get(passage["source_id"])
        if source is None:
            failures.append(Failure("render", f"{passage['id']}: source not in files"))
            continue
        if printed.source_line != _source_line_from_raw(source):
            failures.append(
                Failure("render", f"{passage['id']}: printed source line {printed.source_line!r}")
            )
        if printed.is_paraphrase and passage.get("text") is not None:
            failures.append(Failure("render", f"{passage['id']}: quoted text shown as paraphrase"))
        if printed.marker is None:
            failures.append(Failure("render", f"{passage['id']}: quote block carries no marker"))
            continue
        entry = parsed["table"].get(printed.marker)
        if entry is None:
            failures.append(Failure("render", f"marker {printed.marker} missing from Citations"))
        elif entry != (passage["id"], passage["locator"], passage["source_id"]):
            failures.append(
                Failure("render", f"marker {printed.marker}: Citations row disagrees with files")
            )

    markers = [c.marker for c in parsed["citations"] if c.marker is not None]
    if len(set(markers)) != len(markers):
        failures.append(Failure("render", "a marker labels more than one printed quote block"))
    if set(parsed["table"]) != set(markers):
        failures.append(Failure("render", "Citations table does not match the printed markers"))

    expected = _expected_citation_count(parsed, raw)
    if expected is not None and len(parsed["citations"]) != expected:
        failures.append(
            Failure(
                "render",
                f"{len(parsed['citations'])} citations printed, corpus expects {expected}",
            )
        )
    return failures


def _expected_citation_count(parsed: dict[str, Any], raw: Any) -> int | None:
    """How many citations the rendered positions should have produced."""
    names = {t["name"]: t["id"] for t in raw.traditions}
    rendered_ids = [names[n] for n in parsed["traditions"] if n in names]
    if len(rendered_ids) != len(parsed["traditions"]):
        return None
    total = 0
    for position in raw.positions:
        if position["topic_id"] != parsed["topic_id"]:
            continue
        if position["tradition_id"] not in rendered_ids:
            continue
        ids: list[str] = []
        for point in position["reasoning"]:
            if point.get("passage_id") and point["passage_id"] not in ids:
                ids.append(point["passage_id"])
        for ref in position["passages"]:
            if ref["passage_id"] not in ids:
                ids.append(ref["passage_id"])
        total += len(ids)
    return total


def verify(
    body: AnswerBody,
    corpus: Corpus,
    rendered_text: str,
    requested: list[str] | None,
    pre_polish_envelope: str,
    polished_envelope: str | None = None,
) -> list[Failure]:
    """Both instruments. Empty list means the answer may be shown and stored."""
    failures = check_body(body, corpus, requested, pre_polish_envelope, polished_envelope)
    if corpus.raw is not None:
        failures.extend(check_render_independently(rendered_text, corpus.raw))
    topic = corpus.topic_by_id.get(body.routing.topic_id)
    if topic is not None:
        printed = parse_render(rendered_text)["safeguards"]
        want = [
            corpus.safeguard_by_id[sid].text
            for sid in topic.safeguard_ids
            if sid in corpus.safeguard_by_id
        ]
        if printed != want:
            failures.append(Failure("d", "printed safeguard block is missing, reordered or altered"))
        head = f"=== {topic.title} ===\n\n" + "".join(f"[!] {t}\n" for t in want)
        if want and not rendered_text.startswith(head):
            failures.append(Failure("d", "safeguard block is not first in render order"))
    return failures

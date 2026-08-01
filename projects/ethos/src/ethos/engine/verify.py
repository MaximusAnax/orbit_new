"""FR-8: post-composition citation verification gate, checks (a)-(i).

Runs over the parsed-back AnswerBody and its plain-text render, comparing
against the corpus and against the pre-polish envelope. Fail => the answer is
rejected (null path: integrity error; polished path: fallback). The verifier
runs even with the null polisher — it also catches composer/corpus drift.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ethos.corpus import Corpus
from ethos.engine import envelope as env_mod
from ethos.engine.compose import PARAPHRASE_LABEL, render_text
from ethos.models import AnswerBody

# Deliberately narrower than the anchored C5 validation regexes; applied only
# to polish-introduced spans (DATA_MODEL § Locator regexes, set 2).
PROSE_LOCATOR_SCAN = [
    r"\b\d+:\d+(?:[–-]\d+)?\b",
    r"\b[IVXLC]{1,5}\.\d+(?:[–-]\d+)?\b",
    r"\bQ\.\d+,\s*art\.\d+\b",
    r"\b\d{3,4}[ab]\d{1,2}\b",
    r"\b(?:DN|MN|SN|AN|Dhp|Snp)\s\d+(?:\.\d+)?\b",
    r"\b\d{1,3}[ab]\b(?=\s|,|\.|$)",
]
_SCAN_RES = [re.compile(p) for p in PROSE_LOCATOR_SCAN]


@dataclass(frozen=True)
class Failure:
    check: str  # 'a'..'i' or 'render'
    detail: str


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")


def _check_markers(body: AnswerBody, failures: list[Failure]) -> None:
    # (a) marker correspondence
    table_keys = list(body.citations.keys())
    appearance: list[str] = []
    quote_count: dict[str, int] = {}
    for p in body.perspectives:
        for point in p.reasoning:
            if point.marker is not None:
                if point.marker not in body.citations:
                    failures.append(Failure("a", f"reasoning marker {point.marker} not in table"))
                if point.marker not in appearance:
                    appearance.append(point.marker)
        for q in p.quotes:
            quote_count[q.marker] = quote_count.get(q.marker, 0) + 1
            if q.marker not in appearance:
                appearance.append(q.marker)
    if set(appearance) != set(table_keys):
        failures.append(Failure("a", "marker set != citation table keys"))
        return
    for key in table_keys:
        if quote_count.get(key, 0) != 1:
            failures.append(Failure("a", f"marker {key} has {quote_count.get(key, 0)} quotes"))
    expected = [f"C{i}" for i in range(1, len(appearance) + 1)]
    if appearance != expected or table_keys != expected:
        failures.append(Failure("a", "markers not C1..Cn in first-appearance order"))


def _check_quotes(
    body: AnswerBody, corpus: Corpus, null_path: bool, failures: list[Failure]
) -> None:
    # (b) quote fidelity, (c) locator & source line, (f) per-marker triple
    for p in body.perspectives:
        for q in p.quotes:
            passage = corpus.passage_by_id.get(q.passage_id)
            if passage is None:
                failures.append(Failure("f", f"{q.marker}: unknown passage {q.passage_id}"))
                continue
            source = corpus.source_by_id[passage.source_id]
            committed_is_para = passage.text is None
            committed_text = passage.paraphrase if committed_is_para else passage.text or ""
            if null_path:
                ok = q.text == committed_text
            else:
                ok = _nfc(q.text) == _nfc(committed_text or "")
            if not ok:
                failures.append(Failure("b", f"{q.marker}: quote text != passage {passage.id}"))
            if q.locator != passage.locator:
                failures.append(Failure("c", f"{q.marker}: locator mismatch"))
            if q.source_line != source.source_line:
                failures.append(Failure("c", f"{q.marker}: source line mismatch"))
            if q.is_paraphrase != committed_is_para:
                failures.append(Failure("f", f"{q.marker}: is_paraphrase flipped"))
            expected_label = PARAPHRASE_LABEL if committed_is_para else None
            if q.label != expected_label:
                failures.append(Failure("f", f"{q.marker}: paraphrase label wrong"))
            if q.context_note != passage.context_note:
                failures.append(Failure("i", f"{q.marker}: context note altered"))
            entry = body.citations.get(q.marker)
            if entry is not None and (
                entry.passage_id != passage.id
                or entry.locator != passage.locator
                or entry.source_id != passage.source_id
            ):
                failures.append(Failure("f", f"{q.marker}: citation table disagrees"))


def _check_safeguards(body: AnswerBody, corpus: Corpus, failures: list[Failure]) -> None:
    # (d) required safeguards present, byte-identical, first in render order
    topic = corpus.topic_by_id.get(body.routing.topic_id)
    if topic is None:
        failures.append(Failure("h", f"unknown topic {body.routing.topic_id}"))
        return
    expected = [corpus.safeguard_by_id[sid] for sid in topic.safeguard_ids]
    got = [(s.id, s.kind, s.text) for s in body.safeguards]
    want = [(s.id, s.kind, s.text) for s in expected]
    if got != want:
        failures.append(Failure("d", "safeguard block mismatch"))


def _check_perspectives(
    body: AnswerBody, corpus: Corpus, requested: list[str] | None, failures: list[Failure]
) -> None:
    canonical = [t.id for t in corpus.traditions]
    requested_set = set(requested) if requested is not None else set(canonical)
    have = {t for t in canonical if (body.routing.topic_id, t) in corpus.position_by_cell}
    expected_rendered = [t for t in canonical if t in requested_set and t in have]
    rendered = [p.tradition_id for p in body.perspectives]
    if rendered != expected_rendered:
        failures.append(Failure("h", "perspectives not canonical order over rendered set"))
    for p in body.perspectives:
        tradition = corpus.tradition_by_id.get(p.tradition_id)
        if tradition is None or p.tradition_name != tradition.name:
            failures.append(Failure("h", f"header mismatch for {p.tradition_id}"))
        position = corpus.position_by_cell.get((body.routing.topic_id, p.tradition_id))
        if position is None:
            failures.append(Failure("h", f"no position for {p.tradition_id}"))
            continue
        if p.position_id != position.id:
            failures.append(Failure("h", f"position id mismatch for {p.tradition_id}"))
        if p.stance != position.stance:
            failures.append(Failure("i", f"stance altered for {p.tradition_id}"))
        got_reading = [r.model_dump() for r in p.further_reading]
        want_reading = [r.model_dump() for r in position.further_reading]
        if got_reading != want_reading:
            failures.append(Failure("g", f"further reading altered for {p.tradition_id}"))
    # (i) structural immutables recomputed from the corpus
    expected_not_covered = [t for t in canonical if t in requested_set and t not in have]
    expected_filtered = [t for t in canonical if t in have and t not in requested_set]
    if body.not_covered != expected_not_covered:
        failures.append(Failure("i", "not_covered altered"))
    if body.filtered_out != expected_filtered:
        failures.append(Failure("i", "filtered_out altered"))
    agreement: dict[str, list[str]] = {}
    for p in body.perspectives:
        agreement.setdefault(p.stance.value, []).append(p.tradition_id)
    if {k: v for k, v in body.agreement_map.items()} != agreement:
        failures.append(Failure("i", "agreement map does not partition rendered set"))


def _changed_spans(pre: str, post: str) -> list[str]:
    """Deterministic token-level diff: spans of post not present in pre."""
    pre_tokens = pre.split()
    post_tokens = post.split()
    matcher = SequenceMatcher(a=pre_tokens, b=post_tokens, autojunk=False)
    spans: list[str] = []
    for op, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if op in ("replace", "insert") and j2 > j1:
            spans.append(" ".join(post_tokens[j1:j2]))
    return spans


def _check_polish_diff(pre_env: str, post_env: str, failures: list[Failure]) -> None:
    # (e) polish-introduced text + immutable-region byte equality
    pre_regions = env_mod.regions_of(pre_env)
    post_regions = env_mod.regions_of(post_env)
    pre_tags = [tag for tag, _, _ in pre_regions]
    post_tags = [tag for tag, _, _ in post_regions]
    if pre_tags != post_tags:
        failures.append(Failure("a", "envelope region structure altered"))
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
        if len(pre_payload) > 0:
            ratio = len(post_payload) / len(pre_payload)
            if ratio > 1.4 or ratio < 0.6:
                failures.append(Failure("e", f"mutable region {tag} length changed > ±40%"))
        for span in _changed_spans(pre_payload, post_payload):
            for pattern in _SCAN_RES:
                if pattern.search(span):
                    failures.append(
                        Failure("e", f"locator-shaped text introduced in {tag}: {span[:60]!r}")
                    )
                    break


def verify(
    body: AnswerBody,
    corpus: Corpus,
    rendered_text: str,
    requested: list[str] | None,
    pre_polish_envelope: str,
    polished_envelope: str | None = None,
) -> list[Failure]:
    """Run every FR-8 check; empty list means the answer is verified.

    `polished_envelope` is None on the NullPolisher path (byte-exact quote
    comparison applies); when set, check (e) diffs it against the pre-polish
    envelope and immutable regions must be byte-identical.
    """
    failures: list[Failure] = []
    null_path = polished_envelope is None
    _check_markers(body, failures)
    _check_quotes(body, corpus, null_path, failures)
    _check_safeguards(body, corpus, failures)
    _check_perspectives(body, corpus, requested, failures)
    if polished_envelope is not None:
        _check_polish_diff(pre_polish_envelope, polished_envelope, failures)
    # The render shown must be exactly the render of the verified body, and
    # the safeguard block must lead it (FR-10).
    titles = {t.id: t.title for t in corpus.topics}
    names = {t.id: t.name for t in corpus.traditions}
    expected_render = render_text(body, titles, names)
    if rendered_text != expected_render:
        failures.append(Failure("render", "rendered text != render of verified body"))
    topic = corpus.topic_by_id.get(body.routing.topic_id)
    if topic is not None:
        title = titles[topic.id]
        prefix = f"=== {title} ===\n\n" + "".join(f"[!] {s.text}\n" for s in body.safeguards)
        if not rendered_text.startswith(prefix):
            failures.append(Failure("d", "safeguard block not first in render order"))
    return failures

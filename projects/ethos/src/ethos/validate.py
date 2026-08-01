"""Corpus validation predicates (FR-1 layers 1-3; gates C1-C8, C10-C16).

Shared by `ethos corpus validate` and by evals/corpus_gates.py, so the CLI and
the eval suite enforce identical rules. Each check returns a list of failure
strings; empty means pass.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ethos.corpus import Corpus
from ethos.engine.normalize import normalize
from ethos.models import License, LocatorScheme, PassageRole

VALIDATION_REGEXES: dict[LocatorScheme, str] = {
    LocatorScheme.chapter_verse: r"^[1-3]?\s?[A-Za-z'’\- ]+ \d+[:.]\d+([–-]\d+)?$",
    LocatorScheme.book_section: r"^[A-Za-z ]+ [IVXLC]+\.\d+([–-]\d+)?$",
    LocatorScheme.part_question_article: r"^ST [I]+(-[I]+)?, Q\.\d+, art\.\d+$",
    LocatorScheme.bekker: r"^[A-Za-z ]+ [IVX]+\.\d+, \d{3,4}[ab]\d{1,2}$",
    LocatorScheme.stephanus: r"^[A-Za-z ]+ \d{1,3}[a-e]$",
    LocatorScheme.academy_ed: r"^[A-Za-z ]+ \d:\d{3}([–-]\d{3})?$",
    LocatorScheme.sutta_ref: r"^(DN|MN|SN|AN|Dhp|Snp) \d+(\.\d+)?([–-]\d+)?$",
    LocatorScheme.folio: r"^[A-Za-z ]+ \d+[ab]([–-]\d+[ab])?$",
    LocatorScheme.hadith_ref: r"^[A-Za-z\- ]+ \d+[a-z]?$",
    LocatorScheme.section: r"^[A-Za-z ]+ (ch\.)?\d+$",
}

QUOTABLE = {License.public_domain, License.cc0, License.cc_by}


def _iter_strings(obj: object, path: str = "$"):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_strings(v, f"{path}[{i}]")


def check_c1_byte_preserving(data_dir: Path, corpus: Corpus) -> list[str]:
    """C1: loader fidelity + sentinel/quote character constraints.

    Compares every string field of every loaded corpus record against the raw
    JSON string value read with stdlib json (no model layer in between).
    """
    errors: list[str] = []
    loaded_strings: set[str] = set()
    for records, _name in (
        (corpus.traditions, "tradition"),
        (corpus.topics, "topic"),
        (corpus.sources, "source"),
        (corpus.passages, "passage"),
        (corpus.positions, "position"),
    ):
        for record in records:
            for _path, value in _iter_strings(record.model_dump(mode="json")):
                loaded_strings.add(value)
    raw_files = [
        data_dir / "corpus" / "traditions.json",
        data_dir / "corpus" / "topics.json",
        data_dir / "corpus" / "sources.json",
        *sorted((data_dir / "corpus" / "passages").glob("*.json")),
        *sorted((data_dir / "corpus" / "positions").glob("*.json")),
    ]
    for file in raw_files:
        raw = json.loads(file.read_text(encoding="utf-8"))
        for path, value in _iter_strings(raw):
            if "[[" in value or "]]" in value:
                errors.append(f"C1: envelope sentinel in {file.name}{path}")
            if value not in loaded_strings:
                errors.append(f"C1: loader altered string at {file.name}{path}: {value[:40]!r}")
    for passage in corpus.passages:
        for field_name in ("text", "paraphrase"):
            value = getattr(passage, field_name)
            if value and ("\u201c" in value or "\u201d" in value):
                errors.append(f"C1: curly double quote in passage {passage.id}.{field_name}")
    return errors


def check_c2_unique_cells(corpus: Corpus) -> list[str]:
    seen: set[tuple[str, str]] = set()
    errors = []
    for position in corpus.positions:
        cell = (position.topic_id, position.tradition_id)
        if cell in seen:
            errors.append(f"C2: duplicate position for {cell}")
        seen.add(cell)
    return errors


def check_c3_core_and_reading(corpus: Corpus) -> list[str]:
    errors = []
    for position in corpus.positions:
        if not any(ref.role == PassageRole.core for ref in position.passages):
            errors.append(f"C3: {position.id} has no core passage ref")
        if not position.further_reading:
            errors.append(f"C3: {position.id} has no further reading")
    return errors


def check_c4_quotable_sources(corpus: Corpus) -> list[str]:
    errors = []
    for passage in corpus.passages:
        if passage.text is None:
            continue
        source = corpus.source_by_id.get(passage.source_id)
        if source is None:
            continue
        if source.license not in QUOTABLE:
            errors.append(f"C4: quoted passage {passage.id} from non-quotable source")
        named = source.translator or source.author  # English originals: author suffices
        if not named or not source.translation_year or not source.url:
            errors.append(f"C4: source {source.id} missing translator-or-author/year/url")
    return errors


def check_c5_locators(corpus: Corpus) -> list[str]:
    errors = []
    for passage in corpus.passages:
        source = corpus.source_by_id.get(passage.source_id)
        if source is None:
            continue
        pattern = VALIDATION_REGEXES[source.locator_scheme]
        if not re.match(pattern, passage.locator):
            errors.append(
                f"C5: locator {passage.locator!r} of {passage.id} violates {source.locator_scheme}"
            )
    return errors


def check_c6_quotes(corpus: Corpus) -> list[str]:
    errors = []
    for passage in corpus.passages:
        source = corpus.source_by_id.get(passage.source_id)
        if passage.text is not None:
            if len(passage.text.split()) > 90:
                errors.append(f"C6: quote {passage.id} exceeds 90 words")
            if not passage.transcription_checked:
                errors.append(f"C6: quote {passage.id} not transcription_checked")
            if passage.paraphrase is not None:
                errors.append(f"C6: {passage.id} has both text and paraphrase")
        else:
            if passage.paraphrase is None:
                errors.append(f"C6: {passage.id} has neither text nor paraphrase")
            if source is not None and source.license != License.reference_only:
                errors.append(f"C6: paraphrase-only {passage.id} not from reference_only source")
    for passage in corpus.passages:
        source = corpus.source_by_id.get(passage.source_id)
        if source is not None and source.license == License.reference_only:
            if passage.text is not None:
                errors.append(f"C6: reference_only source quoted in {passage.id}")
    return errors


def check_c7_floors(corpus: Corpus) -> list[str]:
    errors = []
    if len(corpus.traditions) != 10:
        errors.append(f"C7: {len(corpus.traditions)} traditions, need exactly 10")
    if len(corpus.topics) < 24:
        errors.append(f"C7: {len(corpus.topics)} topics, need >= 24")
    per_topic: dict[str, int] = {t.id: 0 for t in corpus.topics}
    per_tradition: dict[str, int] = {t.id: 0 for t in corpus.traditions}
    for position in corpus.positions:
        per_topic[position.topic_id] = per_topic.get(position.topic_id, 0) + 1
        per_tradition[position.tradition_id] = per_tradition.get(position.tradition_id, 0) + 1
    for topic_id, count in per_topic.items():
        if count < 6:
            errors.append(f"C7: topic {topic_id} has {count} positions, need >= 6")
    for tradition_id, count in per_tradition.items():
        if count < 12:
            errors.append(f"C7: tradition {tradition_id} has {count} positions, need >= 12")
    return errors


def check_c8_crossrefs(corpus: Corpus) -> list[str]:
    errors = []
    for passage in corpus.passages:
        if passage.source_id not in corpus.source_by_id:
            errors.append(f"C8: passage {passage.id} -> missing source {passage.source_id}")
    for position in corpus.positions:
        if position.topic_id not in corpus.topic_by_id:
            errors.append(f"C8: position {position.id} -> missing topic {position.topic_id}")
        if position.tradition_id not in corpus.tradition_by_id:
            errors.append(f"C8: position {position.id} -> missing tradition")
        for ref in position.passages:
            if ref.passage_id not in corpus.passage_by_id:
                errors.append(f"C8: position {position.id} -> missing passage {ref.passage_id}")
        for point in position.reasoning:
            if point.passage_id is not None and point.passage_id not in corpus.passage_by_id:
                errors.append(f"C8: reasoning in {position.id} -> missing {point.passage_id}")
    for topic in corpus.topics:
        for sid in topic.safeguard_ids:
            if sid not in corpus.safeguard_by_id:
                errors.append(f"C8: topic {topic.id} -> missing safeguard {sid}")
        for rid in topic.related_topics:
            if rid == topic.id or rid not in corpus.topic_by_id:
                errors.append(f"C8: topic {topic.id} -> bad related topic {rid}")
    return errors


def check_c10_misc(corpus: Corpus) -> list[str]:
    errors = []
    orders = sorted(t.order for t in corpus.traditions)
    if orders != list(range(1, len(corpus.traditions) + 1)):
        errors.append("C10: Tradition.order is not 1..10 without gaps")
    for topic in corpus.topics:
        if not topic.question_forms:
            errors.append(f"C10: topic {topic.id} has no question_forms")
        for kw in topic.keywords:
            if not normalize(kw.term, corpus.stopwords):
                errors.append(f"C10: keyword {kw.term!r} of {topic.id} is all stopwords")
            if not 1 <= kw.weight <= 3:
                errors.append(f"C10: keyword {kw.term!r} of {topic.id} weight out of range")
        if topic.sensitive and "crisis_resources" not in topic.safeguard_ids:
            errors.append(f"C10: sensitive topic {topic.id} lacks crisis_resources")
    return errors


def check_c11_passage_reuse(corpus: Corpus) -> list[str]:
    errors = []
    core_uses: dict[str, int] = {}
    cited: set[str] = set()
    for position in corpus.positions:
        for ref in position.passages:
            cited.add(ref.passage_id)
            if ref.role == PassageRole.core:
                core_uses[ref.passage_id] = core_uses.get(ref.passage_id, 0) + 1
    for passage_id, uses in sorted(core_uses.items()):
        if uses > 3:
            errors.append(f"C11: passage {passage_id} is core in {uses} positions (max 3)")
    floor = 6 * len(corpus.topics)
    if len(cited) < floor:
        errors.append(f"C11: {len(cited)} distinct cited passages, need >= {floor}")
    return errors


def check_c12_stance_diversity(corpus: Corpus, near_unanimous: list[str]) -> list[str]:
    errors = []
    if len(near_unanimous) > 2:
        errors.append("C12: more than 2 allow-listed near-unanimous topics")
    stances: dict[str, set[str]] = {t.id: set() for t in corpus.topics}
    for position in corpus.positions:
        stances.setdefault(position.topic_id, set()).add(position.stance.value)
    for topic_id, stance_set in sorted(stances.items()):
        floor = 2 if topic_id in near_unanimous else 3
        if len(stance_set) < floor:
            errors.append(f"C12: topic {topic_id} has {len(stance_set)} stances, need >= {floor}")
    return errors


def _stem_tokens(text: str, corpus: Corpus) -> set[str]:
    return set(normalize(text, corpus.stopwords))


def check_c13_summary_distinctness(corpus: Corpus) -> list[str]:
    errors = []
    tokens = {p.id: _stem_tokens(p.summary, corpus) for p in corpus.positions}
    by_topic: dict[str, list] = {}
    for position in corpus.positions:
        by_topic.setdefault(position.topic_id, []).append(position)
    for topic_id, positions in sorted(by_topic.items()):
        for i, a in enumerate(positions):
            for b in positions[i + 1 :]:
                ta, tb = tokens[a.id], tokens[b.id]
                union = ta | tb
                jaccard = (len(ta & tb) / len(union)) if union else 0.0
                if jaccard >= 0.5:
                    errors.append(
                        f"C13: summaries {a.id} / {b.id} Jaccard {jaccard:.2f} >= 0.5"
                    )
    all_positions = corpus.positions
    for i, a in enumerate(all_positions):
        for b in all_positions[i + 1 :]:
            ta, tb = tokens[a.id], tokens[b.id]
            smaller = min(len(ta), len(tb))
            if smaller and len(ta & tb) / smaller > 0.7:
                errors.append(f"C13: summaries {a.id} / {b.id} share > 70% of stemmed tokens")
    return errors


def check_c14_complicating(corpus: Corpus) -> list[str]:
    errors = []
    with_complicating = sum(
        1
        for p in corpus.positions
        if any(ref.role == PassageRole.complicating for ref in p.passages)
    )
    if corpus.positions and with_complicating / len(corpus.positions) < 0.30:
        errors.append(
            f"C14: only {with_complicating}/{len(corpus.positions)} positions carry a"
            " complicating passage (need >= 30%)"
        )
    safeguard_topics = {t.id for t in corpus.topics if t.safeguard_ids}
    for position in corpus.positions:
        if position.topic_id in safeguard_topics:
            has_complicating = any(
                ref.role == PassageRole.complicating for ref in position.passages
            )
            if not has_complicating and position.intra_tradition_note is None:
                errors.append(
                    f"C14: {position.id} on safeguard topic lacks complicating ref and note"
                )
    return errors


def check_c15_grounding(corpus: Corpus) -> list[str]:
    errors = []
    quoted_core = 0
    ref_only = 0
    total_refs = 0
    for position in corpus.positions:
        ok = False
        for ref in position.passages:
            total_refs += 1
            passage = corpus.passage_by_id.get(ref.passage_id)
            if passage is None:
                continue
            if passage.text is None:
                ref_only += 1
            elif ref.role == PassageRole.core:
                ok = True
        quoted_core += ok
    if corpus.positions and quoted_core / len(corpus.positions) < 0.80:
        errors.append(f"C15: only {quoted_core}/{len(corpus.positions)} positions have a"
                      " quoted core passage (need >= 80%)")
    if total_refs and ref_only / total_refs > 0.15:
        errors.append(f"C15: reference_only refs are {ref_only}/{total_refs} (> 15%)")
    return errors


def check_c16_reading_breadth(corpus: Corpus) -> list[str]:
    errors = []
    counts: dict[tuple, int] = {}
    for position in corpus.positions:
        for entry in position.further_reading:
            key = (entry.author.casefold(), entry.title.casefold(), entry.year)
            counts[key] = counts.get(key, 0) + 1
    if len(counts) < 120:
        errors.append(f"C16: {len(counts)} distinct further-reading entries, need >= 120")
    if corpus.positions:
        cap = 0.10 * len(corpus.positions)
        for key, uses in sorted(counts.items()):
            if uses > cap:
                errors.append(f"C16: reading entry {key[1]!r} used by {uses} positions (> 10%)")
    return errors


ALL_CHECKS = (
    ("C2", check_c2_unique_cells),
    ("C3", check_c3_core_and_reading),
    ("C4", check_c4_quotable_sources),
    ("C5", check_c5_locators),
    ("C6", check_c6_quotes),
    ("C7", check_c7_floors),
    ("C8", check_c8_crossrefs),
    ("C10", check_c10_misc),
    ("C11", check_c11_passage_reuse),
    ("C14", check_c14_complicating),
    ("C15", check_c15_grounding),
    ("C16", check_c16_reading_breadth),
)


def validate_corpus(
    data_dir: Path, corpus: Corpus, near_unanimous: list[str] | None = None
) -> dict[str, list[str]]:
    """Run every corpus gate; returns {gate: [failures]} (empty lists = pass)."""
    results: dict[str, list[str]] = {}
    results["C1"] = check_c1_byte_preserving(data_dir, corpus)
    for gate, fn in ALL_CHECKS:
        results[gate] = fn(corpus)
    results["C12"] = check_c12_stance_diversity(corpus, near_unanimous or [])
    results["C13"] = check_c13_summary_distinctness(corpus)
    return dict(sorted(results.items(), key=lambda kv: (len(kv[0]), kv[0])))

"""FR-1: byte-preserving corpus parsing and the C1-C20 gate predicates.

This module is **pure** (CONVENTIONS layering, gated by
`test_fr15_engine_has_no_io_imports`): it never touches the filesystem. Reading
`data/` is `ethos.corpus.read_raw`, which hands the untouched stdlib-`json`
objects here as a :class:`RawCorpus`; :func:`parse_corpus` turns them into
Pydantic records without normalizing a single character (FR-1 layer 2), and
:func:`validate_corpus` runs every gate against the parsed records *and* the
raw objects side by side.

Every check returns a list of failure strings; an empty list means pass. The
same predicates back `ethos corpus validate` and `evals/corpus_gates.py`, so
the CLI and the eval suite can never diverge.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ethos.engine.normalize import normalize
from ethos.models import (
    License,
    LocatorScheme,
    Passage,
    PassageRole,
    Position,
    RouterConfig,
    Safeguard,
    Source,
    Topic,
    Tradition,
)

# (1) Anchored validation patterns — DATA_MODEL § Locator regexes, set 1 (C5).
VALIDATION_REGEXES: dict[LocatorScheme, str] = {
    # The en-dashes below are load-bearing: real locators print ranges with
    # U+2013 (as in "Ketubot 16b to 17a"), and folding one would make a citation
    # disagree with the printed edition (DATA_MODEL § Loader contract).
    LocatorScheme.chapter_verse: r"^[1-3]?\s?[A-Za-z'’\- ]+ \d+[:.]\d+([–-]\d+)?$",  # noqa: RUF001
    LocatorScheme.book_section: r"^[A-Za-z ]+ [IVXLC]+\.\d+([–-]\d+)?$",  # noqa: RUF001
    LocatorScheme.part_question_article: r"^ST [I]+(-[I]+)?, Q\.\d+, art\.\d+$",
    LocatorScheme.bekker: r"^[A-Za-z ]+ [IVX]+\.\d+, \d{3,4}[ab]\d{1,2}$",
    LocatorScheme.stephanus: r"^[A-Za-z ]+ \d{1,3}[a-e]$",
    # Academy pagination is 2-3 digits: Critique of Practical Reason 5:27 is a
    # real two-digit page, so the doc's \d{3} is widened to \d{2,3} (recorded
    # deviation; the anchoring and the rest of the shape are unchanged).
    LocatorScheme.academy_ed: r"^[A-Za-z ]+ \d:\d{2,3}([–-]\d{2,3})?$",  # noqa: RUF001
    LocatorScheme.sutta_ref: r"^(DN|MN|SN|AN|Dhp|Snp) \d+(\.\d+)?([–-]\d+)?$",  # noqa: RUF001
    LocatorScheme.folio: r"^[A-Za-z ]+ \d+[ab]([–-]\d+[ab])?$",  # noqa: RUF001
    LocatorScheme.hadith_ref: r"^[A-Za-z\- ]+ \d+[a-z]?$",
    LocatorScheme.section: r"^[A-Za-z ]+ (ch\.)?\d+$",
}

QUOTABLE = frozenset({License.public_domain, License.cc0, License.cc_by})

MAX_QUOTE_WORDS = 90
MAX_CORE_REUSE = 3


@dataclass(frozen=True)
class RawCorpus:
    """Untouched stdlib-json objects, keyed by the file they came from."""

    traditions: list[dict[str, Any]]
    topics: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    passage_files: dict[str, list[dict[str, Any]]]
    position_files: dict[str, list[dict[str, Any]]]
    safeguards: list[dict[str, Any]]
    router: dict[str, Any]
    stopword_lines: list[str]
    corpus_version: str

    @property
    def passages(self) -> list[dict[str, Any]]:
        return [r for name in sorted(self.passage_files) for r in self.passage_files[name]]

    @property
    def positions(self) -> list[dict[str, Any]]:
        return [r for name in sorted(self.position_files) for r in self.position_files[name]]


@dataclass
class Corpus:
    traditions: list[Tradition]
    topics: list[Topic]
    sources: list[Source]
    passages: list[Passage]
    positions: list[Position]
    safeguards: list[Safeguard]
    router_config: RouterConfig
    stopwords: frozenset[str]
    corpus_version: str
    raw: RawCorpus | None = None
    tradition_by_id: dict[str, Tradition] = field(init=False)
    topic_by_id: dict[str, Topic] = field(init=False)
    source_by_id: dict[str, Source] = field(init=False)
    passage_by_id: dict[str, Passage] = field(init=False)
    safeguard_by_id: dict[str, Safeguard] = field(init=False)
    position_by_cell: dict[tuple[str, str], Position] = field(init=False)

    def __post_init__(self) -> None:
        self.tradition_by_id = {t.id: t for t in self.traditions}
        self.topic_by_id = {t.id: t for t in self.topics}
        self.source_by_id = {s.id: s for s in self.sources}
        self.passage_by_id = {p.id: p for p in self.passages}
        self.safeguard_by_id = {s.id: s for s in self.safeguards}
        self.position_by_cell = {(p.topic_id, p.tradition_id): p for p in self.positions}

    def positions_for_topic(self, topic_id: str) -> list[Position]:
        """Positions on a topic, in canonical Tradition.order (FR-5)."""
        order = {t.id: t.order for t in self.traditions}
        found = [p for p in self.positions if p.topic_id == topic_id]
        return sorted(found, key=lambda p: (order.get(p.tradition_id, 99), p.tradition_id))


def parse_corpus(raw: RawCorpus) -> Corpus:
    """Raw json objects -> Pydantic records. Byte-preserving by construction:
    strings are handed to the models unmodified and no model normalizes."""
    return Corpus(
        traditions=sorted((Tradition(**r) for r in raw.traditions), key=lambda t: t.order),
        topics=sorted((Topic(**r) for r in raw.topics), key=lambda t: t.id),
        sources=[Source(**r) for r in raw.sources],
        passages=[Passage(**r) for r in raw.passages],
        positions=[Position(**r) for r in raw.positions],
        safeguards=[Safeguard(**r) for r in raw.safeguards],
        router_config=RouterConfig(**raw.router),
        stopwords=frozenset(line.strip() for line in raw.stopword_lines if line.strip()),
        corpus_version=raw.corpus_version,
        raw=raw,
    )


# --- C1: loader fidelity + sentinel / delimiter constraints ------------------


def _walk_strings(obj: object, path: str = ""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key in obj:
            yield from _walk_strings(obj[key], f"{path}.{key}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_strings(item, f"{path}[{i}]")


def _dumped(records: list) -> dict[str, dict]:
    return {r.id: r.model_dump(mode="json") for r in records}


def check_c1_byte_preserving(corpus: Corpus) -> list[str]:
    """C1: for every string field of every record, the loaded value byte-equals
    the raw JSON string; plus no `[[`/`]]` anywhere and no `“`/`”` in passage
    text or paraphrase (DATA_MODEL § Loader contract).

    The comparison is per record and per field path — not set membership, which
    would pass a loader that swapped two records' locators.
    """
    errors: list[str] = []
    raw = corpus.raw
    if raw is None:
        return ["C1: corpus was not parsed from raw json (no fidelity evidence)"]
    groups = (
        ("tradition", raw.traditions, _dumped(corpus.traditions)),
        ("topic", raw.topics, _dumped(corpus.topics)),
        ("source", raw.sources, _dumped(corpus.sources)),
        ("passage", raw.passages, _dumped(corpus.passages)),
        ("position", raw.positions, _dumped(corpus.positions)),
    )
    for kind, raw_records, loaded in groups:
        for raw_record in raw_records:
            rid = raw_record.get("id")
            got = loaded.get(rid)
            if got is None:
                errors.append(f"C1: {kind} {rid!r} present in files but not loaded")
                continue
            loaded_strings = dict(_walk_strings(got))
            for path, value in _walk_strings(raw_record):
                if "[[" in value or "]]" in value:
                    errors.append(f"C1: envelope sentinel in {kind} {rid}{path}")
                if loaded_strings.get(path) != value:
                    errors.append(
                        f"C1: loader altered {kind} {rid}{path}: "
                        f"{value[:32]!r} -> {loaded_strings.get(path, '<missing>')!r}"
                    )
    for safeguard in corpus.safeguards:
        if "[[" in safeguard.text or "]]" in safeguard.text:
            errors.append(f"C1: envelope sentinel in safeguard {safeguard.id}")
    for passage in corpus.passages:
        for field_name in ("text", "paraphrase"):
            value = getattr(passage, field_name)
            if value and ("“" in value or "”" in value):
                errors.append(f"C1: curly double quote in passage {passage.id}.{field_name}")
    return errors


# --- C2-C8, C10: structural gates (FR-1 layer 1) -----------------------------


def check_c2_unique_cells(corpus: Corpus) -> list[str]:
    seen: set[tuple[str, str]] = set()
    errors: list[str] = []
    for position in corpus.positions:
        cell = (position.topic_id, position.tradition_id)
        if cell in seen:
            errors.append(f"C2: duplicate position for {cell}")
        seen.add(cell)
    return errors


def check_c3_core_and_reading(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    for position in corpus.positions:
        if not any(ref.role == PassageRole.core for ref in position.passages):
            errors.append(f"C3: {position.id} has no core passage ref")
        if not position.further_reading:
            errors.append(f"C3: {position.id} has no further reading")
    return errors


def check_c4_quotable_sources(corpus: Corpus) -> list[str]:
    """C4: a quoted passage's source must be quotable, attributed, dated, linked.

    Deviation from the doc's wording, recorded deliberately: the corpus quotes
    several works written *in* English (Mill, Bentham), which have an author
    and no translator. The rule enforced is "named translator, or named author
    for an English original" — a null attribution is still a failure.
    """
    errors: list[str] = []
    for passage in corpus.passages:
        if passage.text is None:
            continue
        source = corpus.source_by_id.get(passage.source_id)
        if source is None:
            continue
        if source.license not in QUOTABLE:
            errors.append(f"C4: quoted passage {passage.id} from non-quotable source")
        if not (source.translator or source.author):
            errors.append(f"C4: source {source.id} has neither translator nor author")
        if source.translation_year is None:
            errors.append(f"C4: source {source.id} has no translation year")
        if not source.url:
            errors.append(f"C4: source {source.id} has no url")
    return errors


def check_c5_locators(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    for passage in corpus.passages:
        source = corpus.source_by_id.get(passage.source_id)
        if source is None:
            continue
        pattern = VALIDATION_REGEXES[source.locator_scheme]
        if not re.match(pattern, passage.locator):
            errors.append(
                f"C5: locator {passage.locator!r} of {passage.id} "
                f"violates scheme {source.locator_scheme.value}"
            )
    return errors


def check_c6_quotes(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    for passage in corpus.passages:
        source = corpus.source_by_id.get(passage.source_id)
        if passage.text is not None:
            if len(passage.text.split()) > MAX_QUOTE_WORDS:
                errors.append(f"C6: quote {passage.id} exceeds {MAX_QUOTE_WORDS} words")
            if not passage.transcription_checked:
                errors.append(f"C6: quote {passage.id} is not transcription_checked")
            if source is not None and source.license == License.reference_only:
                errors.append(f"C6: reference_only source quoted in {passage.id}")
        else:
            if passage.paraphrase is None:
                errors.append(f"C6: {passage.id} has neither text nor paraphrase")
            if source is not None and source.license != License.reference_only:
                errors.append(f"C6: paraphrase-only {passage.id} is not from a reference_only source")
    return errors


def check_c7_floors(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    if len(corpus.traditions) != 10:
        errors.append(f"C7: {len(corpus.traditions)} traditions, need exactly 10")
    if len(corpus.topics) < 24:
        errors.append(f"C7: {len(corpus.topics)} topics, need >= 24")
    per_topic: dict[str, int] = {t.id: 0 for t in corpus.topics}
    per_tradition: dict[str, int] = {t.id: 0 for t in corpus.traditions}
    for position in corpus.positions:
        per_topic[position.topic_id] = per_topic.get(position.topic_id, 0) + 1
        per_tradition[position.tradition_id] = per_tradition.get(position.tradition_id, 0) + 1
    for topic_id, count in sorted(per_topic.items()):
        if count < 6:
            errors.append(f"C7: topic {topic_id} has {count} positions, need >= 6")
    for tradition_id, count in sorted(per_tradition.items()):
        if count < 12:
            errors.append(f"C7: tradition {tradition_id} has {count} positions, need >= 12")
    return errors


def check_c8_crossrefs(corpus: Corpus) -> list[str]:
    errors: list[str] = []
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
    seen: set[str] = set()
    for passage in corpus.passages:
        if passage.id in seen:
            errors.append(f"C8: duplicate passage id {passage.id}")
        seen.add(passage.id)
    return errors


def check_c10_misc(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    orders = sorted(t.order for t in corpus.traditions)
    if orders != list(range(1, len(corpus.traditions) + 1)):
        errors.append("C10: Tradition.order is not 1..n without gaps")
    for topic in corpus.topics:
        if not topic.question_forms:
            errors.append(f"C10: topic {topic.id} has no question_forms")
        for kw in topic.keywords:
            if not normalize(kw.term, corpus.stopwords):
                errors.append(f"C10: keyword {kw.term!r} of {topic.id} is all stopwords")
        if topic.sensitive and "crisis_resources" not in topic.safeguard_ids:
            errors.append(f"C10: sensitive topic {topic.id} lacks crisis_resources")
    crisis = corpus.safeguard_by_id.get("crisis_resources")
    if crisis is None:
        errors.append("C10: no crisis_resources safeguard")
    else:
        for needle in ("988", "befrienders.org", "findahelpline.com"):
            if needle not in crisis.text:
                errors.append(f"C10: crisis_resources text does not name {needle}")
    return errors


# --- C11-C16: substance gates (FR-1 layer 3) ---------------------------------


def corpus_stats(corpus: Corpus) -> dict[str, float]:
    """The substance ratios C11-C16 gate; also printed by `corpus stats`."""
    core_uses: dict[str, int] = {}
    cited: set[str] = set()
    for position in corpus.positions:
        for ref in position.passages:
            cited.add(ref.passage_id)
            if ref.role == PassageRole.core:
                core_uses[ref.passage_id] = core_uses.get(ref.passage_id, 0) + 1
    n = len(corpus.positions) or 1
    complicating = sum(
        1 for p in corpus.positions
        if any(r.role == PassageRole.complicating for r in p.passages)
    )
    quoted_core = sum(
        1 for p in corpus.positions
        if any(
            r.role == PassageRole.core
            and (corpus.passage_by_id.get(r.passage_id) is not None)
            and corpus.passage_by_id[r.passage_id].text is not None
            for r in p.passages
        )
    )
    ref_only = sum(
        1 for pid in cited
        if (pas := corpus.passage_by_id.get(pid)) is not None and pas.text is None
    )
    reading = reading_entry_counts(corpus)
    return {
        "positions": float(len(corpus.positions)),
        "distinct_cited_passages": float(len(cited)),
        "max_core_reuse": float(max(core_uses.values()) if core_uses else 0),
        "complicating_share": complicating / n,
        "quoted_core_share": quoted_core / n,
        "reference_only_share": (ref_only / len(cited)) if cited else 0.0,
        "distinct_reading_entries": float(len(reading)),
        "max_reading_share": (max(reading.values()) / n) if reading else 0.0,
    }


def reading_entry_counts(corpus: Corpus) -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    for position in corpus.positions:
        for entry in position.further_reading:
            key = (entry.author.casefold(), entry.title.casefold(), str(entry.year))
            counts[key] = counts.get(key, 0) + 1
    return counts


def check_c11_passage_reuse(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    core_uses: dict[str, int] = {}
    cited: set[str] = set()
    for position in corpus.positions:
        for ref in position.passages:
            cited.add(ref.passage_id)
            if ref.role == PassageRole.core:
                core_uses[ref.passage_id] = core_uses.get(ref.passage_id, 0) + 1
    for passage_id, uses in sorted(core_uses.items()):
        if uses > MAX_CORE_REUSE:
            errors.append(f"C11: passage {passage_id} is core in {uses} positions (max 3)")
    floor = 6 * len(corpus.topics)
    if len(cited) < floor:
        errors.append(f"C11: {len(cited)} distinct cited passages, need >= {floor}")
    return errors


def check_c12_stance_diversity(corpus: Corpus, near_unanimous: list[str]) -> list[str]:
    errors: list[str] = []
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


def check_c13_summary_distinctness(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    tokens = {p.id: set(normalize(p.summary, corpus.stopwords)) for p in corpus.positions}
    by_topic: dict[str, list[Position]] = {}
    for position in corpus.positions:
        by_topic.setdefault(position.topic_id, []).append(position)
    for _topic_id, group in sorted(by_topic.items()):
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                ta, tb = tokens[a.id], tokens[b.id]
                union = ta | tb
                jaccard = (len(ta & tb) / len(union)) if union else 0.0
                if jaccard >= 0.5:
                    errors.append(f"C13: summaries {a.id} / {b.id} Jaccard {jaccard:.2f} >= 0.5")
    ordered = corpus.positions
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            ta, tb = tokens[a.id], tokens[b.id]
            smaller = min(len(ta), len(tb))
            if smaller and len(ta & tb) / smaller > 0.7:
                errors.append(f"C13: summaries {a.id} / {b.id} share > 70% of stemmed tokens")
    return errors


def check_c14_complicating(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    stats = corpus_stats(corpus)
    if corpus.positions and stats["complicating_share"] < 0.30:
        errors.append(
            f"C14: only {stats['complicating_share']:.3f} of positions carry a"
            " complicating passage (need >= 0.30)"
        )
    safeguard_topics = {t.id for t in corpus.topics if t.safeguard_ids}
    for position in corpus.positions:
        if position.topic_id not in safeguard_topics:
            continue
        has_complicating = any(r.role == PassageRole.complicating for r in position.passages)
        if not has_complicating and position.intra_tradition_note is None:
            errors.append(
                f"C14: {position.id} is on a safeguard topic but carries neither a"
                " complicating ref nor an intra_tradition_note"
            )
    return errors


def check_c15_grounding(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    stats = corpus_stats(corpus)
    if corpus.positions and stats["quoted_core_share"] < 0.80:
        errors.append(
            f"C15: only {stats['quoted_core_share']:.3f} of positions rest on a quoted"
            " core passage (need >= 0.80)"
        )
    if stats["reference_only_share"] > 0.15:
        errors.append(
            f"C15: reference_only passages are {stats['reference_only_share']:.3f}"
            " of cited passages (> 0.15)"
        )
    return errors


def check_c16_reading_breadth(corpus: Corpus) -> list[str]:
    errors: list[str] = []
    counts = reading_entry_counts(corpus)
    if len(counts) < 120:
        errors.append(f"C16: {len(counts)} distinct further-reading entries, need >= 120")
    if corpus.positions:
        cap = 0.10 * len(corpus.positions)
        for key, uses in sorted(counts.items()):
            if uses > cap:
                errors.append(f"C16: reading entry {key[1]!r} used by {uses} positions (> 10%)")
    return errors


# --- C17-C20: gates whose evidence comes from outside the corpus -------------
# Kept here (pure predicates over data the caller supplies) so `ethos corpus
# validate` and evals/corpus_gates.py share one implementation.


def topic_documents(corpus: Corpus) -> dict[str, set[str]]:
    """Stemmed token sets of each topic document, for C9's distinctive-token
    rule. Weight-3 keyword terms are included; weight-1/2 terms are not — the
    scenario-vocabulary channel is deliberately left open (EVALS § C9)."""
    docs: dict[str, set[str]] = {}
    for topic in corpus.topics:
        tokens = set(normalize(topic.title, corpus.stopwords))
        tokens |= set(normalize(topic.description, corpus.stopwords))
        for form in topic.question_forms:
            tokens |= set(normalize(form, corpus.stopwords))
        for keyword in topic.keywords:
            if keyword.weight >= 3:
                tokens |= set(normalize(keyword.term, corpus.stopwords))
        docs[topic.id] = tokens
    return docs


def check_c9_fixture_questions(
    corpus: Corpus, questions: list[dict[str, Any]], oblique_tiers: tuple[str, ...] = ("oblique",)
) -> list[str]:
    """C9: fixture lexical integrity.

    * every truth topic exists;
    * an oblique question may share no *distinctive* token (topic-document
      frequency <= 3) with its own topic's title/description/forms/weight-3
      keywords;
    * a colloquial question's stemmed Jaccard against each of its topic's
      question forms is < 0.5.
    """
    errors: list[str] = []
    docs = topic_documents(corpus)
    df: dict[str, int] = {}
    for tokens in docs.values():
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    for question in questions:
        truth = question.get("truth_topic") or (question.get("truth_topics") or [None])[0]
        qid = question.get("id", question.get("text", "?"))
        if truth not in corpus.topic_by_id:
            errors.append(f"C9: {qid} names unknown topic {truth!r}")
            continue
        tokens = set(normalize(question["text"], corpus.stopwords))
        tier = question.get("tier", "")
        if tier in oblique_tiers:
            leaked = {t for t in tokens & docs[truth] if df.get(t, 0) <= 3}
            if leaked:
                errors.append(f"C9: oblique {qid} reuses distinctive tokens {sorted(leaked)}")
        if tier == "colloquial":
            for form in corpus.topic_by_id[truth].question_forms:
                form_tokens = set(normalize(form, corpus.stopwords))
                union = tokens | form_tokens
                jaccard = len(tokens & form_tokens) / len(union) if union else 0.0
                if jaccard >= 0.5:
                    errors.append(f"C9: colloquial {qid} restates a question form ({jaccard:.2f})")
    return errors


def check_c19_baselines(
    recorded: dict[str, Any], current: dict[str, Any], tolerance: float = 0.02
) -> list[str]:
    """C19: committed hashes and measured baselines are still current."""
    errors: list[str] = []
    for key in sorted(set(recorded) | set(current)):
        if key not in recorded:
            errors.append(f"C19: {key} is not recorded in baselines.json")
            continue
        if key not in current:
            errors.append(f"C19: {key} is recorded but no longer measured")
            continue
        was, now = recorded[key], current[key]
        if isinstance(was, (int, float)) and isinstance(now, (int, float)):
            if abs(float(was) - float(now)) > tolerance:
                errors.append(f"C19: {key} moved {was} -> {now} (> ±{tolerance})")
        elif was != now:
            errors.append(f"C19: {key} changed; re-derive baselines in this commit")
    return errors


def check_c17_safeguard_cells(cells: list[dict[str, Any]]) -> list[str]:
    """C17: every rendered cell of the safeguard matrix must carry the topic's
    declared blocks, byte-identical and first. Each cell is
    {cell, expected: [{id, kind, text}], got: [...], first: bool}."""
    errors: list[str] = []
    for cell in cells:
        name = cell.get("cell", "?")
        expected = cell.get("expected") or []
        got = cell.get("got") or []
        if not got:
            errors.append(f"C17: {name} rendered no safeguard block")
        if got != expected:
            errors.append(f"C17: {name} safeguard block differs from data/safeguards.json")
        if not cell.get("first", False):
            errors.append(f"C17: {name} safeguard block is not first in render order")
    return errors


def check_c18_stemmer(pairs: list[tuple[str, str]], stem) -> list[str]:
    """C18: stem(voc[i]) == output[i] for every committed Porter pair."""
    errors: list[str] = []
    for word, expected in pairs:
        got = stem(word)
        if got != expected:
            errors.append(f"C18: stem({word!r}) == {got!r}, expected {expected!r}")
    return errors


def check_c20_oos_composition(oos_s1: list[float], direct_s1: list[float]) -> list[str]:
    """C20: >= 25 out-of-scope questions must out-score the median in-scope
    direct question, so the null `abstain iff s1 = 0` rule cannot pass M2."""
    if not direct_s1:
        return ["C20: no in-scope direct scores supplied"]
    ordered = sorted(direct_s1)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    hard = sum(1 for s in oos_s1 if s > median)
    if hard < 25:
        return [f"C20: only {hard} out-of-scope questions out-score the direct median {median:.3f}"]
    return []


ALL_CORPUS_CHECKS = (
    ("C1", check_c1_byte_preserving),
    ("C2", check_c2_unique_cells),
    ("C3", check_c3_core_and_reading),
    ("C4", check_c4_quotable_sources),
    ("C5", check_c5_locators),
    ("C6", check_c6_quotes),
    ("C7", check_c7_floors),
    ("C8", check_c8_crossrefs),
    ("C10", check_c10_misc),
    ("C11", check_c11_passage_reuse),
    ("C13", check_c13_summary_distinctness),
    ("C14", check_c14_complicating),
    ("C15", check_c15_grounding),
    ("C16", check_c16_reading_breadth),
)


def validate_corpus(corpus: Corpus, near_unanimous: list[str] | None = None) -> dict[str, list[str]]:
    """Run every corpus-resident gate; {gate: [failures]}, empty lists = pass."""
    results: dict[str, list[str]] = {gate: fn(corpus) for gate, fn in ALL_CORPUS_CHECKS}
    results["C12"] = check_c12_stance_diversity(corpus, near_unanimous or [])
    return dict(sorted(results.items(), key=lambda kv: (len(kv[0]), kv[0])))

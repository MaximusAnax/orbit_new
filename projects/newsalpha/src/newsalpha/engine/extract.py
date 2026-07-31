"""FR-4: sentence-scoped cascaded pattern extraction (hard part A).

Finite-state information extraction in the FASTUS lineage (Hobbs et al. 1997)
filling MUC-style event templates.  For every sentence of an article's analysis
text and every committed `EventPattern`:

  1. a trigger lexeme must match on word boundaries, case-folded;
  2. every `required_context` term must appear in the same sentence;
  3. suppressors run **within the trigger sentence**:
       negation  -> `mna` becomes `denied`, every other type is suppressed
       hedge     -> stage becomes `rumored`
       historical-reference guard -> suppressed
       metaphor guard             -> suppressed
     with negation taking precedence over hedge inside one sentence;
  4. attribute regexes fill the typed attribute slots;
  5. extraction confidence = 0.9 with >= 1 attribute, else 0.7; +0.05 when every
     attribute the pattern declares is filled; capped at 0.95.

Article-level and cluster-level merges follow FR-4's single merge rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..datasets import Datasets
from ..models import ATTRIBUTE_SCHEMA, Article, Cluster, Event, EventPattern, EventType, Stage
from ..models import EvidenceSpan as Span
from .normalize import Sentence, hex16, parse_iso, split_sentences

TRIGGER_ONLY_CONFIDENCE = 0.70
TRIGGER_WITH_ATTRIBUTE_CONFIDENCE = 0.90
ALL_ATTRIBUTES_BONUS = 0.05
MAX_EXTRACTION_CONFIDENCE = 0.95
HISTORICAL_YEAR_FLOOR = 1990

_YEAR_RE = re.compile(r"\b(19[9]\d|20\d\d)\b")
_MONEY_RE = re.compile(
    r"^\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|bn|million|mn|m|b|thousand|k)?$",
    re.IGNORECASE,
)
_MONEY_SCALE = {
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
}


@dataclass(frozen=True, slots=True)
class TriggerMatch:
    """One pattern firing inside one sentence of one article."""

    article_id: str
    pattern_id: str
    event_type: EventType
    stage: Stage
    has_stage_cue: bool
    attributes: dict[str, object]
    extraction_confidence: float
    sentence: Sentence
    trigger_start: int
    trigger_end: int
    trigger_text: str
    published_at: str


def build_span(article: Article, start: int, end: int) -> Span:
    """The evidence span for `article[start:end]`, quoted verbatim."""
    text = article.analysis_text
    return Span(article_id=article.id, start=start, end=end, quote=text[start:end])


# --------------------------------------------------------------------------- #
# Lexical helpers
# --------------------------------------------------------------------------- #


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    body = r"[\s\-]+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![\w\-]){body}(?![\w\-])", re.IGNORECASE)


_PHRASE_CACHE: dict[str, re.Pattern[str]] = {}


def phrase_matcher(phrase: str) -> re.Pattern[str]:
    """Cached word-boundary matcher for a case-folded lexeme or phrase."""
    matcher = _PHRASE_CACHE.get(phrase)
    if matcher is None:
        matcher = _phrase_regex(phrase)
        _PHRASE_CACHE[phrase] = matcher
    return matcher


def contains_phrase(text: str, phrase: str) -> bool:
    return phrase_matcher(phrase).search(text) is not None


def first_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> str | None:
    """The first phrase of `phrases` (in order) that occurs in `text`."""
    for phrase in phrases:
        if contains_phrase(text, phrase):
            return phrase
    return None


# --------------------------------------------------------------------------- #
# Attribute normalization
# --------------------------------------------------------------------------- #


def normalize_attribute(name: str, raw: str) -> object | None:
    """Turn a captured surface form into the typed attribute value."""
    text = raw.strip()
    if not text:
        return None
    if name.endswith("_usd"):
        return parse_money(text)
    if name.endswith("_pct"):
        try:
            return round(float(text.replace(",", "")), 6)
        except ValueError:  # pragma: no cover - regexes only capture numerics
            return None
    return text


def parse_money(text: str) -> float | None:
    """`"$47 million"` -> `47000000.0`. Returns None when the surface is unparseable."""
    match = _MONEY_RE.match(text.strip().rstrip(".,"))
    if match is None:
        return None
    number = match.group(1).replace(",", "")
    try:
        value = float(number)
    except ValueError:  # pragma: no cover - regex guarantees numeric
        return None
    scale = match.group(2)
    if scale:
        value *= _MONEY_SCALE[scale.lower()]
    return round(value, 2)


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #


def historical_reference(sentence_text: str, article_year: int, guards: tuple[str, ...]) -> bool:
    """FR-4's historical guard: an older year in the sentence, or a guard phrase."""
    for year_text in _YEAR_RE.findall(sentence_text):
        year = int(year_text)
        if HISTORICAL_YEAR_FLOOR <= year < article_year:
            return True
    return any(contains_phrase(sentence_text, guard) for guard in guards)


def stage_for_sentence(
    sentence_text: str, event_type: EventType, datasets: Datasets
) -> tuple[Stage | None, bool, str | None]:
    """Resolve the stage cue of one sentence.

    Returns `(stage, has_cue, suppress_reason)`.  Negation outranks hedge inside a
    single sentence: a sentence carrying both is a denial.
    """
    negation = first_phrase(sentence_text, datasets.lexicons.negation_cues)
    if negation is not None:
        if event_type is EventType.mna:
            return Stage.denied, True, None
        return None, True, f"negation:{negation}"
    hedge = first_phrase(sentence_text, datasets.lexicons.hedge_cues)
    if hedge is not None:
        return Stage.rumored, True, None
    return Stage.confirmed, False, None


def metaphor_suppressed(sentence_text: str, event_type: EventType, datasets: Datasets) -> bool:
    stoplist = datasets.lexicons.metaphor_stoplists.get(event_type.value, ())
    return any(contains_phrase(sentence_text, phrase) for phrase in stoplist)


# --------------------------------------------------------------------------- #
# Per-article extraction
# --------------------------------------------------------------------------- #


def _match_trigger(sentence_text: str, pattern: EventPattern) -> tuple[int, int, str] | None:
    """Longest match at the earliest position among the pattern's triggers."""
    best: tuple[int, int, str] | None = None
    for trigger in pattern.triggers:
        match = phrase_matcher(trigger).search(sentence_text)
        if match is None:
            continue
        candidate = (match.start(), match.end(), match.group(0))
        if best is None or (candidate[0], -(candidate[1] - candidate[0])) < (
            best[0],
            -(best[1] - best[0]),
        ):
            best = candidate
    return best


def _extract_attributes(sentence_text: str, pattern: EventPattern) -> dict[str, object]:
    """Fill the pattern's declared attributes from the trigger sentence."""
    attributes: dict[str, object] = {}
    for name, regex in sorted(pattern.attribute_extractors.items()):
        match = re.search(regex, sentence_text)
        if match is None:
            continue
        raw = match.group(1) if match.re.groups else match.group(0)
        value = normalize_attribute(name, raw)
        if value is None:
            continue
        attributes[name] = value
    if pattern.polarity is not None:
        attributes["polarity"] = pattern.polarity
    return attributes


def _extraction_confidence(pattern: EventPattern, attributes: dict[str, object]) -> float:
    """FR-4's confidence rule.

    `+0.05` applies only when the pattern *declares* at least one attribute and all
    of them are filled; a pattern that declares none stays at the trigger-only 0.70
    rather than collecting a vacuous bonus.
    """
    declared = pattern.declared_attributes()
    confidence = TRIGGER_WITH_ATTRIBUTE_CONFIDENCE if attributes else TRIGGER_ONLY_CONFIDENCE
    if declared and declared <= set(attributes):
        confidence += ALL_ATTRIBUTES_BONUS
    return min(confidence, MAX_EXTRACTION_CONFIDENCE)


def extract_from_article(article: Article, datasets: Datasets) -> list[TriggerMatch]:
    """Every surviving pattern firing in one article, in (sentence, pattern id) order."""
    text = article.analysis_text
    sentences = split_sentences(text, datasets.lexicons.abbreviations)
    article_year = parse_iso(article.published_at).year
    matches: list[TriggerMatch] = []

    for sentence in sentences:
        for pattern in datasets.patterns:
            hit = _match_trigger(sentence.text, pattern)
            if hit is None:
                continue
            # `required_context` terms are alternatives: at least one must appear in
            # the trigger sentence.  They exist to gate broad triggers ("approved",
            # "better than expected") on finance context, and listing every synonym
            # as a conjunction would make such a pattern unfirable.
            if pattern.required_context and not any(
                contains_phrase(sentence.text, term) for term in pattern.required_context
            ):
                continue
            if metaphor_suppressed(sentence.text, pattern.event_type, datasets):
                continue
            if historical_reference(
                sentence.text, article_year, datasets.lexicons.historical_guards
            ):
                continue
            stage, has_cue, suppressed = stage_for_sentence(
                sentence.text, pattern.event_type, datasets
            )
            if suppressed is not None or stage is None:
                continue
            attributes = _extract_attributes(sentence.text, pattern)
            matches.append(
                TriggerMatch(
                    article_id=article.id,
                    pattern_id=pattern.id,
                    event_type=pattern.event_type,
                    stage=stage,
                    has_stage_cue=has_cue,
                    attributes=attributes,
                    extraction_confidence=_extraction_confidence(pattern, attributes),
                    sentence=sentence,
                    trigger_start=sentence.start + hit[0],
                    trigger_end=sentence.start + hit[1],
                    trigger_text=hit[2],
                    published_at=article.published_at,
                )
            )
    matches.sort(key=lambda m: (m.sentence.start, m.trigger_start, m.pattern_id))
    return matches


# --------------------------------------------------------------------------- #
# Merge (FR-4's single merge rule)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExtractedEvent:
    """A merged event plus the trigger matches that produced it (needed by FR-5)."""

    event: Event
    triggers: tuple[TriggerMatch, ...]


def build_events(
    clusters: list[Cluster],
    articles: dict[str, Article],
    datasets: Datasets,
) -> list[ExtractedEvent]:
    """At most one event per (cluster, event_type), merged per FR-4.

    * evidence spans from all trigger-bearing articles, appended in
      (published_at, article_id) order;
    * `stage` from the trigger-bearing article with the **latest** `published_at`
      that carries a stage cue (tie: largest article_id), so a later confirmation
      or denial supersedes an earlier rumor within the cluster;
    * every other attribute takes the first non-null value in (published_at,
      article_id) order;
    * `extraction_confidence` is the maximum over contributing articles.
    """
    per_article: dict[str, list[TriggerMatch]] = {}
    for cluster in clusters:
        for article_id in cluster.article_ids:
            if article_id not in per_article:
                per_article[article_id] = extract_from_article(articles[article_id], datasets)

    results: list[ExtractedEvent] = []
    for cluster in clusters:
        ordered_ids = sorted(cluster.article_ids, key=lambda aid: (articles[aid].published_at, aid))
        by_type: dict[EventType, list[TriggerMatch]] = {}
        for article_id in ordered_ids:
            for match in per_article[article_id]:
                by_type.setdefault(match.event_type, []).append(match)

        for event_type in sorted(by_type, key=lambda t: t.value):
            matches = by_type[event_type]
            event_id = hex16(f"event|{cluster.id}|{event_type.value}")
            stage = _merge_stage(matches, articles)
            attributes = _merge_attributes(matches, event_type)
            evidence = _merge_evidence(matches, articles)
            results.append(
                ExtractedEvent(
                    event=Event(
                        id=event_id,
                        cluster_id=cluster.id,
                        event_type=event_type,
                        stage=stage,
                        attributes=attributes,
                        extraction_confidence=max(m.extraction_confidence for m in matches),
                        evidence=evidence,
                        notes=(),
                        event_date=cluster.event_date,
                        observed_at=cluster.latest_published_at,
                    ),
                    triggers=tuple(matches),
                )
            )
    results.sort(key=lambda r: r.event.id)
    return results


def _merge_evidence(matches: list[TriggerMatch], articles: dict[str, Article]) -> tuple[Span, ...]:
    """Evidence spans in (published_at, article_id) order, de-duplicated."""
    seen: set[tuple[str, int, int]] = set()
    spans: list[Span] = []
    for match in matches:
        key = (match.article_id, match.sentence.start, match.trigger_end)
        if key in seen:
            continue
        seen.add(key)
        spans.append(
            build_span(articles[match.article_id], match.sentence.start, match.trigger_end)
        )
    return tuple(spans)


def _merge_stage(matches: list[TriggerMatch], articles: dict[str, Article]) -> Stage:
    """Latest trigger-bearing article that carries a stage cue wins (tie: larger id).

    Within that one article, negation still outranks hedge (FR-4's cue precedence),
    and otherwise the first cue in reading order is used -- both deterministic.
    """
    cued = [m for m in matches if m.has_stage_cue]
    if not cued:
        return Stage.confirmed
    best = max(cued, key=lambda m: (articles[m.article_id].published_at, m.article_id))
    same_article = [m for m in cued if m.article_id == best.article_id]
    if any(m.stage is Stage.denied for m in same_article):
        return Stage.denied
    return same_article[0].stage


def _merge_attributes(matches: list[TriggerMatch], event_type: EventType) -> dict[str, object]:
    """First non-null value in (published_at, article_id) order, per attribute."""
    allowed = ATTRIBUTE_SCHEMA[event_type]
    merged: dict[str, object] = {}
    for match in matches:
        for name, value in match.attributes.items():
            if name in allowed and value is not None and name not in merged:
                merged[name] = value
    return dict(sorted(merged.items()))


__all__ = [
    "ExtractedEvent",
    "TriggerMatch",
    "build_events",
    "build_span",
    "contains_phrase",
    "extract_from_article",
    "first_phrase",
    "historical_reference",
    "metaphor_suppressed",
    "normalize_attribute",
    "parse_money",
    "phrase_matcher",
    "stage_for_sentence",
]

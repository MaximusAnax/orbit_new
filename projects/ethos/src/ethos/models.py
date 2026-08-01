"""Pydantic v2 domain models and enums for ethos (docs/DATA_MODEL.md).

Models are strict on *shape* (unknown fields rejected) but permissive on
*substance*: content floors and invariants are enforced by the corpus gates
(evals/corpus_gates.py, shared with `ethos corpus validate`), so that
negative-control corpora can be loaded and then rejected by the right gate.
String fields are byte-preserving: no validator strips or normalizes them.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Family(StrEnum):
    abrahamic = "abrahamic"
    dharmic = "dharmic"
    east_asian = "east_asian"
    greco_roman = "greco_roman"
    modern_western = "modern_western"


class Stance(StrEnum):
    obligatory = "obligatory"
    encouraged = "encouraged"
    permitted = "permitted"
    context_dependent = "context_dependent"
    discouraged = "discouraged"
    forbidden = "forbidden"
    contested = "contested"
    reframed = "reframed"


class PassageRole(StrEnum):
    core = "core"
    supporting = "supporting"
    complicating = "complicating"


class License(StrEnum):
    public_domain = "public_domain"
    cc0 = "cc0"
    cc_by = "cc_by"
    reference_only = "reference_only"


class LocatorScheme(StrEnum):
    chapter_verse = "chapter_verse"
    book_section = "book_section"
    part_question_article = "part_question_article"
    bekker = "bekker"
    stephanus = "stephanus"
    academy_ed = "academy_ed"
    sutta_ref = "sutta_ref"
    folio = "folio"
    hadith_ref = "hadith_ref"
    section = "section"


class ReadingKind(StrEnum):
    sep = "sep"
    iep = "iep"
    book = "book"
    article = "article"
    primary_translation = "primary_translation"


class QuestionOutcome(StrEnum):
    answered = "answered"
    refused_out_of_scope = "refused_out_of_scope"


class SafeguardKind(StrEnum):
    crisis_resources = "crisis_resources"
    not_medical_advice = "not_medical_advice"
    not_legal_advice = "not_legal_advice"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyConcept(_Model):
    term: str
    gloss: str


class Tradition(_Model):
    id: str
    name: str
    family: Family
    order: int
    era: str
    summary: str
    key_concepts: list[KeyConcept]


class Keyword(_Model):
    term: str
    weight: int


class Topic(_Model):
    id: str
    title: str
    description: str
    question_forms: list[str]
    keywords: list[Keyword]
    related_topics: list[str]
    sensitive: bool
    safeguard_ids: list[str]


class Safeguard(_Model):
    id: str
    kind: SafeguardKind
    text: str


class Source(_Model):
    id: str
    title: str
    author: str | None
    composed_era: str
    translator: str | None
    translation_year: int | None
    edition_note: str
    license: License
    locator_scheme: LocatorScheme
    url: str | None

    @property
    def source_line(self) -> str:
        if self.license == License.reference_only:
            return f"{self.title} — {self.edition_note}"
        if self.translator is None:
            # English original: provenance is author + edition year (REVIEW.md #30)
            return f"{self.title}, {self.author} ({self.translation_year})"
        return f"{self.title}, trans. {self.translator} ({self.translation_year})"


class Passage(_Model):
    id: str
    source_id: str
    locator: str
    text: str | None
    paraphrase: str | None
    context_note: str
    transcription_checked: bool


class ReasoningPoint(_Model):
    text: str
    passage_id: str | None


class PassageRef(_Model):
    passage_id: str
    role: PassageRole
    note: str | None


class FurtherReading(_Model):
    title: str
    author: str
    year: int | None
    kind: ReadingKind
    url: str | None
    note: str | None


class Position(_Model):
    id: str
    topic_id: str
    tradition_id: str
    stance: Stance
    summary: str
    reasoning: list[ReasoningPoint]
    passages: list[PassageRef]
    intra_tradition_note: str | None
    further_reading: list[FurtherReading]
    curator_note: str


class RouterConfig(_Model):
    k1: float
    b: float
    w_phrase: float
    tau: float
    kappa: float
    tuning_note: str


# --- Routing / answer models -------------------------------------------------


class TopicScore(_Model):
    topic_id: str
    score: float


class RoutingResult(_Model):
    """Full router output (FR-3/FR-4)."""

    ranked: list[TopicScore]  # top 5 scoring topics; may be empty
    confidence: float
    coverage: float
    abstained: bool


class RoutingEcho(_Model):
    """Routing block embedded in an AnswerBody (forced asks: confidence None)."""

    topic_id: str
    confidence: float | None
    alternates: list[TopicScore]
    forced: bool


class Quote(_Model):
    marker: str
    passage_id: str
    text: str
    is_paraphrase: bool
    label: str | None
    locator: str
    source_line: str
    context_note: str


class RenderedReasoning(_Model):
    text: str
    marker: str | None


class Perspective(_Model):
    tradition_id: str
    tradition_name: str
    position_id: str
    stance: Stance
    summary: str
    reasoning: list[RenderedReasoning]
    quotes: list[Quote]
    intra_tradition_note: str | None
    further_reading: list[FurtherReading]


class CitationEntry(_Model):
    passage_id: str
    locator: str
    source_id: str


class AnswerBody(_Model):
    safeguards: list[Safeguard]
    routing: RoutingEcho
    perspectives: list[Perspective]
    not_covered: list[str]
    filtered_out: list[str]
    agreement_map: dict[str, list[str]]
    citations: dict[str, CitationEntry]
    corpus_version: str
    composer_version: str


class Question(_Model):
    id: int | None = None
    text: str
    asked_at: str
    outcome: QuestionOutcome
    forced_topic_id: str | None
    routing: RoutingResult | None


class AnswerOptions(_Model):
    traditions: list[str] | None
    polish: bool


class Answer(_Model):
    id: int | None = None
    question_id: int
    created_at: str
    topic_id: str
    options: AnswerOptions
    corpus_version: str
    composer_version: str
    polish_used: bool
    polish_fell_back: bool
    verified: bool
    body: AnswerBody
    rendered_text: str


class CorpusMeta(_Model):
    corpus_version: str
    loaded_at: str

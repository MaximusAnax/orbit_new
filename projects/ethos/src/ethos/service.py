"""Application service: ask/render orchestration; owns the store and adapters.

Lives outside engine/ deliberately — it persists through the Repository,
which is I/O (SCOPE architecture note). The engine composes; this persists.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ethos.adapters.polisher import ProsePolisher
from ethos.adapters.router import LexicalRouter, TopicRouter
from ethos.corpus import Corpus
from ethos.engine import envelope as env_mod
from ethos.engine.compose import COMPOSER_VERSION, compose_answer, render_text
from ethos.engine.corpus import corpus_stats, reading_entry_counts
from ethos.engine.retrieve import reading_list, retrieve
from ethos.engine.router import build_index
from ethos.engine.verify import Failure, verify
from ethos.models import (
    Answer,
    AnswerBody,
    AnswerOptions,
    CorpusMeta,
    Question,
    QuestionOutcome,
    RoutingEcho,
    RoutingResult,
    TopicScore,
)
from ethos.store.repository import Repository

BROWSE_HINT = (
    "Browse what this corpus can answer with `ethos topics`, or force a topic "
    "with `ethos ask --topic <id>`."
)

EXCLUDED_FAMILIES_NOTE = (
    "Two families of moral questions are deliberately outside this corpus: "
    "modern technology and bioethics (gene editing, AI training on creative "
    "work, data privacy, surveillance, climate duties, organ markets), and "
    "contested modern moral-political questions (abortion, sexuality, "
    "gambling, immigration). They are refused rather than stretched onto the "
    "nearest topic."
)


class PolishUnavailableError(RuntimeError):
    """polish requested but no live polisher is configured (edge: 400 / exit 2)."""


class UnknownTopicError(KeyError):
    pass


class UnknownTraditionError(KeyError):
    pass


class IntegrityError(RuntimeError):
    """FR-8 failure on the null path — corpus or composer bug (500 / exit 3)."""

    def __init__(self, failures: list[Failure]):
        details = "; ".join(f"({f.check}) {f.detail}" for f in failures)
        super().__init__(f"citation verification failed: {details}")
        self.failures = failures


#: The FR-8 gate as the service calls it. Injectable so the eval suite can
#: *measure* its `baseline_no_verifier` / `baseline_reject_all` reference
#: implementations through the real pipeline instead of asserting their values
#: (EVALS § Naive baselines). Production always gets `engine.verify.verify`.
VerifyFn = Callable[
    [AnswerBody, Corpus, str, list[str] | None, str, str | None], list[Failure]
]


@dataclass(frozen=True)
class Refusal:
    outcome: str
    nearest_topics: list[TopicScore]
    browse_hint: str
    note: str


@dataclass(frozen=True)
class AskResult:
    question: Question
    answer: Answer | None
    refusal: Refusal | None


class EthosService:
    def __init__(
        self,
        corpus: Corpus,
        repo: Repository,
        router: TopicRouter | None = None,
        polisher: ProsePolisher | None = None,
        verifier: VerifyFn | None = None,
    ) -> None:
        self.corpus = corpus
        self.repo = repo
        self.router = router if router is not None else LexicalRouter()
        self.polisher = polisher
        self.verifier: VerifyFn = verifier if verifier is not None else verify
        self.index = build_index(corpus.topics, corpus.router_config, corpus.stopwords)
        self._titles = {t.id: t.title for t in corpus.topics}
        self._names = {t.id: t.name for t in corpus.traditions}

    # -- lifecycle ------------------------------------------------------------

    def init_store(self, loaded_at: str) -> CorpusMeta:
        meta = CorpusMeta(corpus_version=self.corpus.corpus_version, loaded_at=loaded_at)
        self.repo.set_corpus_meta(meta)
        return meta

    def _guard(self) -> None:
        self.repo.guard_corpus(self.corpus.corpus_version)

    # -- ask ------------------------------------------------------------------

    def _validate_ask(self, traditions: list[str] | None, topic_id: str | None) -> None:
        if topic_id is not None and topic_id not in self.corpus.topic_by_id:
            raise UnknownTopicError(topic_id)
        for tid in traditions or []:
            if tid not in self.corpus.tradition_by_id:
                raise UnknownTraditionError(tid)

    def _compose_verified(
        self,
        topic_id: str,
        traditions: list[str] | None,
        echo: RoutingEcho,
        polish: bool,
    ) -> tuple[AnswerBody, str, bool, bool]:
        """Returns (body, rendered_text, polish_used, polish_fell_back)."""
        retrieval = retrieve(self.corpus, topic_id, traditions)
        body = compose_answer(self.corpus, topic_id, retrieval, echo)
        pre_env = env_mod.serialize(body, traditions)
        polish_used = False
        polish_fell_back = False
        if polish:
            assert self.polisher is not None
            polished = self.polisher.polish(pre_env)
            if polished != pre_env:
                position_ids = {p.tradition_id: p.position_id for p in body.perspectives}
                try:
                    candidate = env_mod.parse(polished, position_ids)
                    candidate_render = render_text(candidate, self._titles, self._names)
                    failures = self.verifier(
                        candidate, self.corpus, candidate_render, traditions, pre_env, polished
                    )
                except env_mod.EnvelopeError:
                    failures = [Failure("parse", "unparseable envelope")]
                    candidate = None
                if candidate is not None and not failures:
                    return candidate, candidate_render, True, False
                polish_fell_back = True
        rendered = render_text(body, self._titles, self._names)
        failures = self.verifier(body, self.corpus, rendered, traditions, pre_env, None)
        if failures:
            raise IntegrityError(failures)
        return body, rendered, polish_used, polish_fell_back

    def ask(
        self,
        text: str,
        asked_at: str,
        traditions: list[str] | None = None,
        topic_id: str | None = None,
        polish: bool = False,
    ) -> AskResult:
        self._guard()
        if polish and self.polisher is None:
            raise PolishUnavailableError(
                "polish requested but no LLM polisher is configured (set ETHOS_LLM_API_KEY)"
            )
        self._validate_ask(traditions, topic_id)
        routing: RoutingResult | None = None
        if topic_id is not None:
            echo = RoutingEcho(topic_id=topic_id, confidence=None, alternates=[], forced=True)
        else:
            routing = self.router.route(text, self.index)
            if routing.abstained:
                question = self.repo.add_question(
                    Question(
                        text=text,
                        asked_at=asked_at,
                        outcome=QuestionOutcome.refused_out_of_scope,
                        forced_topic_id=None,
                        routing=routing,
                    )
                )
                refusal = Refusal(
                    outcome=QuestionOutcome.refused_out_of_scope.value,
                    nearest_topics=routing.ranked[:3],
                    browse_hint=BROWSE_HINT,
                    note=EXCLUDED_FAMILIES_NOTE,
                )
                return AskResult(question=question, answer=None, refusal=refusal)
            topic_id = routing.ranked[0].topic_id
            echo = RoutingEcho(
                topic_id=topic_id,
                confidence=routing.confidence,
                alternates=routing.ranked[1:3],
                forced=False,
            )
        body, rendered, polish_used, polish_fell_back = self._compose_verified(
            topic_id, traditions, echo, polish
        )
        question = self.repo.add_question(
            Question(
                text=text,
                asked_at=asked_at,
                outcome=QuestionOutcome.answered,
                forced_topic_id=topic_id if routing is None else None,
                routing=routing,
            )
        )
        answer = self.repo.add_answer(
            Answer(
                question_id=question.id or 0,
                created_at=asked_at,
                topic_id=topic_id,
                options=AnswerOptions(traditions=traditions, polish=polish),
                corpus_version=self.corpus.corpus_version,
                composer_version=COMPOSER_VERSION,
                polish_used=polish_used,
                polish_fell_back=polish_fell_back,
                verified=True,
                body=body,
                rendered_text=rendered,
            )
        )
        return AskResult(question=question, answer=answer, refusal=None)

    # -- browse & stats (FR-12) ----------------------------------------------

    def topics_for_tradition(self, tradition_id: str | None) -> list:
        if tradition_id is None:
            return list(self.corpus.topics)
        if tradition_id not in self.corpus.tradition_by_id:
            raise UnknownTraditionError(tradition_id)
        return [
            t
            for t in self.corpus.topics
            if (t.id, tradition_id) in self.corpus.position_by_cell
        ]

    def topic_detail(self, topic_id: str) -> dict:
        topic = self.corpus.topic_by_id.get(topic_id)
        if topic is None:
            raise UnknownTopicError(topic_id)
        covered = [
            {"tradition_id": t.id, "stance": self.corpus.position_by_cell[(topic_id, t.id)].stance}
            for t in self.corpus.traditions
            if (topic_id, t.id) in self.corpus.position_by_cell
        ]
        return {
            "topic": topic,
            "covered": covered,
            "reading": self.reading_list(topic_id, None),
            "safeguards": [self.corpus.safeguard_by_id[s] for s in topic.safeguard_ids],
        }

    def reading_list(self, topic_id: str, tradition_id: str | None) -> list[dict]:
        if topic_id not in self.corpus.topic_by_id:
            raise UnknownTopicError(topic_id)
        if tradition_id is not None and tradition_id not in self.corpus.tradition_by_id:
            raise UnknownTraditionError(tradition_id)
        requested = None if tradition_id is None else [tradition_id]
        owner = {
            entry.title: retrieved.position.tradition_id
            for retrieved in retrieve(self.corpus, topic_id, requested).rendered
            for entry in retrieved.position.further_reading
        }
        return [
            {"tradition_id": owner.get(entry.title, ""), "entry": entry}
            for entry in reading_list(self.corpus, topic_id, requested)
        ]

    def substance_stats(self) -> dict:
        """Counts plus the FR-1 layer-3 ratios. The ratios come from
        `engine.corpus.corpus_stats` — the same function the C11-C16 gates use,
        so `corpus stats` can never report a number the gates disagree with."""
        corpus = self.corpus
        stats = corpus_stats(corpus)
        stances: dict[str, set[str]] = {}
        for position in corpus.positions:
            stances.setdefault(position.topic_id, set()).add(position.stance.value)
        reading = reading_entry_counts(corpus)
        return {
            "positions": len(corpus.positions),
            "topics": len(corpus.topics),
            "traditions": len(corpus.traditions),
            "passages": len(corpus.passages),
            "sources": len(corpus.sources),
            "distinct_cited_passages": int(stats["distinct_cited_passages"]),
            "max_core_reuse": int(stats["max_core_reuse"]),
            "min_stances_per_topic": min((len(v) for v in stances.values()), default=0),
            "complicating_share": stats["complicating_share"],
            "quoted_core_share": stats["quoted_core_share"],
            "reference_only_share": stats["reference_only_share"],
            "distinct_reading_entries": int(stats["distinct_reading_entries"]),
            "max_reading_reuse": max(reading.values(), default=0),
            "corpus_version": corpus.corpus_version,
        }

    def coverage_matrix(self) -> dict[str, dict[str, str | None]]:
        return {
            topic.id: {
                tr.id: (
                    self.corpus.position_by_cell[(topic.id, tr.id)].stance.value
                    if (topic.id, tr.id) in self.corpus.position_by_cell
                    else None
                )
                for tr in self.corpus.traditions
            }
            for topic in self.corpus.topics
        }

    # -- history (FR-11 / US-7) ----------------------------------------------

    def render_stored(self, answer: Answer) -> tuple[str, bool]:
        """Stored render; note flag True when composer has moved on (US-7)."""
        if answer.composer_version == COMPOSER_VERSION:
            return answer.rendered_text, False
        return answer.rendered_text, True

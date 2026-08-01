"""Shared fixtures. The committed corpus is loaded once per session — it is
the fixture for most of the engine tests (EVALS § fixture strategy)."""
from __future__ import annotations

import pytest

from ethos.corpus import default_data_dir, load_corpus
from ethos.engine.compose import compose_answer, render_text
from ethos.engine.retrieve import retrieve
from ethos.engine.router import build_index
from ethos.models import RoutingEcho

TS = "2026-08-01T12:00:00Z"


@pytest.fixture(scope="session")
def corpus():
    return load_corpus(default_data_dir())


@pytest.fixture(scope="session")
def index(corpus):
    return build_index(corpus.topics, corpus.router_config, corpus.stopwords)


@pytest.fixture(scope="session")
def topic_titles(corpus):
    return {t.id: t.title for t in corpus.topics}


@pytest.fixture(scope="session")
def tradition_names(corpus):
    return {t.id: t.name for t in corpus.traditions}


@pytest.fixture(scope="session")
def compose(corpus):
    """compose(topic_id, traditions=None, forced=True) -> (body, rendered)."""

    def _compose(topic_id: str, traditions: list[str] | None = None, forced: bool = True):
        body = make_body(corpus, topic_id, traditions, forced)
        return body, make_render(corpus, body)

    return _compose


def make_body(corpus, topic_id: str, traditions: list[str] | None = None, forced: bool = True):
    echo = RoutingEcho(
        topic_id=topic_id,
        confidence=None if forced else 0.5,
        alternates=[],
        forced=forced,
    )
    return compose_answer(corpus, topic_id, retrieve(corpus, topic_id, traditions), echo)


def make_render(corpus, body):
    return render_text(
        body,
        {t.id: t.title for t in corpus.topics},
        {t.id: t.name for t in corpus.traditions},
    )


@pytest.fixture(scope="session")
def sample(corpus):
    """One fully composed, rendered answer over an ordinary topic."""
    body = make_body(corpus, "honesty_and_deception")
    return body, make_render(corpus, body)

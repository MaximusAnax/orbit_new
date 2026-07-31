"""EVALS.md section 4: mechanical enforcement of the M8 held-split hygiene rule.

A lexicon term that matches at least one *held* quote and has **zero** support
in the dev split, the starter pack and the prompt templates is the signature of
tuning against the held set.  The test prints the offending triples so a
legitimately rare term can be justified by adding dev-split support (a real
starter quote or template that uses it) rather than by weakening the test.

The human half of the rule lives in docs/REVIEW.md: a change to
`data/themes.json` may not cite or be justified by a held-split quote.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from almanac.datasets import data_dir, load_datasets, load_starter_quotes
from almanac.engine.normalize import phrase_occurrences, stemmed_tokens

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"


@pytest.fixture(scope="module")
def labeled() -> list[dict]:
    return json.loads((FIXTURES / "themes_labeled.json").read_text(encoding="utf-8"))


def _matches(term: str, texts: list[str]) -> list[int]:
    needle = stemmed_tokens(term)
    if not needle:
        return []
    return [i for i, text in enumerate(texts) if phrase_occurrences(stemmed_tokens(text), needle)]


def test_fr3_no_lexicon_term_is_supported_only_by_the_held_split(labeled):
    datasets = load_datasets()
    held = [row for row in labeled if row["split"] == "held"]
    dev_texts = [row["text"] for row in labeled if row["split"] == "dev"]
    held_texts = [row["text"] for row in held]
    starter_texts = [quote.text for quote in load_starter_quotes()]
    template_texts = [template.template for template in datasets.templates]

    offenders: list[tuple[str, str, str]] = []
    for theme in datasets.themes:
        for term in theme.lexicon:
            hits = _matches(term.term, held_texts)
            if not hits:
                continue
            supported = (
                _matches(term.term, dev_texts)
                or _matches(term.term, starter_texts)
                or _matches(term.term, template_texts)
            )
            if not supported:
                offenders.extend((term.term, theme.id, held[i]["id"]) for i in hits)

    assert offenders == [], (
        "these lexicon terms match a held-split quote and nothing else — either "
        "they were tuned against the held set, or they need dev-split support:\n"
        + "\n".join(
            f"  term={t!r} theme={theme} held_quote={quote}" for t, theme, quote in offenders
        )
    )


def test_fr3_lexicon_terms_are_normalized_and_unique():
    for theme in load_datasets().themes:
        terms = [entry.term for entry in theme.lexicon]
        assert terms == [t.casefold().strip() for t in terms], theme.id
        assert len(set(terms)) == len(terms), theme.id
        assert len(terms) >= 8, theme.id


def test_fr3_held_split_quotes_do_not_appear_in_the_starter_pack(labeled):
    """The starter pack is readable by lexicon authors, so it must not leak labels."""
    starter = {quote.text for quote in load_starter_quotes()}
    leaks = [row["id"] for row in labeled if row["split"] == "held" and row["text"] in starter]
    assert leaks == [], f"held quotes duplicated in the starter pack: {leaks}"


def test_committed_data_files_are_all_present():
    for name in (
        "themes.json",
        "prompts.json",
        "scheduler.json",
        "misattributions.json",
        "starter_quotes.json",
    ):
        assert (data_dir() / name).exists(), name

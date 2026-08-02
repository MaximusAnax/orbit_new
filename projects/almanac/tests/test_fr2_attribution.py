"""FR-2: attribution matching against the committed misattribution dataset."""

from __future__ import annotations

import pytest
from almanac.adapters.attribution_local import LocalAttributionChecker
from almanac.engine.attribution import find_misattributions, matches
from almanac.models import MisattributionRecord, Verdict

RECORD = MisattributionRecord(
    id="test-case",
    pattern="a made up phrase",
    claimed_authors=["someone famous", "famous"],
    verdict=Verdict.MISATTRIBUTED,
    likely_origin="a test",
    note="a note",
    reference_url="https://example.org/x",
)


def test_fr2_matches_on_contiguous_normalized_tokens_and_author():
    assert matches(RECORD, "Here is A MADE, UP phrase! indeed", "Someone Famous")


def test_fr2_does_not_match_a_different_author():
    assert not matches(RECORD, "a made up phrase", "Someone Else")


def test_fr2_does_not_match_when_the_author_is_missing():
    assert not matches(RECORD, "a made up phrase", None)


def test_fr2_requires_the_phrase_to_be_contiguous():
    assert not matches(RECORD, "a made totally up phrase", "famous")


def test_fr2_empty_claimed_authors_matches_any_author():
    anyone = RECORD.model_copy(update={"claimed_authors": []})
    assert matches(anyone, "a made up phrase", None)
    assert matches(anyone, "a made up phrase", "Nobody")


def test_fr2_findings_are_ordered_by_record_id():
    second = RECORD.model_copy(update={"id": "aaa-first"})
    findings = find_misattributions([RECORD, second], "a made up phrase", "famous")
    assert [f.misattribution_id for f in findings] == ["aaa-first", "test-case"]


def test_fr2_known_case_from_the_committed_dataset(datasets):
    checker = LocalAttributionChecker(datasets.misattributions)
    findings = checker.check(
        "Insanity is doing the same thing over and over and expecting different results.",
        "Albert Einstein",
    )
    assert [f.misattribution_id for f in findings] == ["insanity-einstein"]
    assert findings[0].verdict is Verdict.MISATTRIBUTED
    assert findings[0].reference_url.startswith("https://")


def test_fr2_check_is_silent_for_unknown_quotes(datasets):
    checker = LocalAttributionChecker(datasets.misattributions)
    assert checker.check("An entirely original sentence of my own", "Me") == []


def test_fr2_flags_never_block_capture(service):
    result = service.capture("Be the change you wish to see in the world.", author="Mahatma Gandhi")
    assert result.entry.id
    assert [f.misattribution_id for f in result.attribution_flags] == ["be-the-change-gandhi"]


def test_fr2_every_committed_record_carries_a_reference_url(datasets):
    assert len(datasets.misattributions) >= 40
    for record in datasets.misattributions:
        assert record.reference_url.startswith("https://")
        assert record.pattern == record.pattern.casefold()


@pytest.mark.parametrize("author", ["einstein", "EINSTEIN", "  Albert   Einstein "])
def test_fr2_author_matching_is_normalized(datasets, author):
    checker = LocalAttributionChecker(datasets.misattributions)
    findings = checker.check("insanity is doing the same thing over and over", author)
    assert findings

"""FR-8: the verification gate. One test per check (a)-(i), each proving the
check *rejects* a mutation — a verifier never observed red is not evidence."""
from __future__ import annotations

import pytest
from ethos.engine import envelope as env_mod
from ethos.engine.verify import check_body, check_render_independently, verify
from ethos.models import Stance

TOPIC = "honesty_and_deception"


@pytest.fixture()
def case(corpus, compose):
    body, rendered = compose(TOPIC)
    return body, rendered, env_mod.serialize(body, None)


def _checks(failures) -> set[str]:
    return {f.check for f in failures}


def test_fr8_clean_answer_verifies(corpus, case):
    body, rendered, envelope = case
    assert verify(body, corpus, rendered, None, envelope, None) == []


def test_fr8_a_dropped_marker(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    broken.citations.pop("C1")
    assert "a" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_a_duplicated_quote_block(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    perspective = broken.perspectives[0]
    perspective.quotes.append(perspective.quotes[0].model_copy(deep=True))
    assert "a" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_b_word_swap_in_quote(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quote = broken.perspectives[0].quotes[0]
    broken.perspectives[0].quotes[0] = quote.model_copy(
        update={"text": quote.text.replace("the", "a", 1)}
    )
    assert "b" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_b_homoglyph_in_quote(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quote = broken.perspectives[0].quotes[0]
    broken.perspectives[0].quotes[0] = quote.model_copy(
        update={"text": quote.text.replace("e", "е", 1)}  # noqa: RUF001 - Cyrillic ie
    )
    assert "b" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_c_altered_locator_and_source_line(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quote = broken.perspectives[0].quotes[0]
    broken.perspectives[0].quotes[0] = quote.model_copy(
        update={"locator": quote.locator + "b", "source_line": "Some Other Work (1999)"}
    )
    assert "c" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_d_altered_safeguard_block(corpus, compose):
    body, rendered = compose("suicide_and_self_harm")
    envelope = env_mod.serialize(body, None)
    assert verify(body, corpus, rendered, None, envelope, None) == []
    broken = body.model_copy(deep=True)
    broken.safeguards[0] = broken.safeguards[0].model_copy(
        update={"text": broken.safeguards[0].text.replace("988", "555")}
    )
    assert "d" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_d_dropped_safeguard_block(corpus, compose):
    body, _rendered = compose("euthanasia_and_end_of_life")
    envelope = env_mod.serialize(body, None)
    broken = body.model_copy(deep=True)
    broken.safeguards = broken.safeguards[:1]
    assert "d" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_e_invented_locator_in_polished_prose(corpus, case):
    body, _rendered, envelope = case
    polished = envelope.replace(
        "[[M:summary:christianity]]", "[[M:summary:christianity]]See Matthew 5:37. ", 1
    )
    failures = check_body(body, corpus, None, envelope, polished)
    assert "e" in _checks(failures)


def test_fr8_e_bulk_prose_injection(corpus, case):
    body, _rendered, envelope = case
    marker = "[[M:summary:christianity]]"
    start = envelope.index(marker) + len(marker)
    polished = envelope[:start] + "Lying is always wrong. " * 40 + envelope[start:]
    assert "e" in _checks(check_body(body, corpus, None, envelope, polished))


def test_fr8_e_leaves_curated_locator_prose_alone(corpus, compose):
    """A curated note that legitimately names a locator must survive polish:
    check (e) scans only spans the polisher *changed*."""
    body, _rendered = compose("honesty_and_deception")
    envelope = env_mod.serialize(body, None)
    note = next(
        p.intra_tradition_note for p in body.perspectives if p.intra_tradition_note
    )
    polished = envelope.replace(note, note + " Readers differ.")
    failures = check_body(body, corpus, None, envelope, polished)
    assert "e" not in _checks(failures)


def test_fr8_f_swapped_marker_pairing(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quotes = broken.perspectives[0].quotes
    if len(quotes) < 2:
        pytest.skip("needs two quotes in one perspective")
    quotes[0], quotes[1] = (
        quotes[0].model_copy(update={"locator": quotes[1].locator}),
        quotes[1].model_copy(update={"locator": quotes[0].locator}),
    )
    assert "c" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_f_flipped_is_paraphrase_and_dropped_label(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quote = broken.perspectives[0].quotes[0]
    broken.perspectives[0].quotes[0] = quote.model_copy(
        update={"is_paraphrase": True, "label": None}
    )
    assert "f" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_g_altered_further_reading(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    entry = broken.perspectives[0].further_reading[0]
    broken.perspectives[0].further_reading[0] = entry.model_copy(
        update={"title": entry.title + " (2nd ed.)"}
    )
    assert "g" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_h_swapped_tradition_header(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    broken.perspectives[0] = broken.perspectives[0].model_copy(
        update={"tradition_name": "Stoicism"}
    )
    assert "h" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_h_reordered_perspectives(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    broken.perspectives = list(reversed(broken.perspectives))
    assert "h" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_i_altered_stance_and_agreement_map(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    broken.perspectives[0] = broken.perspectives[0].model_copy(
        update={"stance": Stance.permitted}
    )
    assert "i" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_i_altered_not_covered(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    broken.not_covered = []
    assert "i" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_i_altered_context_note(corpus, case):
    body, _rendered, envelope = case
    broken = body.model_copy(deep=True)
    quote = broken.perspectives[0].quotes[0]
    broken.perspectives[0].quotes[0] = quote.model_copy(update={"context_note": "Trust me."})
    assert "i" in _checks(check_body(broken, corpus, None, envelope))


def test_fr8_immutable_region_edit_is_rejected(corpus, case):
    body, _rendered, envelope = case
    polished = envelope.replace("[[I:stance:christianity]]forbidden", "[[I:stance:christianity]]permitted")
    assert "i" in _checks(check_body(body, corpus, None, envelope, polished))


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda t: t.replace("Yea, yea; Nay, nay", "Yea, yea; Nay, no"), "quote text"),
        (lambda t: t.replace("— Matthew 5:37 —", "— Matthew 5:39 —"), "locator"),
        (lambda t: t.replace("(1611)", "(1901)"), "translation year"),
        (lambda t: t.replace("kjv-matthew-5-37 —", "kjv-matthew-9-99 —"), "citation table"),
    ],
)
def test_fr8_independent_reader_catches_print_tampering(corpus, case, mutate, why):
    """The reader re-parses the printed page and resolves it against the raw
    files: it never sees the composer's objects, so a corrupted render cannot
    agree with it by construction."""
    _body, rendered, _envelope = case
    assert check_render_independently(rendered, corpus.raw) == []
    assert check_render_independently(mutate(rendered), corpus.raw), why


def test_fr8_every_topic_and_filter_verifies(corpus, compose):
    """The whole render matrix passes both instruments: 24 topics x 4 filters.
    This is the regression net under the corpus itself — a bad edit to any
    passage, source or position shows up here."""
    traditions = [t.id for t in corpus.traditions]
    filters = [None, traditions[:3], [traditions[5]], ["judaism", "islam", "stoicism"]]
    checked = 0
    for topic in corpus.topics:
        for tradition_filter in filters:
            body, rendered = compose(topic.id, tradition_filter)
            envelope = env_mod.serialize(body, tradition_filter)
            failures = verify(body, corpus, rendered, tradition_filter, envelope, None)
            assert failures == [], f"{topic.id} / {tradition_filter}: {failures}"
            checked += 1
    assert checked == len(corpus.topics) * len(filters)


def test_fr8_independent_reader_catches_a_missing_citation(corpus, case):
    _body, rendered, _envelope = case
    start = rendered.index("      “")
    end = rendered.index("      Context:", start)
    trimmed = rendered[:start] + rendered[end:]
    assert check_render_independently(trimmed, corpus.raw)

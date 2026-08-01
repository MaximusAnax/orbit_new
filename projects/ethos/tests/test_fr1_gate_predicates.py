"""FR-1 / EVALS: the gate predicates whose evidence arrives from outside the
corpus (C9, C17, C18, C19, C20). The eval stage feeds them fixtures; here they
are proved to accept the good case and reject the bad one."""
from __future__ import annotations

from ethos.engine.corpus import (
    check_c9_fixture_questions,
    check_c17_safeguard_cells,
    check_c18_stemmer,
    check_c19_baselines,
    check_c20_oos_composition,
    topic_documents,
)
from ethos.engine.normalize import porter_stem


def test_c9_accepts_a_clean_oblique_question(corpus):
    question = {
        "id": "q1", "text": "my sister asked if her cooking was good and it was awful",
        "truth_topic": "honesty_and_deception", "tier": "oblique",
    }
    assert check_c9_fixture_questions(corpus, [question]) == []


def test_c9_rejects_an_oblique_question_that_reuses_topic_vocabulary(corpus):
    docs = topic_documents(corpus)
    assert "deceiv" in docs["honesty_and_deception"] or docs["honesty_and_deception"]
    question = {
        "id": "q2", "text": "is deception of a friend acceptable",
        "truth_topic": "honesty_and_deception", "tier": "oblique",
    }
    assert check_c9_fixture_questions(corpus, [question])


def test_c9_rejects_a_colloquial_restatement_of_a_question_form(corpus):
    form = corpus.topic_by_id["divorce"].question_forms[0]
    question = {"id": "q3", "text": form, "truth_topic": "divorce", "tier": "colloquial"}
    assert check_c9_fixture_questions(corpus, [question])


def test_c9_rejects_an_unknown_truth_topic(corpus):
    question = {"id": "q4", "text": "anything", "truth_topic": "not_a_topic", "tier": "direct"}
    assert check_c9_fixture_questions(corpus, [question])


def test_c17_accepts_a_complete_cell_and_rejects_a_missing_block():
    expected = [{"id": "crisis_resources", "kind": "crisis_resources", "text": "help"}]
    good = {"cell": "a", "expected": expected, "got": expected, "first": True}
    assert check_c17_safeguard_cells([good]) == []
    assert check_c17_safeguard_cells([{**good, "got": []}])
    assert check_c17_safeguard_cells([{**good, "first": False}])


def test_c18_accepts_the_real_stemmer_and_rejects_a_wrong_pair():
    assert check_c18_stemmer([("caresses", "caress"), ("ponies", "poni")], porter_stem) == []
    assert check_c18_stemmer([("caresses", "caresse")], porter_stem)


def test_c19_flags_moved_numbers_and_changed_hashes():
    recorded = {"m1_direct": 0.98, "router_sha": "abc"}
    assert check_c19_baselines(recorded, {"m1_direct": 0.99, "router_sha": "abc"}) == []
    assert check_c19_baselines(recorded, {"m1_direct": 0.80, "router_sha": "abc"})
    assert check_c19_baselines(recorded, {"m1_direct": 0.98, "router_sha": "def"})
    assert check_c19_baselines(recorded, {"m1_direct": 0.98})


def test_c20_requires_a_lexically_hard_out_of_scope_set():
    direct = [1.0, 2.0, 3.0, 4.0, 5.0]
    trivial = [0.0] * 40
    hard = [9.0] * 26 + [0.0] * 14
    assert check_c20_oos_composition(trivial, direct)
    assert check_c20_oos_composition(hard, direct) == []

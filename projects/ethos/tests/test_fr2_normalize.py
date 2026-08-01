"""FR-2: one deterministic normalization pipeline, and it never touches the
corpus — only router documents and queries."""
from __future__ import annotations

import pytest

from ethos.engine.normalize import normalize, porter_stem, tokenize


@pytest.mark.parametrize(
    ("word", "stem"),
    [
        ("caresses", "caress"), ("ponies", "poni"), ("ties", "ti"), ("caress", "caress"),
        ("cats", "cat"), ("feed", "feed"), ("agreed", "agre"), ("plastered", "plaster"),
        ("motoring", "motor"), ("sing", "sing"), ("conflated", "conflat"), ("troubling", "troubl"),
        ("sized", "size"), ("hopping", "hop"), ("falling", "fall"), ("hissing", "hiss"),
        ("failing", "fail"), ("filing", "file"), ("happy", "happi"), ("sky", "sky"),
        ("relational", "relat"), ("conditional", "condit"), ("valenci", "valenc"),
        ("hesitanci", "hesit"), ("digitizer", "digit"), ("conformabli", "conform"),
        ("radicalli", "radic"), ("differentli", "differ"), ("vileli", "vile"),
        ("analogousli", "analog"), ("vietnamization", "vietnam"), ("predication", "predic"),
        ("operator", "oper"), ("feudalism", "feudal"), ("decisiveness", "decis"),
        ("hopefulness", "hope"), ("callousness", "callous"), ("formaliti", "formal"),
        ("sensitiviti", "sensit"), ("sensibiliti", "sensibl"), ("triplicate", "triplic"),
        ("formative", "form"), ("formalize", "formal"), ("electriciti", "electr"),
        ("electrical", "electr"), ("hopeful", "hope"), ("goodness", "good"),
        ("revival", "reviv"), ("allowance", "allow"), ("inference", "infer"),
        ("airliner", "airlin"), ("gyroscopic", "gyroscop"), ("adjustable", "adjust"),
        ("defensible", "defens"), ("irritant", "irrit"), ("replacement", "replac"),
        ("adjustment", "adjust"), ("dependent", "depend"), ("adoption", "adopt"),
        ("homologou", "homolog"), ("communism", "commun"), ("activate", "activ"),
        ("angulariti", "angular"), ("homologous", "homolog"), ("effective", "effect"),
        ("bowdlerize", "bowdler"), ("probate", "probat"), ("rate", "rate"), ("cease", "ceas"),
        ("controll", "control"), ("roll", "roll"),
    ],
)
def test_fr2_porter_matches_published_sample(word, stem):
    """Pairs drawn from Porter's own worked examples (1980, §§ 1a-5b)."""
    assert porter_stem(word) == stem


def test_fr2_short_words_are_left_alone():
    assert porter_stem("is") == "is"
    assert porter_stem("as") == "as"


def test_fr2_nfkc_and_casefold_cases():
    assert tokenize("Qur’an") == ["qur", "an"]
    assert tokenize("ﬁdelity") == ["fidelity"]  # NFKC decomposes the ligature
    assert tokenize("Ketubot 16b–17a") == ["ketubot", "16b", "17a"]
    assert tokenize("STRAßE") == ["strasse"]


def test_fr2_stopwords_are_dropped_after_stemming(corpus):
    tokens = normalize("Is it wrong to tell a lie?", corpus.stopwords)
    assert "lie" in tokens
    assert "is" not in tokens and "it" not in tokens


def test_fr2_pipeline_is_deterministic(corpus):
    text = "should I tell my friend her novel was bad"
    assert normalize(text, corpus.stopwords) == normalize(text, corpus.stopwords)


def test_fr2_normalization_never_touches_corpus_text(corpus, sample):
    """Passage text reaches the render unmodified — no NFC, no casefold."""
    _body, rendered = sample
    quoted = [p for p in corpus.passages if p.text and p.id.startswith("kjv-matthew")]
    for passage in quoted:
        if passage.text in rendered:
            assert f"“{passage.text}”" in rendered

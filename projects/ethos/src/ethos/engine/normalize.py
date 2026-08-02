"""FR-2: deterministic text normalization for router documents and queries.

Pipeline: Unicode NFKC -> casefold -> tokenize on non-alphanumerics -> drop
stopwords -> Porter stem (Porter 1980, implemented to the published reference
behaviour, gated by C18 against a committed vocabulary/output sample).

Normalization NEVER touches corpus content: passage text, locators, and
source lines pass through the product unmodified end to end (FR-1 layer 2).
"""
from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[0-9a-z]+")

_VOWELS = "aeiou"


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """The Porter measure m: number of VC sequences in the stem."""
    m = 0
    prev_vowel = False
    for i in range(len(stem)):
        cons = _is_consonant(stem, i)
        if cons and prev_vowel:
            m += 1
        prev_vowel = not cons
    return m


def _contains_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return (
        len(word) >= 2
        and word[-1] == word[-2]
        and _is_consonant(word, len(word) - 1)
    )


def _cvc(word: str) -> bool:
    """True when the word ends consonant-vowel-consonant, last not w/x/y."""
    if len(word) < 3:
        return False
    if not _is_consonant(word, len(word) - 1):
        return False
    if _is_consonant(word, len(word) - 2):
        return False
    if not _is_consonant(word, len(word) - 3):
        return False
    return word[-1] not in "wxy"


def _step1a(w: str) -> str:
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("ies"):
        return w[:-2]
    if w.endswith("ss"):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


def _step1b(w: str) -> str:
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            return w[:-1]
        return w
    flag = False
    if w.endswith("ed") and _contains_vowel(w[:-2]):
        w = w[:-2]
        flag = True
    elif w.endswith("ing") and _contains_vowel(w[:-3]):
        w = w[:-3]
        flag = True
    if flag:
        if w.endswith(("at", "bl", "iz")):
            return w + "e"
        if _ends_double_consonant(w) and w[-1] not in "lsz":
            return w[:-1]
        if _measure(w) == 1 and _cvc(w):
            return w + "e"
    return w


def _step1c(w: str) -> str:
    if w.endswith("y") and _contains_vowel(w[:-1]):
        return w[:-1] + "i"
    return w


_STEP2 = [
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
    ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"),
    ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
    ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
    ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
]

_STEP3 = [
    ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
    ("ical", "ic"), ("ful", ""), ("ness", ""),
]

_STEP4 = [
    "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
    "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
]


def _longest_rule(w: str, rules: list[tuple[str, str]]) -> tuple[str, str] | None:
    best: tuple[str, str] | None = None
    for suffix, repl in rules:
        if w.endswith(suffix) and (best is None or len(suffix) > len(best[0])):
            best = (suffix, repl)
    return best


def _step2(w: str) -> str:
    hit = _longest_rule(w, _STEP2)
    if hit and _measure(w[: -len(hit[0])]) > 0:
        return w[: -len(hit[0])] + hit[1]
    return w


def _step3(w: str) -> str:
    hit = _longest_rule(w, _STEP3)
    if hit and _measure(w[: -len(hit[0])]) > 0:
        return w[: -len(hit[0])] + hit[1]
    return w


def _step4(w: str) -> str:
    best = None
    for suffix in _STEP4:
        if w.endswith(suffix) and (best is None or len(suffix) > len(best)):
            best = suffix
    if best is None:
        return w
    stem = w[: -len(best)]
    if _measure(stem) > 1:
        if best == "ion" and not stem.endswith(("s", "t")):
            return w
        return stem
    return w


def _step5(w: str) -> str:
    if w.endswith("e"):
        stem = w[:-1]
        m = _measure(stem)
        if m > 1 or (m == 1 and not _cvc(stem)):
            w = stem
    if w.endswith("ll") and _measure(w) > 1:
        w = w[:-1]
    return w


def porter_stem(word: str) -> str:
    """Stem one lowercase token exactly per the published Porter (1980) paper.

    Words of two letters or fewer are returned unchanged, as in Porter's own
    reference implementation ("the algorithm is not applied to words of length
    <= 2"); without this, `is` -> `i` and `as` -> `a` collide.
    """
    if len(word) <= 2:
        return word
    return _step5(_step4(_step3(_step2(_step1c(_step1b(_step1a(word)))))))


def tokenize(text: str) -> list[str]:
    """NFKC -> casefold -> alphanumeric tokens (no stopword drop, no stem)."""
    return _TOKEN_RE.findall(unicodedata.normalize("NFKC", text).casefold())


def normalize(text: str, stopwords: frozenset[str]) -> list[str]:
    """Full FR-2 pipeline: content tokens, stemmed, stopwords dropped."""
    return [porter_stem(t) for t in tokenize(text) if t not in stopwords]


def normalize_phrase(term: str, stopwords: frozenset[str]) -> list[str]:
    """Normalize a lexicon term, keeping interior stopwords out (same rule)."""
    return normalize(term, stopwords)

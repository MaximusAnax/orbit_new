"""The Porter (1980) suffix-stripping algorithm, implemented in the stdlib.

FR-3 (theme suggestion) and FR-12 (the LIKE search fallback) both specify
*stemmed* matching with the porter tokenizer, so the stemmer has to exist on
the pure side of the engine and be byte-identical to SQLite's ``porter``
tokenizer in spirit: same algorithm, no data files, no randomness.

Reference: M. F. Porter, "An algorithm for suffix stripping", Program 14(3),
1980, pp. 130-137.
"""

from __future__ import annotations

from functools import lru_cache

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """``m`` in Porter's notation: the number of VC sequences in ``stem``."""
    n = len(stem)
    i = 0
    while i < n and _is_consonant(stem, i):
        i += 1
    count = 0
    while i < n:
        while i < n and not _is_consonant(stem, i):
            i += 1
        if i >= n:
            break
        count += 1
        while i < n and _is_consonant(stem, i):
            i += 1
    return count


def _contains_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _is_consonant(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    """True when the word ends consonant-vowel-consonant, last not in w/x/y."""
    if len(word) < 3:
        return False
    last = len(word) - 1
    if not _is_consonant(word, last):
        return False
    if _is_consonant(word, last - 1):
        return False
    if not _is_consonant(word, last - 2):
        return False
    return word[last] not in "wxy"


def _try_replace(word: str, suffix: str, repl: str, min_measure: int) -> str | None:
    """Replace ``suffix`` with ``repl`` when the remaining stem has m > min_measure."""
    if not word.endswith(suffix):
        return None
    stem = word[: len(word) - len(suffix)]
    if _measure(stem) > min_measure:
        return stem + repl
    return None


_STEP2: tuple[tuple[str, str], ...] = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("abli", "able"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
)

_STEP3: tuple[tuple[str, str], ...] = (
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ful", ""),
    ("ness", ""),
)

_STEP4: tuple[str, ...] = (
    "ement",
    "ance",
    "ence",
    "able",
    "ible",
    "ment",
    "ant",
    "ent",
    "ism",
    "ate",
    "iti",
    "ous",
    "ive",
    "ize",
    "ion",
    "al",
    "er",
    "ic",
    "ou",
)


def _step1a(word: str) -> str:
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies"):
        return word[:-2]
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def _step1b_tidy(word: str) -> str:
    if word.endswith("at") or word.endswith("bl") or word.endswith("iz"):
        return word + "e"
    if _ends_double_consonant(word) and word[-1] not in "lsz":
        return word[:-1]
    if _measure(word) == 1 and _ends_cvc(word):
        return word + "e"
    return word


def _step1b(word: str) -> str:
    if word.endswith("eed"):
        stem = word[:-3]
        if _measure(stem) > 0:
            return word[:-1]
        return word
    for suffix in ("ed", "ing"):
        if word.endswith(suffix):
            stem = word[: len(word) - len(suffix)]
            if _contains_vowel(stem):
                return _step1b_tidy(stem)
            return word
    return word


def _step1c(word: str) -> str:
    if word.endswith("y") and _contains_vowel(word[:-1]):
        return word[:-1] + "i"
    return word


def _step2(word: str) -> str:
    for suffix, repl in _STEP2:
        out = _try_replace(word, suffix, repl, 0)
        if out is not None:
            return out
    return word


def _step3(word: str) -> str:
    for suffix, repl in _STEP3:
        out = _try_replace(word, suffix, repl, 0)
        if out is not None:
            return out
    return word


def _step4(word: str) -> str:
    for suffix in _STEP4:
        if not word.endswith(suffix):
            continue
        stem = word[: len(word) - len(suffix)]
        if suffix == "ion" and not (stem.endswith("s") or stem.endswith("t")):
            continue
        if _measure(stem) > 1:
            return stem
        return word
    return word


def _step5(word: str) -> str:
    if word.endswith("e"):
        stem = word[:-1]
        measure = _measure(stem)
        if measure > 1 or (measure == 1 and not _ends_cvc(stem)):
            word = stem
    if _measure(word) > 1 and _ends_double_consonant(word) and word.endswith("l"):
        word = word[:-1]
    return word


@lru_cache(maxsize=8192)
def stem(word: str) -> str:
    """Return the Porter stem of an already-normalized lowercase token."""
    if len(word) <= 2 or not word.isalpha():
        return word
    out = _step1a(word)
    out = _step1b(out)
    out = _step1c(out)
    out = _step2(out)
    out = _step3(out)
    out = _step4(out)
    out = _step5(out)
    return out


def stem_all(tokens: list[str]) -> list[str]:
    return [stem(t) for t in tokens]
</content>

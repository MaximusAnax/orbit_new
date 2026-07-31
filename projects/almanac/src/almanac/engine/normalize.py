"""FR-1 text normalization: the canonical form behind hashing and matching.

Pure: no I/O, no clock, no randomness.  The pipeline is

    NFKC -> quote folding -> NFKD + combining-mark removal -> casefold
         -> apostrophe removal -> non-alphanumeric to space -> whitespace collapse

DATA_MODEL.md names "NFKC -> casefold -> strip punctuation/whitespace"; FR-4
additionally requires that a *diacritic* fix leave ``normalized_hash``
unchanged, which plain NFKC does not deliver, hence the NFKD + mark-stripping
step.  Both requirements are satisfied by the pipeline above.
"""

from __future__ import annotations

import hashlib
import unicodedata

from almanac.engine.stemmer import stem

_QUOTE_FOLD = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "ʼ": "'",
        "´": "'",
        "`": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)


def normalize_text(text: str) -> str:
    """Return the canonical normalized form of ``text``."""
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTE_FOLD)
    decomposed = unicodedata.normalize("NFKD", folded)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.casefold().replace("'", "")
    cleaned = "".join(ch if ch.isalnum() else " " for ch in lowered)
    return " ".join(cleaned.split())


def normalized_hash(text: str) -> str:
    """SHA-256 hex digest of the normalized form (FR-1 duplicate detection)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    """Normalized whitespace tokens."""
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def stemmed_tokens(text: str) -> list[str]:
    """Normalized tokens, Porter-stemmed (FR-3 / FR-12 matching)."""
    return [stem(token) for token in tokenize(text)]


def normalize_author(author: str | None) -> str:
    """Canonical author string used by the FR-2 claimed-author comparison."""
    return normalize_text(author) if author else ""


def normalize_tag(name: str) -> str:
    """FR-1 tag normalization: casefold, whitespace runs become single hyphens."""
    folded = unicodedata.normalize("NFKC", name).translate(_QUOTE_FOLD).casefold().strip()
    collapsed = "-".join(folded.split())
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in collapsed)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def phrase_occurrences(tokens: list[str], phrase: list[str]) -> int:
    """Count contiguous occurrences of ``phrase`` inside ``tokens``."""
    if not phrase or len(phrase) > len(tokens):
        return 0
    width = len(phrase)
    return sum(1 for i in range(len(tokens) - width + 1) if tokens[i : i + width] == phrase)


def contains_phrase(tokens: list[str], phrase: list[str]) -> bool:
    """True when ``phrase`` appears contiguously in ``tokens`` (FR-2 match rule)."""
    return phrase_occurrences(tokens, phrase) > 0
</content>

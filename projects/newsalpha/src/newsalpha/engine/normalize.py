"""FR-1: text normalization, content hashing, ids and sentence segmentation.

Pure: no clock, no filesystem, no network.  Every function here is a total
function of its arguments.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^<>]*>")
_WS_RE = re.compile(r"\s+")
_SENTENCE_BREAK_RE = re.compile(r"([.!?]+)([ \t\n\r]+)(?=[A-Z0-9\"'“])")


def normalize_text(raw: str) -> str:
    """Deterministic HTML strip -> entity decode -> NFKC -> whitespace collapse (FR-1).

    No external parser: `<script>`/`<style>` blocks are dropped whole, remaining
    tags are replaced by a single space so word boundaries survive, HTML entities
    are decoded, the result is NFKC-normalized and whitespace-collapsed.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip()


def content_hash(normalized_title: str, normalized_body: str) -> str:
    """`sha256(normalized_title + "\\n" + normalized_body)` (FR-1)."""
    payload = f"{normalized_title}\n{normalized_body}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hex16(value: str) -> str:
    """`hex16(x) = sha256(x)[:16]` -- the id scheme of DATA_MODEL.md."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def canonical_json(value: Any) -> str:
    """Canonical (sorted-keys, compact) JSON, so byte-identity is well defined."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Sentence:
    """A sentence of an article's analysis text, with absolute char offsets."""

    start: int
    end: int
    text: str

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


def split_sentences(text: str, abbreviations: tuple[str, ...] | list[str] = ()) -> list[Sentence]:
    """Committed deterministic sentence rule (FR-1).

    Split on ``[.!?]`` + whitespace + uppercase/digit/quote, except when the token
    ending at the punctuation is in the abbreviation exception list.  Newlines are
    always sentence boundaries (the analysis text joins title and body with one),
    so a headline is its own sentence.
    """
    abbrevs = {a.casefold() for a in abbreviations}
    boundaries: list[int] = []
    for index, char in enumerate(text):
        if char == "\n":
            boundaries.append(index + 1)
    for match in _SENTENCE_BREAK_RE.finditer(text):
        punct_end = match.end(1)
        if _ends_with_abbreviation(text, punct_end, abbrevs):
            continue
        boundaries.append(match.end(2))
    return _slice_sentences(text, boundaries)


def _ends_with_abbreviation(text: str, punct_end: int, abbrevs: set[str]) -> bool:
    """True when the word ending at `punct_end` (inclusive of the dot) is an abbreviation."""
    start = punct_end - 1
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    token = text[start:punct_end]
    return token.casefold() in abbrevs


def _slice_sentences(text: str, boundaries: list[int]) -> list[Sentence]:
    cuts = sorted({b for b in boundaries if 0 < b < len(text)})
    sentences: list[Sentence] = []
    prev = 0
    for cut in [*cuts, len(text)]:
        raw = text[prev:cut]
        stripped = raw.strip()
        if stripped:
            start = prev + (len(raw) - len(raw.lstrip()))
            end = start + len(stripped)
            sentences.append(Sentence(start=start, end=end, text=text[start:end]))
        prev = cut
    return sentences


def sentence_at(sentences: list[Sentence], offset: int) -> Sentence | None:
    """The sentence containing `offset`, or None when the offset falls in trimmed space."""
    for sentence in sentences:
        if sentence.contains(offset):
            return sentence
    return None


# --------------------------------------------------------------------------- #
# Time helpers -- parsing only; the clock is never read here.
# --------------------------------------------------------------------------- #


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a trailing `Z`) into an aware UTC datetime."""
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    """Render an aware datetime as `YYYY-MM-DDTHH:MM:SSZ`."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_date(value: str) -> str:
    """The UTC date part of an ISO timestamp."""
    return parse_iso(value).date().isoformat()


__all__ = [
    "Sentence",
    "canonical_json",
    "content_hash",
    "hex16",
    "iso_date",
    "iso_utc",
    "normalize_text",
    "parse_iso",
    "sentence_at",
    "split_sentences",
]

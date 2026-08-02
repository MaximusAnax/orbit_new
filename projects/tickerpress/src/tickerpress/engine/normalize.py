"""Text normalization, sentence splitting and tokenization (SCOPE FR-3).

Pure: no I/O, no clock, no randomness. Everything downstream — candidate
offsets, the +/-12-token cue window, the lede rule, dedup shingles — is defined
against the *normalized* text produced here, so this module fixes the
coordinate system for the rest of the engine.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from html.parser import HTMLParser

from ..resources import Lexicons
from .models import TextField

__all__ = [
    "ArticleText",
    "FieldText",
    "Sentence",
    "Token",
    "analyze_article_text",
    "analyze_field",
    "normalize_text",
    "split_sentences",
    "strip_html",
    "tokenize",
]

#: Characters folded to an ASCII equivalent before matching (FR-3).
_FOLD_MAP = {
    0x2018: "'",  # left single quotation mark
    0x2019: "'",  # right single quotation mark
    0x201A: "'",  # single low-9 quotation mark
    0x201B: "'",  # single high-reversed-9 quotation mark
    0x2032: "'",  # prime
    0x201C: '"',  # left double quotation mark
    0x201D: '"',  # right double quotation mark
    0x201E: '"',  # double low-9 quotation mark
    0x201F: '"',  # double high-reversed-9 quotation mark
    0x2033: '"',  # double prime
    0x2010: "-",  # hyphen
    0x2011: "-",  # non-breaking hyphen
    0x2012: "-",  # figure dash
    0x2013: "-",  # en dash
    0x2014: "-",  # em dash
    0x2015: "-",  # horizontal bar
    0x2212: "-",  # minus sign
    0x2026: "...",  # horizontal ellipsis
    0x00A0: " ",  # no-break space
    0x2007: " ",  # figure space
    0x202F: " ",  # narrow no-break space
    0x200B: "",  # zero-width space
    0x200C: "",  # zero-width non-joiner
    0x200D: "",  # zero-width joiner
    0xFEFF: "",  # byte-order mark
    0x00AD: "",  # soft hyphen
}

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_SENTENCE_END = ".!?"
_SKIP_TAGS = frozenset({"script", "style"})


class _TextExtractor(HTMLParser):
    """Collect text, replacing every tag with a space so tags split tokens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(raw: str) -> str:
    """Remove HTML markup and decode character references."""

    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


def normalize_text(raw: str | None) -> str:
    """HTML strip -> entity decode -> NFC -> quote/dash fold -> whitespace collapse."""

    if not raw:
        return ""
    text = strip_html(raw)
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_FOLD_MAP)
    return " ".join(text.split())


# --------------------------------------------------------------------------
# tokenization
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Token:
    """A token and its character offsets in the normalized field text."""

    text: str
    start: int
    end: int
    index: int

    @property
    def folded(self) -> str:
        return self.text.casefold()

    @property
    def is_allcaps(self) -> bool:
        return self.text.isupper() and any(ch.isalpha() for ch in self.text)


@dataclass(frozen=True, slots=True)
class Sentence:
    """A sentence span plus the token indices it covers (``last`` inclusive)."""

    start: int
    end: int
    first_token: int
    last_token: int


def _abbreviation_regex(abbreviations: frozenset[str]) -> re.Pattern[str] | None:
    if not abbreviations:
        return None
    ordered = sorted(abbreviations, key=lambda item: (-len(item), item))
    return re.compile("|".join(re.escape(item) for item in ordered), re.IGNORECASE)


def _is_word_char(text: str, pos: int) -> bool:
    return 0 <= pos < len(text) and (text[pos].isalnum() or text[pos] == "_")


def tokenize(text: str, lexicons: Lexicons) -> tuple[Token, ...]:
    """Split ``text`` into tokens, preserving character offsets.

    Tokens are maximal runs of word characters. ``-``, ``/`` and ``.`` are
    separators, so ``Meta-analysis`` yields ``Meta`` and ``analysis`` and
    ``Meta`` stays matchable — *except* that a ``.`` inside a committed
    abbreviation is kept, so ``U.S.`` is a single token.
    """

    abbrev_re = _abbreviation_regex(lexicons.abbreviations)
    tokens: list[Token] = []
    pos = 0
    length = len(text)
    while pos < length:
        if not (text[pos].isalnum() or text[pos] == "_"):
            pos += 1
            continue
        matched: str | None = None
        if abbrev_re is not None:
            candidate = abbrev_re.match(text, pos)
            if candidate is not None and not _is_word_char(text, candidate.end()):
                matched = candidate.group()
        if matched is None:
            word = _WORD_RE.match(text, pos)
            if word is None:  # pragma: no cover - guarded by the isalnum test
                pos += 1
                continue
            matched = word.group()
        tokens.append(Token(text=matched, start=pos, end=pos + len(matched), index=len(tokens)))
        pos += len(matched)
    return tuple(tokens)


# --------------------------------------------------------------------------
# sentence splitting
# --------------------------------------------------------------------------


def _ends_with_abbreviation(text: str, end: int, abbreviations: frozenset[str]) -> bool:
    """True if the token ending at ``end`` (exclusive) is a known abbreviation."""

    start = end
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._"):
        start -= 1
    return text[start:end].casefold() in abbreviations


def split_sentences(text: str, lexicons: Lexicons) -> tuple[Sentence, ...]:
    """Deterministic sentence split: ``[.!?]`` + space + uppercase.

    A terminator that closes a committed abbreviation does not split, so
    ``Apple Inc. said Tuesday`` stays one sentence. Token indices are attached
    so callers can ask "is this mention sentence-initial?" without re-scanning.
    """

    tokens = tokenize(text, lexicons)
    spans: list[tuple[int, int]] = []
    start = 0
    pos = 0
    length = len(text)
    while pos < length:
        char = text[pos]
        if char in _SENTENCE_END:
            after = pos + 1
            if after < length and text[after].isspace():
                nxt = after
                while nxt < length and text[nxt].isspace():
                    nxt += 1
                if (
                    nxt < length
                    and text[nxt].isupper()
                    and not _ends_with_abbreviation(text, after, lexicons.abbreviations)
                ):
                    spans.append((start, after))
                    start = nxt
                    pos = nxt
                    continue
        pos += 1
    if start < length:
        spans.append((start, length))

    sentences: list[Sentence] = []
    for span_start, span_end in spans:
        if not text[span_start:span_end].strip():
            continue
        covered = [t.index for t in tokens if t.start >= span_start and t.end <= span_end]
        sentences.append(
            Sentence(
                start=span_start,
                end=span_end,
                first_token=covered[0] if covered else -1,
                last_token=covered[-1] if covered else -1,
            )
        )
    return tuple(sentences)


# --------------------------------------------------------------------------
# field analysis
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldText:
    """Normalized text of one article field with its derived structure."""

    field: TextField
    text: str
    tokens: tuple[Token, ...]
    sentences: tuple[Sentence, ...]
    allcaps_token_indices: frozenset[int]
    _token_starts: tuple[int, ...]

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    def token_span(self, char_start: int, char_end: int) -> tuple[int, int] | None:
        """Indices of the first and last tokens overlapping ``[start, end)``."""

        first: int | None = None
        last: int | None = None
        idx = max(0, bisect_right(self._token_starts, char_start) - 1)
        for token in self.tokens[idx:]:
            if token.start >= char_end:
                break
            if token.end > char_start:
                if first is None:
                    first = token.index
                last = token.index
        if first is None or last is None:
            return None
        return first, last

    def sentence_of_token(self, token_index: int) -> Sentence | None:
        for sentence in self.sentences:
            if sentence.first_token <= token_index <= sentence.last_token:
                return sentence
        return None

    def is_sentence_initial(self, token_index: int) -> bool:
        sentence = self.sentence_of_token(token_index)
        return sentence is not None and sentence.first_token == token_index

    def in_allcaps_run(self, first_token: int, last_token: int) -> bool:
        return any(
            index in self.allcaps_token_indices for index in range(first_token, last_token + 1)
        )


def _allcaps_runs(tokens: tuple[Token, ...], minimum: int = 3) -> frozenset[int]:
    """Token indices belonging to a run of >= ``minimum`` consecutive caps tokens."""

    indices: set[int] = set()
    run: list[int] = []
    for token in tokens:
        if token.is_allcaps:
            run.append(token.index)
            continue
        if len(run) >= minimum:
            indices.update(run)
        run = []
    if len(run) >= minimum:
        indices.update(run)
    return frozenset(indices)


def analyze_field(
    field: TextField,
    text: str,
    lexicons: Lexicons,
    *,
    single_sentence: bool = False,
) -> FieldText:
    """Tokenize and sentence-split one already-normalized field.

    ``single_sentence`` is used for the title, which FR-6 rule 1 treats as one
    sentence container (so its first token *is* sentence-initial).
    """

    tokens = tokenize(text, lexicons)
    if single_sentence:
        sentences = (
            (Sentence(start=0, end=len(text), first_token=0, last_token=len(tokens) - 1),)
            if tokens
            else ()
        )
    else:
        sentences = split_sentences(text, lexicons)
    return FieldText(
        field=field,
        text=text,
        tokens=tokens,
        sentences=sentences,
        allcaps_token_indices=_allcaps_runs(tokens),
        _token_starts=tuple(token.start for token in tokens),
    )


@dataclass(frozen=True)
class ArticleText:
    """The three analysed fields of one article.

    ``content`` is ``None`` when the feed carried no ``content:encoded`` /
    ``atom:content`` element, which is exactly the case DATA_MODEL §2.5 ties to
    ``content_token_count == 0`` and FR-8 ties to the summary-only lede path.
    """

    title: FieldText
    summary: FieldText
    content: FieldText | None

    @property
    def fields(self) -> tuple[FieldText, ...]:
        if self.content is None:
            return (self.title, self.summary)
        return (self.title, self.summary, self.content)

    @property
    def token_count(self) -> int:
        return sum(field.token_count for field in self.fields)

    @property
    def content_token_count(self) -> int:
        return 0 if self.content is None else self.content.token_count

    def field_of(self, field: TextField) -> FieldText | None:
        if field is TextField.TITLE:
            return self.title
        if field is TextField.SUMMARY:
            return self.summary
        return self.content


def analyze_article_text(
    title: str,
    summary: str,
    content: str | None,
    lexicons: Lexicons,
) -> ArticleText:
    """Analyse already-normalized field texts (FR-6 rule 1: the title is one sentence)."""

    return ArticleText(
        title=analyze_field(TextField.TITLE, title, lexicons, single_sentence=True),
        summary=analyze_field(TextField.SUMMARY, summary, lexicons),
        content=(None if content is None else analyze_field(TextField.CONTENT, content, lexicons)),
    )

"""FR-7: the framing safeguard, as implemented behavior.

A brief that fails this check is never persisted or emitted -- the pipeline
raises.  Three assertions:

  1. **zero forbidden-lexicon matches** in template-generated text.  Quoted
     evidence is exempt, but only when it is inside quotation marks *and*
     carries an attribution ("..." -- reuters.example);
  2. all four sections non-empty;
  3. the not-advice footer present verbatim.

Matching is word-boundary with the hyphen treated as a word character, so
"buyout", "buyer", "sell-off" and "sell-side" pass while a bare imperative fails
(SCOPE D-9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..datasets import forbidden_regexes

#: A quoted run followed by an attribution: `"..." -- domain` or `"..." (domain)`.
_ATTRIBUTED_QUOTE_RE = re.compile(
    r"[\"“]([^\"“”]*)[\"”]\s*(?:[-\u2013\u2014]{1,2}\s*|\(\s*)"
    r"[A-Za-z0-9][A-Za-z0-9._\- ]*"
)
_ANY_QUOTE_RE = re.compile(r"[\"“]([^\"“”]*)[\"”]")


class FrameCheckError(ValueError):
    """Raised when a rendered brief fails the FR-7 frame check."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = list(violations)


@dataclass(frozen=True, slots=True)
class FrameCheckResult:
    ok: bool
    violations: tuple[str, ...]


def attributed_quote_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of quoted *and attributed* evidence -- the only exempt regions."""
    return [(m.start(1), m.end(1)) for m in _ATTRIBUTED_QUOTE_RE.finditer(text)]


def unattributed_quote_spans(text: str) -> list[tuple[int, int]]:
    """Quoted runs with no attribution following them."""
    attributed = attributed_quote_spans(text)
    spans: list[tuple[int, int]] = []
    for match in _ANY_QUOTE_RE.finditer(text):
        span = (match.start(1), match.end(1))
        if span not in attributed:
            spans.append(span)
    return spans


def check_text(text: str, forbidden_lexicon: tuple[str, ...] | list[str]) -> list[str]:
    """Forbidden-lexicon violations in `text`, ignoring quoted+attributed evidence."""
    exempt = attributed_quote_spans(text)
    violations: list[str] = []
    for term, matcher in forbidden_regexes(forbidden_lexicon):
        for match in matcher.finditer(text):
            if any(start <= match.start() and match.end() <= end for start, end in exempt):
                continue
            violations.append(f"forbidden term {term!r} at offset {match.start()}")
    return violations


def check_brief(
    *,
    what_happened: str,
    why_it_matters: str,
    what_to_watch: list[str] | tuple[str, ...],
    uncertainty_note: str,
    rendered_text: str,
    footer: str,
    forbidden_lexicon: tuple[str, ...] | list[str],
) -> FrameCheckResult:
    """The full FR-7 check over one rendered brief."""
    violations: list[str] = []

    sections = {
        "what_happened": what_happened,
        "why_it_matters": why_it_matters,
        "uncertainty_note": uncertainty_note,
    }
    for name, value in sections.items():
        if not value or not value.strip():
            violations.append(f"section {name} is empty")
    if not what_to_watch or any(not item.strip() for item in what_to_watch):
        violations.append("section what_to_watch is empty")

    if footer.strip() not in rendered_text:
        violations.append("not-advice footer missing or altered")

    violations.extend(check_text(rendered_text, forbidden_lexicon))
    return FrameCheckResult(ok=not violations, violations=tuple(violations))


def enforce(result: FrameCheckResult) -> None:
    """Raise unless the brief passed -- a failing brief must never reach the store."""
    if not result.ok:
        raise FrameCheckError(list(result.violations))


__all__ = [
    "FrameCheckError",
    "FrameCheckResult",
    "attributed_quote_spans",
    "check_brief",
    "check_text",
    "enforce",
    "unattributed_quote_spans",
]

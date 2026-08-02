"""FR-10: the personalizer output validator — the trust boundary for the LLM.

Every personalizer output, *including the offline identity personalizer*,
passes through :func:`validate_prompt`.  A failure means the personalized text
is discarded and the template rendering is served with
``personalize_fell_back = true``.

Scope of the guarantee (SCOPE.md FR-10, non-goal 13): these checks bound
*form* — structure, length, slot integrity, kind signature.  A fluent,
well-formed rewrite that says the wrong thing passes, which is why the
personalizer is opt-in and off by default.
"""

from __future__ import annotations

import re

from almanac.models import PromptKind, PromptValidation

MAX_CHARS = 400
MAX_WORDS = 60

_SLOT_RE = re.compile(r"\{[^{}]*\}")
_URL_RE = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(?:com|org|net|io|edu|gov|co|ai|dev|info|me)\b)",
    re.IGNORECASE,
)
#: FR-10(e): the Gollwitzer if-then scaffold, structural rather than stylistic.
ACT_SCAFFOLD = ("When ", " I will ")

_OK = PromptValidation(ok=True)


def _fail(check: str, reason: str) -> PromptValidation:
    return PromptValidation(ok=False, reason=reason, check=check)


def validate_prompt(
    text: str,
    kind: PromptKind,
    required_excerpt: str | None = None,
) -> PromptValidation:
    """Validate one personalizer output against the FR-10 contract.

    ``required_excerpt`` is the ``{text_short}`` excerpt the *rendered template*
    embedded, or ``None`` when the template did not use that slot — check (c)
    only applies in the former case.
    """
    # (a) non-empty, <= 400 chars, <= 60 words, single line
    if not text or not text.strip():
        return _fail("a", "prompt is empty")
    if len(text) > MAX_CHARS:
        return _fail("a", f"prompt is {len(text)} chars, limit is {MAX_CHARS}")
    words = text.split()
    if len(words) > MAX_WORDS:
        return _fail("a", f"prompt is {len(words)} words, limit is {MAX_WORDS}")
    if "\n" in text or "\r" in text:
        return _fail("a", "prompt must be a single line")

    # (b) no unfilled slots
    leftover = _SLOT_RE.search(text)
    if leftover is not None:
        return _fail("b", f"unfilled slot {leftover.group(0)!r} remains")

    # (c) the {text_short} excerpt survives verbatim when the template used it
    if required_excerpt and required_excerpt not in text:
        return _fail("c", "the quoted excerpt was altered or dropped")

    # (d) no URLs
    url = _URL_RE.search(text)
    if url is not None:
        return _fail("d", f"prompt contains a url-like token {url.group(0)!r}")

    # (e) kind signature preserved
    body = text.strip()
    if kind in (PromptKind.REFLECT, PromptKind.CONNECT) and not body.endswith("?"):
        return _fail("e", f"{kind.value} prompts must end with a question mark")
    if kind is PromptKind.ACT and not all(token in body for token in ACT_SCAFFOLD):
        return _fail("e", "act prompts must keep the 'When … I will …' scaffold")
    if kind is PromptKind.REFRAME:
        allowed = 1 if body.endswith("?") else 0
        if body.count("?") > allowed:
            return _fail("e", "reframe prompts may only end with a question mark")
    return _OK

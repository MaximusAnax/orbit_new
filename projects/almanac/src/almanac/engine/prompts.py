"""FR-9: application-prompt selection and rendering (hard part B).

For a surfacing of entry ``e`` with prior exposure count ``n`` and last
reflection grade ``g``:

1. kind   — base ``[reflect, act, reframe, connect][n mod 4]``; ``resonated``
            overrides to ``act`` (convert what landed into an if-then plan),
            ``applied`` overrides to ``connect`` (consolidate and generalize).
2. pool   — templates whose theme is one of the entry's themes, or the
            ``general`` pool iff the entry has no themes; minus the template
            ids of the entry's last ``prompt_reuse_window`` surfacings.
3. pick   — argmax of a seeded per-(date, entry, template) hash, ties by id.
4. render — fill ``{author} {theme_name} {source} {text_short}``; no slot may
            remain unfilled.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from almanac.engine.jitter import hash_u64
from almanac.models import (
    GENERAL_THEME_ID,
    KIND_ROTATION,
    Grade,
    PromptKind,
    PromptTemplate,
)

#: ``{text_short}`` renders the first N words of the entry text.
TEXT_SHORT_WORDS = 12

AUTHOR_FALLBACK = "the author"
SOURCE_FALLBACK = "this"
THEME_NAME_FALLBACK = "this idea"

_GRADE_KIND_OVERRIDE: dict[Grade, PromptKind] = {
    Grade.RESONATED: PromptKind.ACT,
    Grade.APPLIED: PromptKind.CONNECT,
}


class PromptSelectionError(RuntimeError):
    """Raised when no template can serve an entry — a data-floor violation."""


@dataclass(frozen=True)
class PromptSelection:
    template: PromptTemplate
    recency_relaxed: bool


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    template: PromptTemplate
    recency_relaxed: bool
    excerpt: str | None
    """The ``{text_short}`` value when the template embedded it (FR-10 check c)."""


def text_short(text: str, max_words: int = TEXT_SHORT_WORDS) -> str:
    """First ``max_words`` words of ``text``, with an ellipsis when truncated."""
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "…"


def base_kind(prior_exposures: int) -> PromptKind:
    """The rotation kind for an entry that has been surfaced ``n`` times."""
    return KIND_ROTATION[prior_exposures % len(KIND_ROTATION)]


def choose_kind(prior_exposures: int, last_grade: Grade | None) -> PromptKind:
    """FR-9 step 1: rotation, overridden by the last reflection grade."""
    if last_grade is not None and last_grade in _GRADE_KIND_OVERRIDE:
        return _GRADE_KIND_OVERRIDE[last_grade]
    return base_kind(prior_exposures)


def theme_pool(
    templates: Sequence[PromptTemplate], theme_ids: Sequence[str]
) -> list[PromptTemplate]:
    """FR-9 step 2: the candidate pool before recency and kind filtering."""
    if theme_ids:
        allowed = set(theme_ids)
        return [t for t in templates if t.theme_id in allowed]
    return [t for t in templates if t.theme_id == GENERAL_THEME_ID]


def _kind_order(chosen: PromptKind) -> list[PromptKind]:
    start = KIND_ROTATION.index(chosen)
    return [KIND_ROTATION[(start + offset) % len(KIND_ROTATION)] for offset in range(4)]


def _pick(
    candidates: Sequence[PromptTemplate], seed: int, on_date: dt.date, entry_id: str
) -> PromptTemplate:
    return min(
        candidates,
        key=lambda t: (-hash_u64(seed, on_date, entry_id, t.id), t.id),
    )


def select_template(
    templates: Sequence[PromptTemplate],
    theme_ids: Sequence[str],
    kind: PromptKind,
    recent_template_ids: Iterable[str],
    seed: int,
    on_date: dt.date,
    entry_id: str,
) -> PromptSelection:
    """FR-9 steps 2-3.

    Tries the chosen kind, then the remaining kinds in rotation order.  If the
    recency-filtered pool is exhausted across all four kinds, drops the recency
    exclusion, retries once, and reports ``recency_relaxed = True`` so the
    surfacing can stamp ``prompt_recency_relaxed``.
    """
    pool = theme_pool(templates, theme_ids)
    if not pool:
        raise PromptSelectionError(
            f"no templates available for themes {list(theme_ids) or [GENERAL_THEME_ID]}"
        )
    excluded = set(recent_template_ids)
    order = _kind_order(kind)
    for relaxed in (False, True):
        available = pool if relaxed else [t for t in pool if t.id not in excluded]
        for candidate_kind in order:
            candidates = [t for t in available if t.kind is candidate_kind]
            if candidates:
                return PromptSelection(_pick(candidates, seed, on_date, entry_id), relaxed)
    raise PromptSelectionError(f"template pool exhausted for entry {entry_id}")


def render_template(
    template: PromptTemplate,
    entry_text: str,
    author: str | None = None,
    source: str | None = None,
    theme_name: str | None = None,
) -> tuple[str, str | None]:
    """FR-9 step 4. Returns the rendered text and the embedded excerpt (if any)."""
    excerpt = text_short(entry_text)
    values = {
        "{author}": author or AUTHOR_FALLBACK,
        "{source}": source or SOURCE_FALLBACK,
        "{theme_name}": theme_name or THEME_NAME_FALLBACK,
        "{text_short}": excerpt,
    }
    rendered = template.template
    used_excerpt = "{text_short}" in rendered
    for slot, value in values.items():
        rendered = rendered.replace(slot, value)
    return rendered, (excerpt if used_excerpt else None)


def build_prompt(
    templates: Sequence[PromptTemplate],
    theme_ids: Sequence[str],
    theme_names: Sequence[str],
    prior_exposures: int,
    last_grade: Grade | None,
    recent_template_ids: Iterable[str],
    seed: int,
    on_date: dt.date,
    entry_id: str,
    entry_text: str,
    author: str | None = None,
    source: str | None = None,
) -> RenderedPrompt:
    """The whole of FR-9 for one surfacing: kind, candidates, pick, render."""
    kind = choose_kind(prior_exposures, last_grade)
    selection = select_template(
        templates, theme_ids, kind, recent_template_ids, seed, on_date, entry_id
    )
    theme_name = _theme_name_for(selection.template, theme_ids, theme_names)
    text, excerpt = render_template(selection.template, entry_text, author, source, theme_name)
    return RenderedPrompt(
        text=text,
        template=selection.template,
        recency_relaxed=selection.recency_relaxed,
        excerpt=excerpt,
    )


def _theme_name_for(
    template: PromptTemplate, theme_ids: Sequence[str], theme_names: Sequence[str]
) -> str | None:
    if template.theme_id == GENERAL_THEME_ID:
        return None
    for theme_id, name in zip(theme_ids, theme_names, strict=False):
        if theme_id == template.theme_id:
            return name
    return None

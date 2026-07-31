"""FR-3: the deterministic keyword theme suggester (hard part B).

Scoring is a transparent lexicon sum, on purpose (SCOPE.md D9): each theme
scores the weighted count of its lexicon terms found — as stemmed single
tokens or stemmed contiguous phrases — in the entry's text, tags and note.
The top three are *proposed*; nothing is ever silently assigned.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from almanac.engine.normalize import phrase_occurrences, stemmed_tokens
from almanac.models import Theme, ThemeSuggestion

#: Scores are rounded before comparison so float accumulation order can never
#: decide a tie that should be broken lexicographically by theme id.
_PRECISION = 6

MAX_THEMES_PER_ENTRY = 3


def suggestion_document(
    text: str, tags: Iterable[str] = (), note: str | None = None
) -> list[str]:
    """The stemmed token stream the suggester scores over."""
    parts = [text, " ".join(tags)]
    if note:
        parts.append(note)
    return stemmed_tokens(" ".join(p for p in parts if p))


def score_theme(tokens: Sequence[str], theme: Theme) -> float:
    """Weighted count of ``theme``'s lexicon hits in ``tokens``."""
    token_list = list(tokens)
    total = 0.0
    for term in theme.lexicon:
        needle = stemmed_tokens(term.term)
        if not needle:
            continue
        hits = phrase_occurrences(token_list, needle)
        if hits:
            total += term.weight * hits
    return round(total, _PRECISION)


def score_themes(
    themes: Sequence[Theme],
    text: str,
    tags: Iterable[str] = (),
    note: str | None = None,
) -> dict[str, float]:
    """Every theme's score, keyed by theme id."""
    tokens = suggestion_document(text, tags, note)
    return {theme.id: score_theme(tokens, theme) for theme in themes}


def suggest_themes(
    themes: Sequence[Theme],
    text: str,
    tags: Iterable[str] = (),
    note: str | None = None,
    limit: int = MAX_THEMES_PER_ENTRY,
    include_zero_scores: bool = False,
) -> list[ThemeSuggestion]:
    """Top-``limit`` theme proposals, highest score first, ties by theme id.

    Zero-score themes are omitted by default: proposing a theme whose lexicon
    never fired would make the rank-1 suggestion a lexicographic artifact.
    ``include_zero_scores=True`` returns a full-length list for callers (the
    CLI's ``--yes`` path) that want a padded top-3 and gate on ``score > 0``.
    """
    scores = score_themes(themes, text, tags, note)
    by_id = {theme.id: theme for theme in themes}
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[ThemeSuggestion] = []
    for theme_id, score in ordered[: max(0, limit)]:
        if score <= 0.0 and not include_zero_scores:
            continue
        out.append(ThemeSuggestion(theme_id=theme_id, name=by_id[theme_id].name, score=score))
    return out
</content>

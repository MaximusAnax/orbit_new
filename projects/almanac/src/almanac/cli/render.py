"""Plain-text rendering for the CLI (FR-16).

No colour and no third-party formatting dependency: the daily card has to read
well in a bare terminal, in a cron mail, and in a pipe.  Everything here is a
pure string function of already-fetched data.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from almanac.models import (
    CaptureResult,
    Card,
    Entry,
    EntryDetail,
    ImportReport,
    Reflection,
    StatsReport,
    Surfacing,
    ThemeSuggestion,
)

RULE = "-" * 68


def _wrap(text: str, width: int = 66, indent: str = "") -> str:
    """Greedy word wrap; long unbreakable tokens are left alone."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(indent + line for line in lines) or indent


def attribution_line(card: Card) -> list[str]:
    out: list[str] = []
    for flag in card.attribution_flags:
        out.append(_wrap(f"[attribution: {flag.verdict}] {flag.note}", indent="  "))
        if flag.reference_url:
            out.append(f"    {flag.reference_url}")
    return out


def render_card(card: Card, position: int = 1, total: int = 1) -> str:
    """One daily card or draw, as `almanac today` prints it."""
    s = card.surfacing
    entry = card.entry
    head = f"{s.on_date}  card {position}/{total}  [{s.kind}:{s.select_pool}]"
    lines = [RULE, head, ""]
    lines.append(_wrap(f'"{entry.text}"' if entry.kind == "quote" else entry.text, indent="  "))
    byline = " ".join(
        part for part in [entry.author or "", f"({entry.source})" if entry.source else ""] if part
    )
    if byline:
        lines.append(f"    -- {byline}")
    lines.append("")
    if entry.note:
        lines.append(_wrap(f"note: {entry.note}", indent="  "))
    facets = []
    if card.themes:
        facets.append("themes: " + ", ".join(card.themes))
    if card.tags:
        facets.append("tags: " + ", ".join(card.tags))
    if facets:
        lines.append("  " + "   ".join(facets))
    lines.append("")
    lines.append(f"  PROMPT ({s.prompt_kind})")
    lines.append(_wrap(s.prompt_text, indent="  "))
    if s.relaxed_cooldown:
        lines.append("  (tiny library: the no-repeat window was relaxed for this card)")
    if s.personalize_fell_back:
        lines.append("  (personalizer output was rejected; showing the template prompt)")
    lines.extend(attribution_line(card))
    lines.append("")
    lines.append(f"  entry {entry.id}   surfacing {s.id}")
    lines.append("  reflect with: almanac reflect --last --grade applied|resonated|flat")
    lines.append(RULE)
    return "\n".join(lines)


def render_cards(cards: Sequence[Card], on_date: dt.date) -> str:
    if not cards:
        return f"No card for {on_date}: the library has no active entries yet. Try `almanac import --starter`."
    return "\n".join(render_card(card, i + 1, len(cards)) for i, card in enumerate(cards))


def render_suggestions(suggestions: Sequence[ThemeSuggestion]) -> str:
    if not suggestions:
        return "  (no theme suggestion fired; add --theme to set one)"
    return "\n".join(
        f"  {i + 1}. {s.theme_id} ({s.name})  score {s.score:g}" for i, s in enumerate(suggestions)
    )


def render_capture(result: CaptureResult) -> str:
    lines = [f"Added {result.entry.id}  [{result.entry.kind}]"]
    lines.append(_wrap(result.entry.text, indent="  "))
    if result.duplicate_of:
        lines.append(f"  WARNING: same normalized text as existing entry {result.duplicate_of}")
    lines.append("  suggested themes:")
    lines.append(render_suggestions(result.suggestions))
    if result.themes:
        lines.append("  themes set: " + ", ".join(result.themes))
    if result.tags:
        lines.append("  tags: " + ", ".join(result.tags))
    for flag in result.attribution_flags:
        lines.append(_wrap(f"[attribution: {flag.verdict}] {flag.note}", indent="  "))
        if flag.reference_url:
            lines.append(f"    {flag.reference_url}")
    return "\n".join(lines)


def _excerpt(text: str, width: int = 58) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def render_entry_row(entry: Entry) -> str:
    marks = "".join(["*" if entry.pinned else " ", "a" if entry.status == "archived" else " "])
    return f"{entry.id}  {marks}  {_excerpt(entry.text)}"


def render_entries(entries: Sequence[Entry]) -> str:
    if not entries:
        return "(no entries match)"
    body = "\n".join(render_entry_row(e) for e in entries)
    return (
        f"{body}\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}  (* pinned, a archived)"
    )


def render_detail(detail: EntryDetail, reflections: Sequence[Reflection] | None = None) -> str:
    entry = detail.entry
    grades = {r.surfacing_id: r for r in (reflections or detail.reflections)}
    lines = [RULE, f"{entry.id}  [{entry.kind}] {entry.status}{'  PINNED' if entry.pinned else ''}"]
    lines.append(_wrap(entry.text, indent="  "))
    if entry.author or entry.source:
        lines.append(
            f"    -- {entry.author or 'unknown'}{f' ({entry.source})' if entry.source else ''}"
        )
    if entry.url:
        lines.append(f"  url: {entry.url}")
    if entry.note:
        lines.append(_wrap(f"note: {entry.note}", indent="  "))
    lines.append(
        f"  captured {entry.captured_on}   themes: {', '.join(detail.themes) or '-'}   tags: {', '.join(detail.tags) or '-'}"
    )
    state = detail.state
    lines.append(
        f"  exposures {state.exposure_count}   last seen {state.last_surfaced_on or '-'}"
        f"   interval {state.interval_days}d   flat streak {state.flat_streak}"
    )
    lines.append("")
    lines.append("  history:")
    if not detail.surfacings:
        lines.append("    (never surfaced)")
    for s in detail.surfacings:
        reflection = grades.get(s.id)
        graded = f"{reflection.grade}" if reflection else "-"
        lines.append(
            f"    {s.on_date}  {s.kind:<5} {s.select_pool:<14} {s.prompt_kind:<8} {graded}"
        )
        lines.append(_wrap(s.prompt_text, indent="        "))
        if reflection and reflection.text:
            lines.append(_wrap(f'"{reflection.text}"', indent="        > "))
    lines.append(RULE)
    return "\n".join(lines)


def render_surfacing_row(s: Surfacing) -> str:
    return f"{s.on_date}  {s.id}  {s.kind:<5} {s.select_pool:<14} {s.entry_id}"


def render_import(report: ImportReport) -> str:
    lines = [
        f"created {report.created}   duplicates skipped {report.duplicates}   errors {len(report.errors)}"
    ]
    for issue in report.errors:
        lines.append(f"  row {issue.row}: {issue.message}")
    if report.drain_note:
        lines.append(_wrap(report.drain_note, indent="  "))
    return "\n".join(lines)


def render_stats(report: StatsReport) -> str:
    cap = report.capacity
    lines = [
        RULE,
        f"almanac stats  {report.on_date}",
        RULE,
        f"library      {report.total_entries} entries ({report.active_entries} active, {report.archived_entries} archived)",
        f"             quotes {report.by_kind.get('quote', 0)}   ideas {report.by_kind.get('idea', 0)}   pinned {report.pinned_count}",
        f"coverage     {report.coverage * 100:.0f}% of active entries surfaced at least once",
        "exposures    "
        + (", ".join(f"{b.exposures}x:{b.entries}" for b in report.exposure_histogram) or "-"),
        f"streaks      open {report.open_streak}d   reflect {report.reflect_streak}d",
        f"novelty      {report.novelty_share:.3f} realized over the last {report.contested_slots} contested slots (target {report.rho})",
        "",
        "capacity (SCOPE.md capacity identity)",
        f"  k={cap.k}  rescue load r={cap.pinned_rescue_load:.3f}/day  capture rate A={cap.capture_rate:.3f}/day",
        f"  review capacity C={cap.review_capacity:.3f}/day   review demand L={cap.review_demand:.3f}/day",
        f"  stretch lambda={cap.stretch_lambda:.2f}"
        if cap.stretch_lambda is not None
        else "  stretch lambda=n/a (no review history yet)",
    ]
    if cap.sustainable_library is not None:
        lines.append(f"  sustainable library N* ~= {cap.sustainable_library:.0f} entries")
    if cap.advisory:
        lines.append(_wrap(f"ADVISORY: {cap.advisory}", indent="  "))
    if report.pinned_status:
        lines.append("")
        lines.append(
            f"pinned (guarantee: back within {report.pinned_status[0].guarantee_days} days)"
        )
        for p in report.pinned_status:
            seen = f"{p.days_since_seen}d ago" if p.days_since_seen is not None else "never seen"
            lines.append(f"  {p.entry_id}  {seen:<12} {_excerpt(p.excerpt, 40)}")
    if report.archive_candidates:
        lines.append("")
        lines.append("archive candidates (flat streak >= 3; never archived automatically)")
        for c in report.archive_candidates:
            lines.append(f"  {c.entry_id}  streak {c.flat_streak}  {_excerpt(c.excerpt, 40)}")
    lines.append(RULE)
    return "\n".join(lines)

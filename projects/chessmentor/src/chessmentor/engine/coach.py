"""FR-12 — the coaching report.

Window = the most recent N games (default 10) that have a **report-basis
analysis**, defined as ``analyst = internal AND node_budget = JUDGE_BUDGET AND
analyst_version = current engine version``.  Exactly one analysis per game
enters a report; games with no qualifying analysis are listed in
``skipped_game_ids``.  Mixing analyst configurations inside a window is
forbidden (D14 — cross-config numbers are not comparable).

Priority per category ``score(c) = sum(delta_w)`` over flagged moves in the
window, tie-broken by count, then by recency of the worst instance, then by
category name.  The top 3 become suggestions, each bound to a committed advice
entry whose ``title``/``body``/``drill`` are **snapshotted** into the suggestion
so an immutable report never re-renders differently after the catalog is edited.

Fully deterministic (M8 gate = 1.0).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum

from ..constants import (
    DEFAULT_REPORT_WINDOW,
    JUDGE_BUDGET,
    MAX_EVIDENCE_PER_SUGGESTION,
    MAX_SUGGESTIONS,
)
from ..models import (
    AdviceEntry,
    AnalystKind,
    CategoryTotal,
    CoachingReport,
    EvidenceItem,
    Game,
    GameAnalysis,
    GameSource,
    MistakeCategory,
    Phase,
    Suggestion,
    WindowEntry,
)

__all__ = [
    "CategoryScore",
    "FlaggedInstance",
    "build_report",
    "collect_flagged",
    "dominant_phase",
    "is_report_basis",
    "rank_categories",
    "select_window",
]


def is_report_basis(analysis: GameAnalysis, engine_version: str) -> bool:
    """FR-12's report-basis predicate — derived, never a stored flag."""
    return (
        analysis.analyst is AnalystKind.INTERNAL
        and analysis.node_budget == JUDGE_BUDGET
        and analysis.analyst_version == engine_version
    )


@dataclass(frozen=True)
class FlaggedInstance:
    """One flagged move inside the report window."""

    game_id: int
    order: int
    ply: int
    san: str
    delta_w: float
    category: MistakeCategory
    phase: Phase


@dataclass(frozen=True)
class CategoryScore:
    """A category's aggregate over the window, with its ordering key."""

    category: MistakeCategory
    dw_sum: float
    count: int
    worst_recency: tuple[int, int]
    instances: tuple[FlaggedInstance, ...]

    @property
    def sort_key(self) -> tuple[float, int, int, int, str]:
        """Higher dw_sum, then count, then a more recent worst instance, then name."""
        return (
            -self.dw_sum,
            -self.count,
            -self.worst_recency[0],
            -self.worst_recency[1],
            str(self.category),
        )


@dataclass(frozen=True)
class WindowedGame:
    """A game paired with the analysis chosen to represent it."""

    game: Game
    analysis: GameAnalysis


@dataclass(frozen=True)
class WindowSelection:
    entries: tuple[WindowedGame, ...]
    skipped_game_ids: tuple[int, ...]


def select_window(
    games: Sequence[Game],
    analyses_by_game: dict[int, Sequence[GameAnalysis]],
    *,
    engine_version: str,
    last_games: int = DEFAULT_REPORT_WINDOW,
    include_imported: bool = False,
) -> WindowSelection:
    """Pick the most recent ``last_games`` games carrying a report-basis analysis.

    ``games`` is scanned most-recent-first (by id).  A candidate game with no
    qualifying analysis goes to ``skipped_game_ids`` and is reported to the user;
    scanning stops once the window is full.
    """
    if last_games < 1:
        raise ValueError("last_games must be >= 1")

    candidates = [
        game
        for game in games
        if game.id is not None
        and (include_imported or game.source is GameSource.PLAYED)
        and game.ply_count > 0
    ]
    candidates.sort(key=lambda g: g.id, reverse=True)  # type: ignore[arg-type,return-value]

    chosen: list[WindowedGame] = []
    skipped: list[int] = []
    for game in candidates:
        if len(chosen) >= last_games:
            break
        assert game.id is not None
        eligible = [
            analysis
            for analysis in analyses_by_game.get(game.id, ())
            if is_report_basis(analysis, engine_version)
        ]
        if not eligible:
            skipped.append(game.id)
            continue
        # Exactly one analysis per game; the tuple is unique per DATA_MODEL, so
        # any tie is broken deterministically by analysis id.
        analysis = min(eligible, key=lambda a: a.id if a.id is not None else 0)
        chosen.append(WindowedGame(game=game, analysis=analysis))

    chosen.reverse()
    skipped.reverse()
    return WindowSelection(entries=tuple(chosen), skipped_game_ids=tuple(skipped))


def collect_flagged(
    entries: Sequence[WindowedGame],
    san_by_game: dict[int, Sequence[str]],
) -> list[FlaggedInstance]:
    """Every categorised move in the window, in game order then ply order."""
    instances: list[FlaggedInstance] = []
    for order, entry in enumerate(entries):
        game_id = entry.game.id
        assert game_id is not None
        sans = san_by_game.get(game_id, ())
        for move in sorted(entry.analysis.moves, key=lambda m: m.ply):
            if move.category is None:
                continue
            san = sans[move.ply - 1] if 0 < move.ply <= len(sans) else move.best_uci
            instances.append(
                FlaggedInstance(
                    game_id=game_id,
                    order=order,
                    ply=move.ply,
                    san=san,
                    delta_w=move.delta_w,
                    category=move.category,
                    phase=move.phase,
                )
            )
    return instances


def rank_categories(instances: Sequence[FlaggedInstance]) -> list[CategoryScore]:
    """Rank categories by the FR-12 priority formula."""
    grouped: dict[MistakeCategory, list[FlaggedInstance]] = {}
    for instance in instances:
        grouped.setdefault(instance.category, []).append(instance)

    scores: list[CategoryScore] = []
    for category, group in grouped.items():
        worst = max(group, key=lambda i: (i.delta_w, i.order, i.ply))
        scores.append(
            CategoryScore(
                category=category,
                dw_sum=fsum(i.delta_w for i in group),
                count=len(group),
                worst_recency=(worst.order, worst.ply),
                instances=tuple(group),
            )
        )
    scores.sort(key=lambda s: s.sort_key)
    return scores


def dominant_phase(score: CategoryScore) -> Phase | None:
    """The phase carrying the most win-probability loss for this category."""
    totals: dict[Phase, float] = {}
    for instance in score.instances:
        totals[instance.phase] = totals.get(instance.phase, 0.0) + instance.delta_w
    if not totals:
        return None
    order = {phase: index for index, phase in enumerate(Phase)}
    return min(totals, key=lambda phase: (-totals[phase], order[phase]))


def _resolve_advice(
    catalog: Sequence[AdviceEntry], category: MistakeCategory, phase: Phase | None
) -> AdviceEntry:
    """Exact ``(category, phase)`` beats ``(category, null)`` (DATA_MODEL.md)."""
    fallback: AdviceEntry | None = None
    for entry in catalog:
        if entry.category is not category:
            continue
        if phase is not None and entry.phase is phase:
            return entry
        if entry.phase is None:
            fallback = entry
    if fallback is None:
        raise KeyError(f"advice catalog has no phase-null entry for {category}")
    return fallback


def build_report(
    *,
    selection: WindowSelection,
    san_by_game: dict[int, Sequence[str]],
    advice_catalog: Sequence[AdviceEntry],
    created_at: str,
    include_imported: bool = False,
    prev_report_id: int | None = None,
) -> CoachingReport:
    """Aggregate a window into an immutable, fully deterministic report."""
    instances = collect_flagged(selection.entries, san_by_game)
    scores = rank_categories(instances)

    totals_by_category = {
        score.category: CategoryTotal(count=score.count, dw_sum=score.dw_sum) for score in scores
    }
    totals_by_phase: dict[Phase, float] = {}
    for instance in instances:
        totals_by_phase[instance.phase] = (
            totals_by_phase.get(instance.phase, 0.0) + instance.delta_w
        )

    suggestions: list[Suggestion] = []
    for rank, score in enumerate(scores[:MAX_SUGGESTIONS], start=1):
        advice = _resolve_advice(advice_catalog, score.category, dominant_phase(score))
        worst = sorted(score.instances, key=lambda i: (-i.delta_w, -i.order, i.ply))
        evidence = [
            EvidenceItem(game_id=i.game_id, ply=i.ply, san=i.san, dw=i.delta_w)
            for i in worst[:MAX_EVIDENCE_PER_SUGGESTION]
        ]
        suggestions.append(
            Suggestion(
                rank=rank,
                category=score.category,
                priority_score=score.dw_sum,
                advice_id=advice.id,
                advice_title=advice.title,
                advice_body=advice.body,
                advice_drill=advice.drill,
                evidence=evidence,
            )
        )

    window: list[WindowEntry] = []
    for entry in selection.entries:
        if entry.game.id is None or entry.analysis.id is None:
            # The window is a report's provenance; an unsaved row cannot appear in it.
            raise ValueError("report window entries need persisted game and analysis ids")
        window.append(WindowEntry(game_id=entry.game.id, analysis_id=entry.analysis.id))

    return CoachingReport(
        created_at=created_at,
        window=window,
        skipped_game_ids=list(selection.skipped_game_ids),
        include_imported=include_imported,
        totals_by_category=totals_by_category,
        totals_by_phase=totals_by_phase,
        prev_report_id=prev_report_id,
        suggestions=suggestions,
    )


def report_deltas(
    report: CoachingReport, previous: CoachingReport | None
) -> dict[MistakeCategory, float]:
    """Per-category ``dw_sum`` change against ``previous`` — computed on read."""
    deltas: dict[MistakeCategory, float] = {}
    previous_totals = previous.totals_by_category if previous is not None else {}
    categories = set(report.totals_by_category) | set(previous_totals)
    for category in categories:
        now = report.totals_by_category.get(category)
        before = previous_totals.get(category)
        deltas[category] = (now.dw_sum if now else 0.0) - (before.dw_sum if before else 0.0)
    return deltas

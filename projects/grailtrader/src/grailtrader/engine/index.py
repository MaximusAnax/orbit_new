"""FR-4: index construction — hard part A.

Leaf strata ``s = (brand, era, category)`` are indexed at ISO-week grain by the
median of condition-adjusted sold prices over a trailing ``window_weeks`` window,
after the FR-3 fence, requiring at least ``min_sales`` survivors. Parent strata
are **chain-linked**, never averaged levels, so a child becoming eligible never
steps the parent (REVIEW D5).

The build is a pure function of ``(listings, as_of, config)``. Because every
quantity is backward-looking, the index built from listings with
``sold_at <= end of week t`` equals the full build restricted to weeks ``<= t`` —
the property that lets the FR-10 replay build once and slice (test T3).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from ..models import IndexConfig, IndexPoint, Listing, ListingStatus
from ..weeks import add_weeks, parse_datetime, week_key, week_range, weeks_between
from .conditions import ConditionMapper
from .fence import apply_fence
from .strata import ancestors, depth, is_prefix, parent_path

__all__ = ["IndexView", "build_index", "median"]


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of an empty sample")
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class IndexView:
    """A built index: weekly points per stratum plus the weights FR-4/FR-8 need.

    Instances are treated as immutable; :meth:`restrict` returns a new view.
    """

    __slots__ = ("_by_week", "_excluded", "_leaf_weights", "_points", "as_of", "built_as_of")

    def __init__(
        self,
        points: Mapping[str, Sequence[IndexPoint]],
        leaf_weights: Mapping[str, Mapping[str, int]],
        excluded: Mapping[tuple[str, str], Sequence[str]] | None = None,
        *,
        built_as_of: str,
        as_of: str,
    ) -> None:
        self._points: dict[str, tuple[IndexPoint, ...]] = {
            stratum: tuple(sorted(rows, key=lambda p: p.week))
            for stratum, rows in points.items()
            if rows
        }
        self._by_week: dict[str, dict[str, IndexPoint]] = {
            stratum: {p.week: p for p in rows} for stratum, rows in self._points.items()
        }
        self._leaf_weights: dict[str, dict[str, int]] = {
            leaf: dict(weeks) for leaf, weeks in leaf_weights.items()
        }
        self._excluded: dict[tuple[str, str], tuple[str, ...]] = {
            key: tuple(ids) for key, ids in (excluded or {}).items()
        }
        self.built_as_of = built_as_of
        self.as_of = as_of

    # -- queries ------------------------------------------------------------ #

    @property
    def strata(self) -> tuple[str, ...]:
        return tuple(sorted(self._points))

    @property
    def leaf_strata(self) -> tuple[str, ...]:
        return tuple(sorted(self._leaf_weights))

    def all_points(self) -> list[IndexPoint]:
        out: list[IndexPoint] = []
        for stratum in sorted(self._points):
            out.extend(self._points[stratum])
        return out

    def points(self, stratum: str) -> tuple[IndexPoint, ...]:
        return self._points.get(stratum, ())

    def point_at(self, stratum: str, week: str) -> IndexPoint | None:
        return self._by_week.get(stratum, {}).get(week)

    def carried(self, stratum: str, week: str) -> tuple[IndexPoint, int] | None:
        """Latest point at or before ``week`` plus its staleness in whole weeks."""
        rows = self._points.get(stratum)
        if not rows:
            return None
        best: IndexPoint | None = None
        for point in rows:
            if point.week <= week:
                best = point
            else:
                break
        if best is None:
            return None
        return best, weeks_between(best.week, week)

    def latest_before(self, stratum: str, week: str) -> IndexPoint | None:
        """Latest point strictly before ``week`` (FR-8 baseline)."""
        rows = self._points.get(stratum)
        if not rows:
            return None
        best: IndexPoint | None = None
        for point in rows:
            if point.week < week:
                best = point
            else:
                break
        return best

    def points_between(self, stratum: str, start: str, end: str) -> tuple[IndexPoint, ...]:
        return tuple(p for p in self._points.get(stratum, ()) if start <= p.week <= end)

    def excluded_listing_ids(self, stratum: str, week: str) -> tuple[str, ...]:
        return self._excluded.get((stratum, week), ())

    def leaf_weight(self, leaf: str, week: str) -> int:
        """Surviving-sale count for ``leaf`` over the trailing weight window ending at ``week``."""
        return self._leaf_weights.get(leaf, {}).get(week, 0)

    def observed_leaves(self, stratum: str) -> tuple[str, ...]:
        """Leaf strata under ``stratum`` that have ever seen a sale."""
        return tuple(sorted(leaf for leaf in self._leaf_weights if is_prefix(stratum, leaf)))

    def subtree_weight(self, stratum: str, week: str) -> int:
        """Summed trailing-window surviving-sale count over the leaves under ``stratum``."""
        return sum(self.leaf_weight(leaf, week) for leaf in self.observed_leaves(stratum))

    def most_specific_fresh(
        self, leaf: str, week: str, stale_max_weeks: int
    ) -> tuple[str, IndexPoint, int] | None:
        """Most specific ancestor of ``leaf`` with a point within ``stale_max_weeks`` of ``week``."""
        for path in ancestors(leaf):
            carried = self.carried(path, week)
            if carried is not None and carried[1] <= stale_max_weeks:
                return path, carried[0], carried[1]
        return None

    def has_any_point(self, leaf: str, week: str) -> bool:
        """True when some ancestor of ``leaf`` has a published point at or before ``week``."""
        return any(self.carried(path, week) is not None for path in ancestors(leaf))

    def restrict(self, week: str) -> IndexView:
        """The same build seen as of ``week`` — points and weights strictly at or before it."""
        points = {
            stratum: [p for p in rows if p.week <= week] for stratum, rows in self._points.items()
        }
        weights = {
            leaf: {w: n for w, n in weeks.items() if w <= week}
            for leaf, weeks in self._leaf_weights.items()
        }
        excluded = {key: ids for key, ids in self._excluded.items() if key[1] <= week}
        return IndexView(points, weights, excluded, built_as_of=self.built_as_of, as_of=week)


def build_index(
    listings: Iterable[Listing],
    *,
    mapper: ConditionMapper,
    config: IndexConfig,
    as_of: str,
    built_as_of: str | None = None,
) -> IndexView:
    """Build every leaf and parent index series (FR-4).

    Only sold listings with ``sold_at <= as_of`` participate; asking prices never
    enter the index (SCOPE D-3).
    """
    built = built_as_of or as_of
    cutoff = parse_datetime(as_of)
    end_week = week_key(as_of)

    by_leaf: dict[str, dict[str, list[Listing]]] = {}
    for listing in listings:
        if listing.status is not ListingStatus.SOLD:
            continue
        assert listing.sold_at is not None
        if parse_datetime(listing.sold_at) > cutoff:
            continue
        week = week_key(listing.sold_at)
        if week > end_week:
            continue
        by_leaf.setdefault(listing.stratum_path, {}).setdefault(week, []).append(listing)

    for weeks in by_leaf.values():
        for rows in weeks.values():
            rows.sort(key=lambda listing: (listing.sold_at or "", listing.id))

    points: dict[str, list[IndexPoint]] = {}
    leaf_weights: dict[str, dict[str, int]] = {}
    excluded: dict[tuple[str, str], tuple[str, ...]] = {}

    for leaf in sorted(by_leaf):
        weeks = by_leaf[leaf]
        first_week = min(weeks)
        leaf_points, leaf_excluded, weights = _build_leaf(
            leaf, weeks, first_week, end_week, mapper=mapper, config=config, built_as_of=built
        )
        leaf_weights[leaf] = weights
        excluded.update(leaf_excluded)
        if leaf_points:
            points[leaf] = leaf_points

    # (brand, era) parents chain-link their leaves; (brand) parents chain-link
    # their (brand, era) strata. Order matters: era parents must exist first.
    era_children: dict[str, list[str]] = {}
    for leaf in sorted(points):
        era_stratum = parent_path(leaf)
        if depth(leaf) != 3 or era_stratum is None:
            continue
        era_children.setdefault(era_stratum, []).append(leaf)

    for era_stratum in sorted(era_children):
        chained = _chain_link(
            era_stratum,
            era_children[era_stratum],
            points,
            weight_of=lambda child, week: _leaf_weight(leaf_weights, child, week),
            end_week=end_week,
            built_as_of=built,
        )
        if chained:
            points[era_stratum] = chained

    brand_children: dict[str, list[str]] = {}
    for era_stratum in sorted(era_children):
        brand = parent_path(era_stratum)
        if era_stratum in points and brand is not None:
            brand_children.setdefault(brand, []).append(era_stratum)

    for brand in sorted(brand_children):
        chained = _chain_link(
            brand,
            brand_children[brand],
            points,
            weight_of=lambda child, week: sum(
                _leaf_weight(leaf_weights, leaf, week)
                for leaf in leaf_weights
                if is_prefix(child, leaf)
            ),
            end_week=end_week,
            built_as_of=built,
        )
        if chained:
            points[brand] = chained

    return IndexView(points, leaf_weights, excluded, built_as_of=built, as_of=as_of)


def _leaf_weight(leaf_weights: Mapping[str, Mapping[str, int]], leaf: str, week: str) -> int:
    return leaf_weights.get(leaf, {}).get(week, 0)


def _build_leaf(
    leaf: str,
    by_week: Mapping[str, Sequence[Listing]],
    first_week: str,
    end_week: str,
    *,
    mapper: ConditionMapper,
    config: IndexConfig,
    built_as_of: str,
) -> tuple[list[IndexPoint], dict[tuple[str, str], tuple[str, ...]], dict[str, int]]:
    levels: list[tuple[str, float, int, int]] = []
    excluded: dict[tuple[str, str], tuple[str, ...]] = {}
    weights: dict[str, int] = {}

    for week in week_range(first_week, end_week):
        window = _window_sales(by_week, week, config.window_weeks)
        if window:
            prices = [
                mapper.adjust(listing.sold_price or 0.0, listing.condition) for listing in window
            ]
            result = apply_fence(
                prices,
                fence_sigma=config.fence_sigma,
                fence_floor_log=config.fence_floor_log,
            )
            if result.n_kept >= config.min_sales:
                level = median([prices[i] for i in result.kept])
                levels.append((week, level, result.n_kept, result.n_excluded))
                if result.excluded:
                    excluded[(leaf, week)] = tuple(window[i].id for i in result.excluded)

        weight_window = _window_sales(by_week, week, config.parent_weight_window_weeks)
        if weight_window:
            prices = [
                mapper.adjust(listing.sold_price or 0.0, listing.condition)
                for listing in weight_window
            ]
            weight_result = apply_fence(
                prices,
                fence_sigma=config.fence_sigma,
                fence_floor_log=config.fence_floor_log,
            )
            weights[week] = weight_result.n_kept
        else:
            weights[week] = 0

    if not levels:
        return [], excluded, weights

    base_level = levels[0][1]
    points = [
        IndexPoint(
            stratum_id=leaf,
            week=week,
            level_usd=level,
            index_value=100.0 * level / base_level,
            n_sales=n_sales,
            n_excluded=n_excluded,
            built_as_of=built_as_of,
        )
        for week, level, n_sales, n_excluded in levels
    ]
    return points, excluded, weights


def _window_sales(
    by_week: Mapping[str, Sequence[Listing]], week: str, window_weeks: int
) -> list[Listing]:
    out: list[Listing] = []
    for offset in range(window_weeks - 1, -1, -1):
        out.extend(by_week.get(add_weeks(week, -offset), ()))
    return out


def _chain_link(
    parent: str,
    children: Sequence[str],
    points: Mapping[str, Sequence[IndexPoint]],
    *,
    weight_of,
    end_week: str,
    built_as_of: str,
) -> list[IndexPoint]:
    """FR-4 parent chain-linking.

    ``ln I_p(t) = ln I_p(t_prev) + sum_c w_c (ln I_c(t) - ln I_c(t_prev)) / sum_c w_c``
    over the children that published at **both** ``t_prev`` and ``t``. A child's
    first point therefore never enters the parent as a rebased level.
    """
    child_points: dict[str, dict[str, IndexPoint]] = {
        child: {p.week: p for p in points.get(child, ())} for child in children
    }
    child_points = {child: rows for child, rows in child_points.items() if rows}
    if not child_points:
        return []

    weeks_with_children = sorted({week for rows in child_points.values() for week in rows})
    t_base = weeks_with_children[0]
    base_sales = sum(rows[t_base].n_sales for rows in child_points.values() if t_base in rows)
    out = [
        IndexPoint(
            stratum_id=parent,
            week=t_base,
            level_usd=None,
            index_value=100.0,
            n_sales=base_sales,
            n_excluded=0,
            built_as_of=built_as_of,
        )
    ]

    ln_parent = math.log(100.0)
    t_prev = t_base
    for week in week_range(add_weeks(t_base, 1), end_week):
        contributing = [
            child
            for child in sorted(child_points)
            if week in child_points[child] and t_prev in child_points[child]
        ]
        if not contributing:
            continue
        weights = [float(weight_of(child, week)) for child in contributing]
        total = sum(weights)
        if total <= 0.0:
            weights = [1.0] * len(contributing)
            total = float(len(contributing))
        delta = 0.0
        for child, weight in zip(contributing, weights, strict=True):
            now = child_points[child][week].index_value
            before = child_points[child][t_prev].index_value
            delta += weight * (math.log(now) - math.log(before))
        ln_parent += delta / total
        out.append(
            IndexPoint(
                stratum_id=parent,
                week=week,
                level_usd=None,
                index_value=math.exp(ln_parent),
                n_sales=sum(child_points[child][week].n_sales for child in contributing),
                n_excluded=0,
                built_as_of=built_as_of,
            )
        )
        t_prev = week
    return out

"""FR-7: garment valuation — a two-method ladder that always names its method.

1. ``repeat_sales``: the garment's own transaction price is the anchor and the
   stratum index supplies the appreciation path (Bailey-Muth-Nourse 1963 /
   Case-Shiller 1989 applied in reverse to mark one asset to market)::

       fair_value = anchor_price * I_s(t)/I_s(t_anchor)
                    * multiplier[current] / multiplier[anchor]

   where ``s`` is the most specific ancestor of the garment's leaf path with
   published points within ``stale_max_weeks`` of **both** ``t`` and
   ``t_anchor`` (each carried back to the nearest earlier published point).
2. ``comp_based``: when no stratum covers the anchor week, the garment's **leaf**
   stratum's fresh level restated to the garment's condition. Parent strata carry
   no ``level_usd``, so this method needs the leaf.
3. Otherwise ``unavailable`` with a named reason — never a silent guess.
"""

from __future__ import annotations

from ..models import Garment, ValuationMethod, ValuationReason, ValuationResult
from ..weeks import week_key
from .context import EngineContext
from .index import IndexView
from .strata import ancestors

__all__ = ["value_garment"]


def value_garment(
    garment: Garment, *, index: IndexView, as_of_week: str, ctx: EngineContext
) -> ValuationResult:
    """Value one garment at ``as_of_week`` (FR-7)."""
    stale_max = ctx.index_config.stale_max_weeks
    leaf = garment.stratum_path
    anchor_week = week_key(garment.anchor_date)
    condition_ratio = ctx.mapper.multiplier(garment.condition) / ctx.mapper.multiplier(
        garment.anchor_condition
    )

    leaf_carried = index.carried(leaf, as_of_week)
    level_usd = (
        leaf_carried[0].level_usd
        if leaf_carried is not None and leaf_carried[1] <= stale_max
        else None
    )

    fresh_at_t = False
    any_point = False
    for path in ancestors(leaf):
        now = index.carried(path, as_of_week)
        if now is None:
            continue
        any_point = True
        if now[1] > stale_max:
            continue
        fresh_at_t = True
        then = index.carried(path, anchor_week)
        if then is None or then[1] > stale_max:
            continue
        fair = garment.anchor_price * (now[0].index_value / then[0].index_value) * condition_ratio
        return ValuationResult(
            method=ValuationMethod.REPEAT_SALES,
            fair_value=fair,
            stratum_id=path,
            level_usd=level_usd,
            unrealized_gain=fair - garment.anchor_price,
        )

    if level_usd is not None:
        fair = ctx.mapper.restate(level_usd, garment.condition)
        return ValuationResult(
            method=ValuationMethod.COMP_BASED,
            fair_value=fair,
            stratum_id=leaf,
            level_usd=level_usd,
            unrealized_gain=fair - garment.anchor_price,
        )

    if not any_point:
        reason = ValuationReason.NO_INDEX
    elif not fresh_at_t:
        reason = ValuationReason.STALE_INDEX
    else:
        reason = ValuationReason.NO_INDEX_AT_ANCHOR
    return ValuationResult(method=ValuationMethod.UNAVAILABLE, reason=reason, level_usd=level_usd)

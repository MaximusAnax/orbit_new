"""FR-2: condition alias mapping and the fixed quality adjustment.

Every sale is restated as an *excellent-condition-equivalent* price before it
enters an index (SCOPE D-2): ``p_adj = sold_price / multiplier[grade]``. The
inverse (``level_usd * multiplier[grade]``) turns a stratum level back into the
price of a specific garment (FR-7 method 2).
"""

from __future__ import annotations

from ..models import ConditionGrade, ConditionTable

__all__ = ["ConditionMapper", "UnmappedConditionLabelError"]


class UnmappedConditionLabelError(ValueError):
    """A platform condition label that the committed alias table does not cover."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(
            f"unmapped platform condition label {label!r}: add it to conditions.json "
            "(FR-2 never guesses a grade)"
        )


class ConditionMapper:
    """Maps platform labels to the canonical five-grade scale and applies multipliers."""

    def __init__(self, table: ConditionTable) -> None:
        self._table = table
        self._aliases = table.alias_map
        self._multipliers = table.multipliers

    @property
    def table(self) -> ConditionTable:
        return self._table

    def grade_for(self, platform_label: str) -> ConditionGrade:
        """Map a raw platform label to a canonical grade, or raise naming the label."""
        try:
            return self._aliases[platform_label.strip().casefold()]
        except KeyError:
            raise UnmappedConditionLabelError(platform_label) from None

    def knows(self, platform_label: str) -> bool:
        return platform_label.strip().casefold() in self._aliases

    def multiplier(self, grade: ConditionGrade) -> float:
        return self._multipliers[grade]

    def adjust(self, price: float, grade: ConditionGrade) -> float:
        """Restate ``price`` as an excellent-condition-equivalent price."""
        return price / self._multipliers[grade]

    def restate(self, level_usd: float, grade: ConditionGrade) -> float:
        """Turn an excellent-condition-equivalent level into a ``grade`` price."""
        return level_usd * self._multipliers[grade]

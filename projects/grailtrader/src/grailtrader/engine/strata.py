"""Stratum paths and the brand/era gazetteer (DATA_MODEL "Stratum ids").

Strata are derived path strings over gazetteer keys::

    brand                      helmut-lang
    brand/era                  helmut-lang/helmut
    brand/era/category (leaf)  helmut-lang/helmut/outerwear

The era segment is the era id's suffix after the colon. Prefix relationships
define the hierarchy used by FR-4 parent chaining, FR-5 event scoping and
FR-7/FR-8 stratum fallback.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence

from ..models import Brand, Category, DesignerEra

__all__ = [
    "Gazetteer",
    "UnknownReferenceError",
    "ancestors",
    "brand_path",
    "depth",
    "era_path",
    "is_prefix",
    "leaf_path",
    "parent_path",
    "split_path",
]


class UnknownReferenceError(ValueError):
    """A brand / era / category reference that the gazetteer cannot resolve."""

    def __init__(self, kind: str, value: str, suggestion: str | None = None) -> None:
        self.kind = kind
        self.value = value
        self.suggestion = suggestion
        message = f"unknown {kind}: {value!r}"
        if suggestion:
            message += f" (did you mean {suggestion!r}?)"
        super().__init__(message)


def brand_path(brand_id: str) -> str:
    return brand_id


def era_path(brand_id: str, era_id: str) -> str:
    return f"{brand_id}/{era_id.split(':', 1)[1]}"


def leaf_path(brand_id: str, era_id: str, category: Category | str) -> str:
    cat = category.value if isinstance(category, Category) else category
    return f"{era_path(brand_id, era_id)}/{cat}"


def split_path(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def depth(path: str) -> int:
    return len(split_path(path))


def parent_path(path: str) -> str | None:
    segments = split_path(path)
    if len(segments) <= 1:
        return None
    return "/".join(segments[:-1])


def ancestors(path: str) -> list[str]:
    """The path itself and each ancestor, most specific first."""
    segments = split_path(path)
    return ["/".join(segments[: i + 1]) for i in range(len(segments) - 1, -1, -1)]


def is_prefix(prefix: str, path: str) -> bool:
    """True when ``prefix`` is a segment-wise prefix of ``path`` (or equal to it)."""
    a, b = split_path(prefix), split_path(path)
    return len(a) <= len(b) and b[: len(a)] == a


class Gazetteer:
    """Read-only lookups over the committed brand/era gazetteer."""

    def __init__(self, brands: Iterable[Brand]) -> None:
        self._brands: dict[str, Brand] = {}
        self._eras: dict[str, DesignerEra] = {}
        self._by_suffix: dict[tuple[str, str], DesignerEra] = {}
        self._aliases: dict[str, str] = {}
        for brand in brands:
            if brand.id in self._brands:
                raise ValueError(f"duplicate brand id {brand.id}")
            self._brands[brand.id] = brand
            for alias in (brand.name, brand.id, *brand.aliases):
                self._aliases.setdefault(alias.strip().casefold(), brand.id)
            for era in brand.eras:
                if era.id in self._eras:
                    raise ValueError(f"duplicate era id {era.id}")
                self._eras[era.id] = era
                self._by_suffix[(brand.id, era.suffix)] = era

    # -- basic lookups ----------------------------------------------------- #

    @property
    def brands(self) -> tuple[Brand, ...]:
        return tuple(self._brands.values())

    @property
    def eras(self) -> tuple[DesignerEra, ...]:
        return tuple(self._eras.values())

    def has_brand(self, brand_id: str) -> bool:
        return brand_id in self._brands

    def brand(self, brand_id: str) -> Brand:
        try:
            return self._brands[brand_id]
        except KeyError:
            raise UnknownReferenceError("brand", brand_id, self.suggest_brand(brand_id)) from None

    def era(self, era_id: str) -> DesignerEra:
        try:
            return self._eras[era_id]
        except KeyError:
            raise UnknownReferenceError("era", era_id, self.suggest_era(era_id)) from None

    def eras_for(self, brand_id: str) -> tuple[DesignerEra, ...]:
        return self.brand(brand_id).eras

    def era_by_suffix(self, brand_id: str, suffix: str) -> DesignerEra:
        try:
            return self._by_suffix[(brand_id, suffix)]
        except KeyError:
            raise UnknownReferenceError(
                "era", f"{brand_id}:{suffix}", self.suggest_era(f"{brand_id}:{suffix}")
            ) from None

    # -- reference resolution ---------------------------------------------- #

    def resolve_brand(self, reference: str) -> str:
        """Resolve a brand id, display name or alias (case-insensitive) to a brand id."""
        key = reference.strip().casefold()
        if key in self._aliases:
            return self._aliases[key]
        raise UnknownReferenceError("brand", reference, self.suggest_brand(reference))

    def resolve_era(self, brand_id: str, reference: str) -> str:
        """Resolve an era id, suffix, designer name or label within a brand."""
        brand = self.brand(brand_id)
        key = reference.strip().casefold()
        for era in brand.eras:
            if key in {
                era.id.casefold(),
                era.suffix.casefold(),
                era.designer.casefold(),
                era.label.casefold(),
            }:
                return era.id
        raise UnknownReferenceError("era", reference, self.suggest_era(reference, brand_id))

    def suggest_brand(self, reference: str) -> str | None:
        return _closest(reference, list(self._aliases) + list(self._brands))

    def suggest_era(self, reference: str, brand_id: str | None = None) -> str | None:
        pool: list[str] = []
        eras = self.eras_for(brand_id) if brand_id and self.has_brand(brand_id) else self.eras
        for era in eras:
            pool.extend([era.id, era.suffix, era.designer, era.label])
        return _closest(reference, pool)

    @staticmethod
    def suggest_category(reference: str) -> str | None:
        return _closest(reference, [c.value for c in Category])

    # -- stratum construction ---------------------------------------------- #

    def leaf(self, brand_id: str, era_id: str, category: Category | str) -> str:
        """Build and validate a leaf stratum path."""
        self.validate_membership(brand_id, era_id)
        cat = Category(category)
        return leaf_path(brand_id, era_id, cat)

    def validate_membership(self, brand_id: str, era_id: str) -> None:
        brand = self.brand(brand_id)
        era = self.era(era_id)
        if era.brand_id != brand.id:
            raise UnknownReferenceError("era for brand", f"{era_id} @ {brand_id}")

    def validate_stratum(self, path: str) -> None:
        """Every segment must exist and the era must belong to the brand."""
        segments = split_path(path)
        if not 1 <= len(segments) <= 3:
            raise UnknownReferenceError("stratum", path)
        brand = self.brand(segments[0])
        if len(segments) >= 2:
            self.era_by_suffix(brand.id, segments[1])
        if len(segments) == 3:
            try:
                Category(segments[2])
            except ValueError:
                raise UnknownReferenceError(
                    "category", segments[2], self.suggest_category(segments[2])
                ) from None

    # -- era ordering ------------------------------------------------------- #

    def predecessor_era(
        self, brand_id: str, designer: str | None, occurred_on: str
    ) -> DesignerEra | None:
        """The era a designer appointment closes (FR-5 ``predecessor_era`` target).

        Resolution rule, in order:

        1. If the appointed designer already has an era in the gazetteer, the
           predecessor is the era immediately before it chronologically.
        2. Otherwise it is the latest era that had already started when the
           appointment happened (ignoring any era of the appointed designer).
        3. If neither exists, the appointment targets the brand only.
        """
        eras = sorted(self.eras_for(brand_id), key=lambda e: e.start)
        if not eras:
            return None
        needle = (designer or "").strip().casefold()
        if needle:
            for position, era in enumerate(eras):
                if era.designer.strip().casefold() == needle:
                    return eras[position - 1] if position > 0 else None
        month = occurred_on[:7]
        candidates = [
            era for era in eras if era.start <= month and era.designer.strip().casefold() != needle
        ]
        return candidates[-1] if candidates else None


def _closest(reference: str, pool: Sequence[str]) -> str | None:
    matches = difflib.get_close_matches(
        reference.strip().casefold(), [p.casefold() for p in pool], n=1, cutoff=0.6
    )
    if not matches:
        return None
    lowered = matches[0]
    for candidate in pool:
        if candidate.casefold() == lowered:
            return candidate
    return None

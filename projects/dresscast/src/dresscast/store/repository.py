"""The repository interface plus the transaction logic both backends share.

The store maps domain models to rows and owns transactional integrity.  It
holds no engine logic: the FR-2 merge below is pure model manipulation over
:func:`dresscast.engine.models.cascade_category`, and the FR-3 counter effects
come from :func:`dresscast.engine.models.apply_wear`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError

from dresscast.engine.models import (
    CATEGORY_PRESETS,
    AcceptedField,
    AttributeSuggestion,
    Color,
    DayForecast,
    Garment,
    LaundryEvent,
    Recommendation,
    WearHistory,
    WearLog,
    cascade_category,
    clo_bounds,
)
from dresscast.errors import InvalidParams


@runtime_checkable
class Repository(Protocol):
    """Persistence for every entity in DATA_MODEL.md §2."""

    def close(self) -> None: ...

    def schema_version(self) -> int: ...

    # -- garments ----------------------------------------------------------
    def add_garment(self, garment: Garment) -> Garment: ...

    def get_garment(self, garment_id: str) -> Garment: ...

    def find_garment(self, needle: str) -> Garment | None: ...

    def list_garments(
        self,
        *,
        status: str | None = None,
        occasion: str | None = None,
        category: str | None = None,
        include_retired: bool = True,
    ) -> list[Garment]: ...

    def update_garment(self, garment: Garment) -> Garment: ...

    def set_status(self, garment_id: str, status: str, *, now: datetime) -> Garment: ...

    # -- suggestions -------------------------------------------------------
    def add_suggestion(self, suggestion: AttributeSuggestion) -> AttributeSuggestion: ...

    def get_suggestion(self, suggestion_id: str) -> AttributeSuggestion: ...

    def list_suggestions(self, garment_id: str | None = None) -> list[AttributeSuggestion]: ...

    def accept_suggestion(
        self, suggestion_id: str, fields: Sequence[str], *, now: datetime
    ) -> tuple[AttributeSuggestion, Garment]: ...

    def reject_suggestion(self, suggestion_id: str, *, now: datetime) -> AttributeSuggestion: ...

    # -- forecasts ---------------------------------------------------------
    def add_snapshot(self, forecast: DayForecast) -> DayForecast: ...

    def get_snapshot(self, snapshot_id: str) -> DayForecast: ...

    def latest_snapshot(self, date: str) -> DayForecast | None: ...

    # -- recommendations ---------------------------------------------------
    def add_recommendation(self, recommendation: Recommendation) -> Recommendation: ...

    def get_recommendation(self, recommendation_id: str) -> Recommendation: ...

    def latest_recommendation(self, date: str | None = None) -> Recommendation | None: ...

    # -- wear logs ---------------------------------------------------------
    def add_wear_log(
        self,
        *,
        date: str,
        garment_ids: Sequence[str],
        source: str = "manual",
        outfit_id: str | None = None,
        now: datetime,
    ) -> WearLog: ...

    def get_wear_log(self, log_id: str) -> WearLog: ...

    def list_wear_logs(
        self, *, since: str | None = None, until: str | None = None
    ) -> list[WearLog]: ...

    def undo_wear_log(self, log_id: str, *, today: str, now: datetime) -> None: ...

    def wear_history(self, date: str) -> WearHistory: ...

    # -- laundry -----------------------------------------------------------
    def add_laundry_event(
        self,
        garment_ids: Sequence[str],
        *,
        now: datetime,
        note: str | None = None,
    ) -> LaundryEvent: ...

    def dirty_garment_ids(self) -> list[str]: ...


# --------------------------------------------------------------------------
# FR-2's three-step accept transaction, shared by both backends
# --------------------------------------------------------------------------


def _coerce(field: str, value: Any) -> Any:
    if field == "colors":
        return [Color.model_validate(c) for c in value]
    return value


def merge_suggestion(
    garment: Garment,
    payload: dict[str, Any],
    fields: Sequence[str],
    *,
    now: datetime,
) -> tuple[Garment, list[AcceptedField]]:
    """FR-2: merge explicit keys, cascade the category, re-validate.

    Raises :class:`InvalidParams` if the merged record fails FR-1 validation —
    the caller rolls the transaction back and leaves the suggestion ``pending``.

    The cascade skips both the garment's ``overridden_fields`` and any field
    named explicitly in this request: a value the user just accepted by hand is
    not re-derived out from under them, which is what makes the documented
    remedy ("accept ``clo`` together with ``category``") do anything.
    """
    unknown = [f for f in fields if f not in payload]
    if unknown:
        raise InvalidParams(
            f"the suggestion does not propose {unknown[0]!r}",
            field=unknown[0],
            available=sorted(payload),
        )
    data = garment.model_dump()
    accepted: list[AcceptedField] = []
    for field in fields:
        new = payload[field]["value"] if isinstance(payload[field], dict) else payload[field]
        accepted.append(AcceptedField(field=field, via="explicit", old=data.get(field), new=new))
        data[field] = _coerce(field, new)

    if "category" in fields:
        staged = garment.model_copy(update={"overridden_fields": garment.overridden_fields})
        derived = cascade_category(staged, data["category"])
        for field, value in derived.items():
            if field in fields:
                continue
            accepted.append(
                AcceptedField(field=field, via="cascade", old=data.get(field), new=value)
            )
            data[field] = value

    data["updated_at"] = now
    try:
        merged = Garment.model_validate(data)
    except ValidationError as exc:
        raise _invalid_params_from(exc, data, garment) from exc
    return merged, accepted


def _invalid_params_from(
    exc: ValidationError, data: dict[str, Any], original: Garment
) -> InvalidParams:
    """Turn a merge failure into FR-2's ``invalid_params`` with both values."""
    category = data.get("category", original.category)
    clo = data.get("clo", original.clo)
    if category in CATEGORY_PRESETS:
        lo, hi = clo_bounds(category)
        if not (lo <= float(clo) <= hi):
            hint = (
                " (user-overridden; accept 'clo' together with 'category')"
                if "clo" in original.overridden_fields
                else ""
            )
            return InvalidParams(
                f"clo {clo} outside {lo:.2f}-{hi:.2f} for category {category}{hint}",
                field="clo",
                value=clo,
                category=category,
                allowed=[lo, hi],
            )
    first = exc.errors()[0]
    field = str(first.get("loc", ("record",))[0]) if first.get("loc") else "record"
    return InvalidParams(
        f"{field}: {first.get('msg', 'validation failed')}",
        field=field,
        value=data.get(field),
    )

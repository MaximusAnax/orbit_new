"""In-memory repository — the same contract as SQLite, for tests and evals."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from dresscast.engine.models import (
    SCHEMA_VERSION,
    AttributeSuggestion,
    DayForecast,
    Garment,
    LaundryEvent,
    Recommendation,
    ScoredOutfit,
    WearHistory,
    WearLog,
    WearLogItem,
    apply_wear,
    check_transition,
    undo_wear,
    wash,
)
from dresscast.engine.variety import previous_day
from dresscast.errors import InvalidParams, InvalidTransition, UnknownGarment
from dresscast.store.repository import merge_suggestion


def _new_id() -> str:
    return str(uuid.uuid4())


class InMemoryRepository:
    """A dict-backed backend with the same transactional semantics.

    Mutating operations build the whole new state before committing it, so a
    validation failure part-way through leaves the store untouched — the
    in-memory equivalent of SQLite's rollback.
    """

    def __init__(self) -> None:
        self._garments: dict[str, Garment] = {}
        self._suggestions: dict[str, AttributeSuggestion] = {}
        self._snapshots: dict[str, DayForecast] = {}
        self._recommendations: dict[str, Recommendation] = {}
        self._wear_logs: dict[str, WearLog] = {}
        self._laundry: dict[str, LaundryEvent] = {}
        self._order: list[str] = []

    def close(self) -> None:
        return None

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Garments
    # ------------------------------------------------------------------

    def add_garment(self, garment: Garment) -> Garment:
        stored = garment if garment.id else garment.model_copy(update={"id": _new_id()})
        if stored.id in self._garments:
            raise InvalidParams(f"garment {stored.id!r} already exists", field="id")
        if any(g.name.lower() == stored.name.lower() for g in self._garments.values()):
            raise InvalidParams(f"a garment named {stored.name!r} already exists", field="name")
        self._garments[stored.id] = stored
        return stored

    def get_garment(self, garment_id: str) -> Garment:
        try:
            return self._garments[garment_id]
        except KeyError as exc:
            raise UnknownGarment(f"no garment {garment_id!r}", garment_id=garment_id) from exc

    def find_garment(self, needle: str) -> Garment | None:
        if needle in self._garments:
            return self._garments[needle]
        for g in sorted(self._garments.values(), key=lambda g: g.id):
            if g.name.lower() == needle.lower():
                return g
        return None

    def list_garments(
        self,
        *,
        status: str | None = None,
        occasion: str | None = None,
        category: str | None = None,
        include_retired: bool = True,
    ) -> list[Garment]:
        out = sorted(self._garments.values(), key=lambda g: g.id)
        if status:
            out = [g for g in out if g.status == status]
        if category:
            out = [g for g in out if g.category == category]
        if occasion:
            out = [g for g in out if occasion in g.occasions]
        if not include_retired:
            out = [g for g in out if g.status != "retired"]
        return out

    def update_garment(self, garment: Garment) -> Garment:
        self.get_garment(garment.id)
        clash = [
            g
            for g in self._garments.values()
            if g.id != garment.id and g.name.lower() == garment.name.lower()
        ]
        if clash:
            raise InvalidParams(f"a garment named {garment.name!r} already exists", field="name")
        self._garments[garment.id] = garment
        return garment

    def set_status(self, garment_id: str, status: str, *, now: datetime) -> Garment:
        garment = self.get_garment(garment_id)
        check_transition(garment.status, status)
        update: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "clean":
            update["wears_since_wash"] = 0
        return self.update_garment(garment.model_copy(update=update))

    # ------------------------------------------------------------------
    # Attribute suggestions
    # ------------------------------------------------------------------

    def add_suggestion(self, suggestion: AttributeSuggestion) -> AttributeSuggestion:
        stored = suggestion if suggestion.id else suggestion.model_copy(update={"id": _new_id()})
        self.get_garment(stored.garment_id)
        self._suggestions[stored.id] = stored
        self._order.append(stored.id)
        return stored

    def get_suggestion(self, suggestion_id: str) -> AttributeSuggestion:
        try:
            return self._suggestions[suggestion_id]
        except KeyError as exc:
            raise InvalidParams(f"no suggestion {suggestion_id!r}", field="suggestion_id") from exc

    def list_suggestions(self, garment_id: str | None = None) -> list[AttributeSuggestion]:
        out = [self._suggestions[i] for i in self._order if i in self._suggestions]
        if garment_id:
            out = [s for s in out if s.garment_id == garment_id]
        return out

    def accept_suggestion(
        self, suggestion_id: str, fields: Sequence[str], *, now: datetime
    ) -> tuple[AttributeSuggestion, Garment]:
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion.status != "pending":
            raise InvalidParams(
                f"suggestion {suggestion_id!r} is already {suggestion.status}",
                field="status",
            )
        garment = self.get_garment(suggestion.garment_id)
        # merge_suggestion raises before anything is committed, so a failed
        # accept leaves both the garment and the suggestion untouched.
        merged, accepted = merge_suggestion(garment, suggestion.payload, fields, now=now)
        resolved = suggestion.model_copy(
            update={"status": "accepted", "accepted_fields": accepted, "resolved_at": now}
        )
        self._suggestions[suggestion_id] = resolved
        self._garments[merged.id] = merged
        return resolved, merged

    def reject_suggestion(self, suggestion_id: str, *, now: datetime) -> AttributeSuggestion:
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion.status != "pending":
            raise InvalidParams(
                f"suggestion {suggestion_id!r} is already {suggestion.status}",
                field="status",
            )
        resolved = suggestion.model_copy(update={"status": "rejected", "resolved_at": now})
        self._suggestions[suggestion_id] = resolved
        return resolved

    # ------------------------------------------------------------------
    # Forecast snapshots
    # ------------------------------------------------------------------

    def add_snapshot(self, forecast: DayForecast) -> DayForecast:
        stored = forecast if forecast.id else forecast.model_copy(update={"id": _new_id()})
        self._snapshots[stored.id] = stored
        return stored

    def get_snapshot(self, snapshot_id: str) -> DayForecast:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise InvalidParams(f"no snapshot {snapshot_id!r}", field="snapshot_id") from exc

    def latest_snapshot(self, date: str) -> DayForecast | None:
        matches = [s for s in self._snapshots.values() if s.date == date]
        if not matches:
            return None
        return max(matches, key=lambda s: (s.fetched_at, s.id))

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def add_recommendation(self, recommendation: Recommendation) -> Recommendation:
        rec_id = recommendation.id or _new_id()
        outfits: list[ScoredOutfit] = [
            o.model_copy(update={"id": o.id or _new_id()}) for o in recommendation.outfits
        ]
        stored = recommendation.model_copy(update={"id": rec_id, "outfits": outfits})
        self._recommendations[rec_id] = stored
        return stored

    def get_recommendation(self, recommendation_id: str) -> Recommendation:
        try:
            return self._recommendations[recommendation_id]
        except KeyError as exc:
            raise InvalidParams(
                f"no recommendation {recommendation_id!r}", field="recommendation_id"
            ) from exc

    def latest_recommendation(self, date: str | None = None) -> Recommendation | None:
        matches = [r for r in self._recommendations.values() if date is None or r.date == date]
        if not matches:
            return None
        return max(matches, key=lambda r: (r.created_at, r.id))

    def outfit_garment_ids(self, outfit_id: str) -> list[str]:
        for rec in self._recommendations.values():
            for outfit in rec.outfits:
                if outfit.id == outfit_id:
                    return [i.garment_id for i in outfit.items]
        raise InvalidParams(f"no outfit {outfit_id!r}", field="outfit_id")

    # ------------------------------------------------------------------
    # Wear logs
    # ------------------------------------------------------------------

    def add_wear_log(
        self,
        *,
        date: str,
        garment_ids: Sequence[str],
        source: str = "manual",
        outfit_id: str | None = None,
        now: datetime,
    ) -> WearLog:
        if not garment_ids:
            raise InvalidParams("a wear log must list at least one garment", field="garment_ids")
        unique = list(dict.fromkeys(garment_ids))
        garments = [self.get_garment(g) for g in unique]
        for g in garments:
            if g.status == "retired":
                raise InvalidTransition(
                    f"{g.name!r} is retired and cannot be worn", garment_id=g.id
                )
        log = WearLog(
            id=_new_id(),
            date=date,
            source=source,  # type: ignore[arg-type]
            outfit_id=outfit_id,
            created_at=now,
            items=[WearLogItem(garment_id=g.id, layer_role=g.layer_role) for g in garments],
        )
        self._wear_logs[log.id] = log
        for g in garments:
            self._garments[g.id] = apply_wear(g, now)
        return log

    def get_wear_log(self, log_id: str) -> WearLog:
        try:
            return self._wear_logs[log_id]
        except KeyError as exc:
            raise InvalidParams(f"no wear log {log_id!r}", field="log_id") from exc

    def list_wear_logs(
        self, *, since: str | None = None, until: str | None = None
    ) -> list[WearLog]:
        out = list(self._wear_logs.values())
        if since:
            out = [log for log in out if log.date >= since]
        if until:
            out = [log for log in out if log.date <= until]
        return sorted(out, key=lambda log: (log.date, log.created_at, log.id))

    def undo_wear_log(self, log_id: str, *, today: str, now: datetime) -> None:
        log = self.get_wear_log(log_id)
        if log.created_at.date().isoformat() != today:
            raise InvalidParams(
                "a wear log may only be undone on the calendar day it was created",
                log_id=log_id,
                created=log.created_at.date().isoformat(),
                today=today,
            )
        for item in log.items:
            garment = self.get_garment(item.garment_id)
            self._garments[garment.id] = undo_wear(garment, now)
        del self._wear_logs[log_id]

    def wear_history(self, date: str) -> WearHistory:
        last: dict[str, str] = {}
        for log in self.list_wear_logs(until=date):
            for item in log.items:
                current = last.get(item.garment_id)
                if current is None or log.date > current:
                    last[item.garment_id] = log.date
        yesterday = previous_day(date)
        sets = [
            log.core_ids()
            for log in self.list_wear_logs(since=yesterday, until=yesterday)
            if log.core_ids()
        ]
        return WearHistory(last_worn=last, yesterday_sets=tuple(sets))

    # ------------------------------------------------------------------
    # Laundry
    # ------------------------------------------------------------------

    def dirty_garment_ids(self) -> list[str]:
        return sorted(g.id for g in self._garments.values() if g.status in {"dirty", "in_laundry"})

    def add_laundry_event(
        self,
        garment_ids: Sequence[str],
        *,
        now: datetime,
        note: str | None = None,
    ) -> LaundryEvent:
        unique = list(dict.fromkeys(garment_ids))
        garments = [self.get_garment(g) for g in unique]
        for g in garments:
            if g.status not in {"dirty", "in_laundry"}:
                raise InvalidTransition(
                    f"{g.name!r} is {g.status} and cannot be laundered",
                    garment_id=g.id,
                    status=g.status,
                )
        event = LaundryEvent(
            id=_new_id(), created_at=now, note=note, garment_ids=[g.id for g in garments]
        )
        self._laundry[event.id] = event
        for g in garments:
            self._garments[g.id] = wash(g, now)
        return event

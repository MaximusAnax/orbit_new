"""Assemble maintenance suggestions from DB."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.db.connection import fetch_all
from app.models.schemas import MaintenanceSuggestionOut
from app.services.maintenance import rank_people


async def list_maintenance_suggestions(limit: int = 10) -> list[MaintenanceSuggestionOut]:
    rows = await fetch_all(
        """
        select
          p.id as person_id,
          p.name as person_name,
          p.orbit,
          rs.desired_cadence,
          rs.direction,
          (
            select extract(epoch from (now() - max(e.date))) / 86400.0
            from event e
            join event_participant ep on ep.event_id = e.id
            where ep.person_id = p.id
          ) as days_since_contact,
          coalesce(
            (
              select array_agg(t.description order by t.created_at desc)
              from task t
              where t.person_id = p.id and t.status = 'open'
            ),
            '{}'
          ) as open_tasks,
          exists(
            select 1 from profile_update_proposal pup
            where pup.person_id = p.id and pup.decision = 'deferred'
          ) as has_deferred_proposals
        from person p
        left join lateral (
          select desired_cadence, direction
          from relationship_state
          where person_id = p.id and superseded_at is null and source = 'explicit'
          order by recorded_at desc
          limit 1
        ) rs on true
        """
    )

    payload = []
    for r in rows:
        days = r["days_since_contact"]
        days_int = int(days) if days is not None else None
        open_tasks = list(r["open_tasks"] or [])
        payload.append(
            {
                "person_id": r["person_id"],
                "person_name": r["person_name"],
                "orbit": r["orbit"],
                "desired_cadence": r["desired_cadence"],
                "direction": r["direction"],
                "days_since_contact": days_int,
                "open_tasks": open_tasks,
                "has_deferred_proposals": r["has_deferred_proposals"],
            }
        )

    ranked = rank_people(payload, limit=limit)
    return [
        MaintenanceSuggestionOut(
            person_id=s.person_id,
            person_name=s.person_name,
            score=s.score,
            target_days=s.target_days,
            days_since_contact=s.days_since_contact,
            reasons=s.reasons,
            reason_codes=s.reason_codes,
        )
        for s in ranked
    ]

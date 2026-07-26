import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.db.connection import execute, fetch_all, fetch_one, fetch_val, json_dumps
from app.models.schemas import (
    FactCategory,
    FactStatus,
    OrbitLevel,
    ParticipantRole,
    ProposalDecision,
    ProposedFact,
    RelationshipSource,
    TaskStatus,
)


async def create_person(
    name: str,
    preferred_name: str | None = None,
    photo_url: str | None = None,
    pronunciation_note: str | None = None,
    orbit: OrbitLevel | None = None,
    linked_phone_contact_id: str | None = None,
) -> dict[str, Any]:
    row = await fetch_one(
        """
        insert into person (name, preferred_name, photo_url, pronunciation_note, orbit, linked_phone_contact_id)
        values ($1, $2, $3, $4, $5, $6)
        returning *
        """,
        name,
        preferred_name,
        photo_url,
        pronunciation_note,
        orbit.value if orbit else None,
        linked_phone_contact_id,
    )
    return dict(row)


async def list_people(q: str | None = None) -> list[dict[str, Any]]:
    if q:
        rows = await fetch_all(
            "select * from person where name ilike $1 or preferred_name ilike $1 order by name",
            f"%{q}%",
        )
    else:
        rows = await fetch_all("select * from person order by name")
    return [dict(r) for r in rows]


async def get_person(person_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one("select * from person where id = $1", person_id)
    return dict(row) if row else None


async def get_people_index() -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select p.id, p.name, p.preferred_name,
               (select f.value from fact f where f.person_id = p.id and f.category = 'job' and f.status = 'current' limit 1) as current_job
        from person p
        order by p.name
        """
    )
    return [dict(r) for r in rows]


async def get_current_facts(person_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select f.*, o.name as organization_name
        from fact f
        left join organization o on o.id = f.organization_id
        where f.person_id = $1 and f.status = 'current'
        order by f.category
        """,
        person_id,
    )
    return [dict(r) for r in rows]


async def get_facts_for_profile(person_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select distinct on (f.category) f.*, o.name as organization_name
        from fact f
        left join organization o on o.id = f.organization_id
        where f.person_id = $1 and f.status = 'current'
        order by f.category, f.created_at desc
        """,
        person_id,
    )
    return [dict(r) for r in rows]


async def get_or_create_organization(name: str, org_type: str = "company") -> UUID:
    existing = await fetch_one(
        "select id from organization where lower(name) = lower($1)",
        name,
    )
    if existing:
        return existing["id"]
    row = await fetch_one(
        "insert into organization (name, type) values ($1, $2) returning id",
        name,
        org_type,
    )
    return row["id"]


async def materialize_fact_from_proposal(
    person_id: UUID,
    proposed: ProposedFact,
    source_event_id: UUID | None,
    edited_value: str | None = None,
) -> dict[str, Any]:
    from app.db.connection import get_pool

    value = edited_value or proposed.formatted_value()
    org_id = None
    if proposed.organization_name:
        org_id = await get_or_create_organization(proposed.organization_name)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if proposed.supersedes_category or proposed.status == FactStatus.CURRENT:
                await conn.execute(
                    """
                    update fact set status = 'past', valid_to = now()
                    where person_id = $1 and category = $2 and status = 'current'
                    """,
                    person_id,
                    proposed.category.value,
                )

            row = await conn.fetchrow(
                """
                insert into fact (person_id, category, value, organization_id, status, valid_from, source_event_id, confidence)
                values ($1, $2, $3, $4, $5, now(), $6, $7)
                returning *
                """,
                person_id,
                proposed.category.value,
                value,
                org_id,
                proposed.status.value,
                source_event_id,
                proposed.confidence,
            )
            return dict(row)


async def get_current_relationship_state(person_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        select * from relationship_state
        where person_id = $1 and superseded_at is null and source = 'explicit'
        order by recorded_at desc
        limit 1
        """,
        person_id,
    )
    return dict(row) if row else None


async def get_suggested_relationship_states(person_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select * from relationship_state
        where person_id = $1 and superseded_at is null and source = 'inferred_suggested'
        order by recorded_at desc
        """,
        person_id,
    )
    return [dict(r) for r in rows]


async def create_explicit_relationship_state(
    person_id: UUID,
    description: str | None,
    desired_closeness: str | None,
    desired_cadence: str | None,
    direction: str | None,
) -> dict[str, Any]:
    from app.db.connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "update relationship_state set superseded_at = now() where person_id = $1 and superseded_at is null",
                person_id,
            )
            row = await conn.fetchrow(
                """
                insert into relationship_state (person_id, description, desired_closeness, desired_cadence, direction, source)
                values ($1, $2, $3, $4, $5, 'explicit')
                returning *
                """,
                person_id,
                description,
                desired_closeness,
                desired_cadence,
                direction,
            )
            return dict(row)


async def get_timeline(person_id: UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select e.id, e.date, e.title, e.summary, e.location
        from event e
        join event_participant ep on ep.event_id = e.id
        where ep.person_id = $1
        order by e.date desc
        limit $2
        """,
        person_id,
        limit,
    )
    return [dict(r) for r in rows]


async def get_open_tasks(person_id: UUID | None = None) -> list[dict[str, Any]]:
    if person_id:
        rows = await fetch_all(
            "select * from task where person_id = $1 and status = 'open' order by created_at desc",
            person_id,
        )
    else:
        rows = await fetch_all(
            "select * from task where status = 'open' order by created_at desc"
        )
    return [dict(r) for r in rows]


async def update_task_status(task_id: UUID, status: TaskStatus) -> dict[str, Any] | None:
    row = await fetch_one(
        "update task set status = $2 where id = $1 returning *",
        task_id,
        status.value,
    )
    return dict(row) if row else None


async def create_event(
    source: str,
    date: datetime,
    location: str | None = None,
    raw_transcript: str | None = None,
    audio_storage_path: str | None = None,
) -> dict[str, Any]:
    row = await fetch_one(
        """
        insert into event (date, location, raw_transcript, source, audio_storage_path)
        values ($1, $2, $3, $4, $5)
        returning *
        """,
        date,
        location,
        raw_transcript,
        source,
        audio_storage_path,
    )
    return dict(row)


async def get_event(event_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one("select * from event where id = $1", event_id)
    return dict(row) if row else None


async def list_events(person_id: UUID | None = None) -> list[dict[str, Any]]:
    if person_id:
        rows = await fetch_all(
            """
            select e.* from event e
            join event_participant ep on ep.event_id = e.id
            where ep.person_id = $1
            order by e.date desc
            """,
            person_id,
        )
    else:
        rows = await fetch_all("select * from event order by date desc")
    return [dict(r) for r in rows]


async def save_transcript(event_id: UUID, transcript: str) -> None:
    await execute(
        "update event set raw_transcript = $2 where id = $1",
        event_id,
        transcript,
    )


async def add_event_participant(
    event_id: UUID,
    person_id: UUID,
    role: ParticipantRole,
    per_person_notes: str | None = None,
) -> None:
    await execute(
        """
        insert into event_participant (event_id, person_id, role, per_person_notes)
        values ($1, $2, $3, $4)
        on conflict (event_id, person_id) do update set role = $3, per_person_notes = $4
        """,
        event_id,
        person_id,
        role.value,
        per_person_notes,
    )


async def get_event_participants(event_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select ep.*, p.name as person_name
        from event_participant ep
        join person p on p.id = ep.person_id
        where ep.event_id = $1
        """,
        event_id,
    )
    return [dict(r) for r in rows]


async def save_ambiguity_flag(
    event_id: UUID,
    raw_fragment: str,
    candidate_person_ids: list[UUID],
) -> None:
    await execute(
        """
        insert into ambiguity_flag (event_id, raw_fragment, candidate_person_ids)
        values ($1, $2, $3)
        """,
        event_id,
        raw_fragment,
        candidate_person_ids,
    )


async def get_ambiguity_flags(event_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        "select * from ambiguity_flag where event_id = $1",
        event_id,
    )
    return [dict(r) for r in rows]


async def get_ambiguity_flag(flag_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one("select * from ambiguity_flag where id = $1", flag_id)
    return dict(row) if row else None


async def resolve_ambiguity_flag(flag_id: UUID, person_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        update ambiguity_flag
        set resolved_person_id = $2
        where id = $1
        returning *
        """,
        flag_id,
        person_id,
    )
    return dict(row) if row else None


async def get_past_facts(person_id: UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select f.*, o.name as organization_name
        from fact f
        left join organization o on o.id = f.organization_id
        where f.person_id = $1 and f.status = 'past'
        order by f.valid_to desc nulls last, f.created_at desc
        """,
        person_id,
    )
    return [dict(r) for r in rows]


async def last_interaction_at(person_id: UUID):
    return await fetch_val(
        """
        select max(e.date) from event e
        join event_participant ep on ep.event_id = e.id
        where ep.person_id = $1
        """,
        person_id,
    )


async def save_proposal(
    event_id: UUID,
    person_id: UUID,
    proposed: ProposedFact,
) -> dict[str, Any] | None:
    existing = await fetch_one(
        """
        select id from profile_update_proposal
        where event_id = $1 and person_id = $2
          and proposed_fact_json->>'category' = $3
          and proposed_fact_json->>'value' = $4
        """,
        event_id,
        person_id,
        proposed.category.value,
        proposed.value,
    )
    if existing:
        return None

    row = await fetch_one(
        """
        insert into profile_update_proposal (event_id, person_id, proposed_fact_json)
        values ($1, $2, $3::jsonb)
        returning *
        """,
        event_id,
        person_id,
        json_dumps(proposed.model_dump(mode="json")),
    )
    return dict(row) if row else None


async def list_proposals(event_id: UUID | None = None) -> list[dict[str, Any]]:
    if event_id:
        rows = await fetch_all(
            "select * from profile_update_proposal where event_id = $1 order by created_at",
            event_id,
        )
    else:
        rows = await fetch_all(
            "select * from profile_update_proposal order by created_at desc"
        )
    return [dict(r) for r in rows]


async def get_proposal(proposal_id: UUID) -> dict[str, Any] | None:
    row = await fetch_one(
        "select * from profile_update_proposal where id = $1",
        proposal_id,
    )
    return dict(row) if row else None


async def resolve_proposal(
    proposal_id: UUID,
    decision: ProposalDecision,
) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        update profile_update_proposal
        set decision = $2, resolved_at = now()
        where id = $1
        returning *
        """,
        proposal_id,
        decision.value,
    )
    return dict(row) if row else None


async def proposal_counts_for_event(event_id: UUID) -> dict[str, int]:
    rows = await fetch_all(
        """
        select decision, count(*)::int as cnt
        from profile_update_proposal
        where event_id = $1
        group by decision
        """,
        event_id,
    )
    return {r["decision"]: r["cnt"] for r in rows}


async def save_task(
    person_id: UUID,
    description: str,
    event_id: UUID | None = None,
    due_date: Any = None,
) -> dict[str, Any]:
    row = await fetch_one(
        """
        insert into task (person_id, event_id, description, due_date)
        values ($1, $2, $3, $4)
        returning *
        """,
        person_id,
        event_id,
        description,
        due_date,
    )
    return dict(row)


async def log_pipeline_debug(
    event_id: UUID,
    step_name: str,
    raw_input: Any,
    raw_output: Any,
) -> None:
    await execute(
        """
        insert into pipeline_debug_log (event_id, step_name, raw_input, raw_output)
        values ($1, $2, $3::jsonb, $4::jsonb)
        """,
        event_id,
        step_name,
        json_dumps(raw_input),
        json_dumps(raw_output),
    )


async def mark_event_status(event_id: UUID, status: str) -> None:
    await execute("update event set status = $2 where id = $1", event_id, status)


async def get_recent_events_for_person(person_id: UUID, limit: int = 5) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """
        select e.id, e.date, e.summary, e.location
        from event e
        join event_participant ep on ep.event_id = e.id
        where ep.person_id = $1 and e.status in ('confirmed', 'synced')
        order by e.date desc
        limit $2
        """,
        person_id,
        limit,
    )
    return [dict(r) for r in rows]

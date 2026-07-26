from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import get_current_user_id
from app.db import repository as repo
from app.models.schemas import (
    CatchupResponse,
    FactOut,
    FactStatus,
    OrbitLevel,
    PastFactOut,
    PersonCreate,
    PersonProfile,
    PersonSummary,
    RelationshipSource,
    RelationshipStateCreate,
    RelationshipStateOut,
    TaskOut,
    TaskStatus,
    TimelineEvent,
)
from app.services.catchup import generate_catchup

router = APIRouter(prefix="/people", tags=["people"])


@router.get("", response_model=list[PersonSummary])
async def list_people(
    q: str | None = None,
    _user: str = Depends(get_current_user_id),
):
    rows = await repo.list_people(q)
    return [
        PersonSummary(
            id=r["id"],
            name=r["name"],
            preferred_name=r.get("preferred_name"),
            photo_url=r.get("photo_url"),
            orbit=OrbitLevel(r["orbit"]) if r.get("orbit") else None,
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("", response_model=PersonSummary, status_code=201)
async def create_person(
    body: PersonCreate,
    _user: str = Depends(get_current_user_id),
):
    row = await repo.create_person(
        name=body.name,
        preferred_name=body.preferred_name,
        photo_url=body.photo_url,
        pronunciation_note=body.pronunciation_note,
        orbit=body.orbit,
        linked_phone_contact_id=body.linked_phone_contact_id,
    )
    return PersonSummary(
        id=row["id"],
        name=row["name"],
        preferred_name=row.get("preferred_name"),
        photo_url=row.get("photo_url"),
        orbit=OrbitLevel(row["orbit"]) if row.get("orbit") else None,
        created_at=row["created_at"],
    )


@router.get("/{person_id}", response_model=PersonProfile)
async def get_person_profile(
    person_id: UUID,
    _user: str = Depends(get_current_user_id),
):
    person = await repo.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    facts = await repo.get_facts_for_profile(person_id)
    past = await repo.get_past_facts(person_id)
    timeline = await repo.get_timeline(person_id)
    tasks = await repo.get_open_tasks(person_id)
    rel = await repo.get_current_relationship_state(person_id)
    suggested = await repo.get_suggested_relationship_states(person_id)
    last_at = await repo.last_interaction_at(person_id)

    return PersonProfile(
        id=person["id"],
        name=person["name"],
        preferred_name=person.get("preferred_name"),
        photo_url=person.get("photo_url"),
        pronunciation_note=person.get("pronunciation_note"),
        orbit=OrbitLevel(person["orbit"]) if person.get("orbit") else None,
        current_facts=[
            FactOut(
                id=f["id"],
                category=f["category"],
                value=f["value"],
                status=FactStatus(f["status"]),
                valid_from=f.get("valid_from"),
                valid_to=f.get("valid_to"),
                organization_name=f.get("organization_name"),
            )
            for f in facts
        ],
        past_facts=[
            PastFactOut(
                id=f["id"],
                category=f["category"],
                value=f["value"],
                valid_from=f.get("valid_from"),
                valid_to=f.get("valid_to"),
            )
            for f in past
        ],
        timeline=[
            TimelineEvent(
                id=e["id"],
                date=e["date"],
                title=e.get("title"),
                summary=e.get("summary"),
                location=e.get("location"),
            )
            for e in timeline
        ],
        open_tasks=[
            TaskOut(
                id=t["id"],
                person_id=t["person_id"],
                event_id=t.get("event_id"),
                description=t["description"],
                status=TaskStatus(t["status"]),
                due_date=t.get("due_date"),
                created_at=t["created_at"],
            )
            for t in tasks
        ],
        relationship_state=RelationshipStateOut(
            id=rel["id"],
            description=rel.get("description"),
            desired_closeness=rel.get("desired_closeness"),
            desired_cadence=rel.get("desired_cadence"),
            direction=rel.get("direction"),
            source=RelationshipSource(rel["source"]),
            recorded_at=rel["recorded_at"],
        )
        if rel
        else None,
        suggested_relationship_states=[
            RelationshipStateOut(
                id=s["id"],
                description=s.get("description"),
                desired_closeness=s.get("desired_closeness"),
                desired_cadence=s.get("desired_cadence"),
                direction=s.get("direction"),
                source=RelationshipSource(s["source"]),
                recorded_at=s["recorded_at"],
            )
            for s in suggested
        ],
        last_interaction_at=last_at,
    )


@router.get("/{person_id}/catchup", response_model=CatchupResponse)
async def catchup(
    person_id: UUID,
    _user: str = Depends(get_current_user_id),
):
    person = await repo.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return await generate_catchup(person_id)


@router.post("/{person_id}/relationship-state", response_model=RelationshipStateOut, status_code=201)
async def update_relationship_state(
    person_id: UUID,
    body: RelationshipStateCreate,
    _user: str = Depends(get_current_user_id),
):
    person = await repo.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    row = await repo.create_explicit_relationship_state(
        person_id,
        body.description,
        body.desired_closeness,
        body.desired_cadence,
        body.direction,
    )
    return RelationshipStateOut(
        id=row["id"],
        description=row.get("description"),
        desired_closeness=row.get("desired_closeness"),
        desired_cadence=row.get("desired_cadence"),
        direction=row.get("direction"),
        source=RelationshipSource(row["source"]),
        recorded_at=row["recorded_at"],
    )

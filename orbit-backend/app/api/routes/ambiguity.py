from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import get_current_user_id
from app.db import repository as repo
from app.models.schemas import AmbiguityFlagOut, AmbiguityResolveBody, ParticipantRole
from app.models.schemas import MaintenanceSuggestionOut
from app.services.maintenance_query import list_maintenance_suggestions

router = APIRouter(tags=["ambiguity", "maintenance"])


@router.get("/ambiguity-flags", response_model=list[AmbiguityFlagOut])
async def list_ambiguity_flags(
    event_id: UUID = Query(...),
    _user: str = Depends(get_current_user_id),
):
    rows = await repo.get_ambiguity_flags(event_id)
    return [
        AmbiguityFlagOut(
            id=r["id"],
            event_id=r["event_id"],
            raw_fragment=r["raw_fragment"],
            candidate_person_ids=list(r["candidate_person_ids"] or []),
            resolved_person_id=r.get("resolved_person_id"),
        )
        for r in rows
    ]


@router.post("/ambiguity-flags/{flag_id}/resolve", response_model=AmbiguityFlagOut)
async def resolve_ambiguity(
    flag_id: UUID,
    body: AmbiguityResolveBody,
    _user: str = Depends(get_current_user_id),
):
    flag = await repo.get_ambiguity_flag(flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Ambiguity flag not found")
    person = await repo.get_person(body.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    updated = await repo.resolve_ambiguity_flag(flag_id, body.person_id)
    await repo.add_event_participant(
        flag["event_id"],
        body.person_id,
        ParticipantRole.ATTENDEE,
        flag["raw_fragment"],
    )
    return AmbiguityFlagOut(
        id=updated["id"],
        event_id=updated["event_id"],
        raw_fragment=updated["raw_fragment"],
        candidate_person_ids=list(updated["candidate_person_ids"] or []),
        resolved_person_id=updated.get("resolved_person_id"),
    )


@router.get("/maintenance", response_model=list[MaintenanceSuggestionOut])
async def maintenance(
    limit: int = Query(10, ge=1, le=50),
    _user: str = Depends(get_current_user_id),
):
    return await list_maintenance_suggestions(limit=limit)

import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status

from app.core.security import get_current_user_id
from app.db import repository as repo
from app.models.schemas import (
    EventAcceptedResponse,
    EventCreateText,
    EventOut,
    EventSource,
    EventStatus,
)
from app.pipeline.orchestrator import run_pipeline

router = APIRouter(prefix="/events", tags=["events"])


def _event_out(event: dict, participants: list, proposal_summary: dict, flags: list) -> EventOut:
    return EventOut(
        id=event["id"],
        date=event["date"],
        location=event.get("location"),
        title=event.get("title"),
        raw_transcript=event.get("raw_transcript"),
        summary=event.get("summary"),
        source=EventSource(event["source"]),
        status=EventStatus(event["status"]),
        created_at=event["created_at"],
        participants=participants,
        proposal_summary=proposal_summary,
        ambiguity_flags=flags,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=EventAcceptedResponse)
async def create_event(
    background_tasks: BackgroundTasks,
    _user: str = Depends(get_current_user_id),
    text: str | None = Form(None),
    date: str | None = Form(None),
    location: str | None = Form(None),
    audio: UploadFile | None = File(None),
):
    event_date = datetime.now(timezone.utc)
    if date:
        event_date = datetime.fromisoformat(date.replace("Z", "+00:00"))

    transcript = text
    source = EventSource.VOICE if audio else EventSource.TEXT
    audio_bytes = None

    event = await repo.create_event(
        source=source.value,
        date=event_date,
        location=location,
        raw_transcript=transcript,
    )

    if audio:
        audio_bytes = await audio.read()

    if transcript and not audio_bytes:
        background_tasks.add_task(run_pipeline, event["id"], None)
    else:
        background_tasks.add_task(run_pipeline, event["id"], audio_bytes)

    return EventAcceptedResponse(id=event["id"])


@router.post("/text", status_code=status.HTTP_202_ACCEPTED, response_model=EventAcceptedResponse)
async def create_event_from_text(
    body: EventCreateText,
    background_tasks: BackgroundTasks,
    _user: str = Depends(get_current_user_id),
):
    event_date = body.date or datetime.now(timezone.utc)
    event = await repo.create_event(
        source=EventSource.TEXT.value,
        date=event_date,
        location=body.location,
        raw_transcript=body.text,
    )
    background_tasks.add_task(run_pipeline, event["id"], None)
    return EventAcceptedResponse(id=event["id"])


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: UUID,
    _user: str = Depends(get_current_user_id),
):
    event = await repo.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    participants = await repo.get_event_participants(event_id)
    proposal_summary = await repo.proposal_counts_for_event(event_id)
    flags = await repo.get_ambiguity_flags(event_id)
    return _event_out(event, participants, proposal_summary, flags)


@router.get("", response_model=list[EventOut])
async def list_events(
    person_id: UUID | None = None,
    _user: str = Depends(get_current_user_id),
):
    events = await repo.list_events(person_id)
    result = []
    for event in events:
        eid = event["id"]
        participants = await repo.get_event_participants(eid)
        proposal_summary = await repo.proposal_counts_for_event(eid)
        flags = await repo.get_ambiguity_flags(eid)
        result.append(_event_out(event, participants, proposal_summary, flags))
    return result

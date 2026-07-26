from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import settings
from app.db import repository as repo
from app.models.schemas import ParticipantRole, ProposedFact
from app.pipeline.providers import get_transcription


class EntityMatch(BaseModel):
    name: str
    person_id: UUID | None = None
    is_new: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    transcript_segment: str
    role: str = "attendee"


class EntityResolutionResult(BaseModel):
    matches: list[EntityMatch]
    ambiguous: list[EntityMatch] = []


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.m4a") -> str:
    provider = get_transcription()
    return await provider.transcribe(audio_bytes, filename)


async def transcribe_event(event_id: UUID, audio_bytes: bytes | None = None) -> str:
    event = await repo.get_event(event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")
    if event.get("raw_transcript"):
        return event["raw_transcript"]
    if audio_bytes:
        transcript = await transcribe_audio(audio_bytes)
        await repo.save_transcript(event_id, transcript)
        return transcript
    raise ValueError("No transcript or audio available")

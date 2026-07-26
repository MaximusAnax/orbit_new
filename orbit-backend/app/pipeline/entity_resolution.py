from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import settings
from app.db import repository as repo
from app.models.schemas import ParticipantRole
from app.pipeline.providers import get_llm


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


class ResolutionOutput(BaseModel):
    matches: list[EntityMatch]


RESOLUTION_SYSTEM = """You resolve people mentioned in transcripts to existing contacts or flag new people.
Return structured matches with confidence 0-1. Below 0.7 confidence means ambiguous.
For each person, include the transcript segment about them."""


def _parse_role(role: str) -> ParticipantRole:
    try:
        return ParticipantRole(role)
    except ValueError:
        return ParticipantRole.ATTENDEE


async def resolve_entities(
    event_id: UUID,
    transcript: str,
    existing_people: list[dict],
) -> EntityResolutionResult:
    llm = get_llm()
    people_context = [
        {
            "id": str(p["id"]),
            "name": p["name"],
            "preferred_name": p.get("preferred_name"),
            "current_job": p.get("current_job"),
        }
        for p in existing_people
    ]
    user = f"Existing people:\n{people_context}\n\nTranscript:\n{transcript}"

    parsed, raw_in, raw_out = await llm.structured_completion(
        RESOLUTION_SYSTEM, user, ResolutionOutput
    )
    await repo.log_pipeline_debug(event_id, "entity_resolution", raw_in, raw_out)

    resolved: list[EntityMatch] = []
    ambiguous: list[EntityMatch] = []

    for match in parsed.matches:
        if match.confidence < settings.confidence_threshold:
            ambiguous.append(match)
            await repo.save_ambiguity_flag(
                event_id,
                match.transcript_segment,
                [match.person_id] if match.person_id else [],
            )
        else:
            resolved.append(match)
            person_id = match.person_id
            if match.is_new or person_id is None:
                person = await repo.create_person(name=match.name)
                person_id = person["id"]
                match.person_id = person_id
            await repo.add_event_participant(
                event_id,
                person_id,
                _parse_role(match.role),
                match.transcript_segment,
            )

    return EntityResolutionResult(matches=resolved, ambiguous=ambiguous)

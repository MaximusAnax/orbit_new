from uuid import UUID

from pydantic import BaseModel, Field

from app.db import repository as repo
from app.db.connection import execute
from app.models.schemas import ParticipantRole
from app.pipeline.providers import get_llm


class PersonContext(BaseModel):
    person_id: UUID
    notes: str


class ContextExtractionOutput(BaseModel):
    title: str = Field(
        description="Short recall label, e.g. 'Coffee with Alex' or 'Dinner with Sarah — Boston'. Max ~8 words."
    )
    event_summary: str = Field(
        description="Full 1-3 sentence summary of what happened and what was discussed."
    )
    per_person: list[PersonContext]


CONTEXT_SYSTEM = """Extract emotional/topical context from the transcript.

Return:
- title: CONCISE event label for a list UI (who + what kind of meetup). Include place only if distinctive.
  Good: "Coffee with Alex", "Chat with Dan (OpenAI)", "Group dinner — Alex, Sarah".
  Bad: long paragraphs, full topic dump, "The speaker had a conversation…"
- event_summary: richer 1–3 sentence summary (topics, bonding moments) — shown only when expanded.
- per_person notes where attributable.
"""


async def extract_context(
    event_id: UUID,
    transcript: str,
    resolved_person_ids: list[UUID],
) -> ContextExtractionOutput:
    llm = get_llm()
    parsed, raw_in, raw_out = await llm.structured_completion(
        CONTEXT_SYSTEM, transcript, ContextExtractionOutput
    )
    await repo.log_pipeline_debug(event_id, "context_extraction", raw_in, raw_out)

    title = (parsed.title or "").strip() or _fallback_title(transcript)
    summary = (parsed.event_summary or "").strip()

    await execute(
        "update event set title = $2, summary = $3 where id = $1",
        event_id,
        title,
        summary,
    )

    for pc in parsed.per_person:
        if pc.person_id in resolved_person_ids:
            await repo.add_event_participant(
                event_id,
                pc.person_id,
                ParticipantRole.ATTENDEE,
                pc.notes,
            )

    return parsed


def _fallback_title(transcript: str) -> str:
    first = transcript.strip().split(".")[0].strip()
    if len(first) > 60:
        return first[:57] + "…"
    return first or "Captured event"

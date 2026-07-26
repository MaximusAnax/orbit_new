from uuid import UUID

from pydantic import BaseModel

from app.db import repository as repo
from app.db.connection import execute, fetch_all
from app.models.schemas import CatchupResponse
from app.pipeline.providers import get_llm


class CatchupLLMOutput(BaseModel):
    summary: str
    talking_points: list[str]


CATCHUP_SYSTEM = """Generate a brief catch-up summary and 3-5 talking points for reconnecting with someone.
Use only the provided confirmed facts, recent events, tasks, and relationship context.
Do not invent information."""


async def generate_catchup(person_id: UUID) -> CatchupResponse:
    person = await repo.get_person(person_id)
    if not person:
        raise ValueError("Person not found")

    facts = await repo.get_facts_for_profile(person_id)
    events = await repo.get_recent_events_for_person(person_id, limit=5)
    tasks = await repo.get_open_tasks(person_id)
    rel = await repo.get_current_relationship_state(person_id)

    context = {
        "person": {"name": person["name"], "preferred_name": person.get("preferred_name")},
        "current_facts": facts,
        "recent_events": [{"date": str(e["date"]), "summary": e.get("summary")} for e in events],
        "open_tasks": tasks,
        "relationship_state": rel,
    }

    llm = get_llm()
    parsed, _, _ = await llm.structured_completion(
        CATCHUP_SYSTEM, str(context), CatchupLLMOutput
    )
    return CatchupResponse(summary=parsed.summary, talking_points=parsed.talking_points)

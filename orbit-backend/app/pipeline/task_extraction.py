from uuid import UUID

from pydantic import BaseModel, Field

from app.db import repository as repo
from app.pipeline.providers import get_llm


class TaskItem(BaseModel):
    person_name: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)


class TaskExtractionOutput(BaseModel):
    tasks: list[TaskItem]


TASK_SYSTEM = """Extract follow-up commitments and tasks from the full transcript.
Include who each task relates to by name."""


async def extract_tasks(
    event_id: UUID,
    transcript: str,
    people_by_name: dict[str, UUID],
) -> list[dict]:
    llm = get_llm()
    parsed, raw_in, raw_out = await llm.structured_completion(
        TASK_SYSTEM, transcript, TaskExtractionOutput
    )
    await repo.log_pipeline_debug(event_id, "task_extraction", raw_in, raw_out)

    saved = []
    for item in parsed.tasks:
        person_id = people_by_name.get(item.person_name.lower())
        if not person_id:
            for name, pid in people_by_name.items():
                if name in item.person_name.lower() or item.person_name.lower() in name:
                    person_id = pid
                    break
        if person_id:
            task = await repo.save_task(person_id, item.description, event_id)
            saved.append(task)
    return saved

from uuid import UUID

from pydantic import BaseModel

from app.db import repository as repo
from app.pipeline.providers import get_llm


class AttributionConflict(BaseModel):
    fact_value: str
    person_ids: list[UUID]
    resolution: str


class ReconciliationOutput(BaseModel):
    conflicts: list[AttributionConflict]


RECONCILE_SYSTEM = """Review per-person extractions from a group event.
Flag conflicts where the same fact is attributed to multiple people. Do not resolve silently."""


async def reconcile_group_attribution(event_id: UUID) -> ReconciliationOutput:
    proposals = await repo.list_proposals(event_id)
    llm = get_llm()
    user = f"Proposals for event {event_id}: {proposals}"
    parsed, raw_in, raw_out = await llm.structured_completion(
        RECONCILE_SYSTEM, user, ReconciliationOutput
    )
    await repo.log_pipeline_debug(event_id, "group_reconciliation", raw_in, raw_out)

    for conflict in parsed.conflicts:
        if len(conflict.person_ids) > 1:
            await repo.save_ambiguity_flag(
                event_id,
                conflict.fact_value,
                conflict.person_ids,
            )

    return parsed

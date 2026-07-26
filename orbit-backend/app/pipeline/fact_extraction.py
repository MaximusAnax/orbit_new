from uuid import UUID

from pydantic import BaseModel, Field

from app.core.config import settings
from app.db import repository as repo
from app.models.schemas import EducationLevel, FactCategory, FactStatus, ProposedFact
from app.pipeline.providers import get_llm


class FactProposalItem(BaseModel):
    category: FactCategory
    value: str
    status: FactStatus = FactStatus.CURRENT
    organization_name: str | None = None
    education_level: EducationLevel | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    supersedes_category: bool = False


class FactExtractionOutput(BaseModel):
    proposals: list[FactProposalItem]


FACT_SYSTEM = """Extract factual profile updates about ONE person from their transcript segment.
Distinguish current vs past facts. Mark supersedes_category=true when a new job/location supersedes old.
Every proposal needs confidence 0-1.

For category=school, ALWAYS set education_level when inferable:
  high_school | undergrad | grad | phd | alumni | other
Examples: "went to Carnegie Mellon" as a student → undergrad (or grad if specified);
"TA at CMU" while studying → undergrad/grad as appropriate; "alumni of X" → alumni.
Put the institution name in value; education_level carries the program stage — do not drop it.
Do not classify fleeting conversation topics (e.g. "discuss AGI") as goals unless the person stated a personal goal."""


async def extract_facts(
    event_id: UUID,
    person_id: UUID,
    segment: str,
    existing_facts: list[dict],
) -> list[ProposedFact]:
    llm = get_llm()
    user = f"Existing facts: {existing_facts}\n\nSegment about this person:\n{segment}"
    parsed, raw_in, raw_out = await llm.structured_completion(
        FACT_SYSTEM, user, FactExtractionOutput
    )
    await repo.log_pipeline_debug(event_id, f"fact_extraction_{person_id}", raw_in, raw_out)

    results: list[ProposedFact] = []
    for item in parsed.proposals:
        if item.confidence < settings.confidence_threshold:
            continue
        proposal = ProposedFact(
            category=item.category,
            value=item.value,
            status=item.status,
            organization_name=item.organization_name,
            education_level=item.education_level,
            confidence=item.confidence,
            supersedes_category=item.supersedes_category,
        )
        await repo.save_proposal(event_id, person_id, proposal)
        results.append(proposal)
    return results

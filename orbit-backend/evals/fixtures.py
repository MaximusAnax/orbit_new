"""Golden transcript fixtures for Orbit pipeline evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class GoldenProposal:
    category: str
    value: str
    status: str = "current"
    supersedes: bool = False
    min_confidence: float = 0.7


@dataclass
class GoldenEntity:
    name: str
    confidence_min: float | None = None  # None = expect ambiguous
    ambiguous: bool = False
    person_id: str | None = None


@dataclass
class GoldenFixture:
    id: str
    transcript: str
    existing_people: list[dict]
    entities: list[GoldenEntity]
    proposals_by_person: dict[str, list[GoldenProposal]] = field(default_factory=dict)
    tasks: list[dict] = field(default_factory=list)
    skip_fact_for_ambiguous: bool = True


SARAH_ID = "11111111-1111-1111-1111-111111111111"
ALEX_ID = "22222222-2222-2222-2222-222222222222"

FIXTURES: list[GoldenFixture] = [
    GoldenFixture(
        id="job_change_supersede",
        transcript=(
            "I had dinner with Sarah tonight. She works at Stripe now, "
            "but she used to be at Google. She's interested in videography."
        ),
        existing_people=[
            {"id": SARAH_ID, "name": "Sarah", "preferred_name": None, "current_job": "Google"}
        ],
        entities=[
            GoldenEntity(name="Sarah", confidence_min=0.9, person_id=SARAH_ID),
        ],
        proposals_by_person={
            "Sarah": [
                GoldenProposal("job", "Stripe", supersedes=True),
                GoldenProposal("job", "Google", status="past"),
                GoldenProposal("interest", "videography"),
            ]
        },
    ),
    GoldenFixture(
        id="ambiguous_james",
        transcript=(
            "I went to an event and someone named James works on AI infrastructure. "
            "Not sure which James."
        ),
        existing_people=[
            {"id": "33333333-3333-3333-3333-333333333333", "name": "James Chen", "preferred_name": None, "current_job": None},
            {"id": "44444444-4444-4444-4444-444444444444", "name": "James Park", "preferred_name": None, "current_job": None},
        ],
        entities=[
            GoldenEntity(name="James", ambiguous=True),
        ],
        proposals_by_person={},
        skip_fact_for_ambiguous=True,
    ),
    GoldenFixture(
        id="multi_person_dinner",
        transcript=(
            "Alex introduced me to Sarah at dinner. Sarah is interested in startups. "
            "Alex works at Anthropic."
        ),
        existing_people=[
            {"id": ALEX_ID, "name": "Alex", "preferred_name": None, "current_job": None},
            {"id": SARAH_ID, "name": "Sarah", "preferred_name": None, "current_job": None},
        ],
        entities=[
            GoldenEntity(name="Alex", confidence_min=0.9, person_id=ALEX_ID),
            GoldenEntity(name="Sarah", confidence_min=0.9, person_id=SARAH_ID),
        ],
        proposals_by_person={
            "Alex": [GoldenProposal("job", "Anthropic")],
            "Sarah": [GoldenProposal("interest", "startups")],
        },
        tasks=[],
    ),
    GoldenFixture(
        id="commitment_two_people",
        transcript=(
            "I told Sarah I'd introduce her to James. Sarah works at Stripe."
        ),
        existing_people=[
            {"id": SARAH_ID, "name": "Sarah", "preferred_name": None, "current_job": None},
        ],
        entities=[
            GoldenEntity(name="Sarah", confidence_min=0.9, person_id=SARAH_ID),
            GoldenEntity(name="James", ambiguous=True),
        ],
        proposals_by_person={
            "Sarah": [GoldenProposal("job", "Stripe")],
        },
        tasks=[{"person_name": "Sarah", "description": "introduce Sarah to James"}],
    ),
]


def stub_resolution_for(fixture: GoldenFixture):
    """Build deterministic ResolutionOutput matching golden labels."""
    from app.pipeline.entity_resolution import EntityMatch, ResolutionOutput
    from uuid import UUID

    matches = []
    for e in fixture.entities:
        conf = 0.5 if e.ambiguous else max(e.confidence_min or 0.95, 0.95)
        matches.append(
            EntityMatch(
                name=e.name,
                person_id=UUID(e.person_id) if e.person_id else None,
                is_new=e.person_id is None and not e.ambiguous,
                confidence=conf,
                transcript_segment=fixture.transcript,
                role="attendee",
            )
        )
    return ResolutionOutput(matches=matches)


def stub_facts_for(fixture: GoldenFixture, person_name: str):
    from app.models.schemas import FactCategory, FactStatus
    from app.pipeline.fact_extraction import FactExtractionOutput, FactProposalItem

    items = []
    for p in fixture.proposals_by_person.get(person_name, []):
        items.append(
            FactProposalItem(
                category=FactCategory(p.category),
                value=p.value,
                status=FactStatus(p.status),
                confidence=0.9,
                supersedes_category=p.supersedes,
            )
        )
    return FactExtractionOutput(proposals=items)

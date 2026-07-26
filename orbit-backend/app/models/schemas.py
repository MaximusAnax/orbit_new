from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class OrbitLevel(StrEnum):
    INNER = "inner"
    CLOSE = "close"
    ACTIVE = "active"
    EXTENDED = "extended"
    OUTER = "outer"


class EventSource(StrEnum):
    VOICE = "voice"
    TEXT = "text"
    MANUAL = "manual"


class EventStatus(StrEnum):
    CAPTURED = "captured"
    CONFIRMED = "confirmed"
    SYNCED = "synced"


class FactCategory(StrEnum):
    JOB = "job"
    LOCATION = "location"
    SCHOOL = "school"
    INTEREST = "interest"
    GOAL = "goal"
    FAMILY = "family"
    OTHER = "other"


class EducationLevel(StrEnum):
    HIGH_SCHOOL = "high_school"
    UNDERGRAD = "undergrad"
    GRAD = "grad"
    PHD = "phd"
    ALUMNI = "alumni"
    OTHER = "other"


class FactStatus(StrEnum):
    CURRENT = "current"
    PAST = "past"
    UNCERTAIN = "uncertain"


class ProposalDecision(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class ParticipantRole(StrEnum):
    ATTENDEE = "attendee"
    INTRODUCER = "introducer"
    INTRODUCED = "introduced"
    MENTIONED_NOT_PRESENT = "mentioned_not_present"


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class RelationshipSource(StrEnum):
    EXPLICIT = "explicit"
    INFERRED_SUGGESTED = "inferred_suggested"


# --- Request/response models ---


class PersonCreate(BaseModel):
    name: str
    preferred_name: str | None = None
    photo_url: str | None = None
    pronunciation_note: str | None = None
    orbit: OrbitLevel | None = None
    linked_phone_contact_id: str | None = None


class PersonSummary(BaseModel):
    id: UUID
    name: str
    preferred_name: str | None = None
    photo_url: str | None = None
    orbit: OrbitLevel | None = None
    created_at: datetime


class ProposedFact(BaseModel):
    category: FactCategory
    value: str
    status: FactStatus = FactStatus.CURRENT
    organization_name: str | None = None
    education_level: EducationLevel | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    supersedes_category: bool = False

    def formatted_value(self) -> str:
        """Preserve education level in the stored/displayed school value."""
        if self.category == FactCategory.SCHOOL and self.education_level:
            label = {
                EducationLevel.HIGH_SCHOOL: "high school",
                EducationLevel.UNDERGRAD: "undergraduate",
                EducationLevel.GRAD: "graduate",
                EducationLevel.PHD: "PhD",
                EducationLevel.ALUMNI: "alumni",
                EducationLevel.OTHER: "other",
            }[self.education_level]
            base = self.value.strip()
            if label.lower() in base.lower():
                return base
            return f"{base} ({label})"
        return self.value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_value(self) -> str:
        return self.formatted_value()


class ProposalOut(BaseModel):
    id: UUID
    event_id: UUID
    person_id: UUID
    proposed_fact: ProposedFact
    decision: ProposalDecision
    resolved_at: datetime | None = None
    created_at: datetime


class ProposalAcceptBody(BaseModel):
    value: str | None = None


class EventCreateText(BaseModel):
    text: str
    date: datetime | None = None
    location: str | None = None


class EventOut(BaseModel):
    id: UUID
    date: datetime
    location: str | None = None
    title: str | None = None
    raw_transcript: str | None = None
    summary: str | None = None
    source: EventSource
    status: EventStatus
    created_at: datetime
    participants: list[dict[str, Any]] = []
    proposal_summary: dict[str, int] = Field(default_factory=dict)
    ambiguity_flags: list[dict[str, Any]] = Field(default_factory=list)


class EventAcceptedResponse(BaseModel):
    id: UUID
    status: str = "accepted"


class FactOut(BaseModel):
    id: UUID
    category: FactCategory
    value: str
    status: FactStatus
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    organization_name: str | None = None


class TaskOut(BaseModel):
    id: UUID
    person_id: UUID
    event_id: UUID | None = None
    description: str
    status: TaskStatus
    due_date: date | None = None
    created_at: datetime


class TaskPatch(BaseModel):
    status: TaskStatus


class RelationshipStateOut(BaseModel):
    id: UUID
    description: str | None = None
    desired_closeness: str | None = None
    desired_cadence: str | None = None
    direction: str | None = None
    source: RelationshipSource
    recorded_at: datetime

class RelationshipStateCreate(BaseModel):
    description: str | None = None
    desired_closeness: str | None = None
    desired_cadence: str | None = None
    direction: str | None = None


class TimelineEvent(BaseModel):
    id: UUID
    date: datetime
    title: str | None = None
    summary: str | None = None
    location: str | None = None


class PastFactOut(BaseModel):
    id: UUID
    category: FactCategory
    value: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class PersonProfile(BaseModel):
    id: UUID
    name: str
    preferred_name: str | None = None
    photo_url: str | None = None
    pronunciation_note: str | None = None
    orbit: OrbitLevel | None = None
    current_facts: list[FactOut] = []
    past_facts: list[PastFactOut] = []
    timeline: list[TimelineEvent] = []
    open_tasks: list[TaskOut] = []
    relationship_state: RelationshipStateOut | None = None
    suggested_relationship_states: list[RelationshipStateOut] = []
    last_interaction_at: datetime | None = None


class CatchupResponse(BaseModel):
    summary: str
    talking_points: list[str]


class SearchResult(BaseModel):
    person_id: UUID
    person_name: str
    match_reason: str
    source_type: str
    source_id: UUID


class DiscoverResult(BaseModel):
    person_id: UUID
    person_name: str
    match_reason: str
    path: list[str] = []


class AmbiguityFlagOut(BaseModel):
    id: UUID
    event_id: UUID
    raw_fragment: str
    candidate_person_ids: list[UUID]
    resolved_person_id: UUID | None = None


class AmbiguityResolveBody(BaseModel):
    person_id: UUID


class MaintenanceSuggestionOut(BaseModel):
    person_id: UUID
    person_name: str
    score: float
    target_days: int
    days_since_contact: int | None = None
    reasons: list[str]
    reason_codes: list[str] = []

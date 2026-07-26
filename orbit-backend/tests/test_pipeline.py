from uuid import uuid4

import pytest
from pydantic import BaseModel, Field

from app.core.config import settings
from app.models.schemas import FactCategory, FactStatus, ProposedFact
from app.pipeline import entity_resolution
from app.pipeline.entity_resolution import EntityMatch, ResolutionOutput
from app.pipeline.providers import LLMProvider, set_llm


class MockLLM(LLMProvider):
    def __init__(self, response: BaseModel):
        self.response = response

    async def structured_completion(self, system, user, response_model):
        return self.response, {"system": system, "user": user}, {"parsed": True}


@pytest.mark.asyncio
async def test_low_confidence_routes_to_ambiguous(monkeypatch):
    event_id = uuid4()
    flags_saved = []

    async def mock_save_flag(eid, fragment, candidates):
        flags_saved.append((fragment, candidates))

    async def mock_log(*args, **kwargs):
        return None

    monkeypatch.setattr(entity_resolution.repo, "save_ambiguity_flag", mock_save_flag)
    monkeypatch.setattr(entity_resolution.repo, "log_pipeline_debug", mock_log)
    monkeypatch.setattr(entity_resolution.repo, "add_event_participant", lambda *a, **k: None)
    monkeypatch.setattr(entity_resolution.repo, "create_person", lambda **k: {"id": uuid4()})

    low_conf = EntityMatch(
        name="James",
        confidence=0.5,
        transcript_segment="someone works in AI",
        person_id=None,
    )
    set_llm(MockLLM(ResolutionOutput(matches=[low_conf])))

    result = await entity_resolution.resolve_entities(event_id, "transcript", [])

    assert len(result.ambiguous) == 1
    assert len(result.matches) == 0
    assert len(flags_saved) == 1


@pytest.mark.asyncio
async def test_high_confidence_resolves_person(monkeypatch):
    event_id = uuid4()
    person_id = uuid4()
    participants = []

    async def mock_add_participant(eid, pid, role, notes):
        participants.append(pid)

    async def mock_log(*args, **kwargs):
        return None

    monkeypatch.setattr(entity_resolution.repo, "save_ambiguity_flag", lambda *a, **k: None)
    monkeypatch.setattr(entity_resolution.repo, "log_pipeline_debug", mock_log)
    monkeypatch.setattr(entity_resolution.repo, "add_event_participant", mock_add_participant)

    match = EntityMatch(
        name="Sarah",
        person_id=person_id,
        confidence=0.95,
        transcript_segment="Sarah works at Stripe",
    )
    set_llm(MockLLM(ResolutionOutput(matches=[match])))

    result = await entity_resolution.resolve_entities(event_id, "transcript", [])

    assert len(result.matches) == 1
    assert participants == [person_id]


def test_proposed_fact_model():
    fact = ProposedFact(
        category=FactCategory.JOB,
        value="Stripe",
        confidence=0.9,
        supersedes_category=True,
    )
    assert fact.status == FactStatus.CURRENT
    assert fact.supersedes_category is True

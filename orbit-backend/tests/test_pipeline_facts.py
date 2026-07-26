"""Additional pipeline fixture tests."""

from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.schemas import FactCategory, FactStatus
from app.pipeline import fact_extraction
from app.pipeline.fact_extraction import FactExtractionOutput, FactProposalItem
from app.pipeline.providers import LLMProvider, set_llm
from app.pipeline.task_extraction import TaskExtractionOutput, TaskItem, extract_tasks


class MockLLM(LLMProvider):
    def __init__(self, response):
        self.response = response

    async def structured_completion(self, system, user, response_model):
        return self.response, {}, {}


@pytest.mark.asyncio
async def test_low_confidence_facts_filtered(monkeypatch):
    event_id = uuid4()
    person_id = uuid4()
    saved = []

    async def mock_save(eid, pid, proposal):
        saved.append(proposal)
        return {"id": uuid4()}

    async def mock_log(*a, **k):
        return None

    monkeypatch.setattr(fact_extraction.repo, "save_proposal", mock_save)
    monkeypatch.setattr(fact_extraction.repo, "log_pipeline_debug", mock_log)

    set_llm(
        MockLLM(
            FactExtractionOutput(
                proposals=[
                    FactProposalItem(
                        category=FactCategory.JOB,
                        value="MaybeStripe",
                        confidence=0.4,
                    ),
                    FactProposalItem(
                        category=FactCategory.INTEREST,
                        value="videography",
                        confidence=0.9,
                    ),
                ]
            )
        )
    )

    results = await fact_extraction.extract_facts(event_id, person_id, "segment", [])
    assert all(p.confidence >= settings.confidence_threshold for p in results)
    assert any(p.value == "videography" for p in results)
    assert not any(p.value == "MaybeStripe" for p in results)


@pytest.mark.asyncio
async def test_task_extraction_maps_person(monkeypatch):
    event_id = uuid4()
    person_id = uuid4()
    saved = []

    async def mock_save(pid, desc, eid=None, due_date=None):
        saved.append({"person_id": pid, "description": desc})
        return {"id": uuid4(), "person_id": pid, "description": desc}

    async def mock_log(*a, **k):
        return None

    from app.pipeline import task_extraction

    monkeypatch.setattr(task_extraction.repo, "save_task", mock_save)
    monkeypatch.setattr(task_extraction.repo, "log_pipeline_debug", mock_log)
    set_llm(
        MockLLM(
            TaskExtractionOutput(
                tasks=[TaskItem(person_name="Sarah", description="send paper", confidence=0.9)]
            )
        )
    )
    await extract_tasks(event_id, "I'll send Sarah the paper", {"sarah": person_id})
    assert len(saved) == 1
    assert saved[0]["person_id"] == person_id

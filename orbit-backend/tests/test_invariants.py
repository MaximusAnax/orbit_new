"""Append-only invariant tests — require running Postgres."""

import os
from uuid import uuid4

import pytest

from app.core.config import settings
from app.db import repository as repo
from app.db.connection import close_pool, fetch_one, init_pool
from app.models.schemas import FactCategory, FactStatus, ProposedFact, ProposalDecision

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    if not os.getenv("RUN_INTEGRATION_TESTS"):
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run DB integration tests")
    await init_pool()
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_fact_append_only_supersedes(db):
    person = await repo.create_person(name=f"Test {uuid4().hex[:8]}")
    person_id = person["id"]

    proposed1 = ProposedFact(category=FactCategory.JOB, value="Google", confidence=0.9)
    fact1 = await repo.materialize_fact_from_proposal(person_id, proposed1, None)
    original_id = fact1["id"]
    original_value = fact1["value"]

    proposed2 = ProposedFact(
        category=FactCategory.JOB,
        value="Stripe",
        confidence=0.9,
        supersedes_category=True,
    )
    await repo.materialize_fact_from_proposal(person_id, proposed2, None)

    old_row = await fetch_one("select * from fact where id = $1", original_id)
    assert old_row["value"] == original_value
    assert old_row["status"] == "past"
    assert old_row["valid_to"] is not None

    current = await repo.get_current_facts(person_id)
    assert len(current) == 1
    assert current[0]["value"] == "Stripe"
    assert current[0]["status"] == "current"


@pytest.mark.asyncio
async def test_proposal_dedupes(db):
    person = await repo.create_person(name=f"Dedup {uuid4().hex[:8]}")
    event = await repo.create_event(
        source="text",
        date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        raw_transcript="test",
    )
    proposed = ProposedFact(category=FactCategory.INTEREST, value="videography", confidence=0.85)
    first = await repo.save_proposal(event["id"], person["id"], proposed)
    second = await repo.save_proposal(event["id"], person["id"], proposed)
    assert first is not None
    assert second is None

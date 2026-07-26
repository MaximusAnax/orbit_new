import os
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def auth_disabled():
    original = settings.auth_disabled
    settings.auth_disabled = True
    yield
    settings.auth_disabled = original


@pytest.fixture
async def client(auth_disabled):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 with Postgres running",
)
@pytest.mark.asyncio
async def test_post_events_returns_202(client):
    response = await client.post(
        "/api/v1/events/text",
        json={"text": "I had dinner with Sarah. She works at Stripe now."},
    )
    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

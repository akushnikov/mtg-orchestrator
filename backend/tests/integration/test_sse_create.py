from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

try:
    from app.api.v1 import instances
    from app.api.v1.instances import router
    from app.main import app
except ImportError:
    instances = None
    router = None
    app = None


def _require_sse_endpoint():
    if router is None or not hasattr(instances, "create_instance_stream"):
        pytest.skip("SSE create endpoint not yet implemented")


@pytest_asyncio.fixture
async def client(monkeypatch):
    if app is None:
        pytest.skip("FastAPI app not yet implemented")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_sse_create_stream_unauthenticated(client):
    _require_sse_endpoint()

    response = await client.post("/api/v1/instances/create/stream", json={"domain": "ria.ru"})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_sse_create_stream_stub(client, monkeypatch):
    _require_sse_endpoint()
    monkeypatch.setattr(instances, "require_owner", AsyncMock(return_value=12345), raising=False)
    monkeypatch.setattr(
        instances.proxy_service,
        "create_instance",
        AsyncMock(return_value={"stage": "done", "domain": "ria.ru"}),
    )

    async with client.stream(
        "POST",
        "/api/v1/instances/create/stream",
        json={"domain": "ria.ru"},
        headers={"Authorization": "tma test"},
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    assert "done" in body.decode()

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

try:
    from app.api.v1 import instances
    from app.db.models import Base
    from app.api.v1.instances import router
    from app.main import app
except ImportError:
    instances = None
    Base = None
    router = None
    app = None

from tests.conftest import owner_auth_headers


def _require_sse_endpoint():
    if router is None or not hasattr(instances, "create_instance_stream"):
        pytest.skip("SSE create endpoint not yet implemented")


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path):
    if app is None or Base is None:
        pytest.skip("FastAPI app not yet implemented")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(instances, "AsyncSessionLocal", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

    await engine.dispose()


@pytest.mark.asyncio
async def test_sse_create_stream_unauthenticated(client):
    _require_sse_endpoint()

    response = await client.post("/api/v1/instances/create/stream", json={"domain": "ria.ru"})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_sse_create_stream(client, monkeypatch):
    _require_sse_endpoint()
    fake_row = object()
    monkeypatch.setattr(
        instances.proxy_service,
        "create_instance",
        AsyncMock(
            return_value=(
                fake_row,
                "tg://proxy?server=1.2.3.4&port=443&secret=eeAA",
            )
        ),
    )

    async with client.stream(
        "POST",
        "/api/v1/instances/create/stream",
        json={"domain": "ria.ru"},
        headers=owner_auth_headers(),
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    text = body.decode()
    assert "event: done" in text
    assert '"stage": "done"' in text
    assert "retry: 0" in text


@pytest.mark.asyncio
async def test_sse_create_stream_invalid_domain(client, monkeypatch):
    _require_sse_endpoint()
    monkeypatch.setattr(
        instances.proxy_service,
        "create_instance",
        AsyncMock(side_effect=instances.proxy_service.InvalidDomainError("invalid")),
    )

    async with client.stream(
        "POST",
        "/api/v1/instances/create/stream",
        json={"domain": "bad.local"},
        headers=owner_auth_headers(),
    ) as response:
        body = await response.aread()

    assert response.status_code == 200
    text = body.decode()
    assert "event: error" in text
    assert '"stage": "validating"' in text
    assert '"status": "error"' in text
    assert "retry: 0" in text


@pytest.mark.asyncio
async def test_default_proxy_with_secret(client, monkeypatch):
    _require_sse_endpoint()
    monkeypatch.setattr(instances.settings, "mtg_default_domain", "ria.ru")
    monkeypatch.setattr(instances.settings, "mtg_default_secret", "eeAA")

    response = await client.get("/api/v1/instances/default", headers=owner_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "id": -1,
        "domain": "ria.ru",
        "tg_url": "tg://proxy?server=1.2.3.4&port=443&secret=eeAA",
        "read_only": True,
    }


@pytest.mark.asyncio
async def test_default_proxy_empty_secret(client, monkeypatch):
    _require_sse_endpoint()
    monkeypatch.setattr(instances.settings, "mtg_default_domain", "ria.ru")
    monkeypatch.setattr(instances.settings, "mtg_default_secret", "")

    response = await client.get("/api/v1/instances/default", headers=owner_auth_headers())

    assert response.status_code == 200
    assert response.json()["tg_url"] == ""


@pytest.mark.asyncio
async def test_default_proxy_unauthenticated(client):
    _require_sse_endpoint()

    response = await client.get("/api/v1/instances/default")

    assert response.status_code == 403

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import instances
from app.db.models import Base
from app.main import app
from app.services import docker_service, domain_validator, nginx_service, proxy_service


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path):
    db_path = tmp_path / "registry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(instances, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(domain_validator, "is_safe_external_domain", lambda domain: True)
    monkeypatch.setattr(domain_validator, "validate_domain_tls", AsyncMock(return_value=True))
    monkeypatch.setattr(docker_service, "get_proxy_net_name", AsyncMock(return_value="test_proxy-net"))
    monkeypatch.setattr(
        docker_service,
        "create_mtg_container",
        AsyncMock(return_value=("container-1", None)),
    )
    monkeypatch.setattr(docker_service, "remove_container", AsyncMock(return_value=None))
    monkeypatch.setattr(nginx_service, "render_and_reload", AsyncMock(return_value=None))
    monkeypatch.setattr(nginx_service, "backup_nginx_config", lambda: "")
    monkeypatch.setattr(nginx_service, "restore_nginx_config", lambda backup: None)
    monkeypatch.setattr(proxy_service.settings, "mtg_configs_dir", str(tmp_path / "mtg-configs"))
    monkeypatch.setattr(proxy_service.settings, "nginx_config_dir", str(tmp_path / "nginx"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_instance(client):
    response = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["domain"] == "ria.ru"
    assert payload["tg_url"].startswith("tg://proxy?server=1.2.3.4&port=443&secret=ee")
    assert "secret" not in payload


@pytest.mark.asyncio
async def test_create_duplicate_domain(client):
    first = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert first.status_code == 201

    second = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_list_instances_excludes_secret(client):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201

    response = await client.get("/api/v1/instances/")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["tg_url"] == ""
    assert "secret" not in payload[0]


@pytest.mark.asyncio
async def test_create_invalid_domain_tls(client, monkeypatch):
    monkeypatch.setattr(domain_validator, "validate_domain_tls", AsyncMock(return_value=False))

    response = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})

    assert response.status_code == 422

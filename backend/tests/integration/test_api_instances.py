from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1 import instances
from app.db import crud
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
    monkeypatch.setattr(docker_service, "start_container", AsyncMock(return_value=None))
    monkeypatch.setattr(docker_service, "stop_container", AsyncMock(return_value=None))
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


@pytest.mark.asyncio
async def test_delete_instance(client):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201
    instance_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/instances/{instance_id}")
    assert deleted.status_code == 204

    listed = await client.get("/api/v1/instances/")
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.asyncio
async def test_delete_nonexistent(client):
    response = await client.delete("/api/v1/instances/9999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stop_instance(client):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201
    instance_id = created.json()["id"]

    stopped = await client.patch(f"/api/v1/instances/{instance_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"

    listed = await client.get("/api/v1/instances/")
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "stopped"
    assert listed.json()[0]["tg_url"] == ""


@pytest.mark.asyncio
async def test_start_instance(client):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201
    instance_id = created.json()["id"]
    assert (await client.patch(f"/api/v1/instances/{instance_id}/stop")).status_code == 200

    started = await client.patch(f"/api/v1/instances/{instance_id}/start")

    assert started.status_code == 200
    assert started.json()["status"] == "running"


@pytest.mark.asyncio
async def test_stop_already_stopped(client):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201
    instance_id = created.json()["id"]
    assert (await client.patch(f"/api/v1/instances/{instance_id}/stop")).status_code == 200

    stopped_again = await client.patch(f"/api/v1/instances/{instance_id}/stop")

    assert stopped_again.status_code == 409


@pytest.mark.asyncio
async def test_reconcile_on_startup_marks_missing_container_error(client, monkeypatch):
    async with instances.AsyncSessionLocal() as session:
        row = await crud.create_instance_row(
            session,
            domain="ria.ru",
            slug="ria_ru",
            secret="secret-one",
            port=20000,
            status=proxy_service.ProxyStatus.running,
        )

    monkeypatch.setattr(proxy_service, "_container_exists", AsyncMock(return_value=False))

    async with instances.AsyncSessionLocal() as session:
        await proxy_service.reconcile_on_startup(session, "test_proxy-net")
        updated = await crud.get_instance(session, row.id)
        assert updated.status == proxy_service.ProxyStatus.error

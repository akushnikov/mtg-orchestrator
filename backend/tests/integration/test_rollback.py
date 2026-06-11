from unittest.mock import AsyncMock

import docker.errors
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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}", echo=False)
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

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_rollback_on_docker_failure(client, monkeypatch):
    create_mtg = AsyncMock(side_effect=docker.errors.APIError("injected failure"))
    stop_container = AsyncMock()
    remove_container = AsyncMock()
    monkeypatch.setattr(docker_service, "create_mtg_container", create_mtg)
    monkeypatch.setattr(docker_service, "stop_container", stop_container)
    monkeypatch.setattr(docker_service, "remove_container", remove_container)

    response = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})

    assert response.status_code == 500
    assert (await client.get("/api/v1/instances/")).json() == []
    stop_container.assert_not_called()
    remove_container.assert_not_called()


@pytest.mark.asyncio
async def test_create_rollback_on_nginx_failure(client, monkeypatch):
    render = AsyncMock(side_effect=nginx_service.NginxValidationError("injected nginx failure"))
    stop_container = AsyncMock()
    remove_container = AsyncMock()
    monkeypatch.setattr(nginx_service, "render_and_reload", render)
    monkeypatch.setattr(docker_service, "stop_container", stop_container)
    monkeypatch.setattr(docker_service, "remove_container", remove_container)

    response = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})

    assert response.status_code == 500
    assert (await client.get("/api/v1/instances/")).json() == []
    stop_container.assert_awaited_once_with("mtg-ria_ru")
    remove_container.assert_awaited_once_with("mtg-ria_ru")


@pytest.mark.asyncio
async def test_delete_partial_failure_container_removal(client, monkeypatch):
    created = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})
    assert created.status_code == 201
    instance_id = created.json()["id"]

    render = AsyncMock(return_value=None)
    remove_container = AsyncMock(side_effect=docker.errors.APIError("removal failed"))
    monkeypatch.setattr(nginx_service, "render_and_reload", render)
    monkeypatch.setattr(docker_service, "remove_container", remove_container)

    response = await client.delete(f"/api/v1/instances/{instance_id}")

    assert response.status_code == 500
    listed = (await client.get("/api/v1/instances/")).json()
    assert listed[0]["status"] == "error"
    render.assert_awaited()
    remove_container.assert_awaited_once_with("mtg-ria_ru")


@pytest.mark.asyncio
async def test_create_validation_fail_no_db_row(client, monkeypatch):
    monkeypatch.setattr(domain_validator, "validate_domain_tls", AsyncMock(return_value=False))

    response = await client.post("/api/v1/instances/", json={"domain": "ria.ru"})

    assert response.status_code == 422
    assert (await client.get("/api/v1/instances/")).json() == []

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import quote, urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

try:
    from app.api.v1 import auth as auth_module
    from app.api.v1 import instances
    from app.db.models import Base
    from app.main import app
    from app.services import docker_service, domain_validator, nginx_service, proxy_service
except ImportError:
    auth_module = None
    instances = None
    Base = None
    app = None
    docker_service = None
    domain_validator = None
    nginx_service = None
    proxy_service = None


BOT_TOKEN = "test:token123"
OWNER_USER_ID = 12345


def _require_auth():
    if auth_module is None:
        pytest.skip("auth module not yet implemented")


def _build_init_data(
    *,
    auth_date: int | None = None,
    user_id: int = OWNER_USER_ID,
    tamper_hash: bool = False,
) -> str:
    params = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if tamper_hash:
        params["hash"] = "0" * 64
    return urlencode(params, quote_via=quote)


@pytest_asyncio.fixture
async def client(monkeypatch, tmp_path):
    if app is None or Base is None:
        pytest.skip("FastAPI app not yet implemented")
    db_path = tmp_path / "registry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(auth_module.settings, "bot_token", BOT_TOKEN)
    monkeypatch.setattr(auth_module.settings, "owner_user_id", OWNER_USER_ID)
    monkeypatch.setattr(auth_module.settings, "dev_mock_init_data", False)
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
async def test_expired_auth_date_returns_403(client):
    _require_auth()

    response = await client.get(
        "/api/v1/instances/",
        headers={"Authorization": f"tma {_build_init_data(auth_date=int(time.time()) - 400)}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_tampered_hmac_returns_403(client):
    _require_auth()

    response = await client.get(
        "/api/v1/instances/",
        headers={"Authorization": f"tma {_build_init_data(tamper_hash=True)}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_non_owner_valid_hmac_returns_403(client):
    _require_auth()

    response = await client.get(
        "/api/v1/instances/",
        headers={"Authorization": f"tma {_build_init_data(user_id=99999)}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_valid_owner_returns_200(client):
    _require_auth()

    response = await client.get(
        "/api/v1/instances/",
        headers={"Authorization": f"tma {_build_init_data()}"},
    )

    assert response.status_code == 200

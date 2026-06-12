import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

try:
    from app.api.v1 import auth as auth_module
    from app.main import app
except ImportError:
    auth_module = None
    app = None


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
async def client():
    if app is None:
        pytest.skip("FastAPI app not yet implemented")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


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

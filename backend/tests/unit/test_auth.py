import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import pytest

try:
    from app.api.v1 import auth as auth_module
    from app.api.v1.auth import _validate_init_data, require_owner
except ImportError:
    auth_module = None
    _validate_init_data = None
    require_owner = None


BOT_TOKEN = "test:token123"
OWNER_USER_ID = 12345


@dataclass
class FakeSettings:
    bot_token: str = BOT_TOKEN
    owner_user_id: int = OWNER_USER_ID
    dev_mock_init_data: bool = False


@dataclass
class FakeCredentials:
    credentials: str


def _require_auth():
    if _validate_init_data is None:
        pytest.skip("auth module not yet implemented")


def _build_init_data(
    *,
    bot_token: str = BOT_TOKEN,
    auth_date: int | None = None,
    user_id: int = OWNER_USER_ID,
    extra: dict[str, str] | None = None,
) -> str:
    params = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAEAAAE",
        "user": json.dumps(
            {"id": user_id, "first_name": "Test", "username": "owner"},
            separators=(",", ":"),
        ),
    }
    if extra:
        params.update(extra)

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(params, quote_via=quote)


def test_valid_init_data_passes():
    _require_auth()

    parsed = _validate_init_data(_build_init_data(), BOT_TOKEN)

    assert parsed["user"]["id"] == OWNER_USER_ID


def test_expired_auth_date_raises():
    _require_auth()

    with pytest.raises(ValueError):
        _validate_init_data(_build_init_data(auth_date=int(time.time()) - 400), BOT_TOKEN)


def test_tampered_hash_raises():
    _require_auth()
    init_data = _build_init_data().replace("hash=", "hash=deadbeef")

    with pytest.raises(ValueError):
        _validate_init_data(init_data, BOT_TOKEN)


def test_missing_hash_raises():
    _require_auth()
    init_data = "&".join(part for part in _build_init_data().split("&") if not part.startswith("hash="))

    with pytest.raises(ValueError):
        _validate_init_data(init_data, BOT_TOKEN)


@pytest.mark.asyncio
async def test_owner_whitelist_pass(monkeypatch):
    _require_auth()
    monkeypatch.setattr(auth_module, "settings", FakeSettings())

    user_id = await require_owner(FakeCredentials(_build_init_data()))

    assert user_id == OWNER_USER_ID


@pytest.mark.asyncio
async def test_owner_whitelist_reject(monkeypatch):
    _require_auth()
    monkeypatch.setattr(auth_module, "settings", FakeSettings())

    with pytest.raises(Exception) as exc_info:
        await require_owner(FakeCredentials(_build_init_data(user_id=99999)))

    assert getattr(exc_info.value, "status_code", None) == 403


@pytest.mark.asyncio
async def test_dev_bypass_refused_when_bot_token_set(monkeypatch):
    _require_auth()
    monkeypatch.setattr(auth_module, "settings", FakeSettings(dev_mock_init_data=True))

    with pytest.raises(Exception) as exc_info:
        await require_owner(FakeCredentials("dev"))

    assert getattr(exc_info.value, "status_code", None) == 403

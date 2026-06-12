import os
import hashlib
import hmac
import json
import time
from urllib.parse import quote, urlencode

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("MOSCOW_IP", "1.2.3.4")
os.environ.setdefault("PANEL_DOMAIN", "panel.example.com")
os.environ.setdefault("BOT_TOKEN", "test:token123")
os.environ.setdefault("OWNER_USER_ID", "12345")


def build_init_data(
    *,
    bot_token: str = "test:token123",
    auth_date: int | None = None,
    user_id: int = 12345,
) -> str:
    params = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(params, quote_via=quote)


def owner_auth_headers() -> dict[str, str]:
    return {"Authorization": f"tma {build_init_data()}"}


@pytest_asyncio.fixture
async def db_session():
    try:
        from app.db.models import Base
    except ImportError:
        pytest.skip("Database models not implemented yet", allow_module_level=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()

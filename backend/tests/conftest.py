import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("MOSCOW_IP", "1.2.3.4")
os.environ.setdefault("PANEL_DOMAIN", "panel.example.com")
os.environ.setdefault("BOT_TOKEN", "test:token123")
os.environ.setdefault("OWNER_USER_ID", "12345")


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

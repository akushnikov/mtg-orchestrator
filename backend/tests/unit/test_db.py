from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.crud import (
    allocate_port,
    create_instance_row,
    delete_instance_row,
    list_instances,
)
from app.db.models import Base, ProxyStatus


def _registry_engine(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_conn, _) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def _create_schema(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_wal_mode(tmp_path):
    engine = _registry_engine(tmp_path / "registry.db")
    try:
        await _create_schema(engine)
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            assert result.scalar_one().lower() == "wal"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_busy_timeout(tmp_path):
    engine = _registry_engine(tmp_path / "registry.db")
    try:
        await _create_schema(engine)
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA busy_timeout"))
            assert result.scalar_one() == 5000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persistence(tmp_path):
    db_path = tmp_path / "registry.db"
    engine = _registry_engine(db_path)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_schema(engine)
        async with session_factory() as session:
            await create_instance_row(
                session,
                domain="one.example.com",
                slug="one-example-com",
                secret="ee" + ("a" * 32) + "6f6e652e6578616d706c652e636f6d",
                port=20000,
                status=ProxyStatus.running,
            )
    finally:
        await engine.dispose()

    engine = _registry_engine(db_path)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            rows = await list_instances(session)
            assert len(rows) == 1
            assert rows[0].domain == "one.example.com"
            assert rows[0].port == 20000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_port_allocation_no_collision(db_session):
    port1 = await allocate_port(db_session, 20000, 20002)
    await create_instance_row(
        db_session,
        domain="one.example.com",
        slug="one-example-com",
        secret="secret-one",
        port=port1,
    )

    port2 = await allocate_port(db_session, 20000, 20002)
    assert port2 == port1 + 1


@pytest.mark.asyncio
async def test_port_allocation_range_start(db_session):
    assert await allocate_port(db_session, 20000, 20002) == 20000


@pytest.mark.asyncio
async def test_port_allocation_reuses_freed_port(db_session):
    first = await create_instance_row(
        db_session,
        domain="one.example.com",
        slug="one-example-com",
        secret="secret-one",
        port=20000,
    )
    await create_instance_row(
        db_session,
        domain="two.example.com",
        slug="two-example-com",
        secret="secret-two",
        port=20001,
    )

    await delete_instance_row(db_session, first.id)

    assert await allocate_port(db_session, 20000, 20002) == 20000


@pytest.mark.asyncio
async def test_list_documents_secret_boundary(db_session):
    await create_instance_row(
        db_session,
        domain="one.example.com",
        slug="one-example-com",
        secret="secret-one",
        port=20000,
    )

    rows = await list_instances(db_session)
    assert rows[0].secret == "secret-one"

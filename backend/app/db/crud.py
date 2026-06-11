from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProxyInstance, ProxyStatus


async def allocate_port(session: AsyncSession, start: int, end: int) -> int:
    result = await session.execute(
        select(ProxyInstance.port)
        .where(ProxyInstance.port.between(start, end))
        .order_by(ProxyInstance.port)
    )
    used_ports = set(result.scalars().all())

    for port in range(start, end + 1):
        if port not in used_ports:
            return port

    raise RuntimeError("Port range exhausted")


async def create_instance_row(
    session: AsyncSession,
    domain: str,
    slug: str,
    secret: str,
    port: int,
    status: ProxyStatus = ProxyStatus.creating,
) -> ProxyInstance:
    instance = ProxyInstance(
        domain=domain,
        slug=slug,
        secret=secret,
        port=port,
        status=status,
    )
    session.add(instance)
    await session.commit()
    await session.refresh(instance)
    return instance


async def update_instance_status(
    session: AsyncSession,
    instance_id: int,
    status: ProxyStatus,
    container_id: str | None = None,
) -> ProxyInstance:
    instance = await get_instance(session, instance_id)
    if instance is None:
        raise LookupError(f"Proxy instance {instance_id} not found")

    instance.status = status
    if container_id is not None:
        instance.container_id = container_id
    await session.commit()
    await session.refresh(instance)
    return instance


async def delete_instance_row(session: AsyncSession, instance_id: int) -> None:
    await session.execute(delete(ProxyInstance).where(ProxyInstance.id == instance_id))
    await session.commit()


async def get_instance(session: AsyncSession, instance_id: int) -> ProxyInstance | None:
    result = await session.execute(
        select(ProxyInstance).where(ProxyInstance.id == instance_id)
    )
    return result.scalar_one_or_none()


async def get_instance_by_domain(
    session: AsyncSession,
    domain: str,
) -> ProxyInstance | None:
    result = await session.execute(
        select(ProxyInstance).where(ProxyInstance.domain == domain)
    )
    return result.scalar_one_or_none()


async def list_instances(session: AsyncSession) -> list[ProxyInstance]:
    result = await session.execute(
        select(ProxyInstance).order_by(ProxyInstance.created_at.desc())
    )
    return list(result.scalars().all())

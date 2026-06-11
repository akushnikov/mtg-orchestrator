import asyncio
from pathlib import Path
import re
import secrets
from types import SimpleNamespace

import docker.errors
import docker
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import crud
from app.db.models import ProxyInstance, ProxyStatus
from app.services import docker_service, domain_validator, nginx_service


class DuplicateDomainError(ValueError):
    pass


class InvalidDomainError(ValueError):
    pass


class InstanceNotFoundError(ValueError):
    pass


class InstanceStateError(ValueError):
    pass


class LifecycleOperationError(RuntimeError):
    pass


def domain_to_slug(domain: str) -> str:
    slug = domain.lower().replace(".", "_")
    slug = re.sub(r"[^a-z0-9_-]", "_", slug)
    return slug[:63]


def generate_mtg_secret(domain: str) -> str:
    key = secrets.token_bytes(16)
    domain_bytes = domain.encode("ascii")
    raw = bytes([0xEE]) + key + domain_bytes
    secret = raw.hex()
    assert secret.startswith("ee")
    assert secret[2:34] == key.hex()
    assert bytes.fromhex(secret[34:]).decode("ascii") == domain
    return secret


def build_tg_proxy_url(moscow_ip: str, secret: str) -> str:
    return f"tg://proxy?server={moscow_ip}&port=443&secret={secret}"


def build_nginx_instance_context(row: ProxyInstance) -> SimpleNamespace:
    return SimpleNamespace(
        domain=row.domain,
        slug=row.slug,
        port=row.port,
        upstream_host=None,
    )


def write_instance_toml(slug: str, secret: str, mtproto_port: int) -> Path:
    # Template ships inside the app package (backend/app/templates) so it is
    # packaged into the image via `COPY app/ ./app/` — same pattern as
    # nginx_service. A repo-relative path (parents[3]/infra/mtg) does not exist
    # in the container, where the code lives at /backend/app with no /infra.
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    rendered = env.get_template("instance.config.toml.j2").render(
        secret=secret,
        mtproto_port=mtproto_port,
        prometheus_port=mtproto_port + 1,
    )
    target_dir = Path(settings.mtg_configs_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{slug}.toml"
    target.write_text(rendered, encoding="utf-8")
    return target


def _remove_toml(slug: str) -> None:
    path = Path(settings.mtg_configs_dir) / f"{slug}.toml"
    if path.exists():
        path.unlink()


def _container_exists_sync(slug: str) -> bool:
    client = docker.DockerClient(base_url=settings.docker_host)
    try:
        client.containers.get(f"mtg-{slug}")
        return True
    except docker.errors.NotFound:
        return False


async def _container_exists(slug: str) -> bool:
    return await asyncio.to_thread(_container_exists_sync, slug)


async def _active_contexts(session: AsyncSession, include: ProxyInstance | None = None):
    rows = await crud.list_instances(session)
    contexts = [
        build_nginx_instance_context(row)
        for row in rows
        if row.status == ProxyStatus.running
    ]
    if include is not None and include.status != ProxyStatus.running:
        contexts.append(build_nginx_instance_context(include))
    return contexts


async def _running_contexts_excluding(
    session: AsyncSession,
    instance_id: int,
) -> list[SimpleNamespace]:
    rows = await crud.list_instances(session)
    return [
        build_nginx_instance_context(row)
        for row in rows
        if row.status == ProxyStatus.running and row.id != instance_id
    ]


async def _running_contexts_including(
    session: AsyncSession,
    instance: ProxyInstance,
) -> list[SimpleNamespace]:
    contexts = await _running_contexts_excluding(session, instance.id)
    contexts.append(build_nginx_instance_context(instance))
    return contexts


async def _get_required_instance(
    session: AsyncSession,
    instance_id: int,
) -> ProxyInstance:
    row = await crud.get_instance(session, instance_id)
    if row is None:
        raise InstanceNotFoundError("Instance not found")
    return row


async def write_startup_nginx_config(session: AsyncSession) -> None:
    """Render the active nginx.conf into the shared volume at startup.

    The nginx container loads its config from the nginx-config volume
    (`nginx -c /data/nginx/nginx.conf`). On a fresh boot that file must exist
    before nginx starts, so we render it here (panel route + default + any
    currently-running instances). No Docker calls and no SIGHUP — nginx is not
    up yet; it reads this file on its own first start.
    """
    contexts = await _active_contexts(session)
    config_str = nginx_service.render_nginx_config(settings.panel_domain, contexts)
    nginx_service.write_candidate(config_str)
    nginx_service.swap_nginx_config()


async def reconcile_on_startup(session: AsyncSession, proxy_net_name: str) -> None:  # noqa: ARG001
    rows = await crud.list_instances(session)
    for row in rows:
        if row.status != ProxyStatus.running:
            continue
        if not await _container_exists(row.slug):
            await crud.update_instance_status(session, row.id, ProxyStatus.error)


async def create_instance(
    session: AsyncSession,
    domain: str,
    proxy_net_name: str | None = None,
) -> tuple[ProxyInstance, str]:
    domain = domain.strip().lower()
    if not domain_validator.is_safe_external_domain(domain):
        raise InvalidDomainError("Domain resolves to a private or reserved IP address")
    if not await domain_validator.validate_domain_tls(domain):
        raise InvalidDomainError("Domain TLS handshake failed")
    if await crud.get_instance_by_domain(session, domain):
        raise DuplicateDomainError("A proxy instance for this domain already exists")

    slug = domain_to_slug(domain)
    port = await crud.allocate_port(
        session,
        settings.mtg_port_range_start,
        settings.mtg_port_range_end,
    )
    secret = generate_mtg_secret(domain)
    row: ProxyInstance | None = None
    container_name = f"mtg-{slug}"
    container_created = False
    nginx_backup = nginx_service.backup_nginx_config()

    try:
        row = await crud.create_instance_row(
            session,
            domain=domain,
            slug=slug,
            secret=secret,
            port=port,
            status=ProxyStatus.creating,
        )
        write_instance_toml(slug, secret, row.port)
        if proxy_net_name is None:
            proxy_net_name = await docker_service.get_proxy_net_name()
        container_id, upstream_host = await docker_service.create_mtg_container(
            slug,
            proxy_net_name,
        )
        container_created = True

        active_contexts = await _active_contexts(session, include=row)
        for context in active_contexts:
            if context.slug == row.slug:
                context.upstream_host = upstream_host
        await nginx_service.render_and_reload(settings.panel_domain, active_contexts)

        row = await crud.update_instance_status(
            session,
            row.id,
            ProxyStatus.running,
            container_id=container_id,
        )
        return row, build_tg_proxy_url(settings.moscow_ip, secret)
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateDomainError("A proxy instance for this domain already exists") from exc
    except Exception:
        if row is not None:
            await crud.delete_instance_row(session, row.id)
        _remove_toml(slug)
        if container_created:
            try:
                await docker_service.stop_container(container_name)
            except Exception:
                pass
            try:
                await docker_service.remove_container(container_name)
            except Exception:
                pass
        nginx_service.restore_nginx_config(nginx_backup)
        raise


async def delete_instance(session: AsyncSession, instance_id: int) -> None:
    row = await _get_required_instance(session, instance_id)
    active_contexts = await _running_contexts_excluding(session, row.id)
    await nginx_service.render_and_reload(settings.panel_domain, active_contexts)

    container_name = f"mtg-{row.slug}"
    try:
        await docker_service.stop_container(container_name)
    except docker.errors.NotFound:
        pass

    try:
        await docker_service.remove_container(container_name)
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        await crud.update_instance_status(session, row.id, ProxyStatus.error)
        raise LifecycleOperationError("Container removal failed after route removal") from exc

    await crud.delete_instance_row(session, row.id)
    _remove_toml(row.slug)


async def stop_instance(session: AsyncSession, instance_id: int) -> ProxyInstance:
    row = await _get_required_instance(session, instance_id)
    if row.status == ProxyStatus.stopped:
        raise InstanceStateError("Instance already stopped")

    active_contexts = await _running_contexts_excluding(session, row.id)
    await nginx_service.render_and_reload(settings.panel_domain, active_contexts)

    try:
        await docker_service.stop_container(f"mtg-{row.slug}")
    except docker.errors.NotFound:
        pass

    return await crud.update_instance_status(session, row.id, ProxyStatus.stopped)


async def start_instance(session: AsyncSession, instance_id: int) -> ProxyInstance:
    row = await _get_required_instance(session, instance_id)
    if row.status == ProxyStatus.running:
        raise InstanceStateError("Instance already running")

    try:
        await docker_service.start_container(f"mtg-{row.slug}")
        active_contexts = await _running_contexts_including(session, row)
        await nginx_service.render_and_reload(settings.panel_domain, active_contexts)
    except Exception as exc:
        try:
            await docker_service.stop_container(f"mtg-{row.slug}")
        except Exception:
            pass
        await crud.update_instance_status(session, row.id, ProxyStatus.error)
        raise LifecycleOperationError("Instance restart failed") from exc

    return await crud.update_instance_status(session, row.id, ProxyStatus.running)

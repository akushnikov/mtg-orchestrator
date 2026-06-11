import asyncio
import re

import docker
from docker.types import LogConfig

from app.config import settings


def _client():
    return docker.DockerClient(base_url=settings.docker_host)


def _alias_from_slug(slug: str) -> str:
    return re.sub(r"[^a-z0-9]", "", slug.lower())


def _get_proxy_net_name() -> str:
    client = _client()
    networks = client.networks.list(filters={"name": "proxy-net"})
    for network in networks:
        if network.name.endswith("proxy-net"):
            return network.name
    raise RuntimeError("proxy-net not found")


async def get_proxy_net_name() -> str:
    return await asyncio.to_thread(_get_proxy_net_name)


def _create_mtg_container(slug: str, proxy_net_name: str, use_alias: bool = False) -> tuple[str, str | None]:
    client = _client()
    name = f"mtg-{slug}"
    container = client.containers.run(
        "nineseconds/mtg:2",
        command=["run", "--config", f"/data/mtg-configs/{slug}.toml"],
        name=name,
        detach=True,
        network=proxy_net_name,
        volumes={"mtg-configs": {"bind": "/data/mtg-configs", "mode": "ro"}},
        restart_policy={"Name": "unless-stopped"},
        log_config=LogConfig(
            type="json-file",
            config={"max-size": "10m", "max-file": "3"},
        ),
        labels={"managed-by": "mtg-orchestrator", "phase": "2"},
    )

    upstream_host = None
    if use_alias:
        upstream_host = _alias_from_slug(slug)
        if upstream_host and upstream_host != name:
            network = client.networks.get(proxy_net_name)
            network.connect(container, aliases=[upstream_host])

    return container.id, upstream_host


async def create_mtg_container(
    slug: str,
    proxy_net_name: str,
    use_alias: bool = False,
) -> tuple[str, str | None]:
    return await asyncio.to_thread(_create_mtg_container, slug, proxy_net_name, use_alias)


def _stop_container(name: str) -> None:
    _client().containers.get(name).stop(timeout=10)


async def stop_container(name: str) -> None:
    await asyncio.to_thread(_stop_container, name)


def _remove_container(name: str) -> None:
    _client().containers.get(name).remove(force=True)


async def remove_container(name: str) -> None:
    await asyncio.to_thread(_remove_container, name)


def _send_sighup(name: str = "nginx") -> None:
    _client().containers.get(name).kill(signal="HUP")


async def send_sighup(name: str = "nginx") -> None:
    await asyncio.to_thread(_send_sighup, name)

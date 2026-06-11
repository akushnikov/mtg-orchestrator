import asyncio
from pathlib import Path

import docker
from docker.types import LogConfig
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.services import docker_service


class NginxValidationError(RuntimeError):
    pass


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_nginx_config(panel_domain: str, active_instances) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("nginx.conf.j2")
    return template.render(panel_domain=panel_domain, active_instances=active_instances)


def _nginx_dir() -> Path:
    return Path(settings.nginx_config_dir)


def write_candidate(config_str: str) -> None:
    nginx_dir = _nginx_dir()
    nginx_dir.mkdir(parents=True, exist_ok=True)
    (nginx_dir / "nginx.conf.candidate").write_text(config_str, encoding="utf-8")


def backup_nginx_config() -> str:
    active = _nginx_dir() / "nginx.conf"
    if not active.exists():
        return ""
    return active.read_text(encoding="utf-8")


def swap_nginx_config() -> None:
    nginx_dir = _nginx_dir()
    (nginx_dir / "nginx.conf.candidate").replace(nginx_dir / "nginx.conf")


def restore_nginx_config(backup: str) -> None:
    write_candidate(backup)
    swap_nginx_config()


def _validate_nginx_config_sync() -> bool:
    client = docker.DockerClient(base_url=settings.docker_host)
    container = client.containers.run(
        "nginx:1.27-alpine",
        # Mount the shared config volume at /data/nginx (NOT /etc/nginx): the
        # rendered config `include`s /etc/nginx/mime.types, which must keep
        # coming from the image. Mounting over /etc/nginx would shadow it and
        # make `nginx -t` fail. The volume name must match the pinned compose
        # volume name (docker-compose.yml: volumes.nginx-config.name).
        command=["nginx", "-t", "-c", "/data/nginx/nginx.conf.candidate"],
        volumes={"nginx-config": {"bind": "/data/nginx", "mode": "ro"}},
        detach=True,
        remove=False,
        log_config=LogConfig(
            type="json-file",
            config={"max-size": "1m", "max-file": "1"},
        ),
    )
    try:
        result = container.wait(timeout=30)
        if result.get("StatusCode") != 0:
            logs = container.logs().decode("utf-8", errors="replace")
            raise NginxValidationError("nginx candidate config failed validation: " + logs)
        return True
    finally:
        container.remove(force=True)


async def validate_nginx_config() -> bool:
    return await asyncio.to_thread(_validate_nginx_config_sync)


async def render_and_reload(panel_domain: str, active_instances) -> None:
    write_candidate(render_nginx_config(panel_domain, active_instances))
    await validate_nginx_config()
    swap_nginx_config()
    await docker_service.send_sighup("nginx")

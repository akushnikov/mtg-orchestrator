import os

import pytest

pytestmark = pytest.mark.integration


def _docker_client():
    if not os.environ.get("DOCKER_HOST"):
        pytest.skip("DOCKER_HOST not set - skipping live Docker smoke tests")

    import docker

    client = docker.DockerClient(base_url=os.environ["DOCKER_HOST"])
    try:
        client.ping()
    except docker.errors.DockerException as exc:
        pytest.skip(f"Docker stack not running: {exc}")
    return client


def _proxy_net_name(client):
    networks = client.networks.list(filters={"name": "proxy-net"})
    for network in networks:
        if network.name.endswith("proxy-net"):
            return network.name
    pytest.fail("proxy-net not found")


def test_underscore_dns_resolution():
    client = _docker_client()
    proxy_net = _proxy_net_name(client)
    target_name = "smoke_underscore"
    target = None
    resolver = None

    try:
        target = client.containers.run(
            "busybox:1.36",
            command=["sleep", "60"],
            name=target_name,
            detach=True,
            network=proxy_net,
            remove=False,
        )
        resolver = client.containers.run(
            "busybox:1.36",
            command=["nslookup", target_name],
            detach=True,
            network=proxy_net,
            remove=False,
        )
        result = resolver.wait(timeout=30)
        if result.get("StatusCode") != 0:
            pytest.fail("FALLBACK REQUIRED: use network alias mtgXXX (no underscore/dash)")
    finally:
        for container in (resolver, target):
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def test_named_volume_candidate_visible_to_validation_container():
    client = _docker_client()
    writer = None
    reader = None
    try:
        writer = client.containers.run(
            "busybox:1.36",
            command=["sh", "-c", "echo smoke-ok > /data/nginx/smoke-candidate.txt"],
            volumes={"nginx-config": {"bind": "/data/nginx", "mode": "rw"}},
            detach=True,
            remove=False,
        )
        assert writer.wait(timeout=30).get("StatusCode") == 0

        reader = client.containers.run(
            "busybox:1.36",
            command=["cat", "/etc/nginx/smoke-candidate.txt"],
            volumes={"nginx-config": {"bind": "/etc/nginx", "mode": "ro"}},
            detach=True,
            remove=False,
        )
        assert reader.wait(timeout=30).get("StatusCode") == 0
        assert "smoke-ok" in reader.logs().decode("utf-8", errors="replace")
    finally:
        for container in (reader, writer):
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def test_nginx_sighup_signal_is_available():
    client = _docker_client()
    try:
        nginx = client.containers.get("nginx")
    except Exception as exc:
        pytest.fail(f"nginx container not found: {exc}")

    nginx.kill(signal="HUP")
    nginx.reload()
    assert nginx.status == "running"


def test_proxy_net_discovery_uses_compose_prefix():
    client = _docker_client()
    proxy_net = _proxy_net_name(client)
    assert proxy_net.endswith("proxy-net")

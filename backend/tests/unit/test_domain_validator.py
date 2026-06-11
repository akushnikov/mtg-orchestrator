import os
from unittest.mock import AsyncMock, patch

import pytest

from app.services.domain_validator import is_safe_external_domain, validate_domain_tls


def test_safe_domain_public():
    if not os.environ.get("NETWORK_TESTS"):
        pytest.skip("NETWORK_TESTS not set")

    assert is_safe_external_domain("httpbin.org") is True


def test_unsafe_domain_private_ip():
    assert is_safe_external_domain("10.0.0.1") is False


def test_unsafe_loopback():
    assert is_safe_external_domain("localhost") is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tls_valid_public_domain():
    if not os.environ.get("NETWORK_TESTS"):
        pytest.skip("NETWORK_TESTS not set")

    assert await validate_domain_tls("httpbin.org") is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tls_unreachable():
    if not os.environ.get("NETWORK_TESTS"):
        pytest.skip("NETWORK_TESTS not set")

    assert await validate_domain_tls("127.0.0.1", timeout=1.0) is False


@pytest.mark.asyncio
async def test_tls_safe_check_first():
    with (
        patch("socket.gethostbyname", return_value="10.0.0.1"),
        patch("httpx.AsyncClient", AsyncMock()) as async_client,
    ):
        assert await validate_domain_tls("internal.example") is False

    async_client.assert_not_called()


@pytest.mark.asyncio
async def test_ssrf_prevention():
    assert await validate_domain_tls("192.168.1.1") is False

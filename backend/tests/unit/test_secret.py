import pytest

try:
    from app.services.proxy_service import (
        build_tg_proxy_url,
        domain_to_slug,
        generate_mtg_secret,
    )
except ImportError:
    build_tg_proxy_url = None
    domain_to_slug = None
    generate_mtg_secret = None


def _require_proxy_service():
    if generate_mtg_secret is None:
        pytest.skip("proxy_service not yet implemented")


def test_secret_format_ria_ru():
    _require_proxy_service()

    secret = generate_mtg_secret("ria.ru")
    assert secret.startswith("ee")
    assert len(secret[2:34]) == 32
    int(secret[2:34], 16)
    assert bytes.fromhex(secret[34:]).decode("ascii") == "ria.ru"


def test_secret_length():
    _require_proxy_service()

    secret = generate_mtg_secret("ria.ru")
    assert len(secret) == 2 + 32 + len("ria.ru") * 2


def test_secret_uniqueness():
    _require_proxy_service()

    first = generate_mtg_secret("ria.ru")
    second = generate_mtg_secret("ria.ru")
    assert first != second, "generate_mtg_secret produced duplicate random secrets"


def test_tg_url_format():
    _require_proxy_service()

    assert (
        build_tg_proxy_url("1.2.3.4", "eeAABBCC")
        == "tg://proxy?server=1.2.3.4&port=443&secret=eeAABBCC"
    )


def test_domain_to_slug_dots():
    _require_proxy_service()

    assert domain_to_slug("ria.ru") == "ria_ru"


def test_domain_to_slug_subdomain():
    _require_proxy_service()

    assert domain_to_slug("api.max.ru") == "api_max_ru"


def test_domain_to_slug_max_length():
    _require_proxy_service()

    assert len(domain_to_slug(("a" * 100) + ".ru")) <= 63

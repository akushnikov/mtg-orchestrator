import pytest
from httpx import ASGITransport, AsyncClient

try:
    from app.main import app
except ImportError:
    app = None


def _require_app():
    if app is None:
        pytest.skip("app not importable yet")


@pytest.mark.asyncio
async def test_spa_deep_link_returns_index_html():
    """A hard refresh / deep link on a client route serves index.html, not 404."""
    _require_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/list", "/create", "/proxy/7"):
            resp = await client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
            assert "text/html" in resp.headers["content-type"], path


@pytest.mark.asyncio
async def test_unknown_api_path_still_returns_404():
    """API paths must NOT fall back to index.html — unknown endpoints stay 404."""
    _require_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_healthz_still_json():
    """The real /healthz endpoint must win over the SPA fallback."""
    _require_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

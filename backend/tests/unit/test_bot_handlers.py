from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

try:
    from app.bot import handlers as handlers_module
    from app.bot.handlers import bot, cmd_proxies, cmd_start, dp
    from app.main import app
except ImportError:
    handlers_module = None
    app = None
    bot = None
    dp = None
    cmd_proxies = None
    cmd_start = None


class FakeMessage:
    def __init__(self):
        self.answer = AsyncMock()


class FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _require_bot_handlers():
    if cmd_start is None:
        pytest.skip("bot handlers module not yet implemented")


@pytest.mark.asyncio
async def test_start_command(monkeypatch):
    _require_bot_handlers()
    monkeypatch.setattr(
        handlers_module,
        "settings",
        SimpleNamespace(panel_domain="panel.example.com"),
    )
    message = FakeMessage()

    await cmd_start(message)

    assert message.answer.await_count == 1
    _, kwargs = message.answer.await_args
    markup = kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://panel.example.com/"


@pytest.mark.asyncio
async def test_proxies_command_empty(monkeypatch):
    _require_bot_handlers()
    monkeypatch.setattr(handlers_module, "AsyncSessionLocal", lambda: FakeSessionContext())
    monkeypatch.setattr(
        handlers_module.crud,
        "list_instances",
        AsyncMock(return_value=[]),
    )
    message = FakeMessage()

    await cmd_proxies(message)

    message.answer.assert_awaited_once_with("No active proxies.")


@pytest.mark.asyncio
async def test_proxies_command_running(monkeypatch):
    _require_bot_handlers()
    monkeypatch.setattr(handlers_module, "AsyncSessionLocal", lambda: FakeSessionContext())
    monkeypatch.setattr(
        handlers_module.crud,
        "list_instances",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    domain="ria.ru",
                    status="running",
                    secret="eeAA",
                )
            ]
        ),
    )
    message = FakeMessage()

    await cmd_proxies(message)

    text = message.answer.await_args.args[0]
    assert "tg://proxy" in text
    assert "ria.ru" in text
    assert "🟢" in text


@pytest.mark.asyncio
async def test_webhook_wrong_secret_returns_403(monkeypatch):
    _require_bot_handlers()
    monkeypatch.setattr(handlers_module.settings, "webhook_secret", "expected-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/bot/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_correct_secret_dispatches(monkeypatch):
    _require_bot_handlers()
    dispatch = AsyncMock(return_value=None)
    monkeypatch.setattr(handlers_module.settings, "webhook_secret", "expected-secret")
    monkeypatch.setattr(handlers_module, "bot", object())
    monkeypatch.setattr(handlers_module.dp, "feed_webhook_update", dispatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/bot/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "expected-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    dispatch.assert_awaited_once()


def test_webhook_registration_path_matches_served_route():
    """Lifespan registers set_webhook at app.url_path_for('telegram_webhook').

    Guards the prod-dead-bot regression: if the served route path and the
    registered webhook path ever drift, Telegram would POST to a path the
    backend does not route and /start + /proxies would silently never fire.
    """
    _require_bot_handlers()
    # The path main.py uses to build the set_webhook URL must equal the path
    # the webhook handler is actually served at (under the /api/v1 prefix).
    assert app.url_path_for("telegram_webhook") == "/api/v1/bot/webhook"

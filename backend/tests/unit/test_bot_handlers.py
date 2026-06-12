from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

try:
    from app.bot import handlers as handlers_module
    from app.bot.handlers import bot, cmd_proxies, cmd_start, dp
except ImportError:
    handlers_module = None
    bot = None
    dp = None
    cmd_proxies = None
    cmd_start = None


class FakeMessage:
    def __init__(self):
        self.answer = AsyncMock()


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
    monkeypatch.setattr(
        handlers_module.crud,
        "list_instances",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    domain="ria.ru",
                    status="running",
                    tg_url="tg://proxy?server=1.2.3.4&port=443&secret=eeAA",
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

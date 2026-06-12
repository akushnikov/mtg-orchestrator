import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import (
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import settings
from app.db import crud
from app.db.engine import AsyncSessionLocal
from app.services.proxy_service import build_tg_proxy_url

logger = logging.getLogger("app.bot.handlers")

bot: Bot | None = None
dp = Dispatcher()
router = Router()
dp.include_router(router)


@dp.errors()
async def on_bot_error(event: ErrorEvent) -> bool:
    """Catch-all so a handler exception is logged instead of silently swallowed."""
    update_id = event.update.update_id if event.update else None
    logger.exception(
        "Error handling update id=%s: %s",
        update_id,
        event.exception,
        exc_info=event.exception,
    )
    return True


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("/start from user_id=%s", user_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Open MTG Proxies",
                    web_app=WebAppInfo(url=f"https://{settings.panel_domain}/"),
                )
            ]
        ]
    )
    await message.answer("Manage your MTProto proxies:", reply_markup=keyboard)


@router.message(Command("proxies"))
async def cmd_proxies(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    logger.info("/proxies from user_id=%s", user_id)
    async with AsyncSessionLocal() as session:
        rows = await crud.list_instances(session)

    running = [
        row
        for row in rows
        if (row.status.value if hasattr(row.status, "value") else str(row.status)) == "running"
    ]
    logger.info("/proxies -> %d running proxies for user_id=%s", len(running), user_id)
    if not running:
        await message.answer("No active proxies.")
        return

    lines = []
    for row in running:
        url = build_tg_proxy_url(settings.moscow_ip, row.secret)
        lines.append(f"\U0001f7e2 {row.domain} — <a href='{url}'>tap to connect</a>")

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

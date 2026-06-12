from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.config import settings
from app.db import crud
from app.db.engine import AsyncSessionLocal
from app.services.proxy_service import build_tg_proxy_url


bot: Bot | None = None
dp = Dispatcher()
router = Router()
dp.include_router(router)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
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
    async with AsyncSessionLocal() as session:
        rows = await crud.list_instances(session)

    running = [
        row
        for row in rows
        if (row.status.value if hasattr(row.status, "value") else str(row.status)) == "running"
    ]
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

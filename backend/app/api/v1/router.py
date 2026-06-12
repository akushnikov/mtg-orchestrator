import aiogram.types
from fastapi import APIRouter, HTTPException, Request, status

from app.api.v1 import instances
from app.bot import handlers as bot_handlers
from app.config import settings


api_router = APIRouter()
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])


@api_router.post("/bot/webhook", include_in_schema=False)
async def telegram_webhook(request: Request) -> dict[str, bool]:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or secret != settings.webhook_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    data = await request.json()
    update = aiogram.types.Update.model_validate(data, context={"bot": bot_handlers.bot})
    await bot_handlers.dp.feed_webhook_update(bot_handlers.bot, update)
    return {"ok": True}

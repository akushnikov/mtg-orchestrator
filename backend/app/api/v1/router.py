import logging

import aiogram.types
from fastapi import APIRouter, HTTPException, Request, status

from app.api.v1 import instances
from app.bot import handlers as bot_handlers
from app.config import settings

logger = logging.getLogger("app.bot.webhook")

api_router = APIRouter()
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])


@api_router.post("/bot/webhook", include_in_schema=False, name="telegram_webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or secret != settings.webhook_secret:
        logger.warning("Rejected webhook request: missing or invalid secret token")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    data = await request.json()
    update = aiogram.types.Update.model_validate(data, context={"bot": bot_handlers.bot})
    logger.info("Received Telegram update id=%s", update.update_id)
    try:
        await bot_handlers.dp.feed_webhook_update(bot_handlers.bot, update)
    except Exception:
        # Never return 500 to Telegram for a handler bug — it triggers endless
        # delivery retries. Log the failure and acknowledge the update instead.
        logger.exception("Unhandled error dispatching update id=%s", update.update_id)
        return {"ok": False}
    return {"ok": True}

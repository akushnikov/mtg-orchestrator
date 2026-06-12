"""
MTG Orchestrator — minimal FastAPI backend (Phase 1 scaffold).

Phase 1 scope:
  - Serve frontend placeholder (static/index.html) at GET /
  - Expose GET /healthz for Docker healthcheck
  - No Telegram bot, auth, SQLite, Docker SDK, or business APIs in this plan

Uvicorn is started with TLS in production (port 8443, SSL certs from Let's Encrypt
volume). In development (no certs), run on plain HTTP for local verification.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiogram
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from app.api.v1.router import api_router
from app.bot import handlers as bot_handlers
from app.config import settings
from app.db.engine import AsyncSessionLocal, engine
from app.db.models import Base
from app.services.docker_service import get_proxy_net_name
from app.services.proxy_service import reconcile_on_startup, write_startup_nginx_config

# Resolve paths relative to this file so the app works regardless of cwd.
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"

# Configure root logging once so application loggers (app.*, aiogram.*) actually
# emit under uvicorn. Without this the bot is effectively silent.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.main")


class SPAStaticFiles(StaticFiles):
    """Serve static assets, falling back to index.html for SPA client routes.

    The Vue Mini App uses history-mode routing (/list, /create, /proxy/:id).
    A hard refresh or deep link requests a path with no matching file; instead
    of returning 404 we serve index.html so the client-side router takes over.
    Paths under /api are excluded so unknown endpoints still return a real 404.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and not path.startswith("api"):
                return await super().get_response("index.html", scope)
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Initialize persistent registry and discover Docker network context."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.proxy_net_name = await get_proxy_net_name()
    async with AsyncSessionLocal() as session:
        await reconcile_on_startup(session, app.state.proxy_net_name)
        # Seed the shared nginx-config volume so the nginx container has a valid
        # config to load on its own first start (it depends on this backend
        # being healthy before it boots).
        await write_startup_nginx_config(session)
    if settings.bot_token:
        bot_handlers.bot = aiogram.Bot(token=settings.bot_token)
        # Derive the webhook path from the actual route so registration can
        # never drift from what FastAPI serves (the route lives under the
        # /api/v1 prefix as /api/v1/bot/webhook).
        webhook_path = app.url_path_for("telegram_webhook")
        # Telegram's servers are abroad and must reach the backend directly.
        # panel_domain points at the Moscow relay (so the RU owner can open the
        # Mini App); routing webhook delivery through that cascade times out.
        # webhook_domain lets us register a Frankfurt-direct host instead.
        webhook_host = settings.webhook_domain or settings.panel_domain
        webhook_url = f"https://{webhook_host}{webhook_path}"
        await bot_handlers.bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
            allowed_updates=["message"],
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook registered at %s", webhook_url)
    try:
        yield
    finally:
        if settings.bot_token and bot_handlers.bot is not None:
            await bot_handlers.bot.delete_webhook()
            await bot_handlers.bot.session.close()
        await engine.dispose()


app = FastAPI(
    title="MTG Orchestrator",
    description="Self-hosted MTProto proxy orchestrator management panel.",
    version="0.1.0",
    # Disable /docs and /redoc in production; enable during development as needed.
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Docker / compose healthcheck endpoint. Returns 200 with status ok."""
    return {"status": "ok"}


app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------

# Mount /assets (JS, CSS, images) from the static directory.
# html=True makes StaticFiles serve index.html for directory requests.
app.mount(
    "/",
    SPAStaticFiles(directory=str(STATIC_DIR), html=True),
    name="static",
)

"""
MTG Orchestrator — minimal FastAPI backend (Phase 1 scaffold).

Phase 1 scope:
  - Serve frontend placeholder (static/index.html) at GET /
  - Expose GET /healthz for Docker healthcheck
  - No Telegram bot, auth, SQLite, Docker SDK, or business APIs in this plan

Uvicorn is started with TLS in production (port 8443, SSL certs from Let's Encrypt
volume). In development (no certs), run on plain HTTP for local verification.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Resolve paths relative to this file so the app works regardless of cwd.
APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Application lifespan — reserved for future startup/shutdown hooks."""
    yield


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


# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------

# Mount /assets (JS, CSS, images) from the static directory.
# html=True makes StaticFiles serve index.html for directory requests.
app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static",
)

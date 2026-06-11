# =============================================================================
# MTG Orchestrator — backend image
# Python 3.12-slim; runs Uvicorn on internal port 8080 (plain HTTP for dev)
# or 8443 with TLS (prod — certs mounted from Let's Encrypt volume).
#
# Phase 1: minimal FastAPI app (static + healthz only).
# Later phases add aiogram, Docker SDK, SQLAlchemy, etc. — all deps are
# already listed in pyproject.toml so the image only needs to be rebuilt
# when source or deps change.
# =============================================================================

FROM python:3.12-slim AS base

# Prevent .pyc files, enable unbuffered stdout/stderr for live Docker logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /backend

# Install dependencies in a separate layer to leverage Docker build cache.
COPY pyproject.toml ./
# pip install in non-interactive mode; no cache to keep the image lean.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# Copy application source + entrypoint/healthcheck helpers.
COPY app/ ./app/
COPY entrypoint.sh healthcheck.py ./

# Internal ports: 8080 (plain HTTP fallback) and 8443 (TLS panel).
EXPOSE 8080 8443

# The entrypoint picks the listener: TLS on :8443 when the panel cert exists,
# else plain HTTP on :8080. (sh-invoked so no +x bit is needed on Windows.)
CMD ["sh", "entrypoint.sh"]

from __future__ import annotations

import os
import logging
import time
import sys
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request
from starlette.responses import Response
from fastapi.middleware.cors import CORSMiddleware

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_BACKEND_DIR)
for _path in (_BACKEND_DIR, _PROJECT_DIR, os.getcwd()):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from cache.redis_client import redis_cache
from config import get_settings
from db.database import create_db_and_tables
from routers.launch import router as launch_router
from routers.curriculum import router as curriculum_router
from routers.interactions import router as interactions_router
from routers.feedback import router as feedback_router
from routers.rank import router as rank_router
from routers.canvas import router as canvas_router

settings = get_settings()
logger = logging.getLogger("backend")
logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    # Touch Redis at startup to fail fast if cache infra is down.
    try:
        redis_cache.client.ping()
    except Exception:
        redis_cache.disable()
        logger.warning("Redis ping failed; continuing without cache")
    yield


app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)

# Ensure application logs are visible in the same terminal as uvicorn.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

cors_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
if not cors_origins:
    cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    if request.url.path == "/api/v1/curriculum/top":
        return Response(status_code=404)
    suppress_log = False
    if not suppress_log:
        logger.warning("%s %s started", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        if not suppress_log:
            logger.exception("%s %s failed", request.method, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    if not suppress_log:
        logger.warning(
            "%s %s completed status=%s in %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    if settings.security_headers_enabled:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        if request.url.path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "frame-ancestors 'none'; "
                "base-uri 'none'; "
                "form-action 'none'"
            )
        if settings.security_hsts_enabled:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.include_router(curriculum_router)
app.include_router(interactions_router)
app.include_router(feedback_router)
app.include_router(launch_router)
app.include_router(rank_router)
app.include_router(canvas_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/dependencies")
def health_dependencies() -> dict[str, dict[str, str]]:
    """Check health of external dependencies (Redis, NVIDIA NIM)."""
    from cache.redis_client import redis_cache

    status: dict[str, dict[str, str]] = {}
    try:
        redis_cache.client.ping()
        status["redis"] = {"status": "ok"}
    except Exception as exc:  # pragma: no cover - runtime dependency check
        status["redis"] = {"status": "error", "detail": str(exc)}
    return status

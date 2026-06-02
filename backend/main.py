from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request
from starlette.responses import Response
from fastapi.middleware.cors import CORSMiddleware

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.db.database import create_db_and_tables
from backend.routers.launch import router as launch_router
from backend.routers.curriculum import router as curriculum_router
from backend.routers.interactions import router as interactions_router
from backend.routers.feedback import router as feedback_router
# Import moat feature models to ensure they're registered in SQLModel metadata
from backend.models import moat_features as _  # noqa: F401


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
        logger.warning("%s %s completed status=%s in %.1fms", request.method, request.url.path, response.status_code, elapsed_ms)

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
from backend.routers.rank import router as rank_router
app.include_router(rank_router)
from backend.routers.canvas import router as canvas_router
app.include_router(canvas_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/dependencies")
def health_dependencies() -> dict[str, dict[str, str]]:
    """Check health of external dependencies (Redis, NVIDIA NIM)."""
    from backend.cache.redis_client import redis_cache
    from backend.services.llm_client import llm_service
    
    redis_status = "ok"
    redis_message = ""
    try:
        redis_cache.client.ping()
    except Exception as exc:
        redis_status = "down"
        redis_message = str(exc)
    
    nvidia_status = "ok"
    nvidia_message = ""
    try:
        # Small test to see if NVIDIA endpoint is reachable and responding.
        response = requests.get(
            f"{llm_service._base_url}/v1/models",
            headers={"Authorization": f"Bearer {llm_service._api_key}"},
            timeout=5,
        )
        if response.status_code != 200:
            nvidia_status = "down"
            nvidia_message = f"HTTP {response.status_code}"
    except requests.exceptions.Timeout:
        nvidia_status = "timeout"
        nvidia_message = "NVIDIA endpoint did not respond within 5s"
    except Exception as exc:
        nvidia_status = "down"
        nvidia_message = str(exc)
    
    return {
        "redis": {"status": redis_status, "message": redis_message},
        "nvidia_nim": {"status": nvidia_status, "message": nvidia_message},
    }

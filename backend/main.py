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

import importlib.util

def _load_module(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_BACKEND_DIR, rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

redis_cache = _load_module("cache.redis_client", "cache/redis_client.py")
get_settings = _load_module("config", "config.py").get_settings
create_db_and_tables = _load_module("db.database", "db/database.py").create_db_and_tables
launch_router = _load_module("routers.launch", "routers/launch.py").router
curriculum_router = _load_module("routers.curriculum", "routers/curriculum.py").router
interactions_router = _load_module("routers.interactions", "routers/interactions.py").router
feedback_router = _load_module("routers.feedback", "routers/feedback.py").router
moat_features = _load_module("models.moat_features", "models/moat_features.py")
rank_router = _load_module("routers.rank", "routers/rank.py").router
canvas_router = _load_module("routers.canvas", "routers/canvas.py").router


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
app.include_router(rank_router)
app.include_router(canvas_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health/dependencies")
def health_dependencies() -> dict[str, dict[str, str]]:
    """Check health of external dependencies (Redis, NVIDIA NIM)."""
    from cache.redis_client import redis_cache
    from services.llm_client import llm_service
    
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

"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.endpoints.routes import router
from app.core.config import get_settings
from app.core.database import create_tables

settings = get_settings()

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("devonboard")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    await create_tables()
    logger.info("Database tables ready")
    yield
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request ID + structured logging middleware ─────────────────────────────────
@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start = time.monotonic()
    response = await call_next(request)
    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        f"method={request.method} path={request.url.path} "
        f"status={response.status_code} duration_ms={elapsed:.1f} "
        f"request_id={request_id}"
    )
    response.headers["X-Request-ID"] = request_id
    return response


# ── Global exception handler ───────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exc_handler(request: Request, exc: Exception):
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.error(f"Unhandled exception request_id={request_id}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": str(exc), "request_id": request_id}},
    )


# ── Include routes ─────────────────────────────────────────────────────────────
app.include_router(router)

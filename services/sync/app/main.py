"""Croniixx Sync service.

Receives wearable data from Terra, normalizes it across manufacturers, stores
it in TimescaleDB, and assembles the unified circadian profile that the Engine
consumes.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as redis
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import Database
from app.engine.terra_client import TerraClient
from app.envelope import fail, ok
from app.routers import ingest

settings = get_settings()

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    database = Database(settings)
    await database.start()

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    terra = TerraClient(settings)
    await terra.start()

    app.state.db = database
    app.state.redis = redis_client
    app.state.terra = terra

    log.info(
        "sync.started",
        terra_configured=settings.terra_configured,
        database=settings.database_url.split("@")[-1],
    )

    try:
        yield
    finally:
        await terra.close()
        await redis_client.aclose()
        await database.close()
        log.info("sync.stopped")


app = FastAPI(
    title="Croniixx Sync",
    version="0.1.0",
    description="Terra ingestion and circadian profile assembly",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return fail(
        code=f"http_{exc.status_code}",
        message=str(exc.detail),
        service=settings.service_name,
        status_code=exc.status_code,
        request_id=getattr(request.state, "request_id", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return fail(
        code="validation_error",
        message="Request body failed validation",
        service=settings.service_name,
        status_code=422,
        request_id=getattr(request.state, "request_id", None),
        details={"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("sync.unhandled", error=str(exc))
    return fail(
        code="internal_error",
        message="Unexpected error in the sync service",
        service=settings.service_name,
        status_code=500,
        request_id=getattr(request.state, "request_id", None),
    )


app.include_router(ingest.router)


@app.get("/health")
async def health(request: Request):
    database: Database = request.app.state.db
    redis_client: redis.Redis = request.app.state.redis

    db_ok = await database.ping()
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001 - health check never raises
        redis_ok = False

    healthy = db_ok and redis_ok
    return ok(
        {
            "status": "ok" if healthy else "degraded",
            "database": db_ok,
            "redis": redis_ok,
            "terra_configured": settings.terra_configured,
        },
        service=settings.service_name,
        status_code=200 if healthy else 503,
    )

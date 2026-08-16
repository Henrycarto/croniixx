"""Croniixx reminder API.

Owns the reminder queue and the push delivery path. The Engine pushes adaptive
schedules in; this service works out which notifications those imply, holds
them until they are due, and delivers them to the patient's devices.
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
from app.devices import DeviceRegistry
from app.engine.dispatcher import Dispatcher
from app.engine.push_client import ExpoPushClient
from app.engine.queue_manager import QueueManager
from app.envelope import fail, ok
from app.routers import remind

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
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    queue = QueueManager(redis_client, visibility_seconds=settings.claim_visibility_seconds)
    devices = DeviceRegistry(redis_client)
    push = ExpoPushClient(settings)
    await push.start()

    dispatcher = Dispatcher(queue=queue, devices=devices, push=push, settings=settings)

    app.state.redis = redis_client
    app.state.queue = queue
    app.state.devices = devices
    app.state.push = push
    app.state.dispatcher = dispatcher

    dispatcher.start()
    log.info("reminder_api.started", interval=settings.dispatch_interval_seconds)

    try:
        yield
    finally:
        await dispatcher.stop()
        await push.close()
        await redis_client.aclose()
        log.info("reminder_api.stopped")


app = FastAPI(
    title="Croniixx Reminder API",
    version="0.1.0",
    description="Reminder queue and Expo push delivery",
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
    log.exception("reminder_api.unhandled", error=str(exc))
    return fail(
        code="internal_error",
        message="Unexpected error in the reminder service",
        service=settings.service_name,
        status_code=500,
        request_id=getattr(request.state, "request_id", None),
    )


app.include_router(remind.router)


@app.get("/health")
async def health(request: Request):
    redis_client: redis.Redis = request.app.state.redis
    dispatcher: Dispatcher = request.app.state.dispatcher

    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001 - health check never raises
        redis_ok = False

    healthy = redis_ok and dispatcher.running
    return ok(
        {
            "status": "ok" if healthy else "degraded",
            "redis": redis_ok,
            "dispatcher_running": dispatcher.running,
            "dispatcher_ticks": dispatcher.ticks,
        },
        service=settings.service_name,
        status_code=200 if healthy else 503,
    )

"""Croniixx Engine service.

Takes the unified circadian profile from Sync, estimates the patient's phase
position, and assembles adaptive schedule objects from chronopharmacological
timing windows.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.db import Database
from app.envelope import fail, ok
from app.routers import circadian, schedule

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

    http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

    app.state.db = database
    app.state.http = http

    log.info(
        "engine.started",
        phase_method=circadian.estimator.method_version,
        phase_source=circadian.estimator.coefficient_source.value,
        catalog=schedule.catalog.catalog_version,
        catalog_source=schedule.catalog.coefficient_source.value,
    )

    try:
        yield
    finally:
        await http.aclose()
        await database.close()
        log.info("engine.stopped")


app = FastAPI(
    title="Croniixx Engine",
    version="0.1.0",
    description="Circadian phase estimation and adaptive schedule assembly",
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
    log.exception("engine.unhandled", error=str(exc))
    return fail(
        code="internal_error",
        message="Unexpected error in the engine service",
        service=settings.service_name,
        status_code=500,
        request_id=getattr(request.state, "request_id", None),
    )


app.include_router(circadian.router)
app.include_router(schedule.router)


@app.get("/health")
async def health(request: Request):
    database: Database = request.app.state.db
    db_ok = await database.ping()

    return ok(
        {
            "status": "ok" if db_ok else "degraded",
            "database": db_ok,
            "phase_method": circadian.estimator.method_version,
            "phase_source": circadian.estimator.coefficient_source.value,
            "catalog_version": schedule.catalog.catalog_version,
            "catalog_source": schedule.catalog.coefficient_source.value,
        },
        service=settings.service_name,
        status_code=200 if db_ok else 503,
    )

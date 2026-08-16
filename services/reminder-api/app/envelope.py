"""Response envelope shared by every Croniixx HTTP surface.

Every response carries the same three keys so the mobile client can parse a
success and a failure with one code path. That matters offline: the app queues
responses it could not process and replays them later without branching on
shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class Meta(BaseModel):
    service: str
    request_id: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = Field(default_factory=dict)


class Envelope(BaseModel, Generic[T]):
    data: T | None = None
    error: ErrorBody | None = None
    meta: Meta


def ok(
    data: Any,
    *,
    service: str,
    request_id: str | None = None,
    status_code: int = 200,
    **extra: Any,
) -> JSONResponse:
    body = Envelope[Any](
        data=data,
        error=None,
        meta=Meta(service=service, request_id=request_id, extra=extra),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def fail(
    code: str,
    message: str,
    *,
    service: str,
    status_code: int = 400,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = Envelope[Any](
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=Meta(service=service, request_id=request_id),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))

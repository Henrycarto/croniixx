"""JWT authentication for the mobile client.

The mobile app authenticates with a bearer token scoped to one patient. Every
patient scoped route checks that the token's subject matches the patient id in
the path, so a valid token for patient A cannot read patient B's dose schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)


class MobilePrincipal:
    """The authenticated patient behind a request."""

    def __init__(self, patient_id: str, claims: dict[str, Any]) -> None:
        self.patient_id = patient_id
        self.claims = claims


def issue_access_token(patient_id: str, *, ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": patient_id,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "scope": "doses:read doses:ack",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )


async def current_patient(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> MobilePrincipal:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )

    try:
        claims = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is not valid"
        ) from exc

    subject = claims.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no subject"
        )

    return MobilePrincipal(patient_id=str(subject), claims=claims)


def require_patient(principal: MobilePrincipal, patient_id: str) -> None:
    """Reject a token that belongs to a different patient."""
    if principal.patient_id != patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not grant access to this patient",
        )


async def service_caller(request: Request) -> None:
    """Guard for service to service routes.

    The Engine pushes schedules into the queue over the compose network. In
    production this sits behind a service mesh and the shared secret is a
    second line rather than the only one.
    """
    provided = request.headers.get("x-service-token")
    if not provided or provided != settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Service token required"
        )

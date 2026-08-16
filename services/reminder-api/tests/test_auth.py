"""Mobile auth tests.

A token that reads another patient's dose schedule is the worst failure this
service can have, so patient scoping gets an explicit test rather than being
assumed from the dependency wiring.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import (
    MobilePrincipal,
    current_patient,
    decode_token,
    issue_access_token,
    require_patient,
)
from app.config import get_settings

settings = get_settings()


def credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_a_valid_token_resolves_to_its_patient():
    principal = await current_patient(credentials(issue_access_token("patient-1")))
    assert principal.patient_id == "patient-1"


async def test_a_missing_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        await current_patient(None)
    assert exc.value.status_code == 401


async def test_a_token_signed_with_another_secret_is_rejected():
    forged = jwt.encode(
        {
            "sub": "patient-1",
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "exp": int(time.time()) + 600,
        },
        "not-the-secret",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        await current_patient(credentials(forged))
    assert exc.value.status_code == 401


async def test_an_expired_token_is_rejected():
    expired = issue_access_token("patient-1", ttl_seconds=-10)
    with pytest.raises(HTTPException) as exc:
        await current_patient(credentials(expired))
    assert exc.value.status_code == 401


async def test_a_token_for_the_wrong_audience_is_rejected():
    wrong = jwt.encode(
        {
            "sub": "patient-1",
            "iss": settings.jwt_issuer,
            "aud": "some-other-app",
            "exp": int(time.time()) + 600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        await current_patient(credentials(wrong))
    assert exc.value.status_code == 401


def test_one_patients_token_cannot_reach_another_patient():
    principal = MobilePrincipal("patient-1", {})
    require_patient(principal, "patient-1")

    with pytest.raises(HTTPException) as exc:
        require_patient(principal, "patient-2")
    assert exc.value.status_code == 403


def test_issued_tokens_carry_the_expected_claims():
    claims = decode_token(issue_access_token("patient-9"))
    assert claims["sub"] == "patient-9"
    assert claims["iss"] == settings.jwt_issuer
    assert "doses:ack" in claims["scope"]

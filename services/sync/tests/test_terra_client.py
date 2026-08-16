"""Webhook signature verification.

A patient's biometric stream is a write path into a system that decides when
they take medication. Signature verification is the only thing standing in
front of it, so the failure cases get explicit tests.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.engine.terra_client import TerraSignatureError, verify_webhook_signature

SECRET = "test-signing-secret"
BODY = b'{"type":"sleep","user":{"user_id":"u1","provider":"OURA"},"data":[]}'


def sign(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    payload = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_valid_signature_passes():
    now = int(time.time())
    verify_webhook_signature(BODY, sign(BODY, now), SECRET, now=now)


def test_body_tampering_is_rejected():
    now = int(time.time())
    header = sign(BODY, now)
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY + b" ", header, SECRET, now=now)


def test_wrong_secret_is_rejected():
    now = int(time.time())
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY, sign(BODY, now, "other-secret"), SECRET, now=now)


def test_replayed_old_signature_is_rejected():
    old = int(time.time()) - 3600
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY, sign(BODY, old), SECRET, tolerance_seconds=300, now=time.time())


def test_missing_header_is_rejected():
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY, None, SECRET)


def test_malformed_header_is_rejected():
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY, "nonsense", SECRET)


def test_missing_secret_is_rejected_rather_than_skipped():
    now = int(time.time())
    with pytest.raises(TerraSignatureError):
        verify_webhook_signature(BODY, sign(BODY, now), "", now=now)

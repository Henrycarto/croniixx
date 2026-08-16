"""Terra API transport: webhook verification, backfill requests, auth links.

Terra sits between Croniixx and four device manufacturers. It handles OAuth
with each vendor and pushes normalized payloads to our webhook. What it does
not do is reconcile the semantic differences between vendors, which is the
job of device_normalizer.

Reference: https://docs.tryterra.co
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import date, datetime, timezone
from typing import Any

import httpx
import structlog

from app.config import Settings, get_settings

log = structlog.get_logger(__name__)


class TerraError(RuntimeError):
    """Raised when Terra returns a response we cannot act on."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TerraSignatureError(TerraError):
    """Raised when a webhook signature fails verification."""


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    signing_secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> None:
    """Verify the terra-signature header.

    Terra sends `t=<unix_seconds>,v1=<hex_hmac_sha256>` where the HMAC is taken
    over `<t>.<raw_body>` with the signing secret. The raw body must be the
    exact bytes received; re-serializing parsed JSON changes key order and
    whitespace and breaks the comparison.

    Raises TerraSignatureError on any failure.
    """
    if not signing_secret:
        raise TerraSignatureError("No signing secret configured")
    if not signature_header:
        raise TerraSignatureError("Missing terra-signature header")

    timestamp: str | None = None
    provided: str | None = None
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            provided = value

    if not timestamp or not provided:
        raise TerraSignatureError("Malformed terra-signature header")

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise TerraSignatureError("Signature timestamp is not an integer") from exc

    current = now if now is not None else time.time()
    if abs(current - sent_at) > tolerance_seconds:
        raise TerraSignatureError("Signature timestamp outside tolerance window")

    payload = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(signing_secret.encode(), payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, provided):
        raise TerraSignatureError("Signature mismatch")


class TerraClient:
    """Async client for the Terra REST surface.

    One client is created at application start and shared. Terra rate limits per
    dev id, so a per request client would waste connections and make the limit
    harder to respect.
    """

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> "TerraClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.terra_base_url,
                timeout=httpx.Timeout(20.0, connect=5.0),
                headers=self._auth_headers(),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            self._owns_client = True

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "dev-id": self._settings.terra_dev_id,
            "x-api-key": self._settings.terra_api_key,
            "Content-Type": "application/json",
        }

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise TerraError("TerraClient used before start()")
        return self._client

    # -- request helper ----------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        attempts: int = 3,
    ) -> dict[str, Any]:
        """Issue a request with bounded retries.

        Terra returns 429 with a Retry-After on rate limit and 5xx during
        provider outages. Both are transient for our purposes; a 4xx that is
        not 429 means our request is wrong and retrying will not fix it.
        """
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self.client.request(method, path, params=params, json=json_body)
            except httpx.RequestError as exc:
                last_error = exc
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if response.status_code == 429:
                retry_after = float(response.headers.get("retry-after", 2**attempt))
                log.warning("terra.rate_limited", path=path, retry_after=retry_after)
                await asyncio.sleep(min(retry_after, 30))
                last_error = TerraError("Rate limited", status_code=429)
                continue

            if response.status_code >= 500:
                last_error = TerraError(
                    f"Terra server error on {path}", status_code=response.status_code
                )
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if response.status_code >= 400:
                raise TerraError(
                    f"Terra rejected {method} {path}: {response.text[:400]}",
                    status_code=response.status_code,
                )

            try:
                return response.json()
            except ValueError as exc:
                raise TerraError(f"Terra returned non JSON body for {path}") from exc

        raise TerraError(f"Terra request failed after {attempts} attempts: {last_error}")

    # -- authentication ----------------------------------------------------

    async def generate_widget_session(
        self,
        reference_id: str,
        *,
        providers: list[str] | None = None,
        auth_success_redirect_url: str | None = None,
        auth_failure_redirect_url: str | None = None,
        language: str = "en",
    ) -> dict[str, Any]:
        """Create a hosted connection session for a patient.

        reference_id is our patient uuid. Terra echoes it on every subsequent
        webhook, which is how a payload finds its patient without us storing a
        mapping before the device is connected.
        """
        body: dict[str, Any] = {
            "reference_id": reference_id,
            "language": language,
        }
        if providers:
            body["providers"] = ",".join(providers)
        if auth_success_redirect_url:
            body["auth_success_redirect_url"] = auth_success_redirect_url
        if auth_failure_redirect_url:
            body["auth_failure_redirect_url"] = auth_failure_redirect_url

        return await self._request("POST", "/auth/generateWidgetSession", json_body=body)

    async def deauthenticate_user(self, terra_user_id: str) -> dict[str, Any]:
        return await self._request("DELETE", "/auth/deauthenticateUser", params={"user_id": terra_user_id})

    async def get_user(self, terra_user_id: str) -> dict[str, Any]:
        return await self._request("GET", "/userInfo", params={"user_id": terra_user_id})

    async def list_users(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/subscriptions")
        users = payload.get("users")
        return users if isinstance(users, list) else []

    # -- historical pulls --------------------------------------------------

    async def request_backfill(
        self,
        terra_user_id: str,
        resource: str,
        start: date | datetime,
        end: date | datetime | None = None,
        *,
        to_webhook: bool = True,
    ) -> dict[str, Any]:
        """Ask Terra to resend a historical window.

        A new patient needs fourteen days of history before the phase estimate
        means anything, and Terra only pushes forward from the connection
        moment. This call closes that gap.

        With to_webhook true the data arrives through the same webhook path as
        live data, so there is one ingestion code path rather than two.
        """
        params: dict[str, Any] = {
            "user_id": terra_user_id,
            "start_date": _as_iso_date(start),
            "to_webhook": str(to_webhook).lower(),
            "with_samples": "true",
        }
        if end is not None:
            params["end_date"] = _as_iso_date(end)

        return await self._request("GET", f"/{resource}", params=params)

    async def backfill_circadian_window(
        self,
        terra_user_id: str,
        start: date | datetime,
        end: date | datetime | None = None,
    ) -> dict[str, list[str]]:
        """Pull every resource the circadian profile needs, concurrently.

        Sleep drives midsleep and REM latency, daily carries HRV and resting
        heart rate, activity carries the movement rhythm. A profile missing any
        one of the three loses a term in the phase estimate.
        """
        resources = ["sleep", "daily", "activity"]
        results = await asyncio.gather(
            *(self.request_backfill(terra_user_id, r, start, end) for r in resources),
            return_exceptions=True,
        )

        requested: list[str] = []
        failed: list[str] = []
        for resource, result in zip(resources, results):
            if isinstance(result, Exception):
                log.warning("terra.backfill_failed", resource=resource, error=str(result))
                failed.append(resource)
            else:
                requested.append(resource)

        return {"requested": requested, "failed": failed}


def _as_iso_date(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    return value.isoformat()

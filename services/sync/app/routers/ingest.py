"""Ingestion endpoints: Terra webhook, device connection, profile assembly."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import Database
from app.engine.device_normalizer import DeviceNormalizer, NormalizationError, parse_timestamp
from app.engine.profile_builder import ProfileBuilder
from app.engine.terra_client import TerraClient, TerraError, TerraSignatureError, verify_webhook_signature
from app.envelope import fail, ok
from app.schemas import (
    IngestAck,
    Metric,
    NormalizedSample,
    NormalizedSleepSegment,
    NormalizedSleepSession,
    Provider,
    SleepStage,
    TerraWebhookPayload,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])
settings = get_settings()
normalizer = DeviceNormalizer()

FEED_KEY = "croniixx:sync:feed:{patient_id}"
FEED_MAX_ENTRIES = 200


class ConnectRequest(BaseModel):
    patient_id: str
    providers: list[str] = Field(default_factory=lambda: ["OURA", "APPLE", "GARMIN", "WHOOP"])
    success_redirect_url: str | None = None
    failure_redirect_url: str | None = None


class BackfillRequest(BaseModel):
    terra_user_id: str
    days: int = Field(default=14, ge=1, le=90)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@router.post("/terra/webhook")
async def terra_webhook(
    request: Request,
    terra_signature: str | None = Header(default=None, alias="terra-signature"),
):
    """Terra webhook receiver.

    Terra retries any response that is not 2xx, with backoff, for several
    hours. That means a payload we cannot normalize must still be acknowledged,
    or one malformed block from a firmware update will keep replaying and crowd
    out live data. Errors are recorded and acknowledged, not returned.
    """
    database: Database = request.app.state.db
    redis_client: redis.Redis = request.app.state.redis
    request_id = getattr(request.state, "request_id", None)

    raw_body = await request.body()

    try:
        verify_webhook_signature(
            raw_body,
            terra_signature,
            settings.terra_signing_secret,
            tolerance_seconds=settings.signature_tolerance_seconds,
        )
    except TerraSignatureError as exc:
        log.warning("terra.signature_rejected", reason=str(exc))
        # A bad signature is the one case that must not be acknowledged as
        # accepted, because accepting it would let anyone write biometric data.
        return fail(
            "invalid_signature",
            "Webhook signature verification failed",
            service=settings.service_name,
            status_code=401,
            request_id=request_id,
        )

    try:
        body = json.loads(raw_body)
    except ValueError:
        return fail(
            "malformed_payload",
            "Webhook body is not valid JSON",
            service=settings.service_name,
            status_code=400,
            request_id=request_id,
        )

    payload = TerraWebhookPayload.model_validate(body)

    # Terra replays on timeout, so the same payload can arrive twice. Hashing
    # the body gives a stable id without depending on Terra supplying one.
    fingerprint = hashlib.sha256(raw_body).hexdigest()
    dedupe_key = f"croniixx:sync:seen:{fingerprint}"
    first_time = await redis_client.set(
        dedupe_key, "1", ex=settings.webhook_replay_window_seconds, nx=True
    )
    if not first_time:
        return ok(
            IngestAck(accepted=True, payload_type=payload.type, warnings=["duplicate_replay"]).model_dump(),
            service=settings.service_name,
            request_id=request_id,
            deduplicated=True,
        )

    handler_result = await _handle_payload(payload, database, redis_client)
    return ok(
        handler_result.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
    )


async def _handle_payload(
    payload: TerraWebhookPayload, database: Database, redis_client: redis.Redis
) -> IngestAck:
    payload_type = payload.type.lower()
    terra_user_id = payload.user.user_id if payload.user else None

    if payload_type in {"auth", "user_reauth"}:
        return await _handle_auth(payload, database)

    if payload_type == "deauth":
        if terra_user_id:
            await database.deactivate_link(terra_user_id)
        return IngestAck(accepted=True, payload_type=payload.type, terra_user_id=terra_user_id)

    if payload_type in {"connection_error", "request_processing", "large_request_sending"}:
        log.info("terra.control_event", type=payload.type, message=payload.message)
        return IngestAck(accepted=True, payload_type=payload.type, terra_user_id=terra_user_id)

    if not terra_user_id:
        return IngestAck(
            accepted=False, payload_type=payload.type, warnings=["payload has no terra user id"]
        )

    patient_id = await database.resolve_patient(terra_user_id)
    if patient_id is None:
        # Terra can deliver data before our auth webhook lands. Acknowledging
        # keeps the retry queue clear; the backfill after auth recovers it.
        log.warning("terra.unlinked_user", terra_user_id=terra_user_id)
        return IngestAck(
            accepted=True,
            payload_type=payload.type,
            terra_user_id=terra_user_id,
            warnings=["no patient linked to this terra user id"],
        )

    try:
        batch = normalizer.normalize(payload)
    except NormalizationError as exc:
        log.warning("normalizer.rejected", error=str(exc), terra_user_id=terra_user_id)
        return IngestAck(
            accepted=True,
            payload_type=payload.type,
            terra_user_id=terra_user_id,
            warnings=[str(exc)],
        )

    if batch.is_empty:
        return IngestAck(
            accepted=True,
            payload_type=payload.type,
            provider=batch.provider,
            terra_user_id=terra_user_id,
            warnings=batch.warnings + ["payload contained no usable metrics"],
        )

    stored_samples = await database.store_samples(patient_id, batch.samples)
    stored_sessions = await database.store_sleep_sessions(patient_id, batch.sleep_sessions)
    await database.touch_link(terra_user_id, datetime.now(timezone.utc))

    await _push_feed_event(
        redis_client,
        patient_id,
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "provider": batch.provider.value,
            "payload_type": payload.type,
            "samples": stored_samples,
            "sleep_sessions": stored_sessions,
            "warnings": batch.warnings,
        },
    )

    log.info(
        "sync.ingested",
        provider=batch.provider.value,
        patient_id=patient_id,
        samples=stored_samples,
        sleep_sessions=stored_sessions,
    )

    return IngestAck(
        accepted=True,
        payload_type=payload.type,
        provider=batch.provider,
        terra_user_id=terra_user_id,
        samples_stored=stored_samples,
        sleep_sessions_stored=stored_sessions,
        warnings=batch.warnings,
    )


async def _handle_auth(payload: TerraWebhookPayload, database: Database) -> IngestAck:
    user = payload.user
    if not user or not user.user_id:
        return IngestAck(accepted=False, payload_type=payload.type, warnings=["auth event without user"])

    provider = Provider.parse(user.provider)
    if provider is None:
        return IngestAck(
            accepted=True,
            payload_type=payload.type,
            terra_user_id=user.user_id,
            warnings=[f"unsupported provider {user.provider}"],
        )

    if not user.reference_id:
        return IngestAck(
            accepted=True,
            payload_type=payload.type,
            provider=provider,
            terra_user_id=user.user_id,
            warnings=["auth event carried no reference_id, cannot link to a patient"],
        )

    await database.register_link(
        patient_id=user.reference_id,
        terra_user_id=user.user_id,
        provider=provider,
        scopes=(user.scopes or "").split(",") if user.scopes else [],
    )

    log.info("sync.device_linked", provider=provider.value, patient_id=user.reference_id)
    return IngestAck(
        accepted=True,
        payload_type=payload.type,
        provider=provider,
        terra_user_id=user.user_id,
    )


async def _push_feed_event(redis_client: redis.Redis, patient_id: str, event: dict) -> None:
    """Keep a short ingestion trail for the dashboard feed.

    Redis rather than Postgres because this is display state with no clinical
    value; losing it on a cache flush costs nothing.
    """
    key = FEED_KEY.format(patient_id=patient_id)
    pipeline = redis_client.pipeline()
    pipeline.lpush(key, json.dumps(event))
    pipeline.ltrim(key, 0, FEED_MAX_ENTRIES - 1)
    pipeline.expire(key, 86400 * 7)
    await pipeline.execute()


# ---------------------------------------------------------------------------
# Device connection
# ---------------------------------------------------------------------------


@router.post("/connect")
async def connect_device(request: Request, body: ConnectRequest):
    """Start a Terra hosted connection session for a patient."""
    terra: TerraClient = request.app.state.terra
    request_id = getattr(request.state, "request_id", None)

    if not settings.terra_configured:
        return fail(
            "terra_not_configured",
            "TERRA_DEV_ID and TERRA_API_KEY are not set in this environment",
            service=settings.service_name,
            status_code=503,
            request_id=request_id,
        )

    try:
        session = await terra.generate_widget_session(
            body.patient_id,
            providers=body.providers,
            auth_success_redirect_url=body.success_redirect_url,
            auth_failure_redirect_url=body.failure_redirect_url,
        )
    except TerraError as exc:
        return fail(
            "terra_error",
            str(exc),
            service=settings.service_name,
            status_code=502,
            request_id=request_id,
        )

    return ok(
        {"widget_url": session.get("url"), "session_id": session.get("session_id"), "expires_in": session.get("expires_in")},
        service=settings.service_name,
        request_id=request_id,
    )


@router.delete("/connect/{terra_user_id}")
async def disconnect_device(request: Request, terra_user_id: str):
    terra: TerraClient = request.app.state.terra
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    try:
        await terra.deauthenticate_user(terra_user_id)
    except TerraError as exc:
        log.warning("terra.deauth_failed", error=str(exc), terra_user_id=terra_user_id)

    # The local link is deactivated regardless. If Terra still believes the
    # device is connected, its payloads land on an inactive link and are
    # dropped, which is the safe direction for this failure.
    await database.deactivate_link(terra_user_id)
    return ok({"terra_user_id": terra_user_id, "active": False}, service=settings.service_name, request_id=request_id)


@router.post("/backfill")
async def backfill(request: Request, body: BackfillRequest):
    """Pull historical data so a new patient has a usable profile immediately."""
    terra: TerraClient = request.app.state.terra
    request_id = getattr(request.state, "request_id", None)

    if not settings.terra_configured:
        return fail(
            "terra_not_configured",
            "TERRA_DEV_ID and TERRA_API_KEY are not set in this environment",
            service=settings.service_name,
            status_code=503,
            request_id=request_id,
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=body.days)
    result = await terra.backfill_circadian_window(body.terra_user_id, start, end)

    return ok(
        {"terra_user_id": body.terra_user_id, "window_days": body.days, **result},
        service=settings.service_name,
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# Read surfaces
# ---------------------------------------------------------------------------


@router.get("/status/{patient_id}")
async def wearable_status(request: Request, patient_id: str):
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    statuses = await database.link_statuses(patient_id, datetime.now(timezone.utc))
    return ok(
        [s.model_dump(mode="json") for s in statuses],
        service=settings.service_name,
        request_id=request_id,
        device_count=len(statuses),
    )


@router.get("/feed/{patient_id}")
async def ingestion_feed(request: Request, patient_id: str, limit: int = Query(default=50, ge=1, le=200)):
    redis_client: redis.Redis = request.app.state.redis
    request_id = getattr(request.state, "request_id", None)

    entries = await redis_client.lrange(FEED_KEY.format(patient_id=patient_id), 0, limit - 1)
    events = []
    for entry in entries:
        try:
            events.append(json.loads(entry))
        except ValueError:
            continue

    return ok(events, service=settings.service_name, request_id=request_id, count=len(events))


@router.get("/profile/{patient_id}")
async def circadian_profile(
    request: Request,
    patient_id: str,
    days: int = Query(default=0, ge=0, le=90),
    persist: bool = Query(default=False),
):
    """Assemble the unified circadian profile from stored samples.

    Built on read rather than on a schedule. The window a clinician wants
    varies with the question they are asking, and a fourteen day profile
    cached overnight would be wrong the moment a patient's device backfilled.
    """
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    window_days = days or settings.profile_window_days
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=window_days)

    timezone_name = await database.patient_timezone(patient_id)
    sample_rows = await database.load_samples(patient_id, start, end)
    segment_rows = await database.load_sleep_segments(patient_id, start, end)

    samples = _rows_to_samples(sample_rows)
    sessions = _rows_to_sessions(segment_rows)

    builder = ProfileBuilder(timezone_name)
    profile = builder.build(patient_id, samples, sessions, start, end)

    if persist:
        await database.store_profile(
            patient_id,
            profile.model_dump(mode="json"),
            window_start=start,
            window_end=end,
            completeness=profile.data_completeness,
        )

    return ok(
        profile.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        window_days=window_days,
        timezone=timezone_name,
    )


def _rows_to_samples(rows: list[dict]) -> list[NormalizedSample]:
    samples: list[NormalizedSample] = []
    for row in rows:
        provider = Provider.parse(row["source_provider"])
        if provider is None:
            continue
        try:
            metric = Metric(row["metric"])
        except ValueError:
            continue
        samples.append(
            NormalizedSample(
                timestamp=row["time"],
                metric=metric,
                value=float(row["value"]),
                unit=row["unit"],
                provider=provider,
                terra_user_id=row.get("terra_user_id"),
                confidence=float(row.get("confidence", 1.0)),
            )
        )
    return samples


def _rows_to_sessions(rows: list[dict]) -> list[NormalizedSleepSession]:
    """Regroup stored segments into sessions.

    Segments are stored flat because that is what the hypertable wants. A gap
    of more than ninety minutes between the end of one segment and the start of
    the next separates two sleep periods rather than one interrupted night;
    below that a bathroom trip would split a night in two and halve the
    apparent sleep duration.
    """
    if not rows:
        return []

    gap_threshold = timedelta(minutes=90)
    sessions: list[NormalizedSleepSession] = []
    current: list[NormalizedSleepSegment] = []
    previous_end: datetime | None = None

    for row in rows:
        provider = Provider.parse(row["source_provider"])
        if provider is None:
            continue
        try:
            stage = SleepStage(row["stage"])
        except ValueError:
            stage = SleepStage.UNMEASURABLE

        start = row["time"]
        end = row["end_time"]
        segment = NormalizedSleepSegment(
            start=start,
            end=end,
            stage=stage,
            duration_s=int(row["duration_s"]),
            provider=provider,
            terra_user_id=row.get("terra_user_id"),
        )

        if previous_end is not None and start - previous_end > gap_threshold:
            sessions.append(_session_from_segments(current))
            current = []

        current.append(segment)
        previous_end = end

    if current:
        sessions.append(_session_from_segments(current))

    return [s for s in sessions if s is not None]


def _session_from_segments(segments: list[NormalizedSleepSegment]) -> NormalizedSleepSession:
    start = min(s.start for s in segments)
    end = max(s.end for s in segments)
    duration_h = (end - start).total_seconds() / 3600.0
    return NormalizedSleepSession(
        start=start,
        end=end,
        provider=segments[0].provider,
        terra_user_id=segments[0].terra_user_id,
        segments=segments,
        # Anything under three hours is treated as a nap. Including naps in
        # midsleep would drag the estimate toward the middle of the day.
        is_nap=duration_h < 3.0,
    )


__all__ = ["router", "parse_timestamp"]

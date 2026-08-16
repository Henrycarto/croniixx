"""Circadian phase endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import structlog
from fastapi import APIRouter, Query, Request

from app.config import get_settings
from app.db import Database
from app.engine.phase_calculator import compute_drift, is_stale, load_estimator
from app.envelope import fail, ok
from app.schemas import (
    CircadianProfileInput,
    CoefficientSource,
    PhaseEstimate,
    PhaseRequest,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/circadian", tags=["circadian"])
settings = get_settings()
estimator = load_estimator()


@router.post("/phase")
async def compute_phase(request: Request, body: PhaseRequest):
    """Compute a phase estimate from a supplied profile.

    Stateless. The dashboard uses this to preview what a profile implies before
    committing a schedule, and the test suite uses it without a database.
    """
    request_id = getattr(request.state, "request_id", None)
    estimate = estimator.estimate(body.profile, patient_timezone=body.timezone)

    return ok(
        estimate.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        coefficient_source=estimate.coefficient_source.value,
    )


@router.get("/phase/{patient_id}")
async def patient_phase(
    request: Request,
    patient_id: str,
    days: int = Query(default=14, ge=1, le=90),
    persist: bool = Query(default=True),
):
    """Pull the patient's profile from Sync and estimate their phase."""
    database: Database = request.app.state.db
    http: httpx.AsyncClient = request.app.state.http
    request_id = getattr(request.state, "request_id", None)

    profile = await _fetch_profile(http, patient_id, days)
    if profile is None:
        return fail(
            "profile_unavailable",
            "The sync service did not return a circadian profile for this patient",
            service=settings.service_name,
            status_code=502,
            request_id=request_id,
        )

    patient_timezone = await database.patient_timezone(patient_id)
    estimate = estimator.estimate(profile, patient_timezone=patient_timezone)

    if persist:
        await database.store_phase_estimate(estimate)

    return ok(
        estimate.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        timezone=patient_timezone,
        profile_completeness=profile.data_completeness,
        coefficient_source=estimate.coefficient_source.value,
    )


@router.get("/drift/{patient_id}")
async def phase_drift(
    request: Request,
    patient_id: str,
    baseline_days: int = Query(default=30, ge=2, le=180),
):
    """Compare the newest phase estimate against the oldest in the window.

    Drift is what triggers the amber alert on the dashboard. A patient whose
    phase has moved an hour since their regimen was written is being dosed
    against a clock their body no longer keeps.
    """
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    since = datetime.now(timezone.utc) - timedelta(days=baseline_days)
    history = await database.phase_history(patient_id, since)

    if len(history) < 2:
        return fail(
            "insufficient_history",
            "At least two phase estimates are needed to report drift",
            service=settings.service_name,
            status_code=409,
            request_id=request_id,
            details={"estimates_found": len(history)},
        )

    baseline = _row_to_estimate(patient_id, history[0])
    current = _row_to_estimate(patient_id, history[-1])
    drift = compute_drift(baseline, current, baseline_days)

    return ok(
        drift.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        estimates_considered=len(history),
    )


@router.get("/history/{patient_id}")
async def phase_history(
    request: Request,
    patient_id: str,
    days: int = Query(default=30, ge=1, le=365),
):
    """Phase offset over time, for the dashboard trend line."""
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    history = await database.phase_history(patient_id, since)

    points = [
        {
            "at": row["time"].isoformat(),
            "phase_offset_min": int(row["phase_offset_min"]),
            "confidence": float(row["confidence"]),
            "method_version": row["method_version"],
        }
        for row in history
    ]

    return ok(points, service=settings.service_name, request_id=request_id, count=len(points))


@router.get("/method")
async def method_info(request: Request):
    """Report which estimator is running.

    Every clinical surface needs to be able to answer "is this the validated
    model or the reference one" without reading a log line.
    """
    request_id = getattr(request.state, "request_id", None)
    return ok(
        {
            "method_version": estimator.method_version,
            "coefficient_source": estimator.coefficient_source.value,
            "clinically_validated": estimator.coefficient_source
            is CoefficientSource.PRIVATE_VALIDATED,
            "phase_estimate_ttl_hours": settings.phase_estimate_ttl_hours,
        },
        service=settings.service_name,
        request_id=request_id,
    )


async def _fetch_profile(
    http: httpx.AsyncClient, patient_id: str, days: int
) -> CircadianProfileInput | None:
    try:
        response = await http.get(
            f"{settings.sync_service_url}/ingest/profile/{patient_id}",
            params={"days": days},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("engine.profile_fetch_failed", patient_id=patient_id, error=str(exc))
        return None

    body = response.json()
    data = body.get("data")
    if not data:
        return None

    return CircadianProfileInput.model_validate(data)


def _row_to_estimate(patient_id: str, row: dict) -> PhaseEstimate:
    return PhaseEstimate(
        patient_id=patient_id,
        computed_at=row["time"],
        phase_offset_min=int(row["phase_offset_min"]),
        dlmo_estimate=row.get("dlmo_estimate"),
        amplitude=row.get("amplitude"),
        stability=row.get("stability"),
        confidence=float(row["confidence"]),
        method_version=row["method_version"],
        coefficient_source=(
            CoefficientSource.REFERENCE_FALLBACK
            if str(row["method_version"]).startswith("reference")
            else CoefficientSource.PRIVATE_VALIDATED
        ),
    )


__all__ = ["router", "estimator", "is_stale"]

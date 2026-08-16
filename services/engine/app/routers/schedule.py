"""Adaptive schedule endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import Database
from app.engine.drug_timer import load_catalog
from app.engine.schedule_builder import AnchorResolutionError, ScheduleBuilder
from app.envelope import fail, ok
from app.routers.circadian import _fetch_profile, estimator
from app.schemas import DoseStatus, ScheduleRequest

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/schedule", tags=["schedule"])
settings = get_settings()

catalog = load_catalog()
builder = ScheduleBuilder(catalog)


class DoseAck(BaseModel):
    status: DoseStatus
    taken_at: datetime | None = None


class GenerateRequest(BaseModel):
    profile_days: int = Field(default=14, ge=1, le=90)
    horizon_hours: int | None = Field(default=None, ge=6, le=168)
    push_to_queue: bool = True


@router.post("/build")
async def build_schedule(request: Request, body: ScheduleRequest):
    """Build a schedule from supplied inputs without touching storage.

    This is the function the whole Engine exists to perform, exposed directly.
    A clinician can post a hypothetical profile and regimen and see the
    resulting windows, which is how the timing model gets reviewed before a
    patient is put on it.
    """
    request_id = getattr(request.state, "request_id", None)

    phase = estimator.estimate(body.profile, patient_timezone=body.timezone)

    try:
        schedule = builder.build(
            patient_id=body.patient_id,
            profile=body.profile,
            phase=phase,
            medications=body.medications,
            patient_timezone=body.timezone,
            horizon_hours=body.horizon_hours or settings.schedule_horizon_hours,
            reference_time=body.reference_time,
            supersedes=body.supersedes,
            min_completeness=settings.min_profile_completeness,
        )
    except AnchorResolutionError as exc:
        return fail(
            "no_biological_anchor",
            str(exc),
            service=settings.service_name,
            status_code=422,
            request_id=request_id,
        )

    return ok(
        schedule.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        provisional=schedule.meta.provisional,
        coefficient_source=schedule.meta.coefficient_source.value,
    )


@router.post("/generate/{patient_id}")
async def generate_schedule(request: Request, patient_id: str, body: GenerateRequest):
    """Full pipeline: profile from Sync, regimen from storage, schedule out.

    The schedule supersedes the patient's current one rather than sitting
    beside it, and the reminder queue is rebuilt from the new object. A stale
    reminder firing against a superseded window is the failure this ordering
    prevents.
    """
    database: Database = request.app.state.db
    http: httpx.AsyncClient = request.app.state.http
    request_id = getattr(request.state, "request_id", None)

    profile = await _fetch_profile(http, patient_id, body.profile_days)
    if profile is None:
        return fail(
            "profile_unavailable",
            "The sync service did not return a circadian profile for this patient",
            service=settings.service_name,
            status_code=502,
            request_id=request_id,
        )

    medications = await database.active_medications(patient_id)
    if not medications:
        return fail(
            "empty_regimen",
            "This patient has no active medications to schedule",
            service=settings.service_name,
            status_code=409,
            request_id=request_id,
        )

    patient_timezone = await database.patient_timezone(patient_id)
    phase = estimator.estimate(profile, patient_timezone=patient_timezone)
    await database.store_phase_estimate(phase)

    previous_id = await database.latest_schedule_id(patient_id)

    try:
        schedule = builder.build(
            patient_id=patient_id,
            profile=profile,
            phase=phase,
            medications=medications,
            patient_timezone=patient_timezone,
            horizon_hours=body.horizon_hours or settings.schedule_horizon_hours,
            supersedes=previous_id,
            schedule_version=1,
            min_completeness=settings.min_profile_completeness,
        )
    except AnchorResolutionError as exc:
        return fail(
            "no_biological_anchor",
            str(exc),
            service=settings.service_name,
            status_code=422,
            request_id=request_id,
        )

    await database.store_schedule(schedule)

    queue_result: dict[str, object] = {"pushed": False}
    if body.push_to_queue:
        queue_result = await _push_to_reminder_queue(http, schedule.to_reminder_payload())

    log.info(
        "engine.schedule_generated",
        patient_id=patient_id,
        entries=schedule.entry_count,
        phase_offset_min=phase.phase_offset_min,
        provisional=schedule.meta.provisional,
    )

    return ok(
        schedule.model_dump(mode="json"),
        service=settings.service_name,
        request_id=request_id,
        supersedes=previous_id,
        queue=queue_result,
        provisional=schedule.meta.provisional,
    )


@router.get("/{patient_id}/current")
async def current_schedule(request: Request, patient_id: str):
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    schedule = await database.latest_schedule(patient_id)
    if schedule is None:
        return fail(
            "no_schedule",
            "This patient has no current schedule",
            service=settings.service_name,
            status_code=404,
            request_id=request_id,
        )

    return ok(schedule, service=settings.service_name, request_id=request_id)


@router.post("/dose/{entry_id}")
async def acknowledge_dose(request: Request, entry_id: str, body: DoseAck):
    """Record what actually happened with a dose.

    Adherence against a biological window is the outcome measure this system
    is judged on, so the record keeps the time the patient reports rather than
    the time the acknowledgement arrived. An offline app can sync a dose taken
    hours earlier and the record stays accurate.
    """
    database: Database = request.app.state.db
    request_id = getattr(request.state, "request_id", None)

    taken_at = body.taken_at
    if body.status is DoseStatus.TAKEN and taken_at is None:
        taken_at = datetime.now(timezone.utc)

    updated = await database.record_dose(entry_id, body.status.value, taken_at)
    if not updated:
        return fail(
            "unknown_dose",
            "No dose event matches this entry id",
            service=settings.service_name,
            status_code=404,
            request_id=request_id,
        )

    return ok(
        {"entry_id": entry_id, "status": body.status.value, "taken_at": taken_at},
        service=settings.service_name,
        request_id=request_id,
    )


@router.get("/catalog/info")
async def catalog_info(request: Request):
    request_id = getattr(request.state, "request_id", None)
    return ok(
        {
            "catalog_version": catalog.catalog_version,
            "coefficient_source": catalog.coefficient_source.value,
            "schedule_horizon_hours": settings.schedule_horizon_hours,
        },
        service=settings.service_name,
        request_id=request_id,
    )


async def _push_to_reminder_queue(
    http: httpx.AsyncClient, payload: dict
) -> dict[str, object]:
    """Hand the schedule to the reminder service.

    A failure here does not fail the request. The schedule is already stored
    and the mobile app can pull it directly; the queue is a delivery
    optimisation, not the source of truth.
    """
    try:
        response = await http.post(
            f"{settings.reminder_service_url}/remind/schedule",
            json=payload,
            headers={"x-service-token": settings.jwt_secret},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("engine.queue_push_failed", error=str(exc))
        return {"pushed": False, "error": str(exc)}

    body = response.json()
    return {"pushed": True, "queued": (body.get("data") or {}).get("queued")}

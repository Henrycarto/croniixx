"""Reminder queue endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Request

from app.auth import MobilePrincipal, current_patient, require_patient, service_caller
from app.config import get_settings
from app.devices import DeviceRegistry
from app.engine.dispatcher import Dispatcher
from app.engine.queue_manager import QueueManager
from app.envelope import fail, ok
from app.schemas import AckRequest, DeviceRegistration, SchedulePush

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/remind", tags=["remind"])
settings = get_settings()


@router.post("/schedule", dependencies=[Depends(service_caller)])
async def install_schedule(request: Request, body: SchedulePush):
    """Replace a patient's queued reminders with a new schedule's.

    Called by the Engine every time it generates a schedule. Replacement is
    total by design: the Engine supersedes schedules rather than patching them,
    and the queue mirrors that.
    """
    queue: QueueManager = request.app.state.queue
    request_id = getattr(request.state, "request_id", None)

    queued = await queue.replace_schedule(body)

    return ok(
        {"schedule_id": body.schedule_id, "patient_id": body.patient_id, "queued": queued},
        service=settings.service_name,
        request_id=request_id,
    )


@router.delete("/schedule/{patient_id}", dependencies=[Depends(service_caller)])
async def clear_patient(request: Request, patient_id: str):
    queue: QueueManager = request.app.state.queue
    request_id = getattr(request.state, "request_id", None)

    removed = await queue.cancel_patient(patient_id, keep_schedule_id=None)
    return ok(
        {"patient_id": patient_id, "cancelled": removed},
        service=settings.service_name,
        request_id=request_id,
    )


@router.post("/devices")
async def register_device(
    request: Request,
    body: DeviceRegistration,
    principal: MobilePrincipal = Depends(current_patient),
):
    """Register an Expo push token for the authenticated patient."""
    require_patient(principal, body.patient_id)

    devices: DeviceRegistry = request.app.state.devices
    request_id = getattr(request.state, "request_id", None)

    count = await devices.register(body)
    return ok(
        {"patient_id": body.patient_id, "devices_registered": count},
        service=settings.service_name,
        request_id=request_id,
    )


@router.delete("/devices/{token}")
async def unregister_device(
    request: Request,
    token: str,
    principal: MobilePrincipal = Depends(current_patient),
):
    devices: DeviceRegistry = request.app.state.devices
    request_id = getattr(request.state, "request_id", None)

    owned = await devices.tokens_for(principal.patient_id)
    if token not in owned:
        return fail(
            "unknown_device",
            "That token is not registered to this patient",
            service=settings.service_name,
            status_code=404,
            request_id=request_id,
        )

    await devices.drop(token)
    return ok({"token": token, "removed": True}, service=settings.service_name, request_id=request_id)


@router.get("/pending/{patient_id}")
async def pending_reminders(
    request: Request,
    patient_id: str,
    principal: MobilePrincipal = Depends(current_patient),
):
    """What the patient still has coming.

    The mobile app calls this on every foreground so its local schedule and the
    server queue agree. Offline the app falls back to its own SQLite copy, so
    this endpoint being unavailable degrades quietly rather than blocking doses.
    """
    require_patient(principal, patient_id)

    queue: QueueManager = request.app.state.queue
    request_id = getattr(request.state, "request_id", None)

    reminders = await queue.pending_for_patient(patient_id)
    return ok(
        [r.model_dump(mode="json") for r in reminders],
        service=settings.service_name,
        request_id=request_id,
        count=len(reminders),
    )


@router.post("/ack")
async def acknowledge(
    request: Request,
    body: AckRequest,
    principal: MobilePrincipal = Depends(current_patient),
):
    """Record that a reminder was acted on.

    Acknowledging one reminder retires the whole dose, so a patient who takes
    the dose as the window opens is not chased twice more.
    """
    queue: QueueManager = request.app.state.queue
    request_id = getattr(request.state, "request_id", None)

    reminder = await queue.get(body.reminder_id)
    if reminder is None:
        return fail(
            "unknown_reminder",
            "No reminder matches that id",
            service=settings.service_name,
            status_code=404,
            request_id=request_id,
        )

    require_patient(principal, reminder.patient_id)

    acknowledged = await queue.acknowledge(
        body.reminder_id, at=body.acknowledged_at or datetime.now(timezone.utc)
    )
    return ok(
        {
            "reminder_id": body.reminder_id,
            "entry_id": acknowledged.entry_id if acknowledged else None,
            "acknowledged_at": (body.acknowledged_at or datetime.now(timezone.utc)).isoformat(),
        },
        service=settings.service_name,
        request_id=request_id,
    )


@router.get("/queue/stats")
async def queue_stats(request: Request):
    """Operational view of the queue, used by the dashboard status strip."""
    queue: QueueManager = request.app.state.queue
    dispatcher: Dispatcher = request.app.state.dispatcher
    request_id = getattr(request.state, "request_id", None)

    stats = await queue.stats()
    return ok(
        {
            **stats,
            "dispatcher_running": dispatcher.running,
            "dispatcher_ticks": dispatcher.ticks,
            "last_tick": dispatcher.last_tick.isoformat() if dispatcher.last_tick else None,
        },
        service=settings.service_name,
        request_id=request_id,
    )


@router.post("/queue/tick", dependencies=[Depends(service_caller)])
async def manual_tick(request: Request):
    """Run one dispatch tick immediately.

    Exists so an operator can flush the queue after an incident without waiting
    for the interval, and so integration tests can drive the loop by hand.
    """
    dispatcher: Dispatcher = request.app.state.dispatcher
    request_id = getattr(request.state, "request_id", None)

    result = await dispatcher.tick()
    return ok(result, service=settings.service_name, request_id=request_id)

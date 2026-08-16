"""Redis backed reminder queue.

A medication reminder is not a best effort notification. If the queue loses a
dose the patient does not take it, and if the queue fires a dose twice the
patient may take it twice. Both are clinical events, so the queue is built as a
reliable delay queue rather than a fire and forget scheduler.

Structure:

    croniixx:rem:due            sorted set, score = fire time, member = id
    croniixx:rem:claimed        sorted set, score = claim expiry, member = id
    croniixx:rem:item:<id>      the reminder payload
    croniixx:rem:patient:<pid>  set of that patient's reminder ids
    croniixx:rem:sched:<sid>    set of one schedule's reminder ids

Claiming moves an id from `due` to `claimed` inside a Lua script, so two
dispatchers racing on the same tick cannot both take the same reminder. A claim
that is never acknowledged expires and the reminder returns to `due`, which is
what makes a dispatcher crash survivable.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis
import structlog

from app.schemas import Reminder, ReminderKind, ReminderState, SchedulePush

log = structlog.get_logger(__name__)

DUE_KEY = "croniixx:rem:due"
CLAIMED_KEY = "croniixx:rem:claimed"
ITEM_KEY = "croniixx:rem:item:{reminder_id}"
PATIENT_KEY = "croniixx:rem:patient:{patient_id}"
SCHEDULE_KEY = "croniixx:rem:sched:{schedule_id}"

# Payloads outlive their fire time by a day so a late acknowledgement from an
# offline phone still finds the reminder it refers to.
ITEM_TTL_SECONDS = 86400 * 2

# Fire the "window closing" nudge this long before the window ends.
CLOSING_LEAD_MINUTES = 20

# Skip the window open reminder when it would land within this of the target.
# Two notifications a few minutes apart read as a bug, not as care.
MIN_REMINDER_SPACING_MINUTES = 20


_CLAIM_SCRIPT = """
local due_key = KEYS[1]
local claimed_key = KEYS[2]
local now = tonumber(ARGV[1])
local visibility_until = tonumber(ARGV[2])
local batch = tonumber(ARGV[3])

local ids = redis.call('ZRANGEBYSCORE', due_key, '-inf', now, 'LIMIT', 0, batch)
if #ids == 0 then
  return {}
end

for _, id in ipairs(ids) do
  redis.call('ZREM', due_key, id)
  redis.call('ZADD', claimed_key, visibility_until, id)
end

return ids
"""

_RECLAIM_SCRIPT = """
local due_key = KEYS[1]
local claimed_key = KEYS[2]
local now = tonumber(ARGV[1])

local expired = redis.call('ZRANGEBYSCORE', claimed_key, '-inf', now)
for _, id in ipairs(expired) do
  redis.call('ZREM', claimed_key, id)
  redis.call('ZADD', due_key, now, id)
end

return #expired
"""


class QueueManager:
    """Owns every reminder in flight."""

    def __init__(self, client: redis.Redis, *, visibility_seconds: int = 120) -> None:
        self._redis = client
        self._visibility_seconds = visibility_seconds
        self._claim = client.register_script(_CLAIM_SCRIPT)
        self._reclaim = client.register_script(_RECLAIM_SCRIPT)

    # -- building ----------------------------------------------------------

    def reminders_for_schedule(
        self, push: SchedulePush, *, now: datetime | None = None
    ) -> list[Reminder]:
        """Expand a schedule into the notifications it implies.

        Reminders already in the past are not created. A schedule generated at
        noon covers the whole day, and materialising the morning doses would
        fire a burst of alerts for doses the patient has already handled.
        """
        moment = now or datetime.now(timezone.utc)
        reminders: list[Reminder] = []

        for dose in push.doses:
            try:
                window_start = _parse(dose["window_start"])
                window_end = _parse(dose["window_end"])
                target = _parse(dose["target"])
            except (KeyError, ValueError):
                log.warning("queue.malformed_dose", schedule_id=push.schedule_id)
                continue

            closing_at = window_end - timedelta(minutes=CLOSING_LEAD_MINUTES)
            candidates: list[tuple[ReminderKind, datetime]] = [
                (ReminderKind.WINDOW_OPEN, window_start),
                (ReminderKind.TARGET, target),
                (ReminderKind.WINDOW_CLOSING, closing_at),
            ]

            planned: list[tuple[ReminderKind, datetime]] = []
            for kind, fire_at in candidates:
                if fire_at <= moment:
                    continue
                if any(
                    abs((fire_at - existing).total_seconds())
                    < MIN_REMINDER_SPACING_MINUTES * 60
                    for _, existing in planned
                ):
                    continue
                planned.append((kind, fire_at))

            for kind, fire_at in planned:
                reminders.append(
                    Reminder(
                        patient_id=push.patient_id,
                        schedule_id=push.schedule_id,
                        entry_id=str(dose.get("entry_id", "")),
                        medication_id=str(dose.get("medication_id", "")),
                        kind=kind,
                        fire_at=fire_at,
                        window_start=window_start,
                        window_end=window_end,
                        target=target,
                        display_name=str(dose.get("display_name", "Medication")),
                        dose_amount=float(dose.get("dose_amount", 0) or 0),
                        dose_unit=str(dose.get("dose_unit", "")),
                        window_status=str(dose.get("status", "optimal")),
                        timezone=push.timezone,
                    )
                )

        return reminders

    # -- writing -----------------------------------------------------------

    async def replace_schedule(self, push: SchedulePush, *, now: datetime | None = None) -> int:
        """Install a schedule's reminders and cancel the patient's previous ones.

        Cancel first, then enqueue, in one pipeline. The reverse order would
        leave a window where the old and the new schedule are both live and a
        patient could be told to dose twice.
        """
        cancelled = await self.cancel_patient(push.patient_id, keep_schedule_id=None)
        reminders = self.reminders_for_schedule(push, now=now)
        await self.enqueue(reminders)

        log.info(
            "queue.schedule_installed",
            patient_id=push.patient_id,
            schedule_id=push.schedule_id,
            queued=len(reminders),
            cancelled=cancelled,
        )
        return len(reminders)

    async def enqueue(self, reminders: list[Reminder]) -> int:
        if not reminders:
            return 0

        pipeline = self._redis.pipeline()
        for reminder in reminders:
            item_key = ITEM_KEY.format(reminder_id=reminder.reminder_id)
            pipeline.set(item_key, reminder.model_dump_json(), ex=ITEM_TTL_SECONDS)
            pipeline.zadd(DUE_KEY, {reminder.reminder_id: reminder.fire_at.timestamp()})
            pipeline.sadd(PATIENT_KEY.format(patient_id=reminder.patient_id), reminder.reminder_id)
            pipeline.sadd(
                SCHEDULE_KEY.format(schedule_id=reminder.schedule_id), reminder.reminder_id
            )
            pipeline.expire(PATIENT_KEY.format(patient_id=reminder.patient_id), ITEM_TTL_SECONDS)
            pipeline.expire(
                SCHEDULE_KEY.format(schedule_id=reminder.schedule_id), ITEM_TTL_SECONDS
            )
        await pipeline.execute()
        return len(reminders)

    async def cancel_patient(self, patient_id: str, *, keep_schedule_id: str | None) -> int:
        """Remove a patient's pending reminders.

        Used when a schedule is superseded. Reminders belonging to
        keep_schedule_id survive, which lets a caller refresh part of a day
        without tearing down the rest of it.
        """
        patient_key = PATIENT_KEY.format(patient_id=patient_id)
        ids = await self._redis.smembers(patient_key)
        if not ids:
            return 0

        removed = 0
        pipeline = self._redis.pipeline()
        for reminder_id in ids:
            if keep_schedule_id is not None:
                reminder = await self.get(reminder_id)
                if reminder is not None and reminder.schedule_id == keep_schedule_id:
                    continue
            pipeline.zrem(DUE_KEY, reminder_id)
            pipeline.zrem(CLAIMED_KEY, reminder_id)
            pipeline.delete(ITEM_KEY.format(reminder_id=reminder_id))
            pipeline.srem(patient_key, reminder_id)
            removed += 1
        await pipeline.execute()
        return removed

    # -- reading and claiming ---------------------------------------------

    async def get(self, reminder_id: str) -> Reminder | None:
        raw = await self._redis.get(ITEM_KEY.format(reminder_id=reminder_id))
        if not raw:
            return None
        try:
            return Reminder.model_validate_json(raw)
        except ValueError:
            log.warning("queue.unreadable_item", reminder_id=reminder_id)
            return None

    async def claim_due(self, *, limit: int = 100, now: datetime | None = None) -> list[Reminder]:
        """Atomically take the reminders that are due."""
        moment = now or datetime.now(timezone.utc)
        visibility_until = moment.timestamp() + self._visibility_seconds

        ids = await self._claim(
            keys=[DUE_KEY, CLAIMED_KEY],
            args=[moment.timestamp(), visibility_until, limit],
        )

        reminders: list[Reminder] = []
        for reminder_id in ids or []:
            reminder = await self.get(_decode(reminder_id))
            if reminder is None:
                # The payload expired while the id was still in the set.
                await self._redis.zrem(CLAIMED_KEY, reminder_id)
                continue
            reminders.append(reminder)
        return reminders

    async def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """Return abandoned claims to the queue."""
        moment = now or datetime.now(timezone.utc)
        count = await self._reclaim(keys=[DUE_KEY, CLAIMED_KEY], args=[moment.timestamp()])
        if count:
            log.warning("queue.reclaimed_abandoned", count=int(count))
        return int(count or 0)

    async def complete(self, reminder: Reminder, state: ReminderState) -> None:
        """Finish a claimed reminder."""
        updated = reminder.model_copy(update={"state": state})
        pipeline = self._redis.pipeline()
        pipeline.zrem(CLAIMED_KEY, reminder.reminder_id)
        pipeline.set(
            ITEM_KEY.format(reminder_id=reminder.reminder_id),
            updated.model_dump_json(),
            ex=ITEM_TTL_SECONDS,
        )
        await pipeline.execute()

    async def retry(self, reminder: Reminder, *, delay_seconds: int, max_attempts: int) -> bool:
        """Put a failed delivery back with backoff.

        Returns False when the reminder has run out of attempts, at which point
        it is marked failed rather than retried forever. A dose alert that has
        failed four times is not going to succeed on the fifth, and the patient
        needs the dashboard to show the delivery gap instead.
        """
        attempts = reminder.attempts + 1
        if attempts >= max_attempts:
            await self.complete(reminder, ReminderState.FAILED)
            log.warning(
                "queue.delivery_abandoned",
                reminder_id=reminder.reminder_id,
                patient_id=reminder.patient_id,
                attempts=attempts,
            )
            return False

        updated = reminder.model_copy(
            update={"attempts": attempts, "state": ReminderState.QUEUED}
        )
        fire_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)

        pipeline = self._redis.pipeline()
        pipeline.zrem(CLAIMED_KEY, reminder.reminder_id)
        pipeline.set(
            ITEM_KEY.format(reminder_id=reminder.reminder_id),
            updated.model_dump_json(),
            ex=ITEM_TTL_SECONDS,
        )
        pipeline.zadd(DUE_KEY, {reminder.reminder_id: fire_at.timestamp()})
        await pipeline.execute()
        return True

    async def acknowledge(self, reminder_id: str, at: datetime | None = None) -> Reminder | None:
        """Mark a reminder handled and clear the rest of its dose.

        A patient who takes the dose when the window opens should not be
        nudged again at the target and again as it closes. Acknowledging one
        reminder retires every reminder for that dose.
        """
        reminder = await self.get(reminder_id)
        if reminder is None:
            return None

        moment = at or datetime.now(timezone.utc)
        await self.complete(reminder, ReminderState.ACKNOWLEDGED)

        siblings = await self._redis.smembers(
            PATIENT_KEY.format(patient_id=reminder.patient_id)
        )
        pipeline = self._redis.pipeline()
        for sibling_id in siblings:
            sibling = await self.get(_decode(sibling_id))
            if sibling is None or sibling.entry_id != reminder.entry_id:
                continue
            if sibling.reminder_id == reminder.reminder_id:
                continue
            pipeline.zrem(DUE_KEY, sibling.reminder_id)
            pipeline.zrem(CLAIMED_KEY, sibling.reminder_id)
            pipeline.set(
                ITEM_KEY.format(reminder_id=sibling.reminder_id),
                sibling.model_copy(update={"state": ReminderState.CANCELLED}).model_dump_json(),
                ex=ITEM_TTL_SECONDS,
            )
        await pipeline.execute()

        log.info(
            "queue.acknowledged",
            reminder_id=reminder_id,
            entry_id=reminder.entry_id,
            at=moment.isoformat(),
        )
        return reminder

    # -- inspection --------------------------------------------------------

    async def pending_for_patient(self, patient_id: str) -> list[Reminder]:
        ids = await self._redis.smembers(PATIENT_KEY.format(patient_id=patient_id))
        reminders: list[Reminder] = []
        for reminder_id in ids:
            reminder = await self.get(_decode(reminder_id))
            if reminder is not None and reminder.state in {
                ReminderState.QUEUED,
                ReminderState.CLAIMED,
            }:
                reminders.append(reminder)
        return sorted(reminders, key=lambda r: r.fire_at)

    async def stats(self, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or datetime.now(timezone.utc)
        pipeline = self._redis.pipeline()
        pipeline.zcard(DUE_KEY)
        pipeline.zcard(CLAIMED_KEY)
        pipeline.zcount(DUE_KEY, "-inf", moment.timestamp())
        queued, claimed, due_now = await pipeline.execute()
        return {"queued": int(queued), "claimed": int(claimed), "due_now": int(due_now)}


def _parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def to_json(reminder: Reminder) -> str:
    return json.dumps(reminder.model_dump(mode="json"))

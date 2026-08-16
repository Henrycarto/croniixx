"""The loop that turns due reminders into delivered notifications.

Runs inside the reminder API process as a background task. One tick does four
things in order: return abandoned claims, claim what is due, drop anything too
late to be safe, and deliver the rest.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from app.config import Settings
from app.devices import DeviceRegistry
from app.engine.push_client import ExpoPushClient
from app.engine.queue_manager import QueueManager
from app.schemas import Reminder, ReminderState

log = structlog.get_logger(__name__)

# Backoff between delivery attempts, in seconds. Short, because a dose window
# is measured in hours and an alert that arrives after the window closed is
# worse than one that never arrived.
RETRY_BACKOFF_SECONDS = [30, 90, 240]


class Dispatcher:
    def __init__(
        self,
        *,
        queue: QueueManager,
        devices: DeviceRegistry,
        push: ExpoPushClient,
        settings: Settings,
    ) -> None:
        self.queue = queue
        self.devices = devices
        self.push = push
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.last_tick: datetime | None = None
        self.ticks = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="croniixx-dispatcher")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        log.info("dispatcher.started", interval=self.settings.dispatch_interval_seconds)
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must outlive one bad tick
                log.exception("dispatcher.tick_failed", error=str(exc))

            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self.settings.dispatch_interval_seconds
                )
            except asyncio.TimeoutError:
                continue
        log.info("dispatcher.stopped")

    # -- one tick ----------------------------------------------------------

    async def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        moment = now or datetime.now(timezone.utc)
        self.ticks += 1
        self.last_tick = moment

        await self.queue.reclaim_expired(now=moment)
        due = await self.queue.claim_due(limit=200, now=moment)

        if not due:
            return {"claimed": 0, "sent": 0, "expired": 0, "failed": 0}

        fresh: list[Reminder] = []
        expired = 0
        for reminder in due:
            lateness = (moment - reminder.fire_at).total_seconds()
            if lateness > self.settings.max_lateness_seconds:
                # Prompting a dose after its window has passed is an active
                # harm, not a missed convenience.
                await self.queue.complete(reminder, ReminderState.EXPIRED)
                expired += 1
                continue
            fresh.append(reminder)

        sent, failed = await self._deliver(fresh)

        log.info(
            "dispatcher.tick",
            claimed=len(due),
            sent=sent,
            expired=expired,
            failed=failed,
        )
        return {"claimed": len(due), "sent": sent, "expired": expired, "failed": failed}

    async def _deliver(self, reminders: list[Reminder]) -> tuple[int, int]:
        if not reminders:
            return 0, 0

        messages: list[dict] = []
        # One reminder can produce several messages when a patient has more
        # than one device, so the mapping back has to be kept explicitly.
        message_owner: list[Reminder] = []
        undeliverable: list[Reminder] = []

        for reminder in reminders:
            tokens = await self.devices.tokens_for(reminder.patient_id)
            if not tokens:
                undeliverable.append(reminder)
                continue
            for token in tokens:
                messages.append(self.push.build_message(reminder, token))
                message_owner.append(reminder)

        for reminder in undeliverable:
            # No device registered. The dose still exists in the app's local
            # schedule, so this is a delivery gap rather than a lost dose.
            log.info(
                "dispatcher.no_device",
                patient_id=reminder.patient_id,
                reminder_id=reminder.reminder_id,
            )
            await self.queue.complete(reminder, ReminderState.FAILED)

        if not messages:
            return 0, len(undeliverable)

        outcome = await self.push.send(messages)

        if outcome.dead_tokens:
            await self.devices.drop_many(outcome.dead_tokens)

        # Results come back positionally, so each one is matched to the
        # reminder that produced it. Where a reminder went to several devices,
        # the best result wins: one phone receiving the alert is enough, and
        # retrying because a second, unused tablet failed would double notify.
        best: dict[str, tuple[str, Reminder]] = {}
        for reminder, result in zip(message_owner, outcome.results):
            current = best.get(reminder.reminder_id)
            if current is None or _rank(result.status) > _rank(current[0]):
                best[reminder.reminder_id] = (result.status, reminder)

        sent = 0
        failed = len(undeliverable)

        for status, reminder in best.values():
            if status == "ok":
                await self.queue.complete(reminder, ReminderState.SENT)
                sent += 1
                continue

            if status == "retry":
                requeued = await self.queue.retry(
                    reminder,
                    delay_seconds=_backoff(reminder.attempts),
                    max_attempts=self.settings.max_delivery_attempts,
                )
                if not requeued:
                    failed += 1
                continue

            await self.queue.complete(reminder, ReminderState.FAILED)
            failed += 1

        if outcome.errors:
            log.warning("dispatcher.push_errors", errors=outcome.errors[:5])

        return sent, failed


def _backoff(attempt: int) -> int:
    index = min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)
    return RETRY_BACKOFF_SECONDS[index]


def _rank(status: str) -> int:
    return {"ok": 3, "retry": 2, "dead": 1, "failed": 0}.get(status, 0)

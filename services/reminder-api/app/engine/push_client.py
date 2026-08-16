"""Expo push notification client.

Expo's push service has a two stage result. The send call returns a ticket per
message saying the request was accepted, and the ticket has to be exchanged for
a receipt later to learn whether the device actually got it. Treating a ticket
as delivery is the usual mistake and it hides exactly the failure that matters
here: a patient whose token went stale stops receiving dose reminders and
nothing in the system notices.

Reference: https://docs.expo.dev/push-notifications/sending-notifications
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx
import structlog

from app.config import Settings, get_settings
from app.schemas import Reminder

log = structlog.get_logger(__name__)

# Expo error codes that mean the token will never work again. These are the
# only ones where retrying is pointless and the token should be dropped.
DEAD_TOKEN_ERRORS = {"DeviceNotRegistered", "InvalidCredentials"}

# A dose notification must survive Do Not Disturb on iOS, so it goes out at
# high priority with a sound. Medication timing is the case the platform
# exception exists for.
DEFAULT_PRIORITY = "high"


@dataclass
class MessageResult:
    """The fate of one message.

    Results are positional. A patient with two devices, or a busy regimen
    sending several doses to one device, produces multiple messages carrying
    the same token, so keying results by token would silently collapse them
    and mark a reminder delivered on the strength of a different one.
    """

    token: str
    status: str  # ok, retry, dead, failed
    ticket_id: str | None = None
    message: str | None = None


@dataclass
class PushOutcome:
    """What happened to a send, in the order the messages were given."""

    results: list[MessageResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return sum(1 for result in self.results if result.status == "ok")

    @property
    def dead_tokens(self) -> set[str]:
        return {result.token for result in self.results if result.status == "dead"}

    @property
    def retryable(self) -> list[str]:
        return [result.token for result in self.results if result.status == "retry"]

    @property
    def tickets(self) -> dict[str, str]:
        """Ticket id to token, for the later receipt sweep."""
        return {
            result.ticket_id: result.token
            for result in self.results
            if result.status == "ok" and result.ticket_id
        }


class ExpoPushClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    async def start(self) -> None:
        if self._client is None:
            headers = {
                "accept": "application/json",
                "content-type": "application/json",
                # Expo compresses large batches; asking for gzip keeps a full
                # ward's worth of reminders inside one request comfortably.
                "accept-encoding": "gzip, deflate",
            }
            if self._settings.expo_access_token:
                headers["authorization"] = f"Bearer {self._settings.expo_access_token}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0), headers=headers
            )
            self._owns_client = True

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ExpoPushClient used before start()")
        return self._client

    # -- message construction ---------------------------------------------

    def build_message(self, reminder: Reminder, token: str) -> dict[str, Any]:
        """Compose one Expo message.

        The data block carries everything the app needs to render the dose
        screen without a network call, because the app is offline first and a
        reminder that requires connectivity to be useful defeats the point.
        """
        return {
            "to": token,
            "title": reminder.title(),
            "body": reminder.body(),
            "sound": "default",
            "priority": DEFAULT_PRIORITY,
            "channelId": "croniixx-doses",
            "categoryId": "dose",
            # Collapsing on the dose keeps a late nudge from stacking on top of
            # the earlier one for the same dose in the notification tray.
            "collapseId": reminder.entry_id,
            "ttl": max(int((reminder.window_end - reminder.fire_at).total_seconds()), 60),
            "data": {
                "reminder_id": reminder.reminder_id,
                "entry_id": reminder.entry_id,
                "schedule_id": reminder.schedule_id,
                "medication_id": reminder.medication_id,
                "kind": reminder.kind.value,
                "window_start": reminder.window_start.isoformat(),
                "window_end": reminder.window_end.isoformat(),
                "target": reminder.target.isoformat(),
                "window_status": reminder.window_status,
                "dose_amount": reminder.dose_amount,
                "dose_unit": reminder.dose_unit,
                "display_name": reminder.display_name,
            },
        }

    # -- sending -----------------------------------------------------------

    async def send(self, messages: list[dict[str, Any]]) -> PushOutcome:
        """Send messages, batching to Expo's limit."""
        outcome = PushOutcome()
        if not messages:
            return outcome

        for batch in _chunks(messages, self._settings.expo_batch_size):
            await self._send_batch(batch, outcome)
        return outcome

    async def _send_batch(self, batch: list[dict[str, Any]], outcome: PushOutcome) -> None:
        def mark_all(status: str, note: str) -> None:
            outcome.results.extend(
                MessageResult(token=str(message["to"]), status=status, message=note)
                for message in batch
            )

        try:
            response = await self.client.post(self._settings.expo_push_url, json=batch)
        except httpx.RequestError as exc:
            outcome.errors.append(str(exc))
            mark_all("retry", str(exc))
            return

        if response.status_code == 429:
            outcome.errors.append("Expo rate limited the batch")
            mark_all("retry", "rate limited")
            return

        if response.status_code >= 400:
            note = f"Expo returned {response.status_code}: {response.text[:300]}"
            outcome.errors.append(note)
            # A 5xx is Expo's problem and worth retrying. A 4xx other than 429
            # means the batch itself is malformed and will fail identically.
            mark_all("retry" if response.status_code >= 500 else "failed", note)
            return

        try:
            body = response.json()
        except ValueError:
            outcome.errors.append("Expo returned a non JSON body")
            mark_all("retry", "unparseable response")
            return

        tickets = body.get("data")
        if not isinstance(tickets, list) or len(tickets) != len(batch):
            # A ticket list of the wrong length cannot be aligned to messages,
            # and guessing the alignment would attribute a success to the wrong
            # dose. The whole batch is retried instead.
            outcome.errors.append("Expo ticket list did not match the batch")
            mark_all("retry", "ticket count mismatch")
            return

        for message, ticket in zip(batch, tickets):
            token = str(message["to"])
            if not isinstance(ticket, dict):
                outcome.results.append(MessageResult(token=token, status="retry"))
                continue

            if ticket.get("status") == "ok":
                outcome.results.append(
                    MessageResult(token=token, status="ok", ticket_id=str(ticket.get("id", "")))
                )
                continue

            error_code = (ticket.get("details") or {}).get("error", "")
            note = str(ticket.get("message", "unknown error"))
            outcome.errors.append(f"{token}: {note}")
            outcome.results.append(
                MessageResult(
                    token=token,
                    status="dead" if error_code in DEAD_TOKEN_ERRORS else "retry",
                    message=note,
                )
            )

    # -- receipts ----------------------------------------------------------

    async def fetch_receipts(self, ticket_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Exchange tickets for delivery receipts.

        Expo asks for a delay of at least fifteen minutes before checking, so
        this is called by the receipt sweep rather than immediately after a
        send.
        """
        receipts: dict[str, dict[str, Any]] = {}
        for batch in _chunks(ticket_ids, 300):
            try:
                response = await self.client.post(
                    self._settings.expo_receipt_url, json={"ids": batch}
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("push.receipt_fetch_failed", error=str(exc))
                continue

            data = response.json().get("data")
            if isinstance(data, dict):
                receipts.update(data)

            await asyncio.sleep(0)
        return receipts

    def dead_tokens_from_receipts(
        self, receipts: dict[str, dict[str, Any]], ticket_to_token: dict[str, str]
    ) -> set[str]:
        """Find tokens the receipts prove are gone."""
        dead: set[str] = set()
        for ticket_id, receipt in receipts.items():
            if receipt.get("status") == "ok":
                continue
            error_code = (receipt.get("details") or {}).get("error", "")
            if error_code in DEAD_TOKEN_ERRORS:
                token = ticket_to_token.get(ticket_id)
                if token:
                    dead.add(token)
        return dead


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]

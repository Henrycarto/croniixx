"""Dispatcher and push client tests.

Expo is stubbed at the HTTP transport, not at the client, so the real request
building, batching, ticket parsing, and dead token handling all execute.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from app.engine.dispatcher import Dispatcher
from app.engine.push_client import ExpoPushClient
from app.schemas import DevicePlatform, DeviceRegistration, ReminderState
from tests.conftest import NOW, make_push

TOKEN = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
SECOND_TOKEN = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def stub_expo(handler) -> ExpoPushClient:
    from app.config import Settings

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return ExpoPushClient(Settings(), client=client)


def ok_handler(request: httpx.Request) -> httpx.Response:
    messages = json.loads(request.content)
    return httpx.Response(
        200, json={"data": [{"status": "ok", "id": f"ticket-{i}"} for i in range(len(messages))]}
    )


def dead_token_handler(request: httpx.Request) -> httpx.Response:
    messages = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "data": [
                {
                    "status": "error",
                    "message": "device not registered",
                    "details": {"error": "DeviceNotRegistered"},
                }
                for _ in messages
            ]
        },
    )


def server_error_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="expo is unavailable")


async def register(devices, patient_id: str = "patient-1", token: str = TOKEN):
    await devices.register(
        DeviceRegistration(
            patient_id=patient_id, expo_push_token=token, platform=DevicePlatform.IOS
        )
    )


# ---------------------------------------------------------------------------
# Push client
# ---------------------------------------------------------------------------


async def test_message_carries_everything_the_offline_app_needs(queue):
    push = stub_expo(ok_handler)
    reminder = queue.reminders_for_schedule(make_push(), now=NOW)[0]

    message = push.build_message(reminder, TOKEN)

    assert message["to"] == TOKEN
    assert message["priority"] == "high"
    assert message["collapseId"] == reminder.entry_id
    data = message["data"]
    for key in ("window_start", "window_end", "target", "dose_amount", "dose_unit"):
        assert key in data


async def test_ticket_per_message_is_recorded(queue):
    push = stub_expo(ok_handler)
    await push.start()
    reminders = queue.reminders_for_schedule(make_push(doses=2), now=NOW)
    messages = [push.build_message(r, TOKEN) for r in reminders]

    outcome = await push.send(messages)

    assert outcome.accepted == len(messages)
    assert not outcome.retryable


async def test_dead_tokens_are_separated_from_retryable_failures(queue):
    push = stub_expo(dead_token_handler)
    await push.start()
    reminder = queue.reminders_for_schedule(make_push(), now=NOW)[0]

    outcome = await push.send([push.build_message(reminder, TOKEN)])

    assert outcome.dead_tokens == {TOKEN}
    assert not outcome.retryable


async def test_expo_outage_is_retryable(queue):
    push = stub_expo(server_error_handler)
    await push.start()
    reminder = queue.reminders_for_schedule(make_push(), now=NOW)[0]

    outcome = await push.send([push.build_message(reminder, TOKEN)])

    assert outcome.retryable == [TOKEN]
    assert not outcome.dead_tokens


async def test_batches_respect_the_expo_limit(queue):
    seen_batch_sizes: list[int] = []

    def counting_handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)
        seen_batch_sizes.append(len(messages))
        return ok_handler(request)

    push = stub_expo(counting_handler)
    await push.start()
    reminder = queue.reminders_for_schedule(make_push(), now=NOW)[0]
    messages = [push.build_message(reminder, TOKEN) for _ in range(250)]

    await push.send(messages)

    assert seen_batch_sizes == [100, 100, 50]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def test_a_due_reminder_is_sent(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(ok_handler)), settings=settings
    )
    result = await dispatcher.tick(now=NOW + timedelta(hours=2, seconds=30))

    assert result["sent"] >= 1
    assert result["failed"] == 0


async def test_a_reminder_that_is_far_too_late_is_dropped(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(ok_handler)), settings=settings
    )
    # The service was down for six hours. Prompting a dose whose window has
    # closed is worse than staying quiet.
    result = await dispatcher.tick(now=NOW + timedelta(hours=8))

    assert result["expired"] > 0
    assert result["sent"] == 0


async def test_a_patient_with_no_device_is_not_retried_forever(queue, devices, settings):
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(ok_handler)), settings=settings
    )
    result = await dispatcher.tick(now=NOW + timedelta(hours=2, seconds=30))

    assert result["sent"] == 0
    assert result["failed"] >= 1


async def test_a_dead_token_is_removed_from_the_registry(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue,
        devices=devices,
        push=await _started(stub_expo(dead_token_handler)),
        settings=settings,
    )
    await dispatcher.tick(now=NOW + timedelta(hours=2, seconds=30))

    assert await devices.tokens_for("patient-1") == []


async def test_an_expo_outage_leaves_the_reminder_queued(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue,
        devices=devices,
        push=await _started(stub_expo(server_error_handler)),
        settings=settings,
    )
    at = NOW + timedelta(hours=2, seconds=30)
    await dispatcher.tick(now=at)

    pending = await queue.pending_for_patient("patient-1")
    assert any(r.attempts >= 1 for r in pending)


async def test_every_registered_device_gets_the_notification(queue, devices, settings):
    await register(devices, token=TOKEN)
    await register(devices, token=SECOND_TOKEN)
    await queue.replace_schedule(make_push(), now=NOW)

    seen_tokens: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        for message in json.loads(request.content):
            seen_tokens.append(message["to"])
        return ok_handler(request)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(capture)), settings=settings
    )
    await dispatcher.tick(now=NOW + timedelta(hours=2, seconds=30))

    assert set(seen_tokens) == {TOKEN, SECOND_TOKEN}


async def test_a_sent_reminder_is_not_sent_again(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(ok_handler)), settings=settings
    )
    at = NOW + timedelta(hours=2, seconds=30)
    first = await dispatcher.tick(now=at)
    second = await dispatcher.tick(now=at + timedelta(seconds=30))

    assert first["sent"] >= 1
    assert second["sent"] == 0


async def test_states_end_up_terminal(queue, devices, settings):
    await register(devices)
    await queue.replace_schedule(make_push(), now=NOW)

    dispatcher = Dispatcher(
        queue=queue, devices=devices, push=await _started(stub_expo(ok_handler)), settings=settings
    )
    await dispatcher.tick(now=NOW + timedelta(hours=2, seconds=30))

    reminders = [
        await queue.get(r.reminder_id)
        for r in queue.reminders_for_schedule(make_push(), now=NOW)
    ]
    live_states = {
        r.state for r in await queue.pending_for_patient("patient-1")
    }
    assert ReminderState.SENT not in live_states


# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------


async def test_registration_rejects_a_token_that_is_not_expo_shaped():
    with pytest.raises(ValueError):
        DeviceRegistration(
            patient_id="patient-1", expo_push_token="not-a-token", platform=DevicePlatform.ANDROID
        )


async def test_a_patient_can_hold_several_devices(devices):
    await register(devices, token=TOKEN)
    count = await devices.register(
        DeviceRegistration(
            patient_id="patient-1",
            expo_push_token=SECOND_TOKEN,
            platform=DevicePlatform.ANDROID,
        )
    )
    assert count == 2
    assert set(await devices.tokens_for("patient-1")) == {TOKEN, SECOND_TOKEN}


async def test_registering_the_same_token_twice_is_idempotent(devices):
    await register(devices)
    count = await devices.register(
        DeviceRegistration(
            patient_id="patient-1", expo_push_token=TOKEN, platform=DevicePlatform.IOS
        )
    )
    assert count == 1


async def _started(push: ExpoPushClient) -> ExpoPushClient:
    await push.start()
    return push

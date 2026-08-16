"""Queue tests.

The properties under test are the ones that matter clinically: a dose is never
lost, a dose is never delivered twice, a superseded schedule stops firing, and
a dispatcher that dies does not swallow what it was holding.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.engine.queue_manager import CLOSING_LEAD_MINUTES, QueueManager
from app.schemas import ReminderKind, ReminderState
from tests.conftest import NOW, make_push


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


async def test_one_dose_produces_three_reminders(queue: QueueManager):
    reminders = queue.reminders_for_schedule(make_push(), now=NOW)
    kinds = {r.kind for r in reminders}
    assert kinds == {
        ReminderKind.WINDOW_OPEN,
        ReminderKind.TARGET,
        ReminderKind.WINDOW_CLOSING,
    }


async def test_reminders_in_the_past_are_not_created(queue: QueueManager):
    push = make_push(first_window_start=NOW - timedelta(hours=6))
    assert queue.reminders_for_schedule(push, now=NOW) == []


async def test_closing_reminder_leads_the_window_end(queue: QueueManager):
    reminders = queue.reminders_for_schedule(make_push(), now=NOW)
    closing = next(r for r in reminders if r.kind is ReminderKind.WINDOW_CLOSING)
    assert closing.fire_at == closing.window_end - timedelta(minutes=CLOSING_LEAD_MINUTES)


async def test_reminders_too_close_together_are_collapsed(queue: QueueManager):
    push = make_push()
    # A thirty minute window puts open, target, and closing within minutes of
    # each other, which would read as a notification burst.
    start = NOW + timedelta(hours=2)
    push.doses[0]["window_start"] = start.isoformat()
    push.doses[0]["window_end"] = (start + timedelta(minutes=30)).isoformat()
    push.doses[0]["target"] = (start + timedelta(minutes=15)).isoformat()

    reminders = queue.reminders_for_schedule(push, now=NOW)
    fire_times = sorted(r.fire_at for r in reminders)
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(fire_times, fire_times[1:])]
    assert all(gap >= 20 for gap in gaps)


async def test_malformed_dose_is_skipped_not_fatal(queue: QueueManager):
    push = make_push(doses=2)
    push.doses[0].pop("window_start")
    reminders = queue.reminders_for_schedule(push, now=NOW)
    assert reminders
    assert all(r.entry_id == "entry-1" for r in reminders)


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


async def test_nothing_is_claimed_before_it_is_due(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    assert await queue.claim_due(now=NOW) == []


async def test_due_reminders_are_claimed(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    claimed = await queue.claim_due(now=NOW + timedelta(hours=3))
    assert claimed


async def test_two_dispatchers_cannot_claim_the_same_reminder(queue: QueueManager):
    await queue.replace_schedule(make_push(doses=3), now=NOW)
    later = NOW + timedelta(hours=20)

    first, second = await asyncio.gather(
        queue.claim_due(now=later), queue.claim_due(now=later)
    )

    first_ids = {r.reminder_id for r in first}
    second_ids = {r.reminder_id for r in second}
    assert first_ids
    assert not (first_ids & second_ids)


async def test_a_claim_that_is_never_acknowledged_returns_to_the_queue(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    due_at = NOW + timedelta(hours=3)

    claimed = await queue.claim_due(now=due_at)
    assert claimed
    assert await queue.claim_due(now=due_at) == []

    # The dispatcher died holding them. After the visibility timeout they come
    # back rather than staying invisible forever.
    recovered = await queue.reclaim_expired(now=due_at + timedelta(seconds=120))
    assert recovered == len(claimed)

    reclaimed = await queue.claim_due(now=due_at + timedelta(seconds=121))
    assert {r.reminder_id for r in reclaimed} == {r.reminder_id for r in claimed}


async def test_completed_reminders_do_not_come_back(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    due_at = NOW + timedelta(hours=3)

    claimed = await queue.claim_due(now=due_at)
    assert claimed
    for reminder in claimed:
        await queue.complete(reminder, ReminderState.SENT)

    # Well past the visibility timeout. Anything still held would be reclaimed
    # here, and a completed reminder must not be among it.
    await queue.reclaim_expired(now=due_at + timedelta(hours=6))
    later = await queue.claim_due(now=due_at + timedelta(hours=6))

    completed_ids = {r.reminder_id for r in claimed}
    assert not (completed_ids & {r.reminder_id for r in later})


# ---------------------------------------------------------------------------
# Supersede and acknowledge
# ---------------------------------------------------------------------------


async def test_a_new_schedule_clears_the_old_ones_reminders(queue: QueueManager):
    await queue.replace_schedule(make_push(schedule_id="old", doses=2), now=NOW)
    await queue.replace_schedule(make_push(schedule_id="new", doses=2), now=NOW)

    pending = await queue.pending_for_patient("patient-1")
    assert pending
    assert {r.schedule_id for r in pending} == {"new"}


async def test_acknowledging_one_reminder_retires_the_whole_dose(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    pending = await queue.pending_for_patient("patient-1")
    assert len(pending) == 3

    await queue.acknowledge(pending[0].reminder_id)

    remaining = await queue.pending_for_patient("patient-1")
    assert remaining == []


async def test_acknowledging_one_dose_leaves_the_others_alone(queue: QueueManager):
    await queue.replace_schedule(make_push(doses=2), now=NOW)
    pending = await queue.pending_for_patient("patient-1")

    first_dose = [r for r in pending if r.entry_id == "entry-0"]
    await queue.acknowledge(first_dose[0].reminder_id)

    remaining = await queue.pending_for_patient("patient-1")
    assert remaining
    assert {r.entry_id for r in remaining} == {"entry-1"}


async def test_acknowledging_an_unknown_id_is_not_an_error(queue: QueueManager):
    assert await queue.acknowledge("does-not-exist") is None


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


async def test_a_failed_delivery_is_requeued_with_a_higher_attempt_count(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    due_at = NOW + timedelta(hours=3)
    reminder = (await queue.claim_due(now=due_at))[0]

    requeued = await queue.retry(reminder, delay_seconds=1, max_attempts=3)
    assert requeued is True

    stored = await queue.get(reminder.reminder_id)
    assert stored is not None
    assert stored.attempts == 1
    assert stored.state is ReminderState.QUEUED


async def test_retries_stop_at_the_attempt_ceiling(queue: QueueManager):
    await queue.replace_schedule(make_push(), now=NOW)
    reminder = (await queue.claim_due(now=NOW + timedelta(hours=3)))[0]

    exhausted = reminder.model_copy(update={"attempts": 2})
    requeued = await queue.retry(exhausted, delay_seconds=1, max_attempts=3)

    assert requeued is False
    stored = await queue.get(reminder.reminder_id)
    assert stored is not None and stored.state is ReminderState.FAILED


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def test_stats_report_queue_depth(queue: QueueManager):
    await queue.replace_schedule(make_push(doses=2), now=NOW)
    stats = await queue.stats(now=NOW)

    assert stats["queued"] == 6
    assert stats["due_now"] == 0
    assert stats["claimed"] == 0

    later = await queue.stats(now=NOW + timedelta(hours=24))
    assert later["due_now"] == 6


async def test_cancelling_a_patient_empties_their_queue(queue: QueueManager):
    await queue.replace_schedule(make_push(doses=2), now=NOW)
    removed = await queue.cancel_patient("patient-1", keep_schedule_id=None)

    assert removed == 6
    assert await queue.pending_for_patient("patient-1") == []


@pytest.mark.parametrize("doses", [1, 3, 5])
async def test_queue_depth_scales_with_the_regimen(queue: QueueManager, doses):
    await queue.replace_schedule(make_push(doses=doses), now=NOW)
    stats = await queue.stats(now=NOW)
    assert stats["queued"] == doses * 3

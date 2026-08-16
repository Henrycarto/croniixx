"""Fixtures for reminder API tests.

fakeredis runs the real Redis command surface, including the Lua scripts the
queue depends on for atomic claiming. Mocking Redis here would test the mock
rather than the concurrency behaviour that makes the queue safe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest

from app.config import Settings
from app.devices import DeviceRegistry
from app.engine.queue_manager import QueueManager
from app.schemas import SchedulePush

NOW = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        redis_url="redis://localhost:6379/0",
        jwt_secret="test-secret",
        dispatch_interval_seconds=1,
        claim_visibility_seconds=60,
        max_delivery_attempts=3,
        max_lateness_seconds=900,
    )


@pytest.fixture
async def queue(redis_client, settings) -> QueueManager:
    return QueueManager(redis_client, visibility_seconds=settings.claim_visibility_seconds)


@pytest.fixture
async def devices(redis_client) -> DeviceRegistry:
    return DeviceRegistry(redis_client)


def make_push(
    *,
    patient_id: str = "patient-1",
    schedule_id: str = "schedule-1",
    doses: int = 1,
    first_window_start: datetime | None = None,
) -> SchedulePush:
    start = first_window_start or (NOW + timedelta(hours=2))
    payload_doses = []
    for index in range(doses):
        window_start = start + timedelta(hours=index * 6)
        window_end = window_start + timedelta(hours=3)
        payload_doses.append(
            {
                "entry_id": f"entry-{index}",
                "medication_id": f"med-{index}",
                "display_name": f"Agent {index}",
                "dose_amount": 10.0,
                "dose_unit": "mg",
                "target": (window_start + timedelta(minutes=90)).isoformat(),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "status": "optimal",
            }
        )

    return SchedulePush(
        schedule_id=schedule_id,
        patient_id=patient_id,
        timezone="UTC",
        valid_until=NOW + timedelta(hours=26),
        doses=payload_doses,
    )

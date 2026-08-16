"""Push token registry.

Tokens live in Redis rather than Postgres because they are device state, not
clinical record. A patient reinstalling the app gets a new token and the old
one is dead; losing the set on a cache rebuild costs one re-registration on
next app open, which the mobile client does unconditionally at startup.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis.asyncio as redis
import structlog

from app.schemas import DeviceRegistration

log = structlog.get_logger(__name__)

TOKENS_KEY = "croniixx:rem:tokens:{patient_id}"
TOKEN_META_KEY = "croniixx:rem:token:{token}"
TOKEN_TTL_SECONDS = 86400 * 180


class DeviceRegistry:
    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    async def register(self, registration: DeviceRegistration) -> int:
        """Add a token and return how many the patient now has.

        A patient can hold several: a phone and a tablet, or the same phone
        before and after an app reinstall. Reminders go to all of them, since
        which device is in reach at dose time is not knowable here.
        """
        tokens_key = TOKENS_KEY.format(patient_id=registration.patient_id)
        meta = {
            "patient_id": registration.patient_id,
            "platform": registration.platform.value,
            "app_version": registration.app_version,
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }

        pipeline = self._redis.pipeline()
        pipeline.sadd(tokens_key, registration.expo_push_token)
        pipeline.expire(tokens_key, TOKEN_TTL_SECONDS)
        pipeline.set(
            TOKEN_META_KEY.format(token=registration.expo_push_token),
            json.dumps(meta),
            ex=TOKEN_TTL_SECONDS,
        )
        await pipeline.execute()

        return int(await self._redis.scard(tokens_key))

    async def tokens_for(self, patient_id: str) -> list[str]:
        raw = await self._redis.smembers(TOKENS_KEY.format(patient_id=patient_id))
        return sorted(_decode(token) for token in raw)

    async def drop(self, token: str) -> None:
        """Remove a token Expo has told us is dead."""
        meta_key = TOKEN_META_KEY.format(token=token)
        raw = await self._redis.get(meta_key)
        patient_id: str | None = None
        if raw:
            try:
                patient_id = json.loads(raw).get("patient_id")
            except ValueError:
                patient_id = None

        pipeline = self._redis.pipeline()
        pipeline.delete(meta_key)
        if patient_id:
            pipeline.srem(TOKENS_KEY.format(patient_id=patient_id), token)
        await pipeline.execute()

        log.info("devices.token_dropped", patient_id=patient_id)

    async def drop_many(self, tokens: set[str]) -> None:
        for token in tokens:
            await self.drop(token)


def _decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)

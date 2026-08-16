"""Database access for the Sync service.

SQLAlchemy Core rather than the ORM. The write path is a bulk insert of
biometric samples into a hypertable, which is exactly the case where the ORM
identity map costs and returns nothing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.schemas import (
    NormalizedSample,
    NormalizedSleepSession,
    Provider,
    WearableLinkStatus,
)

log = structlog.get_logger(__name__)


class Database:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self) -> None:
        self._engine = create_async_engine(
            self._settings.database_url,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
            future=True,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    @property
    def available(self) -> bool:
        return self._session_factory is not None

    def session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("Database used before start()")
        return self._session_factory()

    async def ping(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001 - health check reports, never raises
            log.warning("db.ping_failed", error=str(exc))
            return False

    # -- link resolution ---------------------------------------------------

    async def resolve_patient(self, terra_user_id: str) -> str | None:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT patient_id FROM wearable_links WHERE terra_user_id = :uid"),
                {"uid": terra_user_id},
            )
            row = result.first()
            return str(row[0]) if row else None

    async def register_link(
        self,
        *,
        patient_id: str,
        terra_user_id: str,
        provider: Provider,
        scopes: Sequence[str] = (),
    ) -> None:
        """Record a device connection.

        Terra reissues the same user id when a patient reconnects the same
        device, so this upserts rather than inserting a second row and
        splitting that device's history in two.
        """
        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO wearable_links (patient_id, terra_user_id, provider, scopes, active)
                    VALUES (:patient_id, :terra_user_id, :provider, :scopes, TRUE)
                    ON CONFLICT (terra_user_id) DO UPDATE
                    SET patient_id = EXCLUDED.patient_id,
                        provider    = EXCLUDED.provider,
                        scopes      = EXCLUDED.scopes,
                        active      = TRUE
                    """
                ),
                {
                    "patient_id": patient_id,
                    "terra_user_id": terra_user_id,
                    "provider": provider.value,
                    "scopes": list(scopes),
                },
            )
            await session.commit()

    async def deactivate_link(self, terra_user_id: str) -> None:
        async with self.session() as session:
            await session.execute(
                text("UPDATE wearable_links SET active = FALSE WHERE terra_user_id = :uid"),
                {"uid": terra_user_id},
            )
            await session.commit()

    async def link_statuses(self, patient_id: str, now: datetime) -> list[WearableLinkStatus]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT terra_user_id, provider, connected_at, last_payload_at, active, scopes
                    FROM wearable_links
                    WHERE patient_id = :patient_id
                    ORDER BY connected_at
                    """
                ),
                {"patient_id": patient_id},
            )
            statuses: list[WearableLinkStatus] = []
            for row in result.mappings():
                provider = Provider.parse(row["provider"])
                if provider is None:
                    continue
                last = row["last_payload_at"]
                statuses.append(
                    WearableLinkStatus(
                        terra_user_id=row["terra_user_id"],
                        provider=provider,
                        connected_at=row["connected_at"],
                        last_payload_at=last,
                        active=row["active"],
                        scopes=list(row["scopes"] or []),
                        staleness_s=int((now - last).total_seconds()) if last else None,
                    )
                )
            return statuses

    # -- writes ------------------------------------------------------------

    async def store_samples(self, patient_id: str, samples: list[NormalizedSample]) -> int:
        if not samples:
            return 0

        rows = [
            {
                "time": s.timestamp,
                "patient_id": patient_id,
                "metric": s.metric.value,
                "value": s.value,
                "unit": s.unit,
                "source_provider": s.provider.value,
                "terra_user_id": s.terra_user_id,
                "confidence": s.confidence,
            }
            for s in samples
        ]

        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO biometric_samples
                        (time, patient_id, metric, value, unit, source_provider, terra_user_id, confidence)
                    VALUES
                        (:time, :patient_id, :metric, :value, :unit, :source_provider, :terra_user_id, :confidence)
                    """
                ),
                rows,
            )
            await session.commit()
        return len(rows)

    async def store_sleep_sessions(
        self, patient_id: str, sessions: list[NormalizedSleepSession]
    ) -> int:
        rows: list[dict[str, Any]] = []
        for session_obj in sessions:
            for segment in session_obj.segments:
                rows.append(
                    {
                        "time": segment.start,
                        "end_time": segment.end,
                        "patient_id": patient_id,
                        "stage": segment.stage.value,
                        "duration_s": segment.duration_s,
                        "source_provider": segment.provider.value,
                        "terra_user_id": segment.terra_user_id,
                    }
                )

        if not rows:
            return 0

        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO sleep_segments
                        (time, end_time, patient_id, stage, duration_s, source_provider, terra_user_id)
                    VALUES
                        (:time, :end_time, :patient_id, :stage, :duration_s, :source_provider, :terra_user_id)
                    """
                ),
                rows,
            )
            await session.commit()
        return len(sessions)

    async def touch_link(self, terra_user_id: str, when: datetime) -> None:
        async with self.session() as session:
            await session.execute(
                text(
                    "UPDATE wearable_links SET last_payload_at = :when WHERE terra_user_id = :uid"
                ),
                {"when": when, "uid": terra_user_id},
            )
            await session.commit()

    async def store_profile(
        self,
        patient_id: str,
        profile_json: dict[str, Any],
        *,
        window_start: datetime,
        window_end: datetime,
        completeness: float,
    ) -> None:
        # asyncpg binds a Python dict as a record, not as JSON, so the payload
        # is serialized here and cast on the server side.
        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO circadian_profiles
                        (patient_id, window_start, window_end, payload, data_completeness)
                    VALUES
                        (:patient_id, :window_start, :window_end, CAST(:payload AS JSONB), :completeness)
                    """
                ),
                {
                    "patient_id": patient_id,
                    "payload": json.dumps(profile_json, default=str),
                    "window_start": window_start,
                    "window_end": window_end,
                    "completeness": completeness,
                },
            )
            await session.commit()

    # -- reads -------------------------------------------------------------

    async def load_samples(
        self, patient_id: str, window_start: datetime, window_end: datetime
    ) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT time, metric, value, unit, source_provider, terra_user_id, confidence
                    FROM biometric_samples
                    WHERE patient_id = :patient_id
                      AND time BETWEEN :start AND :end
                    ORDER BY time
                    """
                ),
                {"patient_id": patient_id, "start": window_start, "end": window_end},
            )
            return [dict(row) for row in result.mappings()]

    async def load_sleep_segments(
        self, patient_id: str, window_start: datetime, window_end: datetime
    ) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT time, end_time, stage, duration_s, source_provider, terra_user_id
                    FROM sleep_segments
                    WHERE patient_id = :patient_id
                      AND time BETWEEN :start AND :end
                    ORDER BY time
                    """
                ),
                {"patient_id": patient_id, "start": window_start, "end": window_end},
            )
            return [dict(row) for row in result.mappings()]

    async def patient_timezone(self, patient_id: str) -> str:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT timezone FROM patients WHERE id = :id"), {"id": patient_id}
            )
            row = result.first()
            return str(row[0]) if row else "UTC"

"""Database access for the Engine service."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.schemas import AdaptiveSchedule, DrugClass, Medication, PhaseEstimate

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

    # -- patients and regimens --------------------------------------------

    async def patient_timezone(self, patient_id: str) -> str:
        async with self.session() as session:
            result = await session.execute(
                text("SELECT timezone FROM patients WHERE id = :id"), {"id": patient_id}
            )
            row = result.first()
            return str(row[0]) if row else "UTC"

    async def active_medications(self, patient_id: str) -> list[Medication]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, patient_id, display_name, drug_class, dose_amount,
                           dose_unit, doses_per_day, rxnorm_code, active
                    FROM medications
                    WHERE patient_id = :patient_id AND active = TRUE
                    ORDER BY display_name
                    """
                ),
                {"patient_id": patient_id},
            )
            medications: list[Medication] = []
            for row in result.mappings():
                try:
                    drug_class = DrugClass(row["drug_class"])
                except ValueError:
                    drug_class = DrugClass.UNCLASSIFIED
                medications.append(
                    Medication(
                        id=str(row["id"]),
                        patient_id=str(row["patient_id"]),
                        display_name=row["display_name"],
                        drug_class=drug_class,
                        dose_amount=float(row["dose_amount"]),
                        dose_unit=row["dose_unit"],
                        doses_per_day=int(row["doses_per_day"]),
                        rxnorm_code=row["rxnorm_code"],
                        active=row["active"],
                    )
                )
            return medications

    # -- phase history -----------------------------------------------------

    async def store_phase_estimate(self, estimate: PhaseEstimate) -> None:
        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO phase_estimates
                        (time, patient_id, phase_offset_min, dlmo_estimate, amplitude,
                         stability, confidence, method_version)
                    VALUES
                        (:time, :patient_id, :offset, :dlmo, :amplitude,
                         :stability, :confidence, :method_version)
                    """
                ),
                {
                    "time": estimate.computed_at,
                    "patient_id": estimate.patient_id,
                    "offset": estimate.phase_offset_min,
                    "dlmo": estimate.dlmo_estimate,
                    "amplitude": estimate.amplitude,
                    "stability": estimate.stability,
                    "confidence": estimate.confidence,
                    "method_version": estimate.method_version,
                },
            )
            await session.commit()

    async def phase_history(
        self, patient_id: str, since: datetime
    ) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT time, phase_offset_min, dlmo_estimate, amplitude,
                           stability, confidence, method_version
                    FROM phase_estimates
                    WHERE patient_id = :patient_id AND time >= :since
                    ORDER BY time
                    """
                ),
                {"patient_id": patient_id, "since": since},
            )
            return [dict(row) for row in result.mappings()]

    # -- schedules ---------------------------------------------------------

    async def store_schedule(self, schedule: AdaptiveSchedule) -> None:
        """Persist a schedule and mark the one it replaces as superseded.

        Both statements run in one transaction. A committed new schedule beside
        a still current old one would give the reminder queue two sources of
        truth for the same dose.
        """
        payload = json.dumps(schedule.model_dump(mode="json"), default=str)

        async with self.session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO adaptive_schedules
                        (id, patient_id, generated_at, valid_from, valid_until,
                         phase_offset_min, schedule_version, payload)
                    VALUES
                        (CAST(:id AS UUID), :patient_id, :generated_at, :valid_from, :valid_until,
                         :phase_offset, :version, CAST(:payload AS JSONB))
                    """
                ),
                {
                    "id": schedule.schedule_id,
                    "patient_id": schedule.patient_id,
                    "generated_at": schedule.generated_at,
                    "valid_from": schedule.valid_from,
                    "valid_until": schedule.valid_until,
                    "phase_offset": schedule.phase.phase_offset_min,
                    "version": schedule.schedule_version,
                    "payload": payload,
                },
            )

            if schedule.supersedes:
                await session.execute(
                    text(
                        "UPDATE adaptive_schedules SET superseded_by = CAST(:new AS UUID) "
                        "WHERE id = CAST(:old AS UUID)"
                    ),
                    {"new": schedule.schedule_id, "old": schedule.supersedes},
                )

            dose_rows = [
                {
                    "id": entry.entry_id,
                    "schedule_id": schedule.schedule_id,
                    "patient_id": schedule.patient_id,
                    "medication_id": entry.medication_id,
                    "window_start": entry.window.start,
                    "window_end": entry.window.end,
                    "target_time": entry.window.target,
                    "status": entry.status.value,
                }
                for entry in schedule.entries
            ]
            if dose_rows:
                await session.execute(
                    text(
                        """
                        INSERT INTO dose_events
                            (id, schedule_id, patient_id, medication_id,
                             window_start, window_end, target_time, status)
                        VALUES
                            (CAST(:id AS UUID), CAST(:schedule_id AS UUID), CAST(:patient_id AS UUID),
                             CAST(:medication_id AS UUID), :window_start, :window_end,
                             :target_time, :status)
                        """
                    ),
                    dose_rows,
                )

            await session.commit()

    async def latest_schedule_id(self, patient_id: str) -> str | None:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id FROM adaptive_schedules
                    WHERE patient_id = :patient_id AND superseded_by IS NULL
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """
                ),
                {"patient_id": patient_id},
            )
            row = result.first()
            return str(row[0]) if row else None

    async def latest_schedule(self, patient_id: str) -> dict[str, Any] | None:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT payload FROM adaptive_schedules
                    WHERE patient_id = :patient_id AND superseded_by IS NULL
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """
                ),
                {"patient_id": patient_id},
            )
            row = result.first()
            if not row:
                return None
            payload = row[0]
            return json.loads(payload) if isinstance(payload, str) else payload

    async def record_dose(self, entry_id: str, status: str, taken_at: datetime | None) -> bool:
        async with self.session() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE dose_events
                    SET status = :status, taken_at = :taken_at, recorded_by = 'patient'
                    WHERE id = CAST(:entry_id AS UUID)
                    """
                ),
                {"status": status, "taken_at": taken_at, "entry_id": entry_id},
            )
            await session.commit()
            return result.rowcount > 0

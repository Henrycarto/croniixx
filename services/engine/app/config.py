"""Runtime configuration for the Engine service."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "croniixx-engine"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://croniixx:croniixx_local@postgres:5432/croniixx"
    redis_url: str = "redis://redis:6379/0"

    sync_service_url: str = "http://sync:8001"
    reminder_service_url: str = "http://reminder-api:8003"

    # Shared with the reminder API so it can tell an internal caller from an
    # arbitrary one. Same value as JWT_SECRET in the compose environment.
    jwt_secret: str = "change_me_in_every_environment"

    # A schedule is regenerated rather than extended. Twenty six hours means
    # the next generation always overlaps the previous one, so a patient never
    # sees a gap if a regeneration is late.
    schedule_horizon_hours: int = 26

    # Below this the Engine returns the schedule but marks every window
    # provisional, because the phase estimate underneath it is thin.
    min_profile_completeness: float = 0.5

    # A phase estimate older than this is stale enough that a shift worker or a
    # patient who has travelled would already be somewhere else.
    phase_estimate_ttl_hours: int = 36

    @property
    def redis_schedule_ttl_seconds(self) -> int:
        return self.schedule_horizon_hours * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()

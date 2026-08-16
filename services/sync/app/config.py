"""Runtime configuration for the Sync service."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "croniixx-sync"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://croniixx:croniixx_local@postgres:5432/croniixx"
    redis_url: str = "redis://redis:6379/0"

    terra_dev_id: str = ""
    terra_api_key: str = ""
    terra_signing_secret: str = ""
    terra_base_url: str = "https://api.tryterra.co/v2"

    engine_service_url: str = "http://engine:8002"

    # Terra replays webhooks on non 2xx responses. A short window is enough to
    # catch the replay storm without holding every payload id forever.
    webhook_replay_window_seconds: int = 900

    # Terra rejects a signature whose timestamp is too far from server time.
    # We apply the same rule so a captured payload cannot be replayed later.
    signature_tolerance_seconds: int = 300

    profile_window_days: int = 14

    @property
    def terra_configured(self) -> bool:
        return bool(self.terra_dev_id and self.terra_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

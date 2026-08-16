"""Runtime configuration for the reminder API."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "croniixx-reminder-api"
    log_level: str = "info"

    redis_url: str = "redis://redis:6379/0"

    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    expo_receipt_url: str = "https://exp.host/--/api/v2/push/getReceipts"
    expo_access_token: str = ""
    # Expo accepts up to 100 messages per request. Larger batches are rejected
    # whole, so a patient with a busy regimen would lose every reminder at once.
    expo_batch_size: int = 100

    jwt_secret: str = "change_me_in_every_environment"
    jwt_issuer: str = "croniixx"
    jwt_audience: str = "croniixx-mobile"
    jwt_algorithm: str = "HS256"

    # How often the dispatcher scans for due reminders. Fifteen seconds keeps
    # delivery inside the resolution a dosing window needs without polling
    # Redis harder than the queue depth justifies.
    dispatch_interval_seconds: int = 15

    # A claimed reminder that is not acknowledged within this returns to the
    # queue. A dispatcher that dies mid batch must not swallow a dose.
    claim_visibility_seconds: int = 120

    max_delivery_attempts: int = 4

    # Reminders more than this far past their fire time are dropped rather than
    # delivered. A dose alert arriving an hour late is worse than none: it can
    # prompt a dose inside a window that has already closed.
    max_lateness_seconds: int = 900

    @property
    def expo_configured(self) -> bool:
        # Expo accepts unauthenticated sends for many projects, so the absence
        # of a token is not by itself a misconfiguration.
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Pydantic models for Terra payloads and the normalized internal format."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Provider(str, Enum):
    """Terra provider slugs we accept.

    Terra exposes several dozen providers. We list only the four the clinical
    protocol validates against, because a device we have not characterised
    would enter the phase model with an unknown error profile.
    """

    OURA = "OURA"
    APPLE = "APPLE"
    GARMIN = "GARMIN"
    WHOOP = "WHOOP"

    @classmethod
    def parse(cls, raw: str | None) -> "Provider | None":
        if not raw:
            return None
        candidate = raw.strip().upper()
        aliases = {
            "OURA": cls.OURA,
            "OURARING": cls.OURA,
            "APPLE": cls.APPLE,
            "APPLE_HEALTH": cls.APPLE,
            "APPLEHEALTH": cls.APPLE,
            "HEALTHKIT": cls.APPLE,
            "GARMIN": cls.GARMIN,
            "WHOOP": cls.WHOOP,
        }
        return aliases.get(candidate)


class Metric(str, Enum):
    """Canonical metric names used everywhere downstream of the normalizer."""

    HRV_RMSSD = "hrv_rmssd"
    HRV_SDNN = "hrv_sdnn"
    RESTING_HR = "resting_hr"
    HEART_RATE = "heart_rate"
    SKIN_TEMP_DELTA = "skin_temp_delta"
    RESPIRATION_RATE = "respiration_rate"
    SPO2 = "spo2"
    STEPS = "steps"
    ACTIVITY_MET = "activity_met"


class SleepStage(str, Enum):
    """Canonical sleep stages.

    UNMEASURABLE exists because Apple pre watchOS 9 and Garmin devices without
    a wrist optical sensor report time in bed with no staging. Dropping those
    rows would bias sleep midpoint toward nights that happened to stage well.
    """

    AWAKE = "awake"
    LIGHT = "light"
    DEEP = "deep"
    REM = "rem"
    UNMEASURABLE = "unmeasurable"


class TerraWebhookType(str, Enum):
    SLEEP = "sleep"
    ACTIVITY = "activity"
    DAILY = "daily"
    BODY = "body"
    ATHLETE = "athlete"
    AUTH = "auth"
    DEAUTH = "deauth"
    USER_REAUTH = "user_reauth"
    CONNECTION_ERROR = "connection_error"
    REQUEST_PROCESSING = "request_processing"
    LARGE_REQUEST_SENDING = "large_request_sending"


class TerraUser(BaseModel):
    user_id: str
    provider: str | None = None
    reference_id: str | None = None
    scopes: str | None = None
    last_webhook_update: datetime | None = None


class TerraWebhookPayload(BaseModel):
    """Loose model over the Terra webhook body.

    Terra ships new optional fields without a version bump, so every data block
    stays as a raw dict and the normalizer decides what it understands. A strict
    model here would drop payloads on a vendor side field addition.
    """

    type: str
    user: TerraUser | None = None
    data: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None
    message: str | None = None

    @field_validator("data", mode="before")
    @classmethod
    def coerce_data(cls, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []


# ---------------------------------------------------------------------------
# Normalized internal format
# ---------------------------------------------------------------------------


class NormalizedSample(BaseModel):
    """One biometric reading after cross device normalization."""

    timestamp: datetime
    metric: Metric
    value: float
    unit: str
    provider: Provider
    terra_user_id: str | None = None
    # Confidence encodes how directly the provider measures this metric. An
    # Oura nightly rMSSD is measured; an Apple rMSSD derived from SDNN is not.
    confidence: float = 1.0


class NormalizedSleepSegment(BaseModel):
    start: datetime
    end: datetime
    stage: SleepStage
    duration_s: int
    provider: Provider
    terra_user_id: str | None = None


class NormalizedSleepSession(BaseModel):
    start: datetime
    end: datetime
    provider: Provider
    terra_user_id: str | None = None
    segments: list[NormalizedSleepSegment] = Field(default_factory=list)
    efficiency: float | None = None
    latency_s: int | None = None
    awakenings: int | None = None
    is_nap: bool = False


class NormalizedBatch(BaseModel):
    """What the normalizer hands to storage and the profile builder."""

    provider: Provider
    terra_user_id: str | None = None
    samples: list[NormalizedSample] = Field(default_factory=list)
    sleep_sessions: list[NormalizedSleepSession] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.samples and not self.sleep_sessions


# ---------------------------------------------------------------------------
# Circadian profile
# ---------------------------------------------------------------------------


class SleepTimingSummary(BaseModel):
    nights_observed: int
    mean_onset_hour: float | None = None
    mean_offset_hour: float | None = None
    mean_midsleep_hour: float | None = None
    midsleep_variability_h: float | None = None
    mean_duration_h: float | None = None
    mean_efficiency: float | None = None
    deep_fraction: float | None = None
    rem_fraction: float | None = None
    # Timing of the first REM episode relative to onset. REM pressure tracks
    # the circadian oscillator more tightly than total sleep time does.
    mean_first_rem_latency_min: float | None = None


class CosinorFit(BaseModel):
    """Single component cosinor fit over a 24 hour period."""

    mesor: float
    amplitude: float
    acrophase_hour: float
    r_squared: float
    n_points: int


class ActigraphySummary(BaseModel):
    """Nonparametric actigraphy measures.

    These are the standard published measures (IS, IV, L5, M10, RA). They are
    descriptive, not proprietary. The proprietary step is how they are weighted
    into a phase offset, which lives in the Engine service.
    """

    interdaily_stability: float | None = None
    intradaily_variability: float | None = None
    l5_onset_hour: float | None = None
    l5_mean: float | None = None
    m10_onset_hour: float | None = None
    m10_mean: float | None = None
    relative_amplitude: float | None = None


class MetricCoverage(BaseModel):
    metric: Metric
    sample_count: int
    days_covered: int
    mean_confidence: float


class CircadianProfile(BaseModel):
    """The unified profile. This is the Sync service's only real output.

    Everything in here is observable and reproducible from the raw wearable
    record. No phase judgement is made at this layer.
    """

    patient_id: str
    window_start: datetime
    window_end: datetime
    providers: list[Provider]
    sleep: SleepTimingSummary
    actigraphy: ActigraphySummary
    hrv_cosinor: CosinorFit | None = None
    temperature_cosinor: CosinorFit | None = None
    activity_cosinor: CosinorFit | None = None
    resting_hr_cosinor: CosinorFit | None = None
    coverage: list[MetricCoverage] = Field(default_factory=list)
    data_completeness: float
    warnings: list[str] = Field(default_factory=list)


class IngestAck(BaseModel):
    accepted: bool
    payload_type: str
    provider: Provider | None = None
    terra_user_id: str | None = None
    samples_stored: int = 0
    sleep_sessions_stored: int = 0
    warnings: list[str] = Field(default_factory=list)


class WearableLinkStatus(BaseModel):
    terra_user_id: str
    provider: Provider
    connected_at: datetime
    last_payload_at: datetime | None = None
    active: bool
    scopes: list[str] = Field(default_factory=list)
    # Seconds since the last payload. The web dashboard turns this into the
    # amber drift warning when a device goes quiet mid regimen.
    staleness_s: int | None = None

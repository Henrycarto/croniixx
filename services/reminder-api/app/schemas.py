"""Reminder API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ReminderKind(str, Enum):
    """Why a reminder is firing.

    Three kinds rather than one because a dosing window has three moments that
    matter, and a patient who ignores the first needs a different message at
    the third than a repeat of the same line.
    """

    WINDOW_OPEN = "window_open"
    TARGET = "target"
    WINDOW_CLOSING = "window_closing"


class ReminderState(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DevicePlatform(str, Enum):
    IOS = "ios"
    ANDROID = "android"


class Reminder(BaseModel):
    """One scheduled notification."""

    reminder_id: str = Field(default_factory=lambda: str(uuid4()))
    patient_id: str
    schedule_id: str
    entry_id: str
    medication_id: str
    kind: ReminderKind
    fire_at: datetime
    window_start: datetime
    window_end: datetime
    target: datetime
    display_name: str
    dose_amount: float
    dose_unit: str
    window_status: str = "optimal"
    timezone: str = "UTC"
    state: ReminderState = ReminderState.QUEUED
    attempts: int = 0

    def title(self) -> str:
        if self.kind is ReminderKind.WINDOW_OPEN:
            return f"{self.display_name} window is open"
        if self.kind is ReminderKind.WINDOW_CLOSING:
            return f"{self.display_name} window closing"
        return f"Take {self.display_name}"

    def body(self) -> str:
        dose = f"{_trim(self.dose_amount)} {self.dose_unit}"
        if self.kind is ReminderKind.WINDOW_OPEN:
            return f"{dose}. Best moment is coming up."
        if self.kind is ReminderKind.WINDOW_CLOSING:
            return f"{dose}. The optimal window closes shortly."
        return f"{dose}. This is the calculated moment for your circadian phase."


class DeviceRegistration(BaseModel):
    patient_id: str
    expo_push_token: str
    platform: DevicePlatform
    app_version: str | None = None

    @field_validator("expo_push_token")
    @classmethod
    def check_token_shape(cls, value: str) -> str:
        # Rejecting a malformed token here keeps it out of the batch. Expo
        # fails an entire request on one bad token, so one typo would silence
        # every other patient in the same batch.
        if not (value.startswith("ExponentPushToken[") or value.startswith("ExpoPushToken[")):
            raise ValueError("Not a valid Expo push token")
        return value


class SchedulePush(BaseModel):
    """The compact schedule form the Engine sends."""

    schedule_id: str
    patient_id: str
    timezone: str = "UTC"
    valid_until: datetime
    doses: list[dict[str, Any]] = Field(default_factory=list)


class AckRequest(BaseModel):
    reminder_id: str
    acknowledged_at: datetime | None = None


class QueueStats(BaseModel):
    queued: int
    claimed: int
    due_now: int
    patients_with_reminders: int


def _trim(value: float) -> str:
    return f"{value:g}"

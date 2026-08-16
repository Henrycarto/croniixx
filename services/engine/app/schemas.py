"""Engine schemas.

The adaptive schedule object defined here is the contract between the Engine,
the web dashboard, the mobile app, and the reminder queue. It is fully
specified even though the coefficients that populate some of its fields are
proprietary, because every consumer of the schedule has to be buildable and
testable without those coefficients.

The defining property of the object: a schedule entry stores a window and an
anchor, never a bare wall clock time. A wall clock time is a snapshot of a
calculation. An anchor plus an offset is the calculation itself, and it stays
correct when the patient's phase moves under it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class PhaseAnchor(str, Enum):
    """Biological reference points a dose window can be pinned to.

    DLMO is the reference standard for circadian phase in humans. The others
    are proxies available when melatonin sampling is not, ordered here roughly
    by how tightly they track the central oscillator.
    """

    DLMO = "dlmo"
    MIDSLEEP = "midsleep"
    SLEEP_ONSET = "sleep_onset"
    WAKE = "wake"
    ACTIVITY_ACROPHASE = "activity_acrophase"
    TEMPERATURE_NADIR = "temperature_nadir"
    CLOCK_TIME = "clock_time"


class DrugClass(str, Enum):
    """Drug classes with a documented chronopharmacological profile.

    A class is listed here only when there is a timing dependence to model. A
    drug whose effect does not vary across the day belongs on a fixed clock
    schedule and should not be routed through this engine at all.
    """

    CORTICOSTEROID = "corticosteroid"
    ANTIHYPERTENSIVE = "antihypertensive"
    STATIN = "statin"
    CHEMOTHERAPY_ANTIMETABOLITE = "chemotherapy_antimetabolite"
    CHEMOTHERAPY_PLATINUM = "chemotherapy_platinum"
    CHEMOTHERAPY_TOPOISOMERASE = "chemotherapy_topoisomerase"
    IMMUNOSUPPRESSANT = "immunosuppressant"
    THYROID_REPLACEMENT = "thyroid_replacement"
    PROTON_PUMP_INHIBITOR = "proton_pump_inhibitor"
    ANTICOAGULANT = "anticoagulant"
    BRONCHODILATOR = "bronchodilator"
    NSAID = "nsaid"
    CHRONOBIOTIC = "chronobiotic"
    UNCLASSIFIED = "unclassified"


class WindowStatus(str, Enum):
    OPTIMAL = "optimal"
    ACCEPTABLE = "acceptable"
    SUBOPTIMAL = "suboptimal"
    CONTRAINDICATED = "contraindicated"


class DoseStatus(str, Enum):
    PENDING = "pending"
    TAKEN = "taken"
    MISSED = "missed"
    SKIPPED = "skipped"
    RESCHEDULED = "rescheduled"


class CoefficientSource(str, Enum):
    """Which model produced the numbers in a response.

    Present on every phase estimate and every schedule so that a result can
    never be mistaken for a clinically validated one when it is not.
    """

    PRIVATE_VALIDATED = "private_validated"
    REFERENCE_FALLBACK = "reference_fallback"


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------


class PhaseEstimate(BaseModel):
    """A patient's circadian position at a moment in time.

    phase_offset_min is the signed distance between this patient's circadian
    position and the population reference for their clock time. Positive means
    delayed: their biological night starts later than the calendar suggests.
    Negative means advanced.
    """

    patient_id: str
    computed_at: datetime
    phase_offset_min: int
    dlmo_estimate: datetime | None = None
    amplitude: float | None = None
    stability: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    method_version: str
    coefficient_source: CoefficientSource
    inputs_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def direction(self) -> str:
        if self.phase_offset_min > 30:
            return "delayed"
        if self.phase_offset_min < -30:
            return "advanced"
        return "aligned"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def offset_display(self) -> str:
        """Signed hours and minutes, the form a chronobiologist reads.

        Rendered here rather than in the frontend so the dashboard and the
        mobile app cannot disagree about the sign convention.
        """
        sign = "+" if self.phase_offset_min >= 0 else "-"
        magnitude = abs(self.phase_offset_min)
        return f"{sign}{magnitude // 60:02d}:{magnitude % 60:02d}"


class PhaseDrift(BaseModel):
    """Movement of the phase estimate against a baseline."""

    patient_id: str
    baseline_offset_min: int
    current_offset_min: int
    drift_min: int
    window_days: int
    # Drift past this raises the amber alert on the dashboard. A shift of an
    # hour or more moves most timing windows past their own width.
    alert_threshold_min: int = 60

    @computed_field  # type: ignore[prop-decorator]
    @property
    def alerting(self) -> bool:
        return abs(self.drift_min) >= self.alert_threshold_min


# ---------------------------------------------------------------------------
# Medication and timing
# ---------------------------------------------------------------------------


class Medication(BaseModel):
    id: str
    patient_id: str
    display_name: str
    drug_class: DrugClass
    dose_amount: float
    dose_unit: str
    doses_per_day: int = Field(default=1, ge=1, le=6)
    rxnorm_code: str | None = None
    active: bool = True
    # A clinician can pin a drug to a fixed clock time when a protocol demands
    # it. The engine then reports the biological cost of that choice instead of
    # silently overriding it.
    fixed_clock_time: str | None = None


class TimingWindow(BaseModel):
    """A timing rule expressed relative to a biological anchor.

    offset_min is measured from the anchor. Negative is before the anchor.
    """

    anchor: PhaseAnchor
    offset_min: int
    duration_min: int = Field(gt=0)
    status: WindowStatus = WindowStatus.OPTIMAL
    # Where inside the window the target sits, as a fraction of its width.
    # Most windows target the centre; a window that opens sharply and decays
    # slowly targets its early edge.
    target_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_offset_min(self) -> int:
        return int(self.offset_min + self.duration_min * self.target_fraction)


class DrugTimingProfile(BaseModel):
    """Every window that applies to one drug class for one dose of the day."""

    drug_class: DrugClass
    dose_index: int = Field(ge=0)
    optimal: TimingWindow
    acceptable: list[TimingWindow] = Field(default_factory=list)
    contraindicated: list[TimingWindow] = Field(default_factory=list)
    coefficient_source: CoefficientSource
    evidence_note: str = ""


# ---------------------------------------------------------------------------
# The adaptive schedule object
# ---------------------------------------------------------------------------


class ResolvedWindow(BaseModel):
    """A timing window after the anchor has been resolved to real timestamps."""

    start: datetime
    end: datetime
    target: datetime
    status: WindowStatus
    anchor: PhaseAnchor
    anchor_offset_min: int
    rationale: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_min(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @model_validator(mode="after")
    def check_ordering(self) -> "ResolvedWindow":
        if self.end <= self.start:
            raise ValueError("Window end must be after window start")
        if not self.start <= self.target <= self.end:
            raise ValueError("Window target must fall inside the window")
        return self


class ScheduleEntry(BaseModel):
    """One dose of one drug, placed against the patient's own clock."""

    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    medication_id: str
    display_name: str
    drug_class: DrugClass
    rxnorm_code: str | None = None
    dose_amount: float
    dose_unit: str
    dose_index: int = Field(ge=0)

    window: ResolvedWindow
    alternate_windows: list[ResolvedWindow] = Field(default_factory=list)
    avoid_windows: list[ResolvedWindow] = Field(default_factory=list)

    status: DoseStatus = DoseStatus.PENDING
    confidence: float = Field(ge=0.0, le=1.0)

    # The wall clock time a conventional printed schedule would have given for
    # this dose. Kept so the interface can show the difference rather than
    # asserting it, which is the entire clinical argument in one number.
    conventional_time: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def drift_from_conventional_min(self) -> int | None:
        if self.conventional_time is None:
            return None
        return int((self.window.target - self.conventional_time).total_seconds() // 60)

    @field_validator("dose_amount")
    @classmethod
    def dose_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Dose amount must be positive")
        return value


class ScheduleMeta(BaseModel):
    profile_completeness: float
    phase_confidence: float
    coefficient_source: CoefficientSource
    method_version: str
    provisional: bool = False
    warnings: list[str] = Field(default_factory=list)


class AdaptiveSchedule(BaseModel):
    """The complete object handed to the dashboard, the app, and the queue.

    Superseding rather than editing is deliberate. A schedule is the output of
    one phase estimate, and patching a single entry from a newer estimate would
    produce a schedule whose entries disagree about what time the patient's
    body thinks it is.
    """

    schedule_id: str = Field(default_factory=lambda: str(uuid4()))
    patient_id: str
    generated_at: datetime
    valid_from: datetime
    valid_until: datetime
    schedule_version: int = 1
    supersedes: str | None = None
    timezone: str = "UTC"

    phase: PhaseEstimate
    entries: list[ScheduleEntry] = Field(default_factory=list)
    meta: ScheduleMeta

    @computed_field  # type: ignore[prop-decorator]
    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def next_dose_at(self) -> datetime | None:
        pending = [
            e.window.target for e in self.entries if e.status is DoseStatus.PENDING
        ]
        return min(pending) if pending else None

    @model_validator(mode="after")
    def check_validity_window(self) -> "AdaptiveSchedule":
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self

    def entries_in_order(self) -> list[ScheduleEntry]:
        return sorted(self.entries, key=lambda e: e.window.target)

    def to_reminder_payload(self) -> dict[str, Any]:
        """Compact form for the reminder queue.

        The queue needs when to fire and what to say. Shipping the full object
        into Redis would put an entire clinical record in a cache tier for no
        operational benefit.
        """
        return {
            "schedule_id": self.schedule_id,
            "patient_id": self.patient_id,
            "timezone": self.timezone,
            "valid_until": self.valid_until.isoformat(),
            "doses": [
                {
                    "entry_id": entry.entry_id,
                    "medication_id": entry.medication_id,
                    "display_name": entry.display_name,
                    "dose_amount": entry.dose_amount,
                    "dose_unit": entry.dose_unit,
                    "target": entry.window.target.isoformat(),
                    "window_start": entry.window.start.isoformat(),
                    "window_end": entry.window.end.isoformat(),
                    "status": entry.window.status.value,
                }
                for entry in self.entries_in_order()
            ],
        }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CircadianProfileInput(BaseModel):
    """The Sync service profile, accepted loosely.

    Only the fields the phase estimator reads are named. The rest passes
    through untouched so a new descriptor added in Sync does not require an
    Engine release before it can be used.
    """

    patient_id: str
    window_start: datetime
    window_end: datetime
    sleep: dict[str, Any] = Field(default_factory=dict)
    actigraphy: dict[str, Any] = Field(default_factory=dict)
    hrv_cosinor: dict[str, Any] | None = None
    temperature_cosinor: dict[str, Any] | None = None
    activity_cosinor: dict[str, Any] | None = None
    resting_hr_cosinor: dict[str, Any] | None = None
    data_completeness: float = 0.0
    providers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScheduleRequest(BaseModel):
    patient_id: str
    profile: CircadianProfileInput
    medications: list[Medication]
    timezone: str = "UTC"
    horizon_hours: int | None = Field(default=None, ge=6, le=168)
    supersedes: str | None = None
    reference_time: datetime | None = None


class PhaseRequest(BaseModel):
    profile: CircadianProfileInput
    timezone: str = "UTC"


def clamp_window(window: ResolvedWindow, floor: datetime, ceiling: datetime) -> ResolvedWindow | None:
    """Trim a window to a horizon, dropping it if nothing survives.

    A window that only partly overlaps the horizon is kept and trimmed rather
    than dropped, because the patient still has a real dose inside it.
    """
    start = max(window.start, floor)
    end = min(window.end, ceiling)
    if end - start < timedelta(minutes=1):
        return None
    target = min(max(window.target, start), end)
    return window.model_copy(update={"start": start, "end": end, "target": target})

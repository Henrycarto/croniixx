"""Shared fixtures for Engine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import CircadianProfileInput, DrugClass, Medication

REFERENCE_NOW = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)


def make_profile(
    *,
    patient_id: str = "patient-1",
    onset: float = 23.0,
    offset: float = 7.0,
    midsleep: float | None = 3.0,
    nights: int = 12,
    completeness: float = 0.9,
    variability: float | None = 0.4,
) -> CircadianProfileInput:
    return CircadianProfileInput(
        patient_id=patient_id,
        window_start=REFERENCE_NOW - timedelta(days=14),
        window_end=REFERENCE_NOW,
        sleep={
            "nights_observed": nights,
            "mean_onset_hour": onset,
            "mean_offset_hour": offset,
            "mean_midsleep_hour": midsleep,
            "midsleep_variability_h": variability,
            "mean_duration_h": (offset - onset) % 24,
        },
        actigraphy={
            "interdaily_stability": 0.62,
            "intradaily_variability": 0.71,
            "l5_onset_hour": 0.0,
            "m10_onset_hour": 11.0,
            "relative_amplitude": 0.86,
        },
        hrv_cosinor={"mesor": 44.0, "amplitude": 11.0, "acrophase_hour": 3.6, "r_squared": 0.71},
        activity_cosinor={"mesor": 210.0, "amplitude": 180.0, "acrophase_hour": 15.0, "r_squared": 0.64},
        temperature_cosinor={"mesor": 0.0, "amplitude": 0.3, "acrophase_hour": 5.0, "r_squared": 0.55},
        data_completeness=completeness,
        providers=["OURA"],
    )


def make_medication(
    drug_class: DrugClass,
    *,
    med_id: str = "med-1",
    name: str = "Test agent",
    doses_per_day: int = 1,
    fixed_clock_time: str | None = None,
) -> Medication:
    return Medication(
        id=med_id,
        patient_id="patient-1",
        display_name=name,
        drug_class=drug_class,
        dose_amount=10.0,
        dose_unit="mg",
        doses_per_day=doses_per_day,
        fixed_clock_time=fixed_clock_time,
    )


@pytest.fixture
def profile() -> CircadianProfileInput:
    return make_profile()


@pytest.fixture
def reference_now() -> datetime:
    return REFERENCE_NOW

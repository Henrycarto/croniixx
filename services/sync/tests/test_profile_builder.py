"""Profile builder tests.

The cosinor and circular statistics are checked against inputs whose correct
answer is known by construction. A phase estimate that is quietly twelve hours
wrong is the failure mode this whole system exists to prevent, so the circular
mean gets the most attention here.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.profile_builder import ProfileBuilder, circular_difference_hours, circular_mean_and_sd
from app.schemas import (
    Metric,
    NormalizedSample,
    NormalizedSleepSegment,
    NormalizedSleepSession,
    Provider,
    SleepStage,
)

WINDOW_END = datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc)
WINDOW_START = WINDOW_END - timedelta(days=14)


def make_sample(when: datetime, metric: Metric, value: float, confidence: float = 1.0):
    return NormalizedSample(
        timestamp=when,
        metric=metric,
        value=value,
        unit="ms",
        provider=Provider.OURA,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Circular statistics
# ---------------------------------------------------------------------------


def test_midnight_straddling_times_average_to_midnight():
    mean, _ = circular_mean_and_sd([23.5, 0.5])
    assert mean == pytest.approx(0.0, abs=1e-6) or mean == pytest.approx(24.0, abs=1e-6)


def test_arithmetic_mean_would_have_been_twelve_hours_wrong():
    hours = [23.0, 23.5, 0.0, 0.5, 1.0]
    circular, _ = circular_mean_and_sd(hours)
    arithmetic = sum(hours) / len(hours)
    assert circular == pytest.approx(0.0, abs=0.1) or circular == pytest.approx(24.0, abs=0.1)
    assert abs(circular_difference_hours(circular, arithmetic)) > 8.0


def test_tight_cluster_has_small_spread():
    _, spread = circular_mean_and_sd([3.0, 3.1, 2.9, 3.05])
    assert spread < 0.2


def test_dispersed_times_have_large_spread():
    _, spread = circular_mean_and_sd([0.0, 6.0, 12.0, 18.0])
    assert spread > 4.0


def test_circular_difference_is_signed_and_shortest():
    assert circular_difference_hours(1.0, 23.0) == pytest.approx(2.0)
    assert circular_difference_hours(23.0, 1.0) == pytest.approx(-2.0)


# ---------------------------------------------------------------------------
# Cosinor
# ---------------------------------------------------------------------------


def test_cosinor_recovers_a_known_acrophase():
    builder = ProfileBuilder("UTC")
    true_acrophase = 4.0
    samples = []
    for day in range(7):
        for hour in range(0, 24, 2):
            when = WINDOW_START + timedelta(days=day, hours=hour)
            value = 45.0 + 12.0 * math.cos(2 * math.pi * (hour - true_acrophase) / 24.0)
            samples.append(make_sample(when, Metric.HRV_RMSSD, value))

    fit = builder.fit_cosinor(samples, Metric.HRV_RMSSD)

    assert fit is not None
    assert fit.mesor == pytest.approx(45.0, abs=0.5)
    assert fit.amplitude == pytest.approx(12.0, abs=0.5)
    assert abs(circular_difference_hours(fit.acrophase_hour, true_acrophase)) < 0.3
    assert fit.r_squared > 0.99


def test_cosinor_declines_to_fit_a_thin_series():
    builder = ProfileBuilder("UTC")
    samples = [
        make_sample(WINDOW_START + timedelta(hours=h), Metric.HRV_RMSSD, 40.0 + h)
        for h in range(5)
    ]
    assert builder.fit_cosinor(samples, Metric.HRV_RMSSD) is None


def test_low_confidence_samples_pull_the_fit_less():
    builder = ProfileBuilder("UTC")
    base = []
    for day in range(7):
        for hour in range(0, 24, 2):
            when = WINDOW_START + timedelta(days=day, hours=hour)
            value = 45.0 + 12.0 * math.cos(2 * math.pi * (hour - 4.0) / 24.0)
            base.append(make_sample(when, Metric.HRV_RMSSD, value))

    # A contradictory series twelve hours out of phase, at low confidence.
    contradictory = []
    for day in range(7):
        for hour in range(0, 24, 2):
            when = WINDOW_START + timedelta(days=day, hours=hour, minutes=30)
            value = 45.0 + 12.0 * math.cos(2 * math.pi * (hour - 16.0) / 24.0)
            contradictory.append(make_sample(when, Metric.HRV_RMSSD, value, confidence=0.2))

    fit = builder.fit_cosinor(base + contradictory, Metric.HRV_RMSSD)
    assert fit is not None
    assert abs(circular_difference_hours(fit.acrophase_hour, 4.0)) < 1.5


# ---------------------------------------------------------------------------
# Sleep summary
# ---------------------------------------------------------------------------


def build_night(start: datetime, hours: float = 8.0, rem_after_min: int = 90):
    end = start + timedelta(hours=hours)
    segments = [
        NormalizedSleepSegment(
            start=start,
            end=start + timedelta(minutes=rem_after_min),
            stage=SleepStage.LIGHT,
            duration_s=rem_after_min * 60,
            provider=Provider.OURA,
        ),
        NormalizedSleepSegment(
            start=start + timedelta(minutes=rem_after_min),
            end=start + timedelta(minutes=rem_after_min + 30),
            stage=SleepStage.REM,
            duration_s=30 * 60,
            provider=Provider.OURA,
        ),
        NormalizedSleepSegment(
            start=start + timedelta(minutes=rem_after_min + 30),
            end=end,
            stage=SleepStage.DEEP,
            duration_s=int((end - start - timedelta(minutes=rem_after_min + 30)).total_seconds()),
            provider=Provider.OURA,
        ),
    ]
    return NormalizedSleepSession(
        start=start, end=end, provider=Provider.OURA, segments=segments, efficiency=0.9
    )


def test_sleep_summary_uses_circular_midsleep():
    builder = ProfileBuilder("UTC")
    nights = [
        build_night(datetime(2026, 3, 1 + d, 23, 0, tzinfo=timezone.utc)) for d in range(5)
    ]
    warnings: list[str] = []
    summary = builder.summarize_sleep(nights, warnings)

    assert summary.nights_observed == 5
    # Onset 23:00 plus four hours of half duration puts midsleep at 03:00.
    assert summary.mean_midsleep_hour == pytest.approx(3.0, abs=0.05)
    assert summary.mean_first_rem_latency_min == pytest.approx(90.0)


def test_naps_are_excluded_from_sleep_timing():
    builder = ProfileBuilder("UTC")
    night = build_night(datetime(2026, 3, 1, 23, 0, tzinfo=timezone.utc))
    nap = build_night(datetime(2026, 3, 2, 13, 0, tzinfo=timezone.utc), hours=1.0, rem_after_min=20)
    nap = nap.model_copy(update={"is_nap": True})

    with_nap = builder.summarize_sleep([night, nap], [])
    without_nap = builder.summarize_sleep([night], [])

    assert with_nap.nights_observed == without_nap.nights_observed == 1
    assert with_nap.mean_midsleep_hour == without_nap.mean_midsleep_hour


def test_empty_input_produces_an_empty_summary_rather_than_an_error():
    builder = ProfileBuilder("UTC")
    summary = builder.summarize_sleep([], [])
    assert summary.nights_observed == 0
    assert summary.mean_midsleep_hour is None


# ---------------------------------------------------------------------------
# Full profile
# ---------------------------------------------------------------------------


def test_profile_reports_low_completeness_on_thin_data():
    builder = ProfileBuilder("UTC")
    profile = builder.build(
        "patient-1",
        [make_sample(WINDOW_START + timedelta(hours=2), Metric.HRV_RMSSD, 40.0)],
        [],
        WINDOW_START,
        WINDOW_END,
    )
    assert profile.data_completeness < 0.5
    assert any("completeness" in w for w in profile.warnings)


def test_profile_with_full_coverage_reports_high_completeness():
    builder = ProfileBuilder("UTC")
    samples: list[NormalizedSample] = []
    for day in range(14):
        for hour in range(0, 24, 2):
            when = WINDOW_START + timedelta(days=day, hours=hour)
            samples.append(
                make_sample(
                    when,
                    Metric.HRV_RMSSD,
                    45.0 + 12.0 * math.cos(2 * math.pi * (hour - 4.0) / 24.0),
                )
            )
            samples.append(
                NormalizedSample(
                    timestamp=when,
                    metric=Metric.STEPS,
                    value=max(0.0, 400.0 * math.cos(2 * math.pi * (hour - 15.0) / 24.0)),
                    unit="count",
                    provider=Provider.OURA,
                )
            )
            samples.append(
                NormalizedSample(
                    timestamp=when,
                    metric=Metric.SKIN_TEMP_DELTA,
                    value=0.2 * math.cos(2 * math.pi * (hour - 5.0) / 24.0),
                    unit="degC",
                    provider=Provider.OURA,
                )
            )

    nights = [
        build_night(WINDOW_START + timedelta(days=d, hours=23)) for d in range(13)
    ]

    profile = builder.build("patient-1", samples, nights, WINDOW_START, WINDOW_END)

    assert profile.data_completeness > 0.85
    assert profile.hrv_cosinor is not None
    assert profile.actigraphy.interdaily_stability is not None
    assert profile.actigraphy.relative_amplitude is not None
    assert Provider.OURA in profile.providers


def test_actigraphy_l5_window_wraps_midnight():
    builder = ProfileBuilder("UTC")
    samples: list[NormalizedSample] = []
    for day in range(7):
        for hour in range(24):
            # Quiet from 23:00 through 03:00, active through the afternoon.
            active = hour not in {23, 0, 1, 2, 3}
            samples.append(
                NormalizedSample(
                    timestamp=WINDOW_START + timedelta(days=day, hours=hour),
                    metric=Metric.STEPS,
                    value=300.0 if active else 2.0,
                    unit="count",
                    provider=Provider.OURA,
                )
            )

    summary = builder.summarize_actigraphy(samples, WINDOW_START, WINDOW_END, [])
    assert summary.l5_onset_hour == 23
    assert summary.relative_amplitude is not None and summary.relative_amplitude > 0.9

"""Assembles the unified circadian profile from normalized wearable data.

Scope boundary: this module produces only measures that are published,
reproducible, and checkable against the raw record. Sleep midpoint, cosinor
fits, and the nonparametric actigraphy set (IS, IV, L5, M10, RA) are all
standard chronobiology instruments with public definitions.

It deliberately stops short of declaring a phase offset. Turning these
descriptors into a phase position, and turning that position into drug timing,
is the Engine service's job and uses coefficients that are not in this repo.
That split is intentional and is described in docs/circadian-methodology.md.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd
import structlog

from app.schemas import (
    ActigraphySummary,
    CircadianProfile,
    CosinorFit,
    Metric,
    MetricCoverage,
    NormalizedSample,
    NormalizedSleepSession,
    Provider,
    SleepStage,
    SleepTimingSummary,
)

log = structlog.get_logger(__name__)

HOURS_PER_DAY = 24.0
OMEGA = 2.0 * math.pi / HOURS_PER_DAY

# A cosinor fit on a handful of points will happily report a confident
# acrophase that is an artefact of the sampling times. Twelve points spread
# across the window is the floor for reporting one at all.
MIN_COSINOR_POINTS = 12

# Nonparametric actigraphy measures assume near continuous coverage. Below
# three days the interdaily term has almost nothing to compare across.
MIN_ACTIGRAPHY_DAYS = 3


class ProfileBuilder:
    """Builds a CircadianProfile for one patient over one time window."""

    def __init__(self, patient_timezone: str = "UTC") -> None:
        self.tz = _load_timezone(patient_timezone)
        self.tz_name = patient_timezone

    # -- public API --------------------------------------------------------

    def build(
        self,
        patient_id: str,
        samples: list[NormalizedSample],
        sleep_sessions: list[NormalizedSleepSession],
        window_start: datetime,
        window_end: datetime,
    ) -> CircadianProfile:
        warnings: list[str] = []

        samples = [s for s in samples if window_start <= s.timestamp <= window_end]
        # A session is attributed to the window by its start, so a night that
        # crosses the window edge is counted once rather than split in half.
        sessions = [s for s in sleep_sessions if window_start <= s.start <= window_end]

        providers = sorted(
            {s.provider for s in samples} | {s.provider for s in sessions},
            key=lambda p: p.value,
        )

        if self.tz_name == "UTC":
            warnings.append(
                "Patient timezone is UTC; sleep timing is reported in UTC and may not "
                "reflect the patient's local clock"
            )

        sleep_summary = self.summarize_sleep(sessions, warnings)
        actigraphy = self.summarize_actigraphy(samples, window_start, window_end, warnings)

        hrv_fit = self.fit_cosinor(samples, Metric.HRV_RMSSD)
        temp_fit = self.fit_cosinor(samples, Metric.SKIN_TEMP_DELTA)
        activity_fit = self.fit_cosinor(samples, Metric.ACTIVITY_MET) or self.fit_cosinor(
            samples, Metric.STEPS
        )
        rhr_fit = self.fit_cosinor(samples, Metric.RESTING_HR) or self.fit_cosinor(
            samples, Metric.HEART_RATE
        )

        coverage = self.metric_coverage(samples)
        window_days = max((window_end - window_start).total_seconds() / 86400.0, 1.0)
        completeness = self.completeness(sleep_summary, coverage, window_days)

        if completeness < 0.5:
            warnings.append(
                f"Data completeness {completeness:.2f} is below the 0.50 threshold; "
                "phase estimates from this profile should be treated as provisional"
            )

        return CircadianProfile(
            patient_id=patient_id,
            window_start=window_start,
            window_end=window_end,
            providers=providers,
            sleep=sleep_summary,
            actigraphy=actigraphy,
            hrv_cosinor=hrv_fit,
            temperature_cosinor=temp_fit,
            activity_cosinor=activity_fit,
            resting_hr_cosinor=rhr_fit,
            coverage=coverage,
            data_completeness=round(completeness, 4),
            warnings=warnings,
        )

    # -- sleep -------------------------------------------------------------

    def summarize_sleep(
        self, sessions: list[NormalizedSleepSession], warnings: list[str]
    ) -> SleepTimingSummary:
        """Summarize sleep timing across the window.

        Naps are excluded. A midday nap has a midpoint near noon, and averaging
        it with a night whose midpoint is near 03:00 produces a midsleep in the
        early evening that describes neither.
        """
        nights = [s for s in sessions if not s.is_nap and s.end > s.start]
        if not nights:
            return SleepTimingSummary(nights_observed=0)

        onsets: list[float] = []
        offsets: list[float] = []
        midpoints: list[float] = []
        durations: list[float] = []
        efficiencies: list[float] = []
        deep_fractions: list[float] = []
        rem_fractions: list[float] = []
        rem_latencies: list[float] = []

        for night in nights:
            onsets.append(self._local_hour(night.start))
            offsets.append(self._local_hour(night.end))
            duration_h = (night.end - night.start).total_seconds() / 3600.0
            durations.append(duration_h)
            midpoints.append((self._local_hour(night.start) + duration_h / 2.0) % HOURS_PER_DAY)

            if night.efficiency is not None:
                efficiencies.append(night.efficiency)

            staged = [seg for seg in night.segments if seg.stage is not SleepStage.UNMEASURABLE]
            staged_total = sum(seg.duration_s for seg in staged)
            if staged_total > 0:
                deep = sum(s.duration_s for s in staged if s.stage is SleepStage.DEEP)
                rem = sum(s.duration_s for s in staged if s.stage is SleepStage.REM)
                deep_fractions.append(deep / staged_total)
                rem_fractions.append(rem / staged_total)

            first_rem = next((s for s in night.segments if s.stage is SleepStage.REM), None)
            if first_rem is not None:
                rem_latencies.append((first_rem.start - night.start).total_seconds() / 60.0)

        if len(nights) < 3:
            warnings.append(
                f"Only {len(nights)} night(s) of sleep in the window; midsleep variability "
                "is not meaningful below three nights"
            )

        mean_midsleep, midsleep_sd = circular_mean_and_sd(midpoints)

        return SleepTimingSummary(
            nights_observed=len(nights),
            mean_onset_hour=round(circular_mean_and_sd(onsets)[0], 3),
            mean_offset_hour=round(circular_mean_and_sd(offsets)[0], 3),
            mean_midsleep_hour=round(mean_midsleep, 3),
            midsleep_variability_h=round(midsleep_sd, 3) if len(nights) >= 3 else None,
            mean_duration_h=round(float(np.mean(durations)), 3),
            mean_efficiency=round(float(np.mean(efficiencies)), 4) if efficiencies else None,
            deep_fraction=round(float(np.mean(deep_fractions)), 4) if deep_fractions else None,
            rem_fraction=round(float(np.mean(rem_fractions)), 4) if rem_fractions else None,
            mean_first_rem_latency_min=(
                round(float(np.mean(rem_latencies)), 2) if rem_latencies else None
            ),
        )

    # -- cosinor -----------------------------------------------------------

    def fit_cosinor(
        self, samples: list[NormalizedSample], metric: Metric
    ) -> CosinorFit | None:
        """Single component cosinor regression at a fixed 24 hour period.

        The period is fixed rather than fitted. A free period on two weeks of
        wearable data is underdetermined and drifts toward whatever the noise
        favours; the clinically useful quantity here is the phase of the
        rhythm against the solar day, not the length of the patient's tau.

        Samples are weighted by the confidence the normalizer assigned, so a
        derived Apple rMSSD moves the acrophase less than a measured Oura one.
        """
        points = [s for s in samples if s.metric is metric]
        if len(points) < MIN_COSINOR_POINTS:
            return None

        hours = np.array([self._local_hour(s.timestamp) for s in points], dtype=float)
        values = np.array([s.value for s in points], dtype=float)
        weights = np.array([max(s.confidence, 0.01) for s in points], dtype=float)

        if np.allclose(values, values[0]):
            return None

        design = np.column_stack(
            [np.ones_like(hours), np.cos(OMEGA * hours), np.sin(OMEGA * hours)]
        )

        sqrt_w = np.sqrt(weights)[:, None]
        try:
            coeffs, *_ = np.linalg.lstsq(design * sqrt_w, values * sqrt_w[:, 0], rcond=None)
        except np.linalg.LinAlgError:
            log.warning("cosinor.singular", metric=metric.value)
            return None

        mesor, beta, gamma = (float(c) for c in coeffs)
        amplitude = math.hypot(beta, gamma)
        acrophase_rad = math.atan2(gamma, beta)
        acrophase_hour = (acrophase_rad / OMEGA) % HOURS_PER_DAY

        fitted = design @ coeffs
        weighted_mean = float(np.average(values, weights=weights))
        ss_res = float(np.sum(weights * (values - fitted) ** 2))
        ss_tot = float(np.sum(weights * (values - weighted_mean) ** 2))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

        return CosinorFit(
            mesor=round(mesor, 4),
            amplitude=round(amplitude, 4),
            acrophase_hour=round(acrophase_hour, 3),
            r_squared=round(max(r_squared, 0.0), 4),
            n_points=len(points),
        )

    # -- actigraphy --------------------------------------------------------

    def summarize_actigraphy(
        self,
        samples: list[NormalizedSample],
        window_start: datetime,
        window_end: datetime,
        warnings: list[str],
    ) -> ActigraphySummary:
        """Nonparametric rhythm measures over hourly binned activity.

        Activity is preferred over MET when both exist because step counts are
        present on all four devices while MET is not, and mixing the two inside
        one series would create steps at the scale of thousands next to MET
        values in the single digits.
        """
        series = self._hourly_activity(samples, window_start, window_end)
        if series is None:
            warnings.append("No activity series available; actigraphy measures omitted")
            return ActigraphySummary()

        days = len(series) / HOURS_PER_DAY
        if days < MIN_ACTIGRAPHY_DAYS:
            warnings.append(
                f"Activity covers {days:.1f} days; interdaily stability needs at least "
                f"{MIN_ACTIGRAPHY_DAYS}"
            )
            return ActigraphySummary()

        values = series.to_numpy(dtype=float)
        hours_of_day = np.array([ts.hour for ts in series.index], dtype=int)

        grand_mean = float(np.mean(values))
        total_variance = float(np.sum((values - grand_mean) ** 2))
        if total_variance <= 0:
            warnings.append("Activity series is flat; actigraphy measures omitted")
            return ActigraphySummary()

        # Interdaily stability: how reliably the same hour looks the same
        # across days. Low IS on a hospitalised patient is the usual sign that
        # the ward routine, not the patient's clock, is driving the rhythm.
        hourly_profile = np.array(
            [values[hours_of_day == hour].mean() for hour in range(24)], dtype=float
        )
        counts = np.array([np.sum(hours_of_day == hour) for hour in range(24)], dtype=float)
        interdaily = (
            len(values)
            * float(np.sum(counts * (hourly_profile - grand_mean) ** 2))
            / (24.0 * total_variance)
        )

        # Intradaily variability: fragmentation. High IV distinguishes a
        # patient napping through the day from one with a shifted but intact
        # rhythm, and those two need different clinical responses.
        diffs = np.diff(values)
        intradaily = (
            len(values) * float(np.sum(diffs**2)) / ((len(values) - 1) * total_variance)
        )

        l5_start, l5_mean = _extreme_window(hourly_profile, 5, lowest=True)
        m10_start, m10_mean = _extreme_window(hourly_profile, 10, lowest=False)
        denominator = m10_mean + l5_mean
        relative_amplitude = (m10_mean - l5_mean) / denominator if denominator > 0 else None

        return ActigraphySummary(
            interdaily_stability=round(interdaily, 4),
            intradaily_variability=round(intradaily, 4),
            l5_onset_hour=float(l5_start),
            l5_mean=round(l5_mean, 4),
            m10_onset_hour=float(m10_start),
            m10_mean=round(m10_mean, 4),
            relative_amplitude=(
                round(relative_amplitude, 4) if relative_amplitude is not None else None
            ),
        )

    def _hourly_activity(
        self, samples: list[NormalizedSample], window_start: datetime, window_end: datetime
    ) -> pd.Series | None:
        preferred = [s for s in samples if s.metric is Metric.STEPS]
        if len(preferred) < 24:
            preferred = [s for s in samples if s.metric is Metric.ACTIVITY_MET]
        if len(preferred) < 24:
            return None

        frame = pd.DataFrame(
            {
                "ts": [s.timestamp.astimezone(self.tz) for s in preferred],
                "value": [s.value for s in preferred],
            }
        ).set_index("ts")

        # Gaps are filled with zero rather than dropped. A watch that is off
        # the wrist reports nothing, and for a movement rhythm the absence of
        # movement is the observation.
        hourly = frame["value"].resample("1h").sum().fillna(0.0)
        local_start = window_start.astimezone(self.tz)
        local_end = window_end.astimezone(self.tz)
        return hourly.loc[(hourly.index >= local_start) & (hourly.index <= local_end)]

    # -- coverage ----------------------------------------------------------

    def metric_coverage(self, samples: list[NormalizedSample]) -> list[MetricCoverage]:
        buckets: dict[Metric, list[NormalizedSample]] = defaultdict(list)
        for sample in samples:
            buckets[sample.metric].append(sample)

        coverage: list[MetricCoverage] = []
        for metric, group in buckets.items():
            days = {s.timestamp.astimezone(self.tz).date() for s in group}
            coverage.append(
                MetricCoverage(
                    metric=metric,
                    sample_count=len(group),
                    days_covered=len(days),
                    mean_confidence=round(float(np.mean([s.confidence for s in group])), 4),
                )
            )
        return sorted(coverage, key=lambda c: c.metric.value)

    def completeness(
        self,
        sleep: SleepTimingSummary,
        coverage: list[MetricCoverage],
        window_days: float,
    ) -> float:
        """Fraction of the evidence a full profile would have.

        Weighted toward sleep and HRV because those two carry most of the phase
        information. Activity is common but weakly specific: a patient can hold
        a normal activity pattern while their internal phase has already moved.
        """
        by_metric = {c.metric: c for c in coverage}

        def metric_fraction(metric: Metric) -> float:
            entry = by_metric.get(metric)
            if entry is None:
                return 0.0
            return min(entry.days_covered / window_days, 1.0) * entry.mean_confidence

        sleep_fraction = min(sleep.nights_observed / window_days, 1.0)
        hrv_fraction = metric_fraction(Metric.HRV_RMSSD)
        activity_fraction = max(
            metric_fraction(Metric.STEPS), metric_fraction(Metric.ACTIVITY_MET)
        )
        temp_fraction = metric_fraction(Metric.SKIN_TEMP_DELTA)

        return float(
            0.40 * sleep_fraction
            + 0.30 * hrv_fraction
            + 0.20 * activity_fraction
            + 0.10 * temp_fraction
        )

    # -- helpers -----------------------------------------------------------

    def _local_hour(self, moment: datetime) -> float:
        local = moment.astimezone(self.tz)
        return local.hour + local.minute / 60.0 + local.second / 3600.0


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def circular_mean_and_sd(hours: list[float]) -> tuple[float, float]:
    """Mean and spread of clock hours treated as angles.

    Sleep midpoints at 23:30 and 00:30 average to midnight, not to noon. An
    arithmetic mean gets this wrong by twelve hours, which is the full width of
    the error the whole product exists to avoid.

    The spread returned is the circular standard deviation in hours.
    """
    if not hours:
        return 0.0, 0.0

    angles = np.array(hours, dtype=float) * OMEGA
    sin_mean = float(np.mean(np.sin(angles)))
    cos_mean = float(np.mean(np.cos(angles)))

    mean_hour = (math.atan2(sin_mean, cos_mean) / OMEGA) % HOURS_PER_DAY

    resultant = math.hypot(sin_mean, cos_mean)
    if resultant <= 1e-9:
        # Perfectly dispersed times carry no mean direction at all.
        return mean_hour, HOURS_PER_DAY / 2.0

    circular_sd_rad = math.sqrt(-2.0 * math.log(min(resultant, 1.0)))
    return mean_hour, min(circular_sd_rad / OMEGA, HOURS_PER_DAY / 2.0)


def circular_difference_hours(a: float, b: float) -> float:
    """Signed shortest distance from b to a on a 24 hour circle."""
    diff = (a - b + HOURS_PER_DAY / 2.0) % HOURS_PER_DAY - HOURS_PER_DAY / 2.0
    return diff


def _extreme_window(profile: np.ndarray, length: int, *, lowest: bool) -> tuple[int, float]:
    """Find the L5 or M10 window on a wrapped 24 hour profile.

    The window wraps midnight because the least active five hours almost always
    straddle it. A non wrapping scan would place L5 in the late evening for
    every patient with a conventional schedule.
    """
    doubled = np.concatenate([profile, profile])
    means = np.array([doubled[i : i + length].mean() for i in range(24)], dtype=float)
    index = int(np.argmin(means) if lowest else np.argmax(means))
    return index, float(means[index])


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("profile.unknown_timezone", requested=name)
        return ZoneInfo("UTC")


def default_window(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    return end - timedelta(days=days), end

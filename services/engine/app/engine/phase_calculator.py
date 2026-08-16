"""Circadian phase estimation.

===========================================================================
PRIVATE COMPONENT. The scoring coefficients are not in this repository.
===========================================================================

What is public, here, in full:
    the PhaseEstimator interface, the descriptors it consumes, the shape of
    what it returns, and a reference implementation that runs without any
    private dependency so the rest of the system is buildable and testable.

What is private, in the `croniixx-phase` package on a private index:
    the weighting of each descriptor against the others, the confidence model
    that converts descriptor agreement into an error estimate, the DLMO
    regression constants, and the corrections applied per provider and per
    clinical population. Those were fit against polysomnography and
    dim light melatonin onset reference data and are the core IP of this
    product.

Substitution is by import. If `croniixx_phase` is installed, the Engine uses
it. If not, the Engine starts in reference mode and stamps
CoefficientSource.REFERENCE_FALLBACK on every estimate it produces, so a
result can never be mistaken for a validated one. The API surface, the schema,
and the schedule geometry are identical in both modes.

The reference implementation below uses one published relationship only: the
association between sleep midpoint and melatonin onset. It is deliberately
simple, it is not validated for clinical use, and it is not what runs in
production. See docs/circadian-methodology.md.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.schemas import CircadianProfileInput, CoefficientSource, PhaseDrift, PhaseEstimate

log = structlog.get_logger(__name__)

HOURS_PER_DAY = 24.0

# Population reference midsleep for a conventionally entrained adult, in local
# hours. An offset is reported against this point, so a patient sleeping
# 23:00 to 07:00 reads as zero rather than as some arbitrary number.
REFERENCE_MIDSLEEP_HOUR = 4.0

# Published mean interval from dim light melatonin onset to sleep midpoint in
# entrained adults. Used only to place a nominal DLMO on the dial in reference
# mode; the validated package models this per patient.
DLMO_TO_MIDSLEEP_HOURS = 7.0

REFERENCE_METHOD_VERSION = "reference-midsleep-1.0"

# Reference mode never claims more than this, whatever the data looks like.
# The ceiling is a property of the method, not of the input.
REFERENCE_MAX_CONFIDENCE = 0.45


@runtime_checkable
class PhaseEstimator(Protocol):
    """The contract any phase estimator must satisfy.

    Implementations must be pure with respect to their inputs. The Engine
    caches estimates by profile content and would return stale results for an
    estimator that carried state between calls.
    """

    method_version: str
    coefficient_source: CoefficientSource

    def estimate(
        self,
        profile: CircadianProfileInput,
        *,
        patient_timezone: str = "UTC",
        now: datetime | None = None,
    ) -> PhaseEstimate:
        """Return the patient's circadian position from their profile."""
        ...


class ReferencePhaseEstimator:
    """Sleep midpoint only. Not clinical grade, and reports itself as such.

    Included so that the schedule builder, the API, the dashboard, and the
    mobile app can all be exercised end to end on a machine that has no access
    to the private coefficients. Every estimate it returns is stamped
    REFERENCE_FALLBACK and capped at low confidence.
    """

    method_version = REFERENCE_METHOD_VERSION
    coefficient_source = CoefficientSource.REFERENCE_FALLBACK

    def estimate(
        self,
        profile: CircadianProfileInput,
        *,
        patient_timezone: str = "UTC",
        now: datetime | None = None,
    ) -> PhaseEstimate:
        moment = now or datetime.now(timezone.utc)
        warnings = [
            "Reference estimator in use. Phase offset derives from sleep midpoint alone "
            "and is not validated for clinical dosing decisions."
        ]
        inputs_used: list[str] = []

        midsleep_hour = _get_float(profile.sleep, "mean_midsleep_hour")
        nights = int(_get_float(profile.sleep, "nights_observed") or 0)

        if midsleep_hour is None or nights == 0:
            warnings.append("No sleep midpoint available; phase offset reported as zero.")
            return PhaseEstimate(
                patient_id=profile.patient_id,
                computed_at=moment,
                phase_offset_min=0,
                confidence=0.0,
                method_version=self.method_version,
                coefficient_source=self.coefficient_source,
                inputs_used=inputs_used,
                warnings=warnings,
            )

        inputs_used.append("sleep.mean_midsleep_hour")

        offset_hours = signed_hour_difference(midsleep_hour, REFERENCE_MIDSLEEP_HOUR)
        offset_min = int(round(offset_hours * 60))

        dlmo = _project_dlmo(
            midsleep_hour - DLMO_TO_MIDSLEEP_HOURS, patient_timezone, moment
        )

        # Confidence rises with how many nights were seen and falls with how
        # scattered they were. Both terms are observable; neither is fitted.
        variability = _get_float(profile.sleep, "midsleep_variability_h")
        nights_term = min(nights / 7.0, 1.0)
        spread_term = 1.0 if variability is None else math.exp(-max(variability, 0.0))
        confidence = min(
            REFERENCE_MAX_CONFIDENCE,
            REFERENCE_MAX_CONFIDENCE * nights_term * spread_term * max(profile.data_completeness, 0.0),
        )

        if variability is not None:
            inputs_used.append("sleep.midsleep_variability_h")

        stability = _get_float(profile.actigraphy, "interdaily_stability")
        if stability is not None:
            inputs_used.append("actigraphy.interdaily_stability")

        amplitude = _get_float(profile.actigraphy, "relative_amplitude")
        if amplitude is not None:
            inputs_used.append("actigraphy.relative_amplitude")

        if profile.hrv_cosinor:
            warnings.append(
                "HRV acrophase is present in the profile but unused in reference mode. "
                "The validated estimator weights it against sleep timing."
            )

        return PhaseEstimate(
            patient_id=profile.patient_id,
            computed_at=moment,
            phase_offset_min=offset_min,
            dlmo_estimate=dlmo,
            amplitude=amplitude,
            stability=stability,
            confidence=round(confidence, 4),
            method_version=self.method_version,
            coefficient_source=self.coefficient_source,
            inputs_used=inputs_used,
            warnings=warnings + list(profile.warnings),
        )


def load_estimator() -> PhaseEstimator:
    """Return the validated estimator when it is installed, otherwise reference.

    Import failure is the intended path in a public checkout and is logged at
    info, not warning. A checkout that expects the private package and does not
    find it will still see the mode on every API response.
    """
    try:
        from croniixx_phase import ValidatedPhaseEstimator  # type: ignore[import-not-found]
    except ImportError:
        log.info("phase.reference_mode", reason="croniixx_phase not installed")
        return ReferencePhaseEstimator()

    estimator = ValidatedPhaseEstimator()
    if not isinstance(estimator, PhaseEstimator):
        # A private package that drifts from the contract is a worse failure
        # than a missing one, because its numbers would look authoritative.
        log.error("phase.private_package_contract_mismatch")
        return ReferencePhaseEstimator()

    log.info("phase.validated_mode", method_version=estimator.method_version)
    return estimator


# ---------------------------------------------------------------------------
# Shared helpers. These are public because they define the conventions the
# private package must also follow: sign, wrapping, and drift definitions.
# ---------------------------------------------------------------------------


def signed_hour_difference(actual: float, reference: float) -> float:
    """Shortest signed distance on a 24 hour circle, in hours.

    Positive means actual is later than reference, which is a delayed phase.
    A patient with a midsleep at 06:00 against a 04:00 reference is delayed by
    two hours, not advanced by twenty two.
    """
    return (actual - reference + HOURS_PER_DAY / 2.0) % HOURS_PER_DAY - HOURS_PER_DAY / 2.0


def compute_drift(
    baseline: PhaseEstimate, current: PhaseEstimate, window_days: int
) -> PhaseDrift:
    """Movement between two phase estimates.

    The difference is taken on the circle for the same reason the estimate is:
    a patient who moves from minus eleven hours to plus eleven has drifted two
    hours through the night, not twenty two hours backwards.
    """
    drift_hours = signed_hour_difference(
        current.phase_offset_min / 60.0, baseline.phase_offset_min / 60.0
    )
    return PhaseDrift(
        patient_id=current.patient_id,
        baseline_offset_min=baseline.phase_offset_min,
        current_offset_min=current.phase_offset_min,
        drift_min=int(round(drift_hours * 60)),
        window_days=window_days,
    )


def is_stale(estimate: PhaseEstimate, ttl_hours: int, now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    return (moment - estimate.computed_at) > timedelta(hours=ttl_hours)


def _project_dlmo(local_hour: float, patient_timezone: str, reference: datetime) -> datetime | None:
    """Place a local hour on the evening nearest the reference moment."""
    try:
        tz = ZoneInfo(patient_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    hour = local_hour % HOURS_PER_DAY
    local_reference = reference.astimezone(tz)
    candidate = local_reference.replace(
        hour=int(hour), minute=int((hour % 1) * 60), second=0, microsecond=0
    )

    # DLMO precedes the coming night. If the calculated point already passed
    # today, the next one belongs to tomorrow evening.
    if candidate < local_reference - timedelta(hours=12):
        candidate += timedelta(days=1)
    elif candidate > local_reference + timedelta(hours=12):
        candidate -= timedelta(days=1)

    return candidate.astimezone(timezone.utc)


def _get_float(mapping: dict[str, Any] | None, key: str) -> float | None:
    if not mapping:
        return None
    value = mapping.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result

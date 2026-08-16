"""Phase estimator tests.

These test the contract and the sign convention, not the coefficients. The
coefficients are not in this repository, and a test that asserted specific
offset values would either encode the IP or fail the moment the validated
package is installed.
"""

from __future__ import annotations

import pytest

from app.engine.phase_calculator import (
    ReferencePhaseEstimator,
    compute_drift,
    load_estimator,
    signed_hour_difference,
)
from app.schemas import CoefficientSource
from tests.conftest import REFERENCE_NOW, make_profile

ESTIMATOR = ReferencePhaseEstimator()


def test_loader_falls_back_to_reference_without_the_private_package():
    estimator = load_estimator()
    assert estimator.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
    assert estimator.method_version.startswith("reference")


def test_every_reference_estimate_is_labelled_as_unvalidated():
    estimate = ESTIMATOR.estimate(make_profile(), now=REFERENCE_NOW)
    assert estimate.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
    assert any("not validated" in w for w in estimate.warnings)


def test_late_sleeper_reads_as_delayed():
    # Midsleep at 06:00 against a 04:00 reference is two hours delayed.
    estimate = ESTIMATOR.estimate(make_profile(midsleep=6.0), now=REFERENCE_NOW)
    assert estimate.phase_offset_min == 120
    assert estimate.direction == "delayed"


def test_early_sleeper_reads_as_advanced():
    estimate = ESTIMATOR.estimate(make_profile(midsleep=2.0), now=REFERENCE_NOW)
    assert estimate.phase_offset_min == -120
    assert estimate.direction == "advanced"


def test_offset_wraps_the_short_way_around_midnight():
    # Midsleep at 23:00 is five hours advanced from 04:00, not nineteen delayed.
    estimate = ESTIMATOR.estimate(make_profile(midsleep=23.0), now=REFERENCE_NOW)
    assert estimate.phase_offset_min == -300


def test_offset_display_carries_an_explicit_sign():
    delayed = ESTIMATOR.estimate(make_profile(midsleep=6.5), now=REFERENCE_NOW)
    advanced = ESTIMATOR.estimate(make_profile(midsleep=1.5), now=REFERENCE_NOW)
    assert delayed.offset_display == "+02:30"
    assert advanced.offset_display == "-02:30"


def test_small_offset_reads_as_aligned():
    estimate = ESTIMATOR.estimate(make_profile(midsleep=4.2), now=REFERENCE_NOW)
    assert estimate.direction == "aligned"


def test_missing_sleep_data_yields_zero_confidence_rather_than_a_guess():
    estimate = ESTIMATOR.estimate(
        make_profile(midsleep=None, nights=0, onset=0.0, offset=0.0), now=REFERENCE_NOW
    )
    assert estimate.confidence == 0.0
    assert estimate.phase_offset_min == 0


def test_reference_confidence_is_capped_however_clean_the_data_is():
    estimate = ESTIMATOR.estimate(
        make_profile(nights=30, completeness=1.0, variability=0.0), now=REFERENCE_NOW
    )
    assert estimate.confidence <= 0.45


def test_scattered_sleep_lowers_confidence():
    tight = ESTIMATOR.estimate(make_profile(variability=0.2), now=REFERENCE_NOW)
    scattered = ESTIMATOR.estimate(make_profile(variability=3.0), now=REFERENCE_NOW)
    assert scattered.confidence < tight.confidence


def test_dlmo_is_projected_before_the_night_it_belongs_to():
    estimate = ESTIMATOR.estimate(make_profile(midsleep=3.0), now=REFERENCE_NOW)
    assert estimate.dlmo_estimate is not None
    # Midsleep 03:00 minus seven hours puts DLMO at 20:00 local.
    assert estimate.dlmo_estimate.hour == 20


@pytest.mark.parametrize(
    "actual,reference,expected",
    [
        (6.0, 4.0, 2.0),
        (2.0, 4.0, -2.0),
        (23.0, 1.0, -2.0),
        (1.0, 23.0, 2.0),
    ],
)
def test_signed_hour_difference_takes_the_short_way(actual, reference, expected):
    assert signed_hour_difference(actual, reference) == pytest.approx(expected)


def test_drift_across_midnight_is_measured_the_short_way():
    baseline = ESTIMATOR.estimate(make_profile(midsleep=15.0), now=REFERENCE_NOW)
    current = ESTIMATOR.estimate(make_profile(midsleep=17.0), now=REFERENCE_NOW)
    drift = compute_drift(baseline, current, window_days=14)
    assert drift.drift_min == 120
    assert drift.alerting is True


def test_small_drift_does_not_alert():
    baseline = ESTIMATOR.estimate(make_profile(midsleep=3.0), now=REFERENCE_NOW)
    current = ESTIMATOR.estimate(make_profile(midsleep=3.5), now=REFERENCE_NOW)
    drift = compute_drift(baseline, current, window_days=14)
    assert drift.drift_min == 30
    assert drift.alerting is False

"""Normalizer tests focused on the cross device semantics, not the plumbing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.device_normalizer import (
    SDNN_TO_RMSSD_RATIO,
    WHOOP_SWS_TO_WHOLE_NIGHT,
    DeviceNormalizer,
    NormalizationError,
    parse_timestamp,
)
from app.schemas import Metric, Provider, SleepStage, TerraWebhookPayload

NORMALIZER = DeviceNormalizer()
NIGHT_START = datetime(2026, 3, 4, 23, 0, tzinfo=timezone.utc)


def sleep_payload(provider: str, *, hypnogram: list[tuple[int, int]], hrv: dict) -> TerraWebhookPayload:
    """Build a Terra sleep payload. hypnogram is a list of (minute_offset, level)."""
    return TerraWebhookPayload.model_validate(
        {
            "type": "sleep",
            "user": {"user_id": "terra-user-1", "provider": provider, "reference_id": "patient-1"},
            "data": [
                {
                    "metadata": {
                        "start_time": NIGHT_START.isoformat(),
                        "end_time": (NIGHT_START + timedelta(hours=8)).isoformat(),
                        "is_nap": False,
                    },
                    "sleep_durations_data": {
                        "sleep_efficiency": 0.91,
                        "awake": {"sleep_latency_seconds": 720, "num_wakeup_events": 2},
                        "hypnogram_samples": [
                            {
                                "timestamp": (NIGHT_START + timedelta(minutes=offset)).isoformat(),
                                "level": level,
                            }
                            for offset, level in hypnogram
                        ],
                    },
                    "heart_rate_data": {"summary": hrv},
                }
            ],
        }
    )


def test_oura_rmssd_passes_through_unchanged():
    payload = sleep_payload("OURA", hypnogram=[(0, 4), (60, 5)], hrv={"avg_hrv_rmssd": 42.0})
    batch = NORMALIZER.normalize(payload)

    rmssd = [s for s in batch.samples if s.metric is Metric.HRV_RMSSD]
    assert len(rmssd) == 1
    assert rmssd[0].value == pytest.approx(42.0)
    assert rmssd[0].confidence == 1.0
    assert batch.provider is Provider.OURA


def test_apple_sdnn_derives_rmssd_at_reduced_confidence():
    payload = sleep_payload("APPLE", hypnogram=[(0, 4), (60, 6)], hrv={"avg_hrv_sdnn": 60.0})
    batch = NORMALIZER.normalize(payload)

    sdnn = next(s for s in batch.samples if s.metric is Metric.HRV_SDNN)
    rmssd = next(s for s in batch.samples if s.metric is Metric.HRV_RMSSD)

    assert sdnn.value == pytest.approx(60.0)
    assert sdnn.confidence == 1.0
    assert rmssd.value == pytest.approx(60.0 * SDNN_TO_RMSSD_RATIO)
    assert rmssd.confidence < 1.0
    assert any("derived from SDNN" in w for w in batch.warnings)


def test_measured_rmssd_is_not_overwritten_by_a_derived_one():
    payload = sleep_payload(
        "GARMIN", hypnogram=[(0, 4)], hrv={"avg_hrv_rmssd": 55.0, "avg_hrv_sdnn": 90.0}
    )
    batch = NORMALIZER.normalize(payload)

    rmssd = [s for s in batch.samples if s.metric is Metric.HRV_RMSSD]
    assert len(rmssd) == 1
    assert rmssd[0].value == pytest.approx(55.0)


def test_whoop_slow_wave_rmssd_is_scaled_toward_whole_night():
    payload = sleep_payload("WHOOP", hypnogram=[(0, 5)], hrv={"avg_hrv_rmssd": 70.0})
    batch = NORMALIZER.normalize(payload)

    rmssd = next(s for s in batch.samples if s.metric is Metric.HRV_RMSSD)
    assert rmssd.value == pytest.approx(70.0 * WHOOP_SWS_TO_WHOLE_NIGHT)
    assert rmssd.confidence < 1.0


def test_whoop_absolute_temperature_becomes_a_delta():
    payload = TerraWebhookPayload.model_validate(
        {
            "type": "daily",
            "user": {"user_id": "terra-user-2", "provider": "WHOOP"},
            "data": [
                {
                    "metadata": {"start_time": NIGHT_START.isoformat()},
                    "temperature_data": {"skin_temperature_celsius": 34.2},
                }
            ],
        }
    )
    batch = NORMALIZER.normalize(payload)

    temp = next(s for s in batch.samples if s.metric is Metric.SKIN_TEMP_DELTA)
    assert temp.value == pytest.approx(34.2 - 33.5)
    assert temp.confidence < 0.6


def test_hypnogram_merges_consecutive_identical_levels():
    payload = sleep_payload(
        "OURA",
        hypnogram=[(0, 4), (5, 4), (10, 4), (15, 5), (20, 5), (25, 6)],
        hrv={"avg_hrv_rmssd": 40.0},
    )
    batch = NORMALIZER.normalize(payload)
    segments = batch.sleep_sessions[0].segments

    assert [s.stage for s in segments] == [SleepStage.LIGHT, SleepStage.DEEP, SleepStage.REM]
    assert segments[0].duration_s == 15 * 60
    assert segments[1].duration_s == 10 * 60


def test_mostly_unstaged_night_is_flagged():
    payload = sleep_payload(
        "APPLE",
        hypnogram=[(0, 2), (30, 2), (60, 2), (90, 4)],
        hrv={"avg_hrv_sdnn": 55.0},
    )
    batch = NORMALIZER.normalize(payload)
    assert any("unstaged" in w for w in batch.warnings)


def test_duplicate_step_samples_from_phone_and_watch_collapse():
    moment = NIGHT_START.isoformat()
    payload = TerraWebhookPayload.model_validate(
        {
            "type": "activity",
            "user": {"user_id": "terra-user-3", "provider": "APPLE"},
            "data": [
                {
                    "metadata": {"start_time": moment},
                    "distance_data": {
                        "detailed": {
                            "step_samples": [
                                {"timestamp": moment, "steps": 120},
                                {"timestamp": moment, "steps": 120},
                            ]
                        }
                    },
                }
            ],
        }
    )
    batch = NORMALIZER.normalize(payload)
    steps = [s for s in batch.samples if s.metric is Metric.STEPS]
    assert len(steps) == 1


def test_unknown_provider_is_rejected():
    payload = TerraWebhookPayload.model_validate(
        {"type": "sleep", "user": {"user_id": "x", "provider": "FITBIT"}, "data": []}
    )
    with pytest.raises(NormalizationError):
        NORMALIZER.normalize(payload)


@pytest.mark.parametrize(
    "raw,expected_hour",
    [
        ("2026-03-04T23:00:00Z", 23),
        ("2026-03-04T23:00:00+00:00", 23),
        ("2026-03-05T01:00:00+02:00", 23),
        (1772665200, 23),
    ],
)
def test_timestamp_shapes_all_land_on_the_same_utc_hour(raw, expected_hour):
    parsed = parse_timestamp(raw)
    assert parsed is not None
    assert parsed.astimezone(timezone.utc).hour == expected_hour

"""Cross device normalization for Oura, Apple Watch, Garmin, and Whoop.

Terra unifies the transport and the field names. It does not unify the meaning
of the values inside those fields, and for circadian work the meaning is the
part that matters. Three examples that this module exists to handle:

  Apple reports heart rate variability as SDNN. Oura, Garmin, and Whoop report
  rMSSD. The two indices are not interchangeable. SDNN carries both short and
  long term variance while rMSSD is dominated by beat to beat parasympathetic
  activity, and the nocturnal rMSSD curve is what tracks the circadian
  oscillator. Feeding an SDNN number into an rMSSD model shifts the estimated
  acrophase.

  Whoop samples HRV inside slow wave sleep only. Oura and Garmin average over
  the whole night. Slow wave windows sit at the parasympathetic peak, so a
  Whoop reading is systematically higher than a whole night reading from the
  same wrist on the same night.

  Oura and Garmin report skin temperature as a deviation from the patient's own
  baseline. Whoop reports an absolute skin temperature in Celsius. Subtracting
  one from the other is meaningless without a per patient baseline.

Every conversion below reduces the confidence field rather than hiding itself.
The Engine weights samples by confidence, so a derived value contributes less
to the phase estimate than a directly measured one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from app.schemas import (
    Metric,
    NormalizedBatch,
    NormalizedSample,
    NormalizedSleepSegment,
    NormalizedSleepSession,
    Provider,
    SleepStage,
    TerraWebhookPayload,
)

log = structlog.get_logger(__name__)


# Terra encodes the hypnogram as integer levels. This mapping is stable across
# providers because Terra applies it before the payload reaches us; what varies
# is which levels a given device is capable of emitting at all.
TERRA_HYPNOGRAM_LEVELS: dict[int, SleepStage] = {
    0: SleepStage.UNMEASURABLE,
    1: SleepStage.AWAKE,
    2: SleepStage.LIGHT,  # generic "asleep" with no staging
    3: SleepStage.AWAKE,  # out of bed
    4: SleepStage.LIGHT,
    5: SleepStage.DEEP,
    6: SleepStage.REM,
}

# Devices that cannot stage sleep emit level 2 for the whole night. Treating
# that as light sleep would inflate the light fraction and drag the computed
# deep and REM fractions toward zero, so those nights are marked instead.
UNSTAGED_LEVELS = {0, 2, 3}

# rMSSD is typically lower than SDNN in nocturnal resting segments. The ratio
# is patient specific; 0.80 is the population midpoint used here as a first
# pass so an Apple only patient still produces a usable rhythm shape. The
# derived value carries low confidence and never overrides a measured rMSSD.
SDNN_TO_RMSSD_RATIO = 0.80
SDNN_DERIVED_CONFIDENCE = 0.55

# Whoop samples HRV inside slow wave sleep, which sits at the parasympathetic
# peak. Scaling toward a whole night equivalent keeps a Whoop patient on the
# same amplitude scale as an Oura patient.
WHOOP_SWS_TO_WHOLE_NIGHT = 0.90
WHOOP_HRV_CONFIDENCE = 0.85

# Whoop skin temperature is absolute. Population mean nocturnal wrist skin
# temperature is used only to place the value on a delta scale; the resulting
# sample is weak enough that it cannot dominate a fit on its own.
WHOOP_SKIN_TEMP_BASELINE_C = 33.5
WHOOP_TEMP_CONFIDENCE = 0.45


class NormalizationError(ValueError):
    """Raised when a payload cannot be attributed to a known provider."""


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------


def parse_timestamp(raw: Any) -> datetime | None:
    """Parse the several timestamp shapes the four providers produce.

    Whoop sends RFC 3339 with a Z suffix. Oura sends RFC 3339 with a numeric
    offset that encodes the patient's local zone. Garmin sends epoch seconds
    alongside a separate offset field. Apple sends either, depending on which
    HealthKit type the sample came from.

    Everything is converted to an aware UTC datetime. The patient's local zone
    is not discarded; it is recovered separately from the patient record,
    because a device offset can be stale after travel while the clinician
    maintained patient timezone is authoritative.
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        # Millisecond epochs appear from some Garmin firmware revisions.
        seconds = float(raw) / 1000.0 if float(raw) > 1e11 else float(raw)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)

    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    # A naive timestamp from a provider is UTC by Terra convention.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first(mapping: dict[str, Any] | None, *keys: str) -> Any:
    """Return the first present, non null key.

    Providers disagree on which optional keys they populate, and Terra passes
    that disagreement through rather than filling gaps with nulls uniformly.
    """
    if not mapping:
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


class ProviderAdapter:
    """Base adapter. Subclasses override only where the device differs."""

    provider: Provider

    #: Metrics this device measures directly rather than deriving.
    native_metrics: set[Metric] = set()

    #: Devices that cannot produce a hypnogram at all.
    stages_sleep: bool = True

    def __init__(self, terra_user_id: str | None) -> None:
        self.terra_user_id = terra_user_id
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def sample(
        self,
        timestamp: datetime,
        metric: Metric,
        value: float | None,
        unit: str,
        confidence: float = 1.0,
    ) -> NormalizedSample | None:
        if value is None:
            return None
        return NormalizedSample(
            timestamp=timestamp,
            metric=metric,
            value=value,
            unit=unit,
            provider=self.provider,
            terra_user_id=self.terra_user_id,
            confidence=confidence,
        )

    # -- heart rate variability -------------------------------------------

    def heart_rate_samples(
        self, heart_rate_data: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        """Extract HRV and heart rate from a Terra heart_rate_data block."""
        out: list[NormalizedSample] = []
        summary = heart_rate_data.get("summary") or {}
        detailed = heart_rate_data.get("detailed") or {}

        rmssd = _as_float(_first(summary, "avg_hrv_rmssd", "avg_hrv_rmssd_ms"))
        sdnn = _as_float(_first(summary, "avg_hrv_sdnn", "avg_hrv_sdnn_ms"))

        if rmssd is not None:
            adjusted, confidence = self.adjust_rmssd(rmssd)
            out.append(self.sample(reference, Metric.HRV_RMSSD, adjusted, "ms", confidence))

        if sdnn is not None:
            out.append(self.sample(reference, Metric.HRV_SDNN, sdnn, "ms", 1.0))
            if rmssd is None:
                derived = sdnn * SDNN_TO_RMSSD_RATIO
                self.warn(
                    f"{self.provider.value}: rMSSD derived from SDNN at ratio "
                    f"{SDNN_TO_RMSSD_RATIO}; phase amplitude is approximate"
                )
                out.append(
                    self.sample(
                        reference, Metric.HRV_RMSSD, derived, "ms", SDNN_DERIVED_CONFIDENCE
                    )
                )

        resting = _as_float(_first(summary, "resting_hr_bpm", "resting_hr"))
        if resting is not None:
            out.append(self.sample(reference, Metric.RESTING_HR, resting, "bpm", 1.0))

        # Detailed series carry the intra night shape, which is what the
        # cosinor fit needs. The summary alone gives one point per day and
        # cannot resolve an acrophase.
        for entry in _iter_samples(detailed.get("hr_samples")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            bpm = _as_float(_first(entry, "bpm", "value"))
            if ts and bpm:
                out.append(self.sample(ts, Metric.HEART_RATE, bpm, "bpm", 1.0))

        for entry in _iter_samples(detailed.get("hrv_samples_rmssd")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "hrv_rmssd", "value"))
            if ts and value:
                adjusted, confidence = self.adjust_rmssd(value)
                out.append(self.sample(ts, Metric.HRV_RMSSD, adjusted, "ms", confidence))

        for entry in _iter_samples(detailed.get("hrv_samples_sdnn")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "hrv_sdnn", "value"))
            if ts and value:
                out.append(self.sample(ts, Metric.HRV_SDNN, value, "ms", 1.0))

        return [s for s in out if s is not None]

    def adjust_rmssd(self, value: float) -> tuple[float, float]:
        """Return (value, confidence) after any device specific correction."""
        return value, 1.0

    # -- temperature -------------------------------------------------------

    def temperature_samples(
        self, temperature_data: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        """Convert whatever the device calls temperature into a delta in C."""
        delta = _as_float(_first(temperature_data, "delta", "temperature_delta"))
        if delta is not None:
            sample = self.sample(reference, Metric.SKIN_TEMP_DELTA, delta, "degC", 1.0)
            return [sample] if sample else []
        return []

    # -- sleep staging -----------------------------------------------------

    def map_hypnogram_level(self, level: int) -> SleepStage:
        return TERRA_HYPNOGRAM_LEVELS.get(level, SleepStage.UNMEASURABLE)


class OuraAdapter(ProviderAdapter):
    """Oura Ring.

    The reference device for this system. Nightly rMSSD averaged across the
    sleep period, five minute hypnogram resolution, and a temperature delta
    already expressed against the patient's own baseline. No corrections apply.
    """

    provider = Provider.OURA
    native_metrics = {
        Metric.HRV_RMSSD,
        Metric.RESTING_HR,
        Metric.SKIN_TEMP_DELTA,
        Metric.RESPIRATION_RATE,
        Metric.SPO2,
    }


class AppleAdapter(ProviderAdapter):
    """Apple Watch through HealthKit.

    Two structural gaps. HealthKit exposes heartRateVariabilitySDNN and has no
    rMSSD type at all, so rMSSD is derived. And sleep staging only exists from
    watchOS 9 on Series 8 and later; earlier hardware reports time in bed with
    no stage detail, which arrives as unstaged hypnogram levels.

    Step counts are also duplicated when an iPhone and a Watch both log the
    same walk, so step samples are deduplicated by minute below.
    """

    provider = Provider.APPLE
    native_metrics = {Metric.HRV_SDNN, Metric.RESTING_HR, Metric.HEART_RATE}

    def temperature_samples(
        self, temperature_data: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        # Apple wrist temperature is already a deviation from the wearer's own
        # sleeping baseline, so it maps straight onto our delta scale. It is
        # only sampled during sleep, which is exactly the window we care about.
        return super().temperature_samples(temperature_data, reference)


class GarminAdapter(ProviderAdapter):
    """Garmin.

    Overnight rMSSD is available on devices that support HRV Status; older
    watches send no HRV block at all. Garmin also reports a large unmeasurable
    sleep bucket on wrist movement, which is preserved rather than folded into
    light sleep so that sleep fraction statistics stay honest.
    """

    provider = Provider.GARMIN
    native_metrics = {Metric.HRV_RMSSD, Metric.RESTING_HR, Metric.HEART_RATE, Metric.SPO2}

    def heart_rate_samples(
        self, heart_rate_data: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        samples = super().heart_rate_samples(heart_rate_data, reference)
        has_hrv = any(s.metric in (Metric.HRV_RMSSD, Metric.HRV_SDNN) for s in samples)
        if not has_hrv:
            self.warn(
                "GARMIN: no HRV block in payload; device likely predates HRV Status support"
            )
        return samples


class WhoopAdapter(ProviderAdapter):
    """Whoop.

    Two corrections. HRV is sampled during slow wave sleep rather than across
    the night, and skin temperature is absolute rather than a delta.
    """

    provider = Provider.WHOOP
    native_metrics = {Metric.HRV_RMSSD, Metric.RESTING_HR, Metric.RESPIRATION_RATE}

    def adjust_rmssd(self, value: float) -> tuple[float, float]:
        self.warn(
            "WHOOP: rMSSD sampled in slow wave sleep, scaled toward a whole night equivalent"
        )
        return value * WHOOP_SWS_TO_WHOLE_NIGHT, WHOOP_HRV_CONFIDENCE

    def temperature_samples(
        self, temperature_data: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        delta = _as_float(_first(temperature_data, "delta", "temperature_delta"))
        if delta is not None:
            sample = self.sample(reference, Metric.SKIN_TEMP_DELTA, delta, "degC", 1.0)
            return [sample] if sample else []

        absolute = _as_float(
            _first(temperature_data, "ambient_temperature_celsius", "skin_temperature_celsius", "avg_temperature_celsius")
        )
        if absolute is None:
            return []

        self.warn(
            "WHOOP: absolute skin temperature converted against a population baseline; "
            "per patient baseline requires fourteen nights of history"
        )
        sample = self.sample(
            reference,
            Metric.SKIN_TEMP_DELTA,
            absolute - WHOOP_SKIN_TEMP_BASELINE_C,
            "degC",
            WHOOP_TEMP_CONFIDENCE,
        )
        return [sample] if sample else []

    def map_hypnogram_level(self, level: int) -> SleepStage:
        # Whoop calls deep sleep "slow wave sleep". Terra already folds that
        # into level 5, so the base mapping is correct and this override exists
        # only to document that the naming difference was checked.
        return super().map_hypnogram_level(level)


ADAPTERS: dict[Provider, type[ProviderAdapter]] = {
    Provider.OURA: OuraAdapter,
    Provider.APPLE: AppleAdapter,
    Provider.GARMIN: GarminAdapter,
    Provider.WHOOP: WhoopAdapter,
}


def _iter_samples(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class DeviceNormalizer:
    """Turns one Terra webhook payload into one NormalizedBatch."""

    def normalize(self, payload: TerraWebhookPayload) -> NormalizedBatch:
        provider = Provider.parse(payload.user.provider if payload.user else None)
        if provider is None:
            raise NormalizationError(
                f"Unsupported or missing provider: {payload.user.provider if payload.user else None}"
            )

        terra_user_id = payload.user.user_id if payload.user else None
        adapter = ADAPTERS[provider](terra_user_id)

        samples: list[NormalizedSample] = []
        sessions: list[NormalizedSleepSession] = []

        payload_type = payload.type.lower()
        for block in payload.data:
            if payload_type == "sleep":
                session = self._normalize_sleep(adapter, block)
                if session:
                    sessions.append(session)
                samples.extend(self._normalize_common_metrics(adapter, block, session_start=session.start if session else None))
            elif payload_type in {"daily", "activity", "body"}:
                samples.extend(self._normalize_common_metrics(adapter, block))
                if payload_type in {"daily", "activity"}:
                    samples.extend(self._normalize_movement(adapter, block))
            else:
                log.debug("normalizer.skipped_type", type=payload.type, provider=provider.value)

        samples = _dedupe_samples(samples)

        return NormalizedBatch(
            provider=provider,
            terra_user_id=terra_user_id,
            samples=samples,
            sleep_sessions=sessions,
            warnings=adapter.warnings,
        )

    # -- sleep -------------------------------------------------------------

    def _normalize_sleep(
        self, adapter: ProviderAdapter, block: dict[str, Any]
    ) -> NormalizedSleepSession | None:
        metadata = block.get("metadata") or {}
        start = parse_timestamp(_first(metadata, "start_time", "start"))
        end = parse_timestamp(_first(metadata, "end_time", "end"))
        if not start or not end or end <= start:
            adapter.warn(f"{adapter.provider.value}: sleep block with unusable start or end time")
            return None

        durations = block.get("sleep_durations_data") or {}
        segments = self._build_segments(adapter, durations, start, end)

        awake = durations.get("awake") or {}
        session = NormalizedSleepSession(
            start=start,
            end=end,
            provider=adapter.provider,
            terra_user_id=adapter.terra_user_id,
            segments=segments,
            efficiency=_as_float(_first(durations, "sleep_efficiency")),
            latency_s=_int_or_none(_first(awake, "sleep_latency_seconds")),
            awakenings=_int_or_none(_first(awake, "num_wakeup_events", "num_awakening_events")),
            is_nap=bool(_first(metadata, "is_nap") or False),
        )

        if not segments:
            adapter.warn(
                f"{adapter.provider.value}: sleep session has no hypnogram; only start and end usable"
            )

        return session

    def _build_segments(
        self,
        adapter: ProviderAdapter,
        durations: dict[str, Any],
        session_start: datetime,
        session_end: datetime,
    ) -> list[NormalizedSleepSegment]:
        """Turn a hypnogram sample list into contiguous stage intervals.

        Terra sends the hypnogram as a series of level readings at a fixed
        cadence, not as intervals. Consecutive identical levels are merged so
        that a night becomes a handful of segments rather than a few hundred
        rows, which is what the clock dial renders directly.
        """
        raw = durations.get("hypnogram_samples")
        entries: list[tuple[datetime, int]] = []
        for item in _iter_samples(raw):
            ts = parse_timestamp(_first(item, "timestamp", "time"))
            level = _int_or_none(_first(item, "level", "value"))
            if ts is not None and level is not None:
                entries.append((ts, level))

        if not entries:
            return []

        entries.sort(key=lambda pair: pair[0])
        unstaged = sum(1 for _, level in entries if level in UNSTAGED_LEVELS)
        if unstaged / len(entries) > 0.5:
            adapter.warn(
                f"{adapter.provider.value}: over half the night is unstaged; "
                "sleep stage fractions are not reliable for this session"
            )

        segments: list[NormalizedSleepSegment] = []
        run_start, run_level = entries[0]

        for index in range(1, len(entries) + 1):
            at_end = index == len(entries)
            current_ts = session_end if at_end else entries[index][0]
            current_level = None if at_end else entries[index][1]

            if at_end or current_level != run_level:
                duration = int((current_ts - run_start).total_seconds())
                if duration > 0:
                    segments.append(
                        NormalizedSleepSegment(
                            start=run_start,
                            end=current_ts,
                            stage=adapter.map_hypnogram_level(run_level),
                            duration_s=duration,
                            provider=adapter.provider,
                            terra_user_id=adapter.terra_user_id,
                        )
                    )
                if not at_end:
                    run_start, run_level = current_ts, current_level  # type: ignore[assignment]

        # Clamp a leading segment that starts before the recorded session.
        if segments and segments[0].start < session_start:
            head = segments[0]
            segments[0] = head.model_copy(
                update={
                    "start": session_start,
                    "duration_s": int((head.end - session_start).total_seconds()),
                }
            )

        return [s for s in segments if s.duration_s > 0]

    # -- shared metric blocks ---------------------------------------------

    def _normalize_common_metrics(
        self,
        adapter: ProviderAdapter,
        block: dict[str, Any],
        *,
        session_start: datetime | None = None,
    ) -> list[NormalizedSample]:
        metadata = block.get("metadata") or {}
        reference = (
            session_start
            or parse_timestamp(_first(metadata, "start_time", "start"))
            or parse_timestamp(_first(metadata, "end_time", "end"))
        )
        if reference is None:
            adapter.warn(f"{adapter.provider.value}: block with no usable metadata timestamp")
            return []

        out: list[NormalizedSample] = []

        heart_rate_data = block.get("heart_rate_data")
        if isinstance(heart_rate_data, dict):
            out.extend(adapter.heart_rate_samples(heart_rate_data, reference))

        temperature_data = block.get("temperature_data")
        if isinstance(temperature_data, dict):
            out.extend(adapter.temperature_samples(temperature_data, reference))

        respiration = block.get("respiration_data")
        if isinstance(respiration, dict):
            out.extend(self._normalize_respiration(adapter, respiration, reference))

        return [s for s in out if s is not None]

    def _normalize_respiration(
        self, adapter: ProviderAdapter, respiration: dict[str, Any], reference: datetime
    ) -> list[NormalizedSample]:
        out: list[NormalizedSample | None] = []

        breaths = respiration.get("breaths_data") or {}
        out.append(
            adapter.sample(
                reference,
                Metric.RESPIRATION_RATE,
                _as_float(_first(breaths, "avg_breaths_per_min", "avg_breaths_per_minute")),
                "brpm",
            )
        )
        for entry in _iter_samples(breaths.get("samples")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "breaths_per_min", "value"))
            if ts and value:
                out.append(adapter.sample(ts, Metric.RESPIRATION_RATE, value, "brpm"))

        oxygen = respiration.get("oxygen_saturation_data") or {}
        out.append(
            adapter.sample(
                reference,
                Metric.SPO2,
                _as_float(_first(oxygen, "avg_saturation_percentage", "avg_saturation")),
                "percent",
            )
        )
        for entry in _iter_samples(oxygen.get("saturation_samples")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "percentage", "value"))
            if ts and value:
                out.append(adapter.sample(ts, Metric.SPO2, value, "percent"))

        return [s for s in out if s is not None]

    def _normalize_movement(
        self, adapter: ProviderAdapter, block: dict[str, Any]
    ) -> list[NormalizedSample]:
        """Extract the movement rhythm.

        The activity acrophase is the single most available circadian marker,
        because every one of the four devices counts steps even when it cannot
        stage sleep or measure HRV.
        """
        metadata = block.get("metadata") or {}
        reference = parse_timestamp(_first(metadata, "start_time", "start"))
        out: list[NormalizedSample | None] = []

        distance = block.get("distance_data") or {}
        detailed = distance.get("detailed") or {}
        summary_steps = _as_float(_first(distance, "steps"))
        if reference and summary_steps is not None:
            out.append(adapter.sample(reference, Metric.STEPS, summary_steps, "count"))

        for entry in _iter_samples(detailed.get("step_samples")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "steps", "value"))
            if ts and value is not None:
                out.append(adapter.sample(ts, Metric.STEPS, value, "count"))

        met_data = block.get("MET_data") or block.get("met_data") or {}
        for entry in _iter_samples(met_data.get("MET_samples") or met_data.get("met_samples")):
            ts = parse_timestamp(_first(entry, "timestamp", "time"))
            value = _as_float(_first(entry, "level", "value"))
            if ts and value is not None:
                out.append(adapter.sample(ts, Metric.ACTIVITY_MET, value, "MET"))

        avg_met = _as_float(_first(met_data, "avg_level"))
        if reference and avg_met is not None:
            out.append(adapter.sample(reference, Metric.ACTIVITY_MET, avg_met, "MET"))

        return [s for s in out if s is not None]


def _dedupe_samples(samples: list[NormalizedSample]) -> list[NormalizedSample]:
    """Collapse duplicate readings for the same metric and minute.

    An iPhone and a paired Watch both log steps for the same walk, and Terra
    forwards both. Counting them twice would double the apparent activity
    amplitude and pull the activity acrophase toward whichever device reported
    more often. Highest confidence wins a tie.
    """
    best: dict[tuple[str, int], NormalizedSample] = {}
    for sample in samples:
        minute = int(sample.timestamp.timestamp() // 60)
        key = (sample.metric.value, minute)
        existing = best.get(key)
        if existing is None or sample.confidence > existing.confidence:
            best[key] = sample
    return sorted(best.values(), key=lambda s: (s.timestamp, s.metric.value))


def _int_or_none(value: Any) -> int | None:
    number = _as_float(value)
    return int(number) if number is not None else None

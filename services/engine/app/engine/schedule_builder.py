"""Assembles adaptive schedule objects from a phase estimate and a regimen.

This module is fully implemented. It is the part of the Engine that turns an
abstract phase position into concrete, dated windows a patient can act on, and
it works identically whether the phase offset came from the validated
estimator or the reference one.

The central operation is anchor resolution. A timing rule says something like
"ninety minutes before sleep onset". Resolution turns that into an interval on
a real calendar for a specific patient, using their own sleep timing rather
than a nominal 23:00. Every window in the output carries the anchor it came
from, so a clinician can see the reasoning and not just the result.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.engine.drug_timer import TimingCatalog
from app.schemas import (
    AdaptiveSchedule,
    CircadianProfileInput,
    CoefficientSource,
    DoseStatus,
    DrugTimingProfile,
    Medication,
    PhaseAnchor,
    PhaseEstimate,
    ResolvedWindow,
    ScheduleEntry,
    ScheduleMeta,
    TimingWindow,
    WindowStatus,
    clamp_window,
)

log = structlog.get_logger(__name__)

HOURS_PER_DAY = 24.0

# A window trimmed below this is no longer usable as a dosing instruction, so
# the builder falls through to an alternate rather than shipping a sliver.
MIN_USABLE_WINDOW_MIN = 30

# Wall clock times a conventional printed schedule would use. Kept so every
# entry can report the distance between the biological time and the printed
# one, which is the comparison the product is arguing about.
CONVENTIONAL_CLOCK_TIMES: dict[int, list[time]] = {
    1: [time(8, 0)],
    2: [time(8, 0), time(20, 0)],
    3: [time(8, 0), time(14, 0), time(20, 0)],
    4: [time(8, 0), time(12, 0), time(16, 0), time(20, 0)],
    5: [time(8, 0), time(11, 0), time(14, 0), time(17, 0), time(20, 0)],
    6: [time(6, 0), time(10, 0), time(14, 0), time(18, 0), time(22, 0), time(2, 0)],
}

# Reference geometry cannot support a confident dosing claim however clean the
# underlying data is, so entries built from it are capped here.
REFERENCE_CATALOG_CONFIDENCE_CEILING = 0.4


class AnchorResolutionError(ValueError):
    """Raised when no anchor in a profile can place a window on a calendar."""


class AnchorMap:
    """Local hours for each biological anchor, derived from one profile.

    Held as local hours rather than timestamps because an anchor recurs daily.
    Resolving to a timestamp is a separate step that needs a target date.
    """

    def __init__(
        self,
        profile: CircadianProfileInput,
        phase: PhaseEstimate,
        patient_timezone: str,
    ) -> None:
        self.tz = _load_timezone(patient_timezone)
        self.warnings: list[str] = []
        self._hours: dict[PhaseAnchor, float] = {}

        sleep = profile.sleep or {}
        onset = _as_float(sleep.get("mean_onset_hour"))
        offset = _as_float(sleep.get("mean_offset_hour"))
        midsleep = _as_float(sleep.get("mean_midsleep_hour"))

        if midsleep is None and onset is not None and offset is not None:
            duration = (offset - onset) % HOURS_PER_DAY
            midsleep = (onset + duration / 2.0) % HOURS_PER_DAY

        if onset is not None:
            self._hours[PhaseAnchor.SLEEP_ONSET] = onset % HOURS_PER_DAY
        if offset is not None:
            self._hours[PhaseAnchor.WAKE] = offset % HOURS_PER_DAY
        if midsleep is not None:
            self._hours[PhaseAnchor.MIDSLEEP] = midsleep % HOURS_PER_DAY

        if phase.dlmo_estimate is not None:
            local_dlmo = phase.dlmo_estimate.astimezone(self.tz)
            self._hours[PhaseAnchor.DLMO] = local_dlmo.hour + local_dlmo.minute / 60.0
        elif midsleep is not None:
            # Fall back to the sleep midpoint relationship rather than dropping
            # DLMO entirely. Several classes anchor only to DLMO, and losing it
            # would silently move those doses onto a weaker anchor.
            self._hours[PhaseAnchor.DLMO] = (midsleep - 7.0) % HOURS_PER_DAY
            self.warnings.append(
                "DLMO not supplied by the estimator; projected from sleep midpoint"
            )

        activity = profile.activity_cosinor or {}
        activity_acrophase = _as_float(activity.get("acrophase_hour"))
        if activity_acrophase is not None:
            self._hours[PhaseAnchor.ACTIVITY_ACROPHASE] = activity_acrophase % HOURS_PER_DAY

        temperature = profile.temperature_cosinor or {}
        temp_acrophase = _as_float(temperature.get("acrophase_hour"))
        if temp_acrophase is not None:
            # Distal skin temperature peaks close to the core temperature
            # minimum, so the skin acrophase stands in for the core nadir.
            self._hours[PhaseAnchor.TEMPERATURE_NADIR] = temp_acrophase % HOURS_PER_DAY

    @property
    def available(self) -> set[PhaseAnchor]:
        return set(self._hours)

    def hour_for(self, anchor: PhaseAnchor) -> float | None:
        if anchor is PhaseAnchor.CLOCK_TIME:
            return None
        return self._hours.get(anchor)

    def substitute(self, anchor: PhaseAnchor) -> tuple[PhaseAnchor, float] | None:
        """Find the closest usable stand in for a missing anchor.

        Order matters clinically. A window anchored to DLMO is better served by
        sleep onset than by wake, because onset sits nearer the evening
        melatonin rise. Falling straight to clock time would discard the
        patient's biology, which is the failure this product exists to fix.
        """
        preference: dict[PhaseAnchor, list[PhaseAnchor]] = {
            PhaseAnchor.DLMO: [PhaseAnchor.SLEEP_ONSET, PhaseAnchor.MIDSLEEP, PhaseAnchor.WAKE],
            PhaseAnchor.SLEEP_ONSET: [PhaseAnchor.DLMO, PhaseAnchor.MIDSLEEP, PhaseAnchor.WAKE],
            PhaseAnchor.MIDSLEEP: [PhaseAnchor.SLEEP_ONSET, PhaseAnchor.WAKE, PhaseAnchor.DLMO],
            PhaseAnchor.WAKE: [PhaseAnchor.MIDSLEEP, PhaseAnchor.SLEEP_ONSET],
            PhaseAnchor.ACTIVITY_ACROPHASE: [PhaseAnchor.WAKE, PhaseAnchor.MIDSLEEP],
            PhaseAnchor.TEMPERATURE_NADIR: [PhaseAnchor.MIDSLEEP, PhaseAnchor.WAKE],
        }
        for candidate in preference.get(anchor, []):
            hour = self._hours.get(candidate)
            if hour is not None:
                return candidate, hour
        return None

    def resolve(
        self, anchor: PhaseAnchor, offset_min: int, on_date: date
    ) -> tuple[datetime, PhaseAnchor] | None:
        """Place an anchor plus offset on a calendar date, returned in UTC."""
        hour = self.hour_for(anchor)
        resolved_anchor = anchor

        if hour is None:
            substitution = self.substitute(anchor)
            if substitution is None:
                return None
            resolved_anchor, hour = substitution
            note = f"Anchor {anchor.value} unavailable; substituted {resolved_anchor.value}"
            if note not in self.warnings:
                self.warnings.append(note)

        local = datetime.combine(on_date, time(0, 0), tzinfo=self.tz) + timedelta(hours=hour)
        return (local + timedelta(minutes=offset_min)).astimezone(timezone.utc), resolved_anchor


class ScheduleBuilder:
    """Turns a regimen plus a phase estimate into an AdaptiveSchedule."""

    def __init__(self, catalog: TimingCatalog) -> None:
        self.catalog = catalog

    def build(
        self,
        *,
        patient_id: str,
        profile: CircadianProfileInput,
        phase: PhaseEstimate,
        medications: list[Medication],
        patient_timezone: str = "UTC",
        horizon_hours: int = 26,
        reference_time: datetime | None = None,
        supersedes: str | None = None,
        schedule_version: int = 1,
        min_completeness: float = 0.5,
    ) -> AdaptiveSchedule:
        now = reference_time or datetime.now(timezone.utc)
        valid_from = now
        valid_until = now + timedelta(hours=horizon_hours)

        anchors = AnchorMap(profile, phase, patient_timezone)
        warnings: list[str] = list(anchors.warnings)

        if not anchors.available:
            raise AnchorResolutionError(
                "Profile contains no usable biological anchor; a schedule cannot be "
                "placed without at least one of sleep onset, wake, or midsleep"
            )

        entries: list[ScheduleEntry] = []
        for medication in medications:
            if not medication.active:
                continue
            entries.extend(
                self._entries_for_medication(
                    medication=medication,
                    anchors=anchors,
                    phase=phase,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    warnings=warnings,
                )
            )

        provisional = (
            profile.data_completeness < min_completeness
            or self.catalog.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
            or phase.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
        )

        if self.catalog.coefficient_source is CoefficientSource.REFERENCE_FALLBACK:
            warnings.append(
                "Timing catalog is reference geometry. Windows are structurally correct "
                "and clinically unvalidated."
            )
        if profile.data_completeness < min_completeness:
            warnings.append(
                f"Profile completeness {profile.data_completeness:.2f} is below "
                f"{min_completeness:.2f}; schedule is provisional"
            )
        if not entries:
            warnings.append("No dose windows fell inside the schedule horizon")

        meta = ScheduleMeta(
            profile_completeness=profile.data_completeness,
            phase_confidence=phase.confidence,
            coefficient_source=(
                CoefficientSource.PRIVATE_VALIDATED
                if phase.coefficient_source is CoefficientSource.PRIVATE_VALIDATED
                and self.catalog.coefficient_source is CoefficientSource.PRIVATE_VALIDATED
                else CoefficientSource.REFERENCE_FALLBACK
            ),
            method_version=f"{phase.method_version}+{self.catalog.catalog_version}",
            provisional=provisional,
            warnings=_dedupe(warnings + phase.warnings),
        )

        return AdaptiveSchedule(
            patient_id=patient_id,
            generated_at=now,
            valid_from=valid_from,
            valid_until=valid_until,
            schedule_version=schedule_version,
            supersedes=supersedes,
            timezone=patient_timezone,
            phase=phase,
            entries=sorted(entries, key=lambda e: e.window.target),
            meta=meta,
        )

    # -- per medication ----------------------------------------------------

    def _entries_for_medication(
        self,
        *,
        medication: Medication,
        anchors: AnchorMap,
        phase: PhaseEstimate,
        valid_from: datetime,
        valid_until: datetime,
        warnings: list[str],
    ) -> list[ScheduleEntry]:
        entries: list[ScheduleEntry] = []

        for dose_index in range(medication.doses_per_day):
            timing = self.catalog.profile_for(
                medication.drug_class, dose_index, medication.doses_per_day
            )

            for target_date in _dates_spanning(valid_from, valid_until, anchors.tz):
                entry = self._build_entry(
                    medication=medication,
                    timing=timing,
                    dose_index=dose_index,
                    anchors=anchors,
                    phase=phase,
                    target_date=target_date,
                    valid_from=valid_from,
                    valid_until=valid_until,
                )
                if entry is not None:
                    entries.append(entry)

        return _deduplicate_entries(entries)

    def _build_entry(
        self,
        *,
        medication: Medication,
        timing: DrugTimingProfile,
        dose_index: int,
        anchors: AnchorMap,
        phase: PhaseEstimate,
        target_date: date,
        valid_from: datetime,
        valid_until: datetime,
    ) -> ScheduleEntry | None:
        if medication.fixed_clock_time:
            window = self._fixed_clock_window(medication, dose_index, anchors, target_date)
        else:
            window = self._resolve(timing.optimal, anchors, target_date)

        if window is None:
            return None

        avoid = [
            resolved
            for resolved in (
                self._resolve(rule, anchors, offset_date)
                for rule in timing.contraindicated
                # A contraindicated window can belong to the day before or
                # after and still overlap this dose, so all three are checked.
                for offset_date in _neighbouring_dates(target_date)
            )
            if resolved is not None
        ]

        alternates = [
            resolved
            for resolved in (self._resolve(rule, anchors, target_date) for rule in timing.acceptable)
            if resolved is not None
        ]

        window = self._apply_contraindications(window, avoid, alternates)

        clamped = clamp_window(window, valid_from, valid_until)
        if clamped is None:
            return None

        confidence = self._entry_confidence(phase)

        return ScheduleEntry(
            medication_id=medication.id,
            display_name=medication.display_name,
            drug_class=medication.drug_class,
            rxnorm_code=medication.rxnorm_code,
            dose_amount=medication.dose_amount,
            dose_unit=medication.dose_unit,
            dose_index=dose_index,
            window=clamped,
            alternate_windows=[
                trimmed
                for trimmed in (clamp_window(a, valid_from, valid_until) for a in alternates)
                if trimmed is not None and trimmed.start != clamped.start
            ],
            avoid_windows=[
                trimmed
                for trimmed in (clamp_window(a, valid_from, valid_until) for a in avoid)
                if trimmed is not None
            ],
            status=DoseStatus.PENDING,
            confidence=confidence,
            conventional_time=_conventional_time(
                medication, dose_index, clamped.target, anchors.tz
            ),
        )

    def _resolve(
        self, rule: TimingWindow, anchors: AnchorMap, target_date: date
    ) -> ResolvedWindow | None:
        resolved = anchors.resolve(rule.anchor, rule.offset_min, target_date)
        if resolved is None:
            return None

        start, used_anchor = resolved
        end = start + timedelta(minutes=rule.duration_min)
        target = start + timedelta(minutes=int(rule.duration_min * rule.target_fraction))

        return ResolvedWindow(
            start=start,
            end=end,
            target=min(max(target, start), end),
            status=rule.status,
            anchor=used_anchor,
            anchor_offset_min=rule.offset_min,
            rationale=rule.rationale,
        )

    def _fixed_clock_window(
        self,
        medication: Medication,
        dose_index: int,
        anchors: AnchorMap,
        target_date: date,
    ) -> ResolvedWindow | None:
        """Honour a clinician pinned clock time.

        The engine does not override a pinned time. It places the dose where
        the clinician asked and lets the interface show how far that sits from
        the biological window, which keeps the decision with the prescriber.
        """
        parsed = _parse_clock(medication.fixed_clock_time)
        if parsed is None:
            return None

        local = datetime.combine(target_date, parsed, tzinfo=anchors.tz) + timedelta(
            hours=dose_index * (24 / max(medication.doses_per_day, 1))
        )
        start = local.astimezone(timezone.utc) - timedelta(minutes=30)
        end = start + timedelta(minutes=60)

        return ResolvedWindow(
            start=start,
            end=end,
            target=start + timedelta(minutes=30),
            status=WindowStatus.ACCEPTABLE,
            anchor=PhaseAnchor.CLOCK_TIME,
            anchor_offset_min=0,
            rationale="Clock time pinned by the prescriber; not phase adjusted",
        )

    def _apply_contraindications(
        self,
        window: ResolvedWindow,
        avoid: list[ResolvedWindow],
        alternates: list[ResolvedWindow],
    ) -> ResolvedWindow:
        """Move a dose out of a contraindicated period where possible.

        Order of preference: keep the window if it is clean, trim it to the
        clean part if enough remains, move to a clean alternate, and only then
        surface it as contraindicated. Silently dropping the dose is not an
        option, since a missing row reads as "nothing due" to the patient.
        """
        overlapping = [a for a in avoid if _overlaps(window, a)]
        if not overlapping:
            return window

        trimmed = _subtract(window, overlapping)
        if trimmed is not None and trimmed.duration_min >= MIN_USABLE_WINDOW_MIN:
            return trimmed.model_copy(
                update={
                    "status": WindowStatus.ACCEPTABLE,
                    "rationale": f"{window.rationale}; trimmed clear of a contraindicated period",
                }
            )

        for alternate in alternates:
            if not any(_overlaps(alternate, a) for a in avoid):
                return alternate.model_copy(
                    update={
                        "rationale": f"{alternate.rationale}; moved off a contraindicated period"
                    }
                )

        return window.model_copy(
            update={
                "status": WindowStatus.CONTRAINDICATED,
                "rationale": f"{window.rationale}; no clear window exists in this cycle",
            }
        )

    def _entry_confidence(self, phase: PhaseEstimate) -> float:
        confidence = phase.confidence
        if self.catalog.coefficient_source is CoefficientSource.REFERENCE_FALLBACK:
            confidence = min(confidence, REFERENCE_CATALOG_CONFIDENCE_CEILING)
        return round(max(0.0, min(confidence, 1.0)), 4)


# ---------------------------------------------------------------------------
# Window arithmetic
# ---------------------------------------------------------------------------


def _overlaps(a: ResolvedWindow, b: ResolvedWindow) -> bool:
    return a.start < b.end and b.start < a.end


def _subtract(window: ResolvedWindow, blockers: list[ResolvedWindow]) -> ResolvedWindow | None:
    """Return the longest sub interval of window that no blocker covers.

    Only the longest run is kept. A dosing instruction with two disjoint valid
    intervals is not something a patient can act on.
    """
    edges = sorted({window.start, window.end} | {
        moment
        for blocker in blockers
        for moment in (blocker.start, blocker.end)
        if window.start <= moment <= window.end
    })

    best: tuple[datetime, datetime] | None = None
    for left, right in zip(edges, edges[1:]):
        if right <= left:
            continue
        midpoint = left + (right - left) / 2
        blocked = any(b.start <= midpoint < b.end for b in blockers)
        if blocked:
            continue
        if best is None or (right - left) > (best[1] - best[0]):
            best = (left, right)

    if best is None:
        return None

    start, end = best
    target = min(max(window.target, start), end)
    return window.model_copy(update={"start": start, "end": end, "target": target})


def _dates_spanning(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    """Local dates the horizon touches, plus one on each side.

    The margin matters: a window anchored to sleep onset on the previous local
    date can still open inside the horizon, and a window anchored to tomorrow's
    wake can too.
    """
    first = (start.astimezone(tz) - timedelta(days=1)).date()
    last = (end.astimezone(tz) + timedelta(days=1)).date()
    span = (last - first).days
    return [first + timedelta(days=offset) for offset in range(span + 1)]


def _neighbouring_dates(target: date) -> Iterable[date]:
    return (target - timedelta(days=1), target, target + timedelta(days=1))


def _deduplicate_entries(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """Drop repeats produced by scanning neighbouring dates.

    The date scan is deliberately generous so no window is missed at a horizon
    edge, which means the same dose can be generated from two adjacent dates.
    Keyed on medication, dose index, and target minute.
    """
    seen: dict[tuple[str, int, int], ScheduleEntry] = {}
    for entry in entries:
        key = (
            entry.medication_id,
            entry.dose_index,
            int(entry.window.target.timestamp() // 60),
        )
        seen.setdefault(key, entry)
    return list(seen.values())


def _conventional_time(
    medication: Medication, dose_index: int, near: datetime, tz: ZoneInfo
) -> datetime | None:
    """The wall clock time a printed schedule would have given for this dose."""
    if medication.fixed_clock_time:
        clock = _parse_clock(medication.fixed_clock_time)
        times = [clock] if clock else []
    else:
        times = CONVENTIONAL_CLOCK_TIMES.get(medication.doses_per_day, [])

    if not times or dose_index >= len(times):
        return None

    local_near = near.astimezone(tz)
    candidate = datetime.combine(local_near.date(), times[dose_index], tzinfo=tz)

    # Pick whichever occurrence of that clock time is nearest the biological
    # target, so the reported drift is the real distance and never a near
    # twenty four hour artefact of picking the wrong day.
    options = [candidate - timedelta(days=1), candidate, candidate + timedelta(days=1)]
    return min(options, key=lambda option: abs(option - local_near)).astimezone(timezone.utc)


def _parse_clock(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hour_str, _, minute_str = value.partition(":")
        return time(int(hour_str), int(minute_str or 0))
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("schedule.unknown_timezone", requested=name)
        return ZoneInfo("UTC")

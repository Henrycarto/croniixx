"""Schedule builder tests.

The builder is fully implemented, so these tests assert real behaviour: that
windows land where the anchors say they should, that a delayed patient's
schedule moves with them, that contraindicated periods are respected, and that
the object holds together as a contract for the app and the queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.drug_timer import ReferenceTimingCatalog, load_catalog
from app.engine.phase_calculator import ReferencePhaseEstimator
from app.engine.schedule_builder import AnchorResolutionError, ScheduleBuilder
from app.schemas import (
    CircadianProfileInput,
    CoefficientSource,
    DrugClass,
    PhaseAnchor,
    WindowStatus,
)
from tests.conftest import REFERENCE_NOW, make_medication, make_profile

ESTIMATOR = ReferencePhaseEstimator()
BUILDER = ScheduleBuilder(ReferenceTimingCatalog())


def build(profile, medications, *, timezone_name: str = "UTC", horizon: int = 26):
    phase = ESTIMATOR.estimate(profile, patient_timezone=timezone_name, now=REFERENCE_NOW)
    return BUILDER.build(
        patient_id=profile.patient_id,
        profile=profile,
        phase=phase,
        medications=medications,
        patient_timezone=timezone_name,
        horizon_hours=horizon,
        reference_time=REFERENCE_NOW,
    )


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_schedule_carries_a_complete_object(profile):
    schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)])

    assert schedule.patient_id == "patient-1"
    assert schedule.entry_count >= 1
    assert schedule.valid_until > schedule.valid_from
    assert schedule.meta.method_version
    assert schedule.next_dose_at is not None

    entry = schedule.entries[0]
    assert entry.window.start < entry.window.target < entry.window.end
    assert entry.window.duration_min > 0
    assert entry.window.anchor in set(PhaseAnchor)
    assert entry.rxnorm_code is None
    assert 0.0 <= entry.confidence <= 1.0


def test_every_window_falls_inside_the_horizon(profile):
    schedule = build(
        profile,
        [
            make_medication(DrugClass.CORTICOSTEROID, med_id="a", name="A"),
            make_medication(DrugClass.STATIN, med_id="b", name="B"),
            make_medication(DrugClass.ANTIHYPERTENSIVE, med_id="c", name="C"),
        ],
    )

    assert schedule.entries
    for entry in schedule.entries:
        assert entry.window.start >= schedule.valid_from
        assert entry.window.end <= schedule.valid_until


def test_entries_come_back_in_chronological_order(profile):
    schedule = build(
        profile,
        [
            make_medication(DrugClass.STATIN, med_id="a", name="A"),
            make_medication(DrugClass.CORTICOSTEROID, med_id="b", name="B"),
            make_medication(DrugClass.CHEMOTHERAPY_ANTIMETABOLITE, med_id="c", name="C"),
        ],
    )
    targets = [e.window.target for e in schedule.entries_in_order()]
    assert targets == sorted(targets)


def test_no_duplicate_doses_from_the_neighbouring_date_scan(profile):
    schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)])
    keys = [(e.medication_id, e.dose_index, e.window.target) for e in schedule.entries]
    assert len(keys) == len(set(keys))


def test_inactive_medications_are_skipped(profile):
    med = make_medication(DrugClass.STATIN).model_copy(update={"active": False})
    schedule = build(profile, [med])
    assert schedule.entry_count == 0


# ---------------------------------------------------------------------------
# Anchoring: the point of the whole system
# ---------------------------------------------------------------------------


def test_corticosteroid_lands_near_the_patients_own_wake_time(profile):
    schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)])
    entry = schedule.entries[0]

    # The reference geometry anchors this class at wake plus zero minutes with
    # a two hour window, so the window opens at the patient's 07:00 wake.
    assert entry.window.anchor is PhaseAnchor.WAKE
    assert entry.window.start.astimezone(timezone.utc).hour == 7


def test_a_delayed_patient_gets_a_later_window_than_an_early_one():
    early = make_profile(onset=21.0, offset=5.0, midsleep=1.0)
    late = make_profile(onset=2.0, offset=10.0, midsleep=6.0)

    early_schedule = build(early, [make_medication(DrugClass.CORTICOSTEROID)])
    late_schedule = build(late, [make_medication(DrugClass.CORTICOSTEROID)])

    early_hour = early_schedule.entries[0].window.target.astimezone(timezone.utc).hour
    late_hour = late_schedule.entries[0].window.target.astimezone(timezone.utc).hour

    assert late_hour > early_hour


def test_the_window_moves_by_the_same_amount_the_patient_did():
    baseline = make_profile(onset=23.0, offset=7.0, midsleep=3.0)
    shifted = make_profile(onset=1.0, offset=9.0, midsleep=5.0)

    baseline_target = build(baseline, [make_medication(DrugClass.CORTICOSTEROID)]).entries[0].window.target
    shifted_target = build(shifted, [make_medication(DrugClass.CORTICOSTEROID)]).entries[0].window.target

    # Both profiles are two hours apart, so the dose should be too.
    assert (shifted_target - baseline_target) == timedelta(hours=2)


def test_patient_timezone_is_respected():
    profile = make_profile(onset=23.0, offset=7.0, midsleep=3.0)
    utc_schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)], timezone_name="UTC")
    berlin_schedule = build(
        profile, [make_medication(DrugClass.CORTICOSTEROID)], timezone_name="Europe/Berlin"
    )

    # Local 07:00 in Berlin during April is 05:00 UTC.
    assert utc_schedule.entries[0].window.start.astimezone(timezone.utc).hour == 7
    assert berlin_schedule.entries[0].window.start.astimezone(timezone.utc).hour == 5


def test_missing_dlmo_is_projected_rather_than_dropping_the_dose():
    profile = make_profile()
    phase = ESTIMATOR.estimate(profile, now=REFERENCE_NOW).model_copy(
        update={"dlmo_estimate": None}
    )
    schedule = BUILDER.build(
        patient_id=profile.patient_id,
        profile=profile,
        phase=phase,
        medications=[make_medication(DrugClass.ANTIHYPERTENSIVE)],
        horizon_hours=26,
        reference_time=REFERENCE_NOW,
    )
    assert schedule.entry_count >= 1
    assert any("projected from sleep midpoint" in w for w in schedule.meta.warnings)


def test_a_profile_with_no_sleep_data_cannot_be_scheduled():
    empty = CircadianProfileInput(
        patient_id="patient-1",
        window_start=REFERENCE_NOW - timedelta(days=14),
        window_end=REFERENCE_NOW,
        sleep={},
        data_completeness=0.0,
    )
    phase = ESTIMATOR.estimate(empty, now=REFERENCE_NOW)
    with pytest.raises(AnchorResolutionError):
        BUILDER.build(
            patient_id="patient-1",
            profile=empty,
            phase=phase,
            medications=[make_medication(DrugClass.STATIN)],
            reference_time=REFERENCE_NOW,
        )


def test_a_missing_anchor_substitutes_rather_than_falling_to_clock_time():
    # Activity acrophase is absent, so a class anchored to it must borrow a
    # sleep anchor instead of being pinned to the wall clock.
    profile = make_profile()
    profile = profile.model_copy(update={"activity_cosinor": None})

    schedule = build(profile, [make_medication(DrugClass.CHEMOTHERAPY_PLATINUM)])
    assert schedule.entry_count >= 1
    assert schedule.entries[0].window.anchor is not PhaseAnchor.CLOCK_TIME


# ---------------------------------------------------------------------------
# Contraindications
# ---------------------------------------------------------------------------


def test_a_dose_is_moved_or_marked_rather_than_dropped(profile):
    schedule = build(profile, [make_medication(DrugClass.CHEMOTHERAPY_ANTIMETABOLITE)])
    assert schedule.entry_count >= 1

    entry = schedule.entries[0]
    if entry.avoid_windows:
        overlaps = [
            avoid
            for avoid in entry.avoid_windows
            if entry.window.start < avoid.end and avoid.start < entry.window.end
        ]
        # An overlap that survived must be labelled, never left as optimal.
        if overlaps:
            assert entry.window.status is WindowStatus.CONTRAINDICATED


def test_contraindicated_windows_are_reported_to_the_interface(profile):
    schedule = build(profile, [make_medication(DrugClass.CHEMOTHERAPY_TOPOISOMERASE)])
    entry = schedule.entries[0]
    assert entry.avoid_windows, "the dial needs the red arcs to render"
    for avoid in entry.avoid_windows:
        assert avoid.status is WindowStatus.CONTRAINDICATED


# ---------------------------------------------------------------------------
# Multi dose and pinned regimens
# ---------------------------------------------------------------------------


def test_multiple_daily_doses_are_spread_across_the_biological_day(profile):
    schedule = build(profile, [make_medication(DrugClass.IMMUNOSUPPRESSANT, doses_per_day=3)])
    targets = sorted(e.window.target for e in schedule.entries)
    assert len(targets) >= 2

    gaps = [(b - a).total_seconds() / 3600.0 for a, b in zip(targets, targets[1:])]
    assert all(gap >= 4.0 for gap in gaps)


def test_a_pinned_clock_time_is_honoured_and_labelled(profile):
    med = make_medication(DrugClass.STATIN, fixed_clock_time="21:00")
    schedule = build(profile, [med])

    assert schedule.entry_count >= 1
    entry = schedule.entries[0]
    assert entry.window.anchor is PhaseAnchor.CLOCK_TIME
    assert entry.window.target.astimezone(timezone.utc).hour == 21
    assert "prescriber" in entry.window.rationale


def test_drift_from_the_conventional_schedule_is_reported(profile):
    schedule = build(profile, [make_medication(DrugClass.STATIN)])
    entry = schedule.entries[0]

    assert entry.conventional_time is not None
    drift = entry.drift_from_conventional_min
    assert drift is not None
    # The printed schedule would say 08:00. A statin anchored to the evening
    # is hours away from that, which is the argument the product makes.
    assert abs(drift) > 120


def test_drift_is_never_a_near_full_day_artefact(profile):
    schedule = build(
        profile,
        [
            make_medication(DrugClass.STATIN, med_id="a", name="A"),
            make_medication(DrugClass.CORTICOSTEROID, med_id="b", name="B"),
            make_medication(DrugClass.ANTIHYPERTENSIVE, med_id="c", name="C"),
        ],
    )
    for entry in schedule.entries:
        if entry.drift_from_conventional_min is not None:
            assert abs(entry.drift_from_conventional_min) <= 12 * 60


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_reference_mode_marks_the_schedule_provisional(profile):
    schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)])
    assert schedule.meta.provisional is True
    assert schedule.meta.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
    assert any("reference geometry" in w for w in schedule.meta.warnings)


def test_thin_profiles_are_marked_provisional_and_say_why():
    thin = make_profile(nights=2, completeness=0.2)
    schedule = build(thin, [make_medication(DrugClass.CORTICOSTEROID)])
    assert schedule.meta.provisional is True
    assert any("completeness" in w for w in schedule.meta.warnings)


def test_catalog_loader_falls_back_without_the_private_package():
    catalog = load_catalog()
    assert catalog.coefficient_source is CoefficientSource.REFERENCE_FALLBACK
    assert catalog.catalog_version.startswith("reference")


def test_unmodelled_drug_class_is_scheduled_but_not_claimed(profile):
    schedule = build(profile, [make_medication(DrugClass.UNCLASSIFIED)])
    assert schedule.entry_count >= 1
    assert schedule.entries[0].window.status is WindowStatus.SUBOPTIMAL


# ---------------------------------------------------------------------------
# Queue contract
# ---------------------------------------------------------------------------


def test_reminder_payload_is_compact_and_ordered(profile):
    schedule = build(
        profile,
        [
            make_medication(DrugClass.CORTICOSTEROID, med_id="a", name="A"),
            make_medication(DrugClass.STATIN, med_id="b", name="B"),
        ],
    )
    payload = schedule.to_reminder_payload()

    assert payload["schedule_id"] == schedule.schedule_id
    assert set(payload) == {"schedule_id", "patient_id", "timezone", "valid_until", "doses"}
    targets = [dose["target"] for dose in payload["doses"]]
    assert targets == sorted(targets)
    for dose in payload["doses"]:
        assert "rationale" not in dose


def test_schedule_serializes_and_reloads_without_loss(profile):
    from app.schemas import AdaptiveSchedule

    schedule = build(profile, [make_medication(DrugClass.CORTICOSTEROID)])
    reloaded = AdaptiveSchedule.model_validate(schedule.model_dump(mode="json"))

    assert reloaded.schedule_id == schedule.schedule_id
    assert reloaded.entry_count == schedule.entry_count
    assert reloaded.entries[0].window.target == schedule.entries[0].window.target


def test_window_target_must_sit_inside_the_window():
    from app.schemas import ResolvedWindow

    start = datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        ResolvedWindow(
            start=start,
            end=start + timedelta(hours=2),
            target=start + timedelta(hours=5),
            status=WindowStatus.OPTIMAL,
            anchor=PhaseAnchor.WAKE,
            anchor_offset_min=0,
        )

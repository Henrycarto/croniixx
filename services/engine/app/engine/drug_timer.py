"""Chronopharmacological timing windows per drug class.

===========================================================================
PRIVATE COMPONENT. The clinical timing windows are not in this repository.
===========================================================================

What is public, here, in full:
    the TimingCatalog interface, the window geometry (anchor, offset, width,
    target position inside the window), how windows compose into a full day of
    doses, and how a contraindicated window overrides an optimal one.

What is private, in the `croniixx-chrono` package on a private index:
    the per class offsets and widths, the receptor and enzyme rhythm models
    they were derived from, the toxicity windows, and the interaction rules
    between concurrent agents in one regimen.

The reference catalog below exists so the schedule builder, the clock dial,
and the mobile app can be exercised without the private package. Its numbers
are placeholder geometry chosen to make windows visibly distinct on the dial.
They are not clinical guidance and must not be used to time a real dose. Every
window it produces is stamped CoefficientSource.REFERENCE_FALLBACK and the
schedule that carries it is marked provisional.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import structlog

from app.schemas import (
    CoefficientSource,
    DrugClass,
    DrugTimingProfile,
    PhaseAnchor,
    TimingWindow,
    WindowStatus,
)

log = structlog.get_logger(__name__)


@runtime_checkable
class TimingCatalog(Protocol):
    """The contract any timing catalog must satisfy."""

    catalog_version: str
    coefficient_source: CoefficientSource

    def profile_for(self, drug_class: DrugClass, dose_index: int, doses_per_day: int) -> DrugTimingProfile:
        """Return the windows for one dose of one drug class."""
        ...

    def supports(self, drug_class: DrugClass) -> bool:
        """Whether this catalog has a timing model for the class."""
        ...


# ---------------------------------------------------------------------------
# Reference catalog
# ---------------------------------------------------------------------------

# Placeholder geometry. Offsets are minutes from the named anchor, widths are
# minutes. These were chosen to be structurally plausible and visually
# distinct, not to be correct. The validated catalog replaces this table
# wholesale rather than adjusting it.
_REFERENCE_GEOMETRY: dict[DrugClass, dict[str, object]] = {
    DrugClass.CORTICOSTEROID: {
        "anchor": PhaseAnchor.WAKE,
        "offset_min": 0,
        "duration_min": 120,
        "target_fraction": 0.25,
        "avoid": [(PhaseAnchor.DLMO, -120, 360)],
        "note": "Anchored near the endogenous cortisol rise",
    },
    DrugClass.ANTIHYPERTENSIVE: {
        "anchor": PhaseAnchor.DLMO,
        "offset_min": -60,
        "duration_min": 180,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Anchored to the evening decline in blood pressure",
    },
    DrugClass.STATIN: {
        "anchor": PhaseAnchor.SLEEP_ONSET,
        "offset_min": -120,
        "duration_min": 150,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Anchored to the nocturnal peak in cholesterol synthesis",
    },
    DrugClass.CHEMOTHERAPY_ANTIMETABOLITE: {
        "anchor": PhaseAnchor.MIDSLEEP,
        "offset_min": 0,
        "duration_min": 240,
        "target_fraction": 0.5,
        "avoid": [(PhaseAnchor.WAKE, 120, 480)],
        "note": "Anchored to the trough in healthy tissue proliferation",
    },
    DrugClass.CHEMOTHERAPY_PLATINUM: {
        "anchor": PhaseAnchor.ACTIVITY_ACROPHASE,
        "offset_min": -60,
        "duration_min": 240,
        "target_fraction": 0.5,
        "avoid": [(PhaseAnchor.MIDSLEEP, -120, 300)],
        "note": "Anchored against the renal clearance rhythm",
    },
    DrugClass.CHEMOTHERAPY_TOPOISOMERASE: {
        "anchor": PhaseAnchor.WAKE,
        "offset_min": 180,
        "duration_min": 240,
        "target_fraction": 0.5,
        "avoid": [(PhaseAnchor.MIDSLEEP, -180, 360)],
        "note": "Anchored to the daytime repair enzyme rhythm",
    },
    DrugClass.IMMUNOSUPPRESSANT: {
        "anchor": PhaseAnchor.WAKE,
        "offset_min": 30,
        "duration_min": 90,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Narrow window, trough concentration is the monitored quantity",
    },
    DrugClass.THYROID_REPLACEMENT: {
        "anchor": PhaseAnchor.WAKE,
        "offset_min": -30,
        "duration_min": 90,
        "target_fraction": 0.3,
        "avoid": [],
        "note": "Anchored before the first meal of the biological day",
    },
    DrugClass.PROTON_PUMP_INHIBITOR: {
        "anchor": PhaseAnchor.WAKE,
        "offset_min": -45,
        "duration_min": 75,
        "target_fraction": 0.4,
        "avoid": [],
        "note": "Anchored ahead of the first proton pump activation",
    },
    DrugClass.ANTICOAGULANT: {
        "anchor": PhaseAnchor.DLMO,
        "offset_min": 60,
        "duration_min": 180,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Anchored to the evening rise in coagulation activity",
    },
    DrugClass.BRONCHODILATOR: {
        "anchor": PhaseAnchor.MIDSLEEP,
        "offset_min": -120,
        "duration_min": 180,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Anchored ahead of the nocturnal airway calibre trough",
    },
    DrugClass.NSAID: {
        "anchor": PhaseAnchor.SLEEP_ONSET,
        "offset_min": -90,
        "duration_min": 150,
        "target_fraction": 0.5,
        "avoid": [],
        "note": "Anchored ahead of the overnight inflammatory rise",
    },
    DrugClass.CHRONOBIOTIC: {
        "anchor": PhaseAnchor.DLMO,
        "offset_min": -30,
        "duration_min": 60,
        "target_fraction": 0.5,
        "avoid": [(PhaseAnchor.WAKE, -60, 300)],
        "note": "Phase response is sign inverted across DLMO, so the window is narrow",
    },
}

# Drugs with more than one dose a day cannot all sit on the same anchor. The
# spacing here keeps later doses inside the biological day rather than stacking
# them against the same reference point.
_MULTI_DOSE_SPACING_MIN = 360


class ReferenceTimingCatalog:
    """Placeholder geometry so the system runs without the private package."""

    catalog_version = "reference-geometry-1.0"
    coefficient_source = CoefficientSource.REFERENCE_FALLBACK

    def supports(self, drug_class: DrugClass) -> bool:
        return drug_class in _REFERENCE_GEOMETRY

    def profile_for(
        self, drug_class: DrugClass, dose_index: int, doses_per_day: int
    ) -> DrugTimingProfile:
        geometry = _REFERENCE_GEOMETRY.get(drug_class)

        if geometry is None:
            # An unclassified drug still needs a place on the schedule. It is
            # anchored to wake with a wide window and marked suboptimal, which
            # renders as an uncoloured row rather than a confident claim.
            return DrugTimingProfile(
                drug_class=drug_class,
                dose_index=dose_index,
                optimal=TimingWindow(
                    anchor=PhaseAnchor.WAKE,
                    offset_min=60 + dose_index * _MULTI_DOSE_SPACING_MIN,
                    duration_min=240,
                    status=WindowStatus.SUBOPTIMAL,
                    rationale="No chronopharmacological model for this class; "
                    "window follows the patient's biological day only",
                ),
                coefficient_source=self.coefficient_source,
                evidence_note="Unmodelled class",
            )

        anchor: PhaseAnchor = geometry["anchor"]  # type: ignore[assignment]
        base_offset: int = geometry["offset_min"]  # type: ignore[assignment]
        duration: int = geometry["duration_min"]  # type: ignore[assignment]
        target_fraction: float = geometry["target_fraction"]  # type: ignore[assignment]
        avoid: list[tuple[PhaseAnchor, int, int]] = geometry["avoid"]  # type: ignore[assignment]
        note: str = geometry["note"]  # type: ignore[assignment]

        offset = base_offset + dose_index * _spacing_for(doses_per_day)

        optimal = TimingWindow(
            anchor=anchor,
            offset_min=offset,
            duration_min=duration,
            status=WindowStatus.OPTIMAL,
            target_fraction=target_fraction,
            rationale=f"{note} (reference geometry, not clinical guidance)",
        )

        # An acceptable window flanks the optimal one on both sides. It exists
        # so a patient who misses the target still has a defensible time to
        # take the dose rather than a binary success or failure.
        acceptable = [
            TimingWindow(
                anchor=anchor,
                offset_min=offset - duration // 2,
                duration_min=duration // 2,
                status=WindowStatus.ACCEPTABLE,
                rationale="Early flank of the reference window",
            ),
            TimingWindow(
                anchor=anchor,
                offset_min=offset + duration,
                duration_min=duration // 2,
                status=WindowStatus.ACCEPTABLE,
                rationale="Late flank of the reference window",
            ),
        ]

        contraindicated = [
            TimingWindow(
                anchor=avoid_anchor,
                offset_min=avoid_offset,
                duration_min=avoid_duration,
                status=WindowStatus.CONTRAINDICATED,
                rationale="Reference avoidance window",
            )
            for avoid_anchor, avoid_offset, avoid_duration in avoid
        ]

        return DrugTimingProfile(
            drug_class=drug_class,
            dose_index=dose_index,
            optimal=optimal,
            acceptable=acceptable,
            contraindicated=contraindicated,
            coefficient_source=self.coefficient_source,
            evidence_note=note,
        )


def _spacing_for(doses_per_day: int) -> int:
    """Spread multiple daily doses across the biological day.

    Dividing the waking span rather than the calendar day keeps a four times
    daily regimen from placing its last dose in the middle of the patient's
    biological night.
    """
    if doses_per_day <= 1:
        return 0
    waking_span_min = 16 * 60
    return waking_span_min // doses_per_day


def load_catalog() -> TimingCatalog:
    """Return the validated catalog when installed, otherwise the reference one."""
    try:
        from croniixx_chrono import ValidatedTimingCatalog  # type: ignore[import-not-found]
    except ImportError:
        log.info("drug_timer.reference_mode", reason="croniixx_chrono not installed")
        return ReferenceTimingCatalog()

    catalog = ValidatedTimingCatalog()
    if not isinstance(catalog, TimingCatalog):
        log.error("drug_timer.private_package_contract_mismatch")
        return ReferenceTimingCatalog()

    log.info("drug_timer.validated_mode", catalog_version=catalog.catalog_version)
    return catalog

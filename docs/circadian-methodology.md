# Circadian methodology

This document states what Croniixx measures, what it infers, and where the
line between the two sits. It is written for someone who will check the claims.

## The quantity being estimated

Circadian phase is the position of the internal oscillator relative to the
solar day. The reference standard for measuring it in humans is dim light
melatonin onset, taken from serial saliva or plasma sampling under controlled
light. That is a laboratory procedure. It cannot be run nightly on an
outpatient managing a chemotherapy regimen at home.

Croniixx estimates the same quantity from wearable data. The output is a
signed offset in minutes against a population reference, where positive means
a delayed phase and negative means an advanced one.

The estimate is not a melatonin measurement and the system does not present it
as one. Every phase estimate carries a confidence value and the identifier of
the method that produced it.

## What is computed in the open

The Sync service produces a circadian profile from fourteen days of normalized
wearable data. Every measure in it is published, reproducible, and checkable
against the raw record.

**Sleep timing.** Onset, offset, and midpoint, averaged as angles rather than
as numbers. Midpoints at 23:30 and 00:30 average to midnight; an arithmetic
mean puts them at noon, which is the full width of the error the product
exists to avoid. Midsleep variability is reported as a circular standard
deviation. Naps are excluded, because a midday nap has a midpoint near noon
and averaging it with a night drags the estimate into the evening.

**Cosinor fits.** Single component regression at a fixed 24 hour period for
heart rate variability, skin temperature, activity, and resting heart rate.
Each returns mesor, amplitude, acrophase, and an r squared. The period is
fixed rather than fitted: a free period on two weeks of wearable data is
underdetermined and drifts toward whatever the noise favours, and the
clinically useful quantity is the phase against the solar day rather than the
length of the patient's tau.

Samples are weighted by the confidence the normalizer assigned them, so an
Apple derived rMSSD moves the acrophase less than a measured Oura one.

**Nonparametric actigraphy.** Interdaily stability, intradaily variability,
L5 and M10 with their onset hours, and relative amplitude. These are the
standard published measures with the standard definitions. L5 and M10 are
searched over a wrapped 24 hour profile, because the least active five hours
almost always straddle midnight and a non wrapping scan places L5 in the late
evening for every patient with a conventional schedule.

**Data completeness.** A single number, weighted toward sleep and heart rate
variability because those two carry most of the phase information. Activity is
common but weakly specific: a patient can hold a normal activity pattern while
their internal phase has already moved.

None of this declares a phase position. The profile describes what was
observed.

## What is not in this repository

Two things.

**Phase scoring coefficients.** How the descriptors above are weighted against
each other, the confidence model that turns descriptor agreement into an error
estimate, the DLMO regression constants, and the corrections applied per
device and per clinical population. These were fit against polysomnography and
dim light melatonin onset reference data.

**Chronopharmacological timing windows.** The per class offsets and widths,
the receptor and enzyme rhythm models they derive from, the toxicity windows,
and the interaction rules between concurrent agents in one regimen.

Both live in private packages, `croniixx-phase` and `croniixx-chrono`, on a
private index. See [the public and private split](#the-public-and-private-split).

## What runs without them

The Engine falls back to a reference implementation for each.

`ReferencePhaseEstimator` uses one published relationship: the association
between sleep midpoint and melatonin onset. It computes the offset as the
circular distance between the patient's midsleep and a population reference of
04:00, and projects a nominal DLMO seven hours earlier. Confidence rises with
nights observed and falls with midsleep scatter, and is capped at 0.45 whatever
the input looks like, because that ceiling is a property of the method rather
than of the data.

`ReferenceTimingCatalog` supplies window geometry: an anchor, an offset, a
width, and where inside the window the target sits. The numbers were chosen to
be structurally plausible and visually distinct on the clock dial. They are
not clinical guidance and must not be used to time a real dose.

Both stamp `coefficient_source: reference_fallback` on everything they touch,
and any schedule built from either is marked provisional. A result from
reference mode cannot be mistaken for a validated one by reading the response.

## Anchors

A timing rule is expressed relative to a biological anchor rather than a clock
time. The anchors, roughly ordered by how tightly they track the central
oscillator:

| Anchor | Source | Notes |
| --- | --- | --- |
| DLMO | Estimated | Reference standard when measured; estimated here |
| Midsleep | Sleep timing | Most stable wearable derived anchor |
| Sleep onset | Sleep timing | Sensitive to sleep opportunity, not only phase |
| Wake | Sleep timing | Often socially constrained rather than biological |
| Temperature nadir | Skin temperature cosinor | Distal skin temperature peaks near the core minimum |
| Activity acrophase | Activity cosinor | Available on every device, weakly specific |
| Clock time | Prescriber | Not phase adjusted; used only when pinned |

When an anchor is unavailable the schedule builder substitutes the nearest
usable one and records that it did. The substitution order is clinical rather
than alphabetical: a DLMO anchored window is better served by sleep onset than
by wake, because onset sits nearer the evening melatonin rise. Falling straight
to clock time is never the first choice, since that discards the patient's
biology entirely.

## Known limitations

**Fourteen days is the floor, not the ideal.** Below three nights the midsleep
variability term is meaningless and the profile says so.

**Shift workers and travellers break the population reference.** An offset
against a 04:00 midsleep reference describes a conventionally entrained adult.
A night shift nurse will read as several hours delayed while being correctly
entrained to their own schedule. Drift against the patient's own baseline is
the more useful signal for that population, which is why the drift endpoint
exists alongside the offset.

**Skin temperature is a proxy for core temperature and not a good one.**
Distal skin temperature is confounded by ambient conditions and bedding. It
contributes to the profile at low weight and is never the sole anchor.

**Device sampling is not uniform.** Oura measures HRV across the whole night,
Whoop inside slow wave sleep, and Apple reports SDNN instead of rMSSD. The
normalizer reconciles these and reduces confidence for every derived value.
See [terra-integration.md](terra-integration.md).

**No estimate is validated for clinical dosing in this repository.** The
validated coefficients require an IRB approved dataset to fit and a
prospective study to defend. Nothing in the public checkout has either.

## The public and private split

The rule is simple. Anything that can be checked against a published method is
open. Anything that was fit against proprietary reference data is not.

That means the Terra integration, the device normalizer, the profile builder,
the schedule object, the schedule assembly, the queue, and the entire frontend
are in this repository and complete. The coefficient tables are not.

The split is enforced by import rather than by convention. The Engine tries to
import the private packages, uses them when present, and falls back when they
are absent. A public checkout is a working system running a weaker model, not
a broken one.

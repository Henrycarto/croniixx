# Chronopharmacology reference

This document describes how Croniixx represents drug timing. It does not
contain the timing windows themselves.

The windows are the product. They live in the private `croniixx-chrono`
package. What is here is the structure they fit into, which is enough to
build against, test against, and argue with.

## Why timing is modelled at all

Absorption, distribution, metabolism, and elimination all vary across the day.
Gastric emptying, hepatic blood flow, glomerular filtration, and the expression
of several cytochrome P450 isoforms follow circadian rhythms. So does the
target side: receptor density, enzyme activity, and the proliferation rate of
the tissues a cytotoxic agent is trying to spare.

The consequence is that the same dose of the same drug given at two different
points in the cycle produces different exposure and different toxicity.

The consequence for software is narrower and more tractable. If timing matters,
then a schedule written against a wall clock is written against the wrong
variable, because the wall clock and the patient's internal clock are not the
same thing and the gap between them differs per patient.

A drug whose effect does not vary across the day belongs on a fixed clock
schedule and should not be routed through this engine at all. `DrugClass` lists
only classes with a timing dependence to model.

## Window geometry

A timing rule has four parts.

**Anchor.** A biological reference point: DLMO, midsleep, sleep onset, wake,
temperature nadir, or activity acrophase. Never a clock time unless the
prescriber pinned one.

**Offset.** Signed minutes from the anchor. Negative is before.

**Duration.** The width of the window in minutes.

**Target fraction.** Where inside the window the recommended moment sits, as a
fraction of its width. Most windows target the centre. A window that opens
sharply and decays slowly targets its early edge.

A drug class has three sets of these per dose of the day: one optimal window,
zero or more acceptable windows flanking it, and zero or more contraindicated
windows.

The acceptable windows are not decoration. A patient who misses the target
needs a defensible time to take the dose rather than a binary success or
failure, and a system that offers only a target trains people to take doses
late and report them as on time.

## Resolution

Resolving a rule means placing it on a real calendar for a real patient.

```
anchor hour (local, from the patient's own profile)
  + offset minutes
  = window start
window start + duration = window end
window start + duration * target fraction = target
```

The schedule builder scans the local dates the horizon touches plus one day on
each side, because a window anchored to the previous night's sleep onset can
still open inside the horizon. Duplicates from that overlap are collapsed on
medication, dose index, and target minute.

## Contraindications

A contraindicated window overrides an optimal one, but never by deleting the
dose. A missing row reads as "nothing due" to the patient, which is the worst
possible way to communicate a timing conflict.

The order of preference:

1. Keep the window if it does not overlap a contraindicated period.
2. Trim it to the clean part if at least thirty minutes survive, and downgrade
   it to acceptable.
3. Move to an acceptable window that is clean.
4. Keep the original window and mark it contraindicated, so the interface shows
   the conflict rather than hiding it.

## Multiple doses per day

Doses are spread across the waking span rather than the calendar day. Dividing
24 hours by four puts the last dose of a four times daily regimen in the middle
of the patient's biological night. Dividing a sixteen hour waking span does not.

## Pinned clock times

A clinician can pin a drug to a fixed clock time when a protocol demands it.
The engine honours it, anchors the window to `clock_time`, marks it acceptable
rather than optimal, and notes in the rationale that it is not phase adjusted.

It does not override the prescriber. It reports the biological cost of the
choice and leaves the decision where it belongs.

## The comparison that makes the case

Every schedule entry carries `conventional_time`: the wall clock time a printed
schedule would have given for that dose, and `drift_from_conventional_min`: the
signed distance between that and the biological target.

The conventional time is chosen as the nearest occurrence of the printed clock
time to the biological target, so the reported drift is the real distance and
never a near twenty four hour artefact of picking the wrong day.

This field is the entire clinical argument expressed as one number per dose. A
drift of ten minutes says the printed schedule was already right for this
patient. A drift of three hours says it was not.

## Implementing a catalog

Any catalog satisfying this protocol can be substituted:

```python
class TimingCatalog(Protocol):
    catalog_version: str
    coefficient_source: CoefficientSource

    def profile_for(
        self, drug_class: DrugClass, dose_index: int, doses_per_day: int
    ) -> DrugTimingProfile: ...

    def supports(self, drug_class: DrugClass) -> bool: ...
```

The Engine imports `croniixx_chrono.ValidatedTimingCatalog` and falls back to
`ReferenceTimingCatalog` when it is absent. A package that satisfies the import
but not the protocol is rejected and the fallback is used, because a private
package that has drifted from the contract is a worse failure than a missing
one: its numbers would look authoritative.

## The reference catalog

`ReferenceTimingCatalog` in `services/engine/app/engine/drug_timer.py` supplies
placeholder geometry so the schedule builder, the clock dial, and the mobile
app can be exercised without the private package.

Its anchors are structurally plausible: corticosteroids near the cortisol rise,
statins near the nocturnal peak in cholesterol synthesis, antimetabolites near
the trough in healthy tissue proliferation. Its offsets and widths are chosen
to be visually distinct on the dial.

They are not clinical guidance. Every window it produces is stamped
`reference_fallback` and every schedule built from it is marked provisional.

## What would be needed to validate a catalog

For each class: a mechanism with a measured rhythm, a pharmacokinetic model
tied to that rhythm, an exposure or toxicity endpoint, and prospective data
showing the endpoint moves with administration time relative to phase rather
than relative to the clock.

The last condition is the hard one and the reason this is not solved by reading
the literature. Most chronotherapy trials randomise on clock time, which
confounds administration phase with the distribution of chronotypes in the
cohort. Wearable derived phase is what makes the phase relative version
measurable outside a sleep laboratory, and that is the thesis this system is
built to test.

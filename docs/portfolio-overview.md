# Croniixx

Circadian aware medication scheduling. A case file.

## Product thesis

Absorption, hepatic metabolism, renal clearance, receptor density, and the
proliferation rate of healthy tissue all vary across the twenty four hour
cycle, which means the same dose of the same agent produces different exposure
and different toxicity depending on when it is given. Chronotherapy has known
this for decades and has still not reached routine practice, and the reason is
mundane rather than scientific: the printed schedule says 08:00 because 08:00
is a convenient number, and nobody in the clinic knows where 08:00 sits in a
particular patient's biological cycle. That position is measurable. Dim light
melatonin onset is the reference standard and requires a sleep laboratory, but
sleep midpoint, the nocturnal heart rate variability curve, the skin
temperature rhythm, and the rest activity pattern are all obtainable from a
consumer wearable a patient is already wearing, and together they constrain
phase closely enough to place a dose. Croniixx is the engineering that turns a
continuous wearable stream into a signed phase offset and then into dated dose
windows anchored to the patient's own clock, so that the timing decision stops
being an assumption and becomes an observation.

## Architecture

Three components, each owning one stage of a single pipeline.

**Croniixx Sync** takes wearable data in. It receives webhooks from the Terra
API, verifies their signature, and reconciles what four different devices
actually measure before anything is stored. It then assembles a circadian
profile: sleep timing computed as circular statistics, cosinor fits for heart
rate variability and temperature and activity, and the standard nonparametric
actigraphy measures. The profile describes what was observed and makes no claim
about phase.

**Croniixx Engine** turns that description into a decision. It scores the
profile into a signed phase offset with a confidence attached, then resolves
each drug's timing rules against that offset to produce dated windows. A rule
is expressed as an anchor and a distance from it, so a window records "ninety
minutes before sleep onset" alongside the resolved timestamps. The timestamp is
a snapshot of a calculation; the anchor is the calculation, and it stays
correct when the patient's phase moves under it.

**Croniixx Mobile** delivers the result to the person who has to act on it. It
is an Expo application that writes the schedule to local SQLite the moment it
arrives, schedules notifications from that local copy, and queues dose
acknowledgements in an outbox that drains when connectivity returns.

## Key engineering decisions

**Terra rather than four device SDKs.** Direct integration with Oura, Apple,
Garmin, and Whoop means four OAuth implementations, four rate limit regimes,
four deprecation schedules, and four vendor review processes for the
applications holding the credentials. Terra costs one integration and one
webhook, and adding a fifth manufacturer becomes a dashboard toggle plus one
adapter class rather than a quarter of engineering time. The tradeoff is a
vendor sitting in front of clinical data and a normalization layer we do not
control, which is managed by treating the webhook contract as loose: every data
block stays as a raw dictionary until our own normalizer decides what it
understands, and an uncharacterised provider is rejected rather than guessed at.

**Normalization is where the real work sits, not the transport.** Terra unifies
field names. It does not unify meaning. Apple reports heart rate variability as
SDNN because HealthKit has no rMSSD type; Oura, Garmin, and Whoop report rMSSD.
The two indices are not interchangeable, and the nocturnal rMSSD curve is the
one that tracks the oscillator. Whoop samples HRV inside slow wave sleep, which
sits at the parasympathetic peak, so its numbers run systematically higher than
a whole night average from the same wrist on the same night. Oura and Garmin
report skin temperature as a deviation from the wearer's own baseline while
Whoop reports an absolute in Celsius. Each of those is handled explicitly, and
every conversion lowers a confidence value that the cosinor fits then weight by,
so a derived Apple reading moves an estimated acrophase less than a measured
Oura one. Nothing is silently coerced.

**TimescaleDB for the continuous metrics.** Wearable ingestion is append heavy
and queried almost exclusively as recent windows per patient per metric. That is
the shape hypertable partitioning is built for, and the continuous aggregate
gives the dashboard a week of heart rate variability without scanning every raw
sample. Running it as a Postgres extension rather than a separate time series
store also keeps the patient record, the regimen, and the schedule in the same
transactional database, which matters because a schedule and the phase estimate
it came from have to be written together or not at all.

**Offline first on mobile, not offline tolerant.** A patient on a cytotoxic
regimen does not reliably have signal at 04:00, and the dose is due at 04:00
regardless. The local SQLite copy is the source of truth for the app rather
than a cache in front of the network: notifications are scheduled from it,
acknowledgements are written to it first and queued for replay second, and no
patient facing action waits on a request. The server remains authoritative for
what the schedule should be, and the device is authoritative for what actually
happened.

**Schedules supersede rather than patch.** A schedule is the output of one
phase estimate. Amending a single entry from a newer estimate produces an object
whose rows disagree about what time the patient's body thinks it is. Writing a
new schedule whole and marking the previous one superseded in the same
transaction removes an entire class of inconsistency, and the reminder queue
mirrors it by replacing a patient's queued notifications rather than merging
into them.

**Stubbing the phase calculation as deliberate boundary work.** The scoring
coefficients and the timing windows are the defensible part of this product and
they are not in the public repository. Substitution happens by import: the
Engine imports the private packages, uses them when present, and falls back to
transparent reference implementations when they are absent. In fallback mode
every response carries `coefficient_source: reference_fallback` and every
schedule is marked provisional, so a result from a public checkout cannot be
mistaken for a validated one by anybody reading the output. The API surface,
the schedule object, and the whole frontend behave identically either way, which
means the boundary costs nothing in testability. A private package that
satisfies the import but not the protocol is rejected in favour of the fallback,
because a drifted coefficient package is a worse failure than a missing one: its
numbers would still look authoritative.

## The clock dial

The signature interface element is a twenty four hour clock face drawn in raw
SVG. The outer ring is one night of the patient's own sleep architecture,
staged by their wearable and coloured by stage. The inner rings are the dose
windows for their regimen, allocated to concentric tracks so overlapping agents
do not obscure each other, green for the calculated window and red for periods
an agent should not be given in, with a tick marking the recommended moment
inside each arc. Two needles run from the centre: a faint dashed one at the wall
clock and a violet one at the patient's biological position.

The angle between those two needles is the entire argument.

A clinical pharmacologist looking at that gap has, in one glance, four things a
table gives up only on careful reading. They see the magnitude and the direction
of the phase displacement, because it is an angle rather than a signed number
they have to interpret. They see whether the sleep architecture supports the
estimate, because a fragmented night with scattered REM sits right next to the
needle that was derived from it. They see which doses fall inside the biological
night and which do not, spatially, without converting timestamps in their head.
And they see whether any window has drifted onto a red arc, which in a table is
a status column that has to be scanned row by row.

A table of numbers can carry all the same values. What it cannot do is make the
relationship between them preattentive. Phase is a circular quantity, and a
circular quantity displayed on a circle is read rather than computed. The
specific failure this prevents is the one that motivates the product: a
clinician looking at a list of times has no way to notice that 08:00 sits in the
middle of this patient's biological night, and a clinician looking at the dial
cannot avoid noticing it.

## Public and private

The split follows one rule. Anything checkable against a published method is
open. Anything fitted against proprietary reference data is not.

Open, and complete in the repository: the Terra integration including signature
verification and replay handling, the device normalizer with every
manufacturer specific correction, the circadian profile builder, the adaptive
schedule object and its assembly, the reminder queue, the SMART on FHIR launch,
the infrastructure definitions, and the entire frontend including the dial.

Private: the phase scoring coefficients, meaning how the profile descriptors are
weighted against each other, the confidence model that converts descriptor
agreement into an error estimate, the melatonin onset regression constants, and
the per device and per population corrections. Also private: the
chronopharmacological timing windows per drug class, the rhythm models they
derive from, the toxicity windows, and the interaction rules between concurrent
agents.

The reasoning is that the integration work is valuable but reproducible by a
competent team in a few months, whereas the coefficients require reference data
that takes a research programme and an ethics approval to obtain. Publishing the
former demonstrates the engineering and costs nothing defensible. Publishing the
latter would give away the only part that cannot be rebuilt from the literature.

## Build status

**Working and tested.** Terra webhook ingestion with HMAC verification,
timestamp tolerance, and replay deduplication. Device normalization across all
four manufacturers including the HRV index conversion, the slow wave sleep
correction, the absolute to delta temperature conversion, hypnogram merging, and
duplicate step collapsing. Circadian profile assembly with circular sleep
statistics, confidence weighted cosinor fits, and the nonparametric actigraphy
set. Adaptive schedule assembly with anchor resolution, clinically ordered
anchor substitution, window trimming around contraindicated periods, multi dose
spreading across the waking span, prescriber pinned clock times, and the drift
comparison against a conventional schedule. A reminder queue with atomic
claiming through a Lua script, claim expiry and recovery after a dispatcher
crash, supersede on new schedule, acknowledgement cascade across a dose, and
bounded retries. An Expo push client with per message result tracking, batch
limits, dead token removal, and receipt handling. SMART on FHIR launch with
PKCE, constant time state comparison, and paged MedicationRequest import. The
clock dial, including the wrap at midnight, concentric track allocation, and
coordinate rounding so server and client renders agree.

That is 154 tests across four suites. Continuous integration runs them on every
push alongside type checking, linting, a production build, container builds for
all three services, compose validation, and a copy rules check.

**Demo ready.** The clinician dashboard renders live service data when the
services are reachable and a labelled worked example when they are not, so the
tool is reviewable without infrastructure. The mobile application typechecks and
lints, and its SQLite schema, synchronisation logic, and notification scheduling
are complete, but it has not been exercised on physical hardware in this
repository.

**Requires Terra credentials.** Ingestion cannot receive anything without a
developer id, an API key, and a signing secret. Everything downstream is
testable with fixtures and is tested that way, so the credentials bring in live
data rather than switching on functionality that was missing.

**Requires validated coefficients.** Without the private packages the Engine
runs a reference phase estimator that uses sleep midpoint alone, capped at a
confidence of 0.45, and a reference timing catalog whose window geometry is
structurally plausible and clinically meaningless. Both stamp themselves as
such. Producing the real coefficients needs polysomnography and dim light
melatonin onset reference data under ethics approval, and defending them needs a
prospective study in which administration time is randomised relative to
measured phase rather than relative to the clock. That last condition is the
hard one, and it is also the reason this problem is still open: most existing
chronotherapy trials randomise on clock time, which confounds administration
phase with the chronotype distribution of the cohort. Wearable derived phase is
what makes the phase relative version measurable outside a sleep laboratory.
This system is the instrument that trial would need.

**Not built.** Multi tenant clinician accounts with role separation, an audit
log meeting HIPAA retention requirements, Terra providers beyond the four
characterised devices, and formal pharmacokinetic modelling per agent.

This is an engineering artefact and not a medical device. It has not been
through regulatory review, and nothing in it should be used to time a dose for a
real patient.

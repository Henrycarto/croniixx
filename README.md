# Croniixx

Drug efficacy varies by up to 40% depending on when in the circadian cycle it
is administered. Croniixx calculates the right time from a patient's actual
biology.

A printed medication schedule says 08:00 because 08:00 is a convenient number.
A patient's internal clock may sit ninety minutes behind that, or two hours
ahead, and it moves during treatment. Croniixx streams wearable data through
the Terra API, builds a circadian profile from sleep timing, heart rate
variability, and activity, and places each drug in a regimen against biological
anchors rather than clock times.

The buyer is an endocrinologist, oncologist, or clinical pharmacologist. The
patient is managing a multi drug regimen where timing affects both efficacy and
toxicity.

## Three components

**Croniixx Sync** receives Terra webhooks, verifies them, and reconciles what
the four supported devices actually measure. Oura, Garmin, and Whoop report
rMSSD; Apple reports SDNN. Whoop samples inside slow wave sleep; the others
average across the night. Oura reports a temperature delta; Whoop reports an
absolute. Reconciling those is most of the work in this service, and every
conversion lowers a confidence value rather than hiding itself.

**Croniixx Engine** turns the profile into a signed phase offset, then turns
that offset into dated dose windows. A window records the anchor it came from,
so a clinician sees "wake plus forty minutes" and not only "07:40".

**Croniixx Mobile** is an offline first React Native app. The schedule is
written to SQLite the moment it arrives, notifications are scheduled locally
from that copy, and dose acknowledgements queue in an outbox that drains when
the network returns. Nothing the patient needs to do requires a request to
succeed.

## Repository layout

```
apps/web            Next.js clinician and patient dashboard
apps/mobile         Expo patient app, offline first
services/sync       Terra ingestion, device normalization, profile assembly
services/engine     Phase estimation and adaptive schedule assembly
services/reminder-api  Redis reminder queue and Expo push delivery
packages/shared-types  Types shared by every client
packages/fhir-client   SMART on FHIR launch and FHIR reads
infra/              docker-compose for local dev, Terraform for AWS
docs/               Architecture, methodology, integration notes
```

## Running it locally

```
cp .env.example .env
npm install
docker compose -f infra/docker-compose.yml up
```

That brings up Postgres with TimescaleDB, Redis, the three Python services, and
the Next.js app on port 3000. The database schema in `infra/db/init` is applied
on first start.

Without Terra credentials the Sync service starts and serves reads, and the
connect and backfill endpoints return 503 with a message saying which variables
are missing.

To run the frontend alone:

```
npm run dev --workspace @croniixx/web
```

To run the test suites:

```
npm run test                                   # frontend
cd services/sync && PYTHONPATH=. pytest -q     # and engine, and reminder-api
```

## The clock dial

`apps/web/components/engine/CircadianClockDial.tsx` is the interface's central
element. It is a 24 hour clock face drawn in raw SVG.

The outer ring is the patient's own sleep architecture, staged from their
wearable and coloured by stage. The inner rings are dose windows, one track per
non overlapping set, green for the calculated window and red for periods the
agent should not be given in. A white tick inside each arc marks the target
moment.

Two needles run from the centre. The faint dashed one is the wall clock. The
violet one is the patient's biological position. The angle between them is the
phase offset.

That angle is the argument. A clinician who sees a wide gap between the two
needles does not need the accompanying table to know that the printed schedule
and the patient's biology have come apart.

## What is not in this repository

The phase scoring coefficients and the chronopharmacological timing windows.
Both were fit against reference data and both live in private packages.

The Engine imports them when they are installed and falls back to transparent
reference implementations when they are not. In fallback mode every response
carries `coefficient_source: reference_fallback` and every schedule is marked
provisional, so a result from a public checkout cannot be mistaken for a
validated one.

The API shape, the schedule object, and the entire frontend are identical in
both modes. A public checkout is a working system running a weaker model, not
a broken one.

See [docs/circadian-methodology.md](docs/circadian-methodology.md) for the full
account of where the line sits and why.

## Picking this up

[docs/handoff.md](docs/handoff.md) is written for the next engineer on the
project. It covers the decisions that look arbitrary until you hit the case that
motivated them, the contract the private packages have to satisfy, the known
sharp edges, and what I would do next in order.

## Build status

Honest, and separated by status.

### Working and tested

- Terra webhook ingestion with HMAC signature verification, timestamp
  tolerance, and replay deduplication
- Device normalization across Oura, Apple Watch, Garmin, and Whoop, including
  the HRV index conversion, the Whoop slow wave correction, the absolute to
  delta temperature conversion, hypnogram merging, and step deduplication
- Circadian profile assembly: circular sleep statistics, weighted cosinor fits,
  and the nonparametric actigraphy set
- Adaptive schedule assembly: anchor resolution, anchor substitution, window
  trimming around contraindicated periods, multi dose spreading, pinned clock
  times, and the drift comparison against a conventional schedule
- Reminder queue with atomic claiming, claim expiry and recovery, supersede on
  new schedule, acknowledgement cascade across a dose, and bounded retries
- Expo push client with per message result tracking, batch limits, dead token
  removal, and receipt handling
- SMART on FHIR launch with PKCE, constant time state verification, and paged
  MedicationRequest import
- The clock dial, including the wrap at midnight, concentric track assignment,
  and coordinate rounding so server and client renders agree

154 tests across the four suites. CI runs them on every push along with type
checking, linting, a production build, container builds, and a copy rules
check.

### Demo ready

- The clinician dashboard. It renders live service data when the services are
  reachable and a labelled worked example when they are not.
- The mobile app. It typechecks and lints, the SQLite schema and the sync and
  notification logic are complete, and it has not been run on a physical
  device in this repository.

### Needs credentials or data to activate

- **Terra API keys.** Ingestion cannot receive anything without a dev id, an
  API key, and a signing secret. Everything downstream of ingestion is testable
  with fixtures and is tested that way.
- **The private coefficient packages.** Without them the Engine runs the
  reference estimator and the reference timing catalog. Structurally complete,
  clinically unvalidated, and labelled as such on every response.
- **IRB validated reference data.** The coefficients themselves require
  polysomnography and dim light melatonin onset data to fit, and a prospective
  study to defend. That is a research programme, not a sprint.

### Not built

- Multi tenant clinician accounts and role separation
- An audit log meeting HIPAA retention requirements
- Terra provider support beyond the four listed devices
- Formal pharmacokinetic modelling per agent

## Licence and use

This code is published as an engineering artefact. It is not a medical device,
it has not been through regulatory review, and nothing in it should be used to
time a dose for a real patient.

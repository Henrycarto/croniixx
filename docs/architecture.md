# Architecture

Croniixx is three backend services, a clinician dashboard, and a patient app.
Each service owns one stage of a single pipeline: wearable data in, phase
position out, dose windows out, reminders delivered.

```
Oura / Apple / Garmin / Whoop
            |
         Terra API
            |  webhook
            v
     +--------------+        +----------------+
     | Croniixx     |        |  TimescaleDB   |
     | Sync  :8001  +------->+  biometric     |
     +------+-------+        |  samples       |
            | profile        +----------------+
            v
     +--------------+        +----------------+
     | Croniixx     |        |  PostgreSQL    |
     | Engine :8002 +------->+  schedules     |
     +------+-------+        +----------------+
            | adaptive schedule
            v
     +--------------+        +----------------+
     | reminder-api +------->+  Redis queue   |
     |        :8003 |        +----------------+
     +------+-------+
            | Expo push
            v
     Croniixx Mobile  <---- offline SQLite copy
```

## Why three services and not one

The three stages have different failure modes and different scaling shapes,
and putting them in one process would tie them together in ways that hurt.

Sync is bursty and IO bound. A batch of Oura rings syncing in the morning
delivers a spike of webhooks that has nothing to do with how many schedules
need calculating. It scales on inbound traffic.

Engine is CPU bound and infrequent. A schedule is regenerated when a phase
estimate moves, which is on the order of once a day per patient. It also holds
the proprietary coefficient packages, and keeping that in one deployable makes
the boundary between what is open and what is not a deployment fact rather
than a convention.

The reminder API is a scheduler with a hard latency requirement. It must fire
within minutes of a dose target, and it must keep running while the other two
are being deployed. Restarting it to ship an unrelated change to the
normalizer is not acceptable.

## Croniixx Sync

Receives Terra webhooks, verifies their signature, normalizes across
manufacturers, and writes to TimescaleDB. Also assembles the circadian profile
on request.

The webhook path is deliberately forgiving in one direction and strict in the
other. A payload that fails signature verification is rejected with a 401. A
payload that verifies but cannot be normalized is acknowledged with a 200 and
recorded as a warning, because Terra retries any non 2xx response for hours,
and one malformed block from a firmware update would otherwise crowd out live
data for every patient.

Normalization is the part that carries real content. See
[terra-integration.md](terra-integration.md).

## Croniixx Engine

Reads a circadian profile, estimates the patient's phase position, and
assembles adaptive schedule objects.

The phase estimator and the drug timing catalog are both interfaces with two
implementations. The validated implementations live in private packages. When
they are absent the service starts in reference mode, stamps every response
with `coefficient_source: reference_fallback`, and marks every schedule
provisional. Nothing about the API shape changes, so the dashboard, the app,
and the tests all work identically in both modes.

See [circadian-methodology.md](circadian-methodology.md) for what is public
and what is not.

## reminder-api

Turns an adaptive schedule into notifications and delivers them.

The queue is a Redis sorted set with a claim step implemented as a Lua script,
so two dispatchers running at once cannot take the same reminder. Claims
expire, which is what makes a dispatcher crash survivable rather than a source
of silently swallowed doses.

A reminder more than fifteen minutes late is dropped rather than delivered.
Prompting a dose after its window closed is worse than staying quiet, because
the patient may act on it.

## Data model

The interesting decisions:

**Sleep as intervals, biometrics as points.** Phase estimation cares about
where a sleep stage started and ended, not about an instantaneous value. Both
live in TimescaleDB hypertables with different chunk intervals matched to how
they are queried.

**Schedules supersede, never patch.** An adaptive schedule is the output of one
phase estimate. Editing a single entry from a newer estimate produces a
schedule whose entries disagree about what time the patient's body thinks it
is. A new schedule is written whole and the previous one is marked superseded
in the same transaction.

**Windows store an anchor, not a time.** A schedule entry records "ninety
minutes before sleep onset" alongside the resolved timestamps. The timestamp
is a snapshot; the anchor is the reasoning, and it is what a clinician can
agree or disagree with.

## API contract

Every response from every service has the same three keys.

```json
{
  "data": { },
  "error": null,
  "meta": { "service": "croniixx-engine", "request_id": "...", "generated_at": "..." }
}
```

The mobile client parses success and failure with one code path. That matters
offline: the app queues what it could not process and replays it later without
branching on shape.

## Authentication

Two separate schemes for two separate populations.

Clinicians authenticate through SMART on FHIR against the EHR that already
holds the patient record. There is no local password. The launch sequence
implements PKCE unconditionally, verifies `state` in constant time, and holds
the code verifier in an httpOnly cookie so injected script cannot read it.

Patients authenticate with a JWT scoped to one patient id. Every patient
scoped route checks the token subject against the patient in the path, so a
valid token for one patient cannot read another's schedule.

## Local development

```
cp .env.example .env
docker compose -f infra/docker-compose.yml up
```

Postgres runs the TimescaleDB image and applies `infra/db/init` on first start.
The web app runs in a Node container against the workspace mount.

Without Terra credentials the Sync service starts and serves, but `/ingest/connect`
returns a 503 saying so rather than failing obscurely.

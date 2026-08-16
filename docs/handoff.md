# Handoff

For the next engineer on Croniixx. This covers what the code does not say about
itself: which decisions were deliberate, which are provisional, where the sharp
edges are, and what I would do next.

Read [architecture.md](architecture.md) first for the shape of the system. This
document assumes it.

## Getting running

```
git clone https://github.com/Henrycarto/croniixx
cd croniixx
cp .env.example .env
npm install
docker compose -f infra/docker-compose.yml up
```

That brings up Postgres with TimescaleDB, Redis, the three Python services, and
the web app on port 3000. `infra/db/init/001_schema.sql` runs once on first
container start. If you change it, you have to drop the volume:

```
docker compose -f infra/docker-compose.yml down -v
```

There is no migration tool yet. See [what I would do next](#what-i-would-do-next).

No Terra credentials are needed to start. Sync serves reads, and the connect and
backfill endpoints return 503 with `terra_not_configured` rather than failing in
a way that needs a log to diagnose.

## Verifying your environment

Run everything before you change anything, so you know what a green baseline
looks like on your machine.

```
npx turbo run lint typecheck test build
```

For the Python suites, each service is its own root:

```
cd services/sync         && PYTHONPATH=. pytest -q   # 34
cd services/engine       && PYTHONPATH=. pytest -q   # 42
cd services/reminder-api && PYTHONPATH=. pytest -q   # 44
```

The reminder queue tests need `fakeredis[lua]`, not plain `fakeredis`. Without
lupa installed, `EVALSHA` is unsupported and sixteen tests fail on
`unknown command 'evalsha'`. The requirements file pins plain `fakeredis`, and
CI installs the lua extra as a separate step. That is worth tidying.

## Where to read first

If you are picking this up cold, four files carry most of the thinking:

1. `services/sync/app/engine/device_normalizer.py`. The manufacturer
   reconciliation. This is where the system earns its accuracy.
2. `services/engine/app/schemas.py`. The adaptive schedule object. Every client
   depends on this shape.
3. `services/engine/app/engine/schedule_builder.py`. Anchor resolution. The step
   that turns an abstract phase into something a patient can act on.
4. `apps/web/components/engine/CircadianClockDial.tsx` with
   `apps/web/lib/dial-geometry.ts`. The interface argument.

Everything else is plumbing around those.

## Non obvious decisions

These will look arbitrary until you hit the case that motivated them.

**Sleep timing is computed as circular statistics, everywhere.** Midpoints at
23:30 and 00:30 average to midnight. An arithmetic mean puts them at noon. That
is a twelve hour error in the exact quantity the product exists to get right,
and it will pass unit tests written with daytime values. `circular_mean_and_sd`
and `signed_hour_difference` exist for this and there is a test called
`test_arithmetic_mean_would_have_been_twelve_hours_wrong` that documents it.

**Every dial coordinate is rounded to four decimals.** Node and V8 in the
browser disagree on the last bit of `Math.cos`, which React reports as a
hydration mismatch and responds to by discarding the whole server render. This
cost an hour to diagnose because the symptom is a blank page with a minified
React error 423. `roundCoordinate` in `dial-geometry.ts` is load bearing. Do not
remove it as a micro optimisation.

**Push results are positional, not keyed by token.** An earlier version keyed
`PushOutcome` by push token. A patient with two devices, or several doses going
to one device, collapsed into one entry and reminders were marked delivered on
the strength of a different message. `MessageResult[]` aligns with the input
message array and the dispatcher takes the best result per reminder. Keep that
invariant if you touch batching.

**A payload that fails signature verification gets a 401, and one that verifies
but cannot be normalized gets a 200.** This looks inconsistent and is not. Terra
retries any non 2xx for hours. One malformed block from a firmware update would
otherwise replay forever and crowd out live data for every patient. Rejecting an
unsigned write is the only case worth failing.

**Schedules supersede rather than patch, and the queue mirrors that.** A
schedule is the output of one phase estimate. Amending one entry from a newer
estimate produces rows that disagree about what time the patient's body thinks
it is. `Database.store_schedule` writes the new schedule and marks the old one
superseded in one transaction, and `QueueManager.replace_schedule` cancels
before it enqueues. Reverse that order and there is a window where a patient can
be told to dose twice.

**Reminders more than fifteen minutes late are dropped, not delivered.**
`max_lateness_seconds`. Prompting a dose after its window closed is an active
harm, because the patient may act on it. This is the one place the system
chooses silence.

**Naps are excluded from sleep timing and anything under three hours is a nap.**
A midday nap has a midpoint near noon. Averaging it with a night gives a
midsleep in the early evening that describes neither.

**Anchor substitution is ordered clinically, not alphabetically.** In
`AnchorMap.substitute`, a DLMO anchored window falls back to sleep onset before
wake, because onset sits nearer the evening melatonin rise. Clock time is never
a first choice, since falling to it discards the patient's biology entirely.

**`_dates_spanning` deliberately over generates and then deduplicates.** It
scans one local day either side of the horizon, because a window anchored to
last night's sleep onset can still open inside the horizon. The duplicates that
produces are collapsed on medication, dose index, and target minute. If you
tighten the scan to save work you will silently lose doses at horizon edges.

## The private package contract

The Engine expects two packages that are not in this repository. Both are
substituted by import, and both fall back transparently when absent.

```python
# croniixx_phase
class ValidatedPhaseEstimator:
    method_version: str
    coefficient_source: CoefficientSource  # must be PRIVATE_VALIDATED

    def estimate(
        self,
        profile: CircadianProfileInput,
        *,
        patient_timezone: str = "UTC",
        now: datetime | None = None,
    ) -> PhaseEstimate: ...
```

```python
# croniixx_chrono
class ValidatedTimingCatalog:
    catalog_version: str
    coefficient_source: CoefficientSource  # must be PRIVATE_VALIDATED

    def profile_for(
        self, drug_class: DrugClass, dose_index: int, doses_per_day: int
    ) -> DrugTimingProfile: ...

    def supports(self, drug_class: DrugClass) -> bool: ...
```

Three rules if you implement either.

Implementations must be pure with respect to their inputs. The Engine caches by
profile content and would return stale results for an estimator carrying state
between calls.

The sign convention is fixed and stated in `signed_hour_difference`. Positive
means delayed, meaning the biological night starts later than the calendar day.
Getting this backwards inverts every schedule in the system and nothing will
crash.

A package that satisfies the import but not the protocol is rejected in favour
of the fallback. That is intentional. A drifted coefficient package is worse
than a missing one because its numbers still look authoritative.

Install path in production is the `CRONIIXX_PRIVATE_INDEX` build argument in
`services/engine/Dockerfile`, wired to a repository secret in `deploy.yml`.

## Testing strategy

The Python suites test behaviour, not implementation. Two patterns worth
keeping:

Tests that assert against a known answer by construction. `test_cosinor_recovers_a_known_acrophase`
builds a synthetic series with a true acrophase of 04:00 and asserts the fit
finds it within eighteen minutes. That catches a sign flip or an off by one
period in a way that asserting a hardcoded output cannot.

Tests that name the failure rather than the code path.
`test_a_claim_that_is_never_acknowledged_returns_to_the_queue` describes a
dispatcher crash. If you refactor the queue, that test should survive unchanged.

Phase estimator tests deliberately do not assert coefficient values. A test that
pinned specific offsets would either encode the IP or fail the moment the
validated package is installed. They test the contract, the sign convention, and
the confidence ceiling.

`fakeredis[lua]` runs the real Lua scripts, so the atomic claim path is actually
exercised rather than mocked. Do not replace it with a mock.

## Deployment

`deploy.yml` runs `ci.yml` first through `workflow_call`, so a tag cannot ship
code that would have failed a pull request.

Order matters in the `services` job and is set with `max-parallel: 1`. The
reminder dispatcher comes up before the Engine starts pushing schedules at it.

Migrations run as a one off ECS task before services roll, so a new revision
never starts against a schema it does not know about. The task definition
`croniixx-migrate` is referenced but not defined in Terraform. That is a gap.

After a production rollout, check `/health` on the Engine and confirm
`coefficient_source` reads `private_validated`. A deployment that silently fell
back to reference mode looks healthy in every other respect.

Terraform expects `ecr_registry` and `environment` as variables, and state in
S3 with DynamoDB locking. `aws_ecs_service` has `ignore_changes` on
`task_definition` and `desired_count` so Terraform does not fight the deploy
pipeline or the autoscaler on the next apply.

## Known sharp edges

**No migration tool.** `infra/db/init` runs once on first container start and
never again. Anything past the first schema change needs Alembic or equivalent,
and the `croniixx-migrate` task definition to go with it. This is the first
thing I would fix.

**`envelope.py` is duplicated three times.** Once per service, because the
Docker build context is per service and there is no shared Python package. It is
forty lines and identical in all three. A `services/_shared` package with a
build context change would fix it properly.

**The reminder dispatcher runs in the API process.** Fine at current scale and
wrong eventually. Two API tasks means two dispatchers competing for claims,
which the Lua script handles correctly but which wastes work. Terraform
deliberately does not scale reminder-api wide for this reason. Splitting the
dispatcher into its own task is the clean fix.

**`cancel_patient` does an N plus one read.** It calls `get` per reminder id
inside the loop when `keep_schedule_id` is set. Fine for a regimen of eight
agents, not fine for a ward sweep.

**The web app has no runtime error boundary.** A malformed schedule from the
Engine renders a Next.js error page rather than a degraded panel. The demo
fallback covers an unreachable service but not a reachable one returning
nonsense.

**Mobile has never run on physical hardware.** It typechecks and lints, and the
SQLite schema, sync logic, and notification scheduling are complete and
reviewable. Notification permissions, Expo push token acquisition, and exact
alarm behaviour on Android 14 all need a device to verify.

**`registerDevice` hardcodes `platform: 'ios'`.** In `apps/mobile/lib/sync.ts`.
Should read `Platform.OS`. It is cosmetic today because the platform field is
only stored, not acted on, but it will not stay that way.

**The population midsleep reference of 04:00 breaks for shift workers.** A night
shift nurse reads as several hours delayed while being correctly entrained to
their own schedule. Drift against the patient's own baseline is the better
signal for that population, which is why `/circadian/drift` exists, but nothing
in the interface prefers it yet.

## What I would do next

In order, and with reasons rather than ceremony.

1. **Alembic migrations and the migrate task definition.** Everything else is
   blocked behind the first schema change in an environment with data in it.
2. **Get the mobile app onto a device.** It is the only component with no
   empirical evidence behind it, and notification delivery is the part of the
   product a patient actually experiences.
3. **Split the dispatcher out of the reminder API.** Removes the reason the
   service cannot scale and makes the queue independently observable.
4. **A `services/_shared` package.** Removes the envelope duplication and gives
   the three services somewhere to share the config and logging setup they
   currently each redefine.
5. **Persist the phase history properly on the read path.** `/circadian/phase`
   persists on every call by default, which will fill `phase_estimates` with
   near duplicate rows if the dashboard polls. It should write only when the
   estimate has moved past a threshold.
6. **An audit log.** Anything touching a medication schedule in a clinical
   setting needs one, and retrofitting it later means backfilling from
   application logs that were not designed for it.

## Environment and secrets

Everything the system reads, and where it comes from.

| Variable | Source | Without it |
| --- | --- | --- |
| `TERRA_DEV_ID`, `TERRA_API_KEY` | Terra dashboard | Connect and backfill return 503 |
| `TERRA_SIGNING_SECRET` | Terra dashboard | Every webhook is rejected with 401 |
| `DATABASE_URL` | RDS, via Secrets Manager | Services start, health reports degraded |
| `REDIS_URL` | ElastiCache, via Secrets Manager | Queue and dedup unavailable |
| `JWT_SECRET` | Generated per environment | Mobile auth and the service token both fail |
| `EXPO_ACCESS_TOKEN` | Expo account | Push still works for most projects |
| `FHIR_CLIENT_ID`, `FHIR_CLIENT_SECRET` | EHR vendor registration | SMART launch throws on missing env |
| `FHIR_REDIRECT_URI` | Must match the registration exactly | Authorization server rejects the callback |
| `CRONIIXX_PRIVATE_INDEX` | Private package index | Engine runs in reference mode and says so |
| `AWS_DEPLOY_ROLE_ARN` | OIDC trust policy | Deploy cannot assume a role |

`JWT_SECRET` doubles as the service to service token between the Engine and the
reminder API. That is deliberate for now and should become a separate value
before this holds real patient data.

# Terra integration

Terra sits between Croniixx and four device manufacturers. It handles OAuth
with each vendor, normalizes field names and units, and pushes payloads to our
webhook.

What it does not do is reconcile what the values mean. That is the work in
`services/sync/app/engine/device_normalizer.py`, and it is the part that
determines whether a phase estimate from an Apple Watch is comparable to one
from an Oura ring.

## Why Terra rather than direct SDK integration

Four manufacturers, four OAuth implementations, four sets of rate limits, four
deprecation schedules, and four review processes for the apps that hold the
credentials. Building against Terra costs one integration and one webhook.

The tradeoff is a dependency on a vendor sitting in front of clinical data,
and a normalization layer whose behaviour we do not control. Both are managed
the same way: the webhook contract is treated as loose, every data block stays
as a raw dictionary until our normalizer decides what it understands, and a
provider we have not characterised is rejected rather than guessed at.

Adding a fifth manufacturer is a Terra dashboard toggle plus one adapter class.
Adding a fifth direct SDK integration is a quarter of engineering time.

## Webhook security

Terra signs each webhook as `t=<unix_seconds>,v1=<hex_hmac_sha256>` where the
HMAC covers `<t>.<raw_body>` under the signing secret.

Three things matter in the implementation:

The signature is computed over the exact bytes received. Re-serializing parsed
JSON changes key order and whitespace and breaks the comparison.

The timestamp is checked against a tolerance window, so a captured payload
cannot be replayed a week later.

Comparison uses `hmac.compare_digest`. A byte by byte comparison leaks the
signature through timing.

A missing signing secret is a failure, not a skip. A configuration mistake that
silently disables verification would leave an unauthenticated write path into
the system that decides when a patient takes a cytotoxic agent.

## Duplicate delivery

Terra retries on timeout, so the same payload arrives more than once. Each body
is hashed and the hash is held in Redis for a replay window. A repeat is
acknowledged as a duplicate rather than written twice.

Hashing the body rather than trusting a vendor supplied id means deduplication
does not depend on a field Terra might stop sending.

## What differs between the four devices

This is the substance of the normalizer.

### Heart rate variability index

| Device | Reports | Sampling window |
| --- | --- | --- |
| Oura | rMSSD | Whole sleep period |
| Garmin | rMSSD | Overnight, HRV Status capable devices only |
| Whoop | rMSSD | Slow wave sleep only |
| Apple | SDNN | Irregular, HealthKit has no rMSSD type |

rMSSD and SDNN are not interchangeable. SDNN carries both short and long term
variance; rMSSD is dominated by beat to beat parasympathetic activity, and the
nocturnal rMSSD curve is what tracks the circadian oscillator. Feeding an SDNN
value into an rMSSD model shifts the estimated acrophase.

An Apple only patient still needs a usable rhythm shape, so rMSSD is derived
from SDNN at a population ratio of 0.80 and stored at confidence 0.55. The raw
SDNN is stored separately at confidence 1.0. A measured rMSSD is never
overwritten by a derived one.

Whoop samples inside slow wave sleep, which sits at the parasympathetic peak,
so its readings run systematically higher than a whole night average from the
same wrist on the same night. They are scaled toward a whole night equivalent
at 0.90 and stored at confidence 0.85.

Garmin devices without HRV Status send no HRV block at all. That is recorded
as a warning rather than treated as a zero.

### Sleep staging

Terra encodes the hypnogram as integer levels, and the mapping is consistent
across providers. What varies is which levels a device can emit.

Oura stages at five minute resolution with deep, light, REM, and awake. Garmin
does the same but reports a substantial unmeasurable bucket on wrist movement.
Whoop calls deep sleep slow wave sleep; Terra folds that into the same level.
Apple only stages from watchOS 9 on Series 8 and later, and earlier hardware
reports time in bed with no stage detail.

An unstaged night is marked rather than folded into light sleep. Treating
unstaged time as light would inflate the light fraction and drag the computed
deep and REM fractions toward zero. When more than half a night is unstaged the
session carries a warning and the stage fractions are not used.

Consecutive identical levels are merged into intervals, so a night becomes a
handful of segments rather than a few hundred rows. That is also the form the
clock dial renders directly.

### Temperature

Oura, Garmin, and Apple report a deviation from the wearer's own baseline.
Whoop reports an absolute skin temperature in Celsius. Subtracting one from the
other is meaningless without a per patient baseline.

Whoop values are converted against a population baseline of 33.5 C and stored
at confidence 0.45, which is low enough that they cannot dominate a fit on
their own. A per patient baseline needs fourteen nights of history, and the
conversion is replaced once that exists.

### Timestamps

Whoop sends RFC 3339 with a Z suffix. Oura sends RFC 3339 with a numeric
offset encoding the patient's local zone. Garmin sends epoch seconds, with
some firmware revisions sending milliseconds. Apple sends either.

Everything is converted to aware UTC. The patient's local zone is recovered
separately from the patient record rather than from the device offset, because
a device offset can be stale after travel while the clinician maintained
timezone is authoritative.

### Duplicate step counts

An iPhone and a paired Watch both log steps for the same walk, and Terra
forwards both. Counting them twice doubles the apparent activity amplitude and
pulls the activity acrophase toward whichever device reported more often.
Samples are deduplicated by metric and minute, with the highest confidence
winning a tie.

## Backfill

Terra pushes forward from the moment a device is connected. A new patient needs
fourteen days of history before a phase estimate means anything, so `/ingest/backfill`
requests sleep, daily, and activity concurrently and asks Terra to deliver them
through the same webhook. There is one ingestion code path rather than two.

## Connecting a device

`POST /ingest/connect` creates a Terra hosted session and returns a widget URL.
The patient's Croniixx uuid is passed as `reference_id`, and Terra echoes it on
every subsequent webhook. That is how a payload finds its patient without us
storing a mapping before the device is connected.

Terra can deliver data before the auth webhook lands. An unlinked payload is
acknowledged with a warning rather than rejected, and the backfill after auth
recovers it.

## Configuration

```
TERRA_DEV_ID=
TERRA_API_KEY=
TERRA_SIGNING_SECRET=
TERRA_BASE_URL=https://api.tryterra.co/v2
```

Without these the Sync service starts and serves reads. The connect and
backfill endpoints return 503 with `terra_not_configured` rather than failing
in a way that needs a log to diagnose.

Reference: https://docs.tryterra.co

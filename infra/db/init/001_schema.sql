-- Croniixx base schema.
-- Runs once on first container start of the timescaledb image.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ---------------------------------------------------------------------------
-- Patients and device links
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS patients (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    fhir_patient_id TEXT UNIQUE,
    display_label   TEXT NOT NULL,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Terra issues one user id per (patient, device) pair, so a patient wearing an
-- Oura ring and an Apple Watch has two rows here, not one.
CREATE TABLE IF NOT EXISTS wearable_links (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id      UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    terra_user_id   TEXT NOT NULL UNIQUE,
    provider        TEXT NOT NULL,
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_payload_at TIMESTAMPTZ,
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS wearable_links_patient_idx ON wearable_links (patient_id);

-- ---------------------------------------------------------------------------
-- Continuous wearable metrics (hypertables)
-- ---------------------------------------------------------------------------

-- One row per normalized sample. source_provider is kept so that a later
-- recalculation can weight an Oura HRV reading differently from a Whoop one.
CREATE TABLE IF NOT EXISTS biometric_samples (
    time            TIMESTAMPTZ NOT NULL,
    patient_id      UUID NOT NULL,
    metric          TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    unit            TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    terra_user_id   TEXT,
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0
);

SELECT create_hypertable(
    'biometric_samples', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS biometric_samples_patient_metric_idx
    ON biometric_samples (patient_id, metric, time DESC);

-- Sleep is stored as intervals rather than point samples because phase
-- estimation cares about segment boundaries, not instantaneous values.
CREATE TABLE IF NOT EXISTS sleep_segments (
    time            TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ NOT NULL,
    patient_id      UUID NOT NULL,
    stage           TEXT NOT NULL,
    duration_s      INTEGER NOT NULL,
    source_provider TEXT NOT NULL,
    terra_user_id   TEXT
);

SELECT create_hypertable(
    'sleep_segments', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS sleep_segments_patient_idx
    ON sleep_segments (patient_id, time DESC);

-- Hourly rollup so the clock dial can render a week of HRV without scanning
-- every raw sample.
CREATE MATERIALIZED VIEW IF NOT EXISTS biometric_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 hour', time) AS bucket,
    patient_id,
    metric,
    avg(value)   AS avg_value,
    min(value)   AS min_value,
    max(value)   AS max_value,
    count(*)     AS sample_count
FROM biometric_samples
GROUP BY bucket, patient_id, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'biometric_hourly',
    start_offset => INTERVAL '30 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);

-- ---------------------------------------------------------------------------
-- Circadian profiles and phase history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS circadian_profiles (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id       UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    payload          JSONB NOT NULL,
    data_completeness DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS circadian_profiles_patient_idx
    ON circadian_profiles (patient_id, computed_at DESC);

CREATE TABLE IF NOT EXISTS phase_estimates (
    time             TIMESTAMPTZ NOT NULL,
    patient_id       UUID NOT NULL,
    phase_offset_min INTEGER NOT NULL,
    dlmo_estimate    TIMESTAMPTZ,
    amplitude        DOUBLE PRECISION,
    stability        DOUBLE PRECISION,
    confidence       DOUBLE PRECISION NOT NULL,
    method_version   TEXT NOT NULL
);

SELECT create_hypertable(
    'phase_estimates', 'time',
    chunk_time_interval => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ---------------------------------------------------------------------------
-- Regimens and adaptive schedules
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS medications (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id     UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    rxnorm_code    TEXT,
    display_name   TEXT NOT NULL,
    drug_class     TEXT NOT NULL,
    dose_amount    DOUBLE PRECISION NOT NULL,
    dose_unit      TEXT NOT NULL,
    doses_per_day  SMALLINT NOT NULL DEFAULT 1,
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS medications_patient_idx ON medications (patient_id, active);

-- The schedule is stored whole rather than per dose because it is generated
-- and superseded as a unit. Partial rewrites would let two phase estimates mix.
CREATE TABLE IF NOT EXISTS adaptive_schedules (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id        UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_from        TIMESTAMPTZ NOT NULL,
    valid_until       TIMESTAMPTZ NOT NULL,
    phase_offset_min  INTEGER NOT NULL,
    schedule_version  INTEGER NOT NULL DEFAULT 1,
    superseded_by     UUID REFERENCES adaptive_schedules(id),
    payload           JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS adaptive_schedules_patient_idx
    ON adaptive_schedules (patient_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS dose_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    schedule_id   UUID NOT NULL REFERENCES adaptive_schedules(id) ON DELETE CASCADE,
    patient_id    UUID NOT NULL,
    medication_id UUID NOT NULL,
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,
    target_time   TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    taken_at      TIMESTAMPTZ,
    recorded_by   TEXT
);

CREATE INDEX IF NOT EXISTS dose_events_patient_window_idx
    ON dose_events (patient_id, window_start);

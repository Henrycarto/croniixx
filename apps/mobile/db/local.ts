import * as SQLite from 'expo-sqlite'
import type { AdaptiveSchedule, ScheduleEntry } from '@croniixx/shared-types'

/**
 * Local SQLite store. This is the app's source of truth, not a cache.
 *
 * A patient on chemotherapy does not reliably have signal at 04:00, and the
 * dose is due at 04:00 regardless. So the schedule is written to disk the
 * moment it arrives, notifications are scheduled locally from that copy, and
 * every acknowledgement goes into an outbox that drains when the network comes
 * back. Nothing the patient needs to do requires a request to succeed.
 *
 * The server is authoritative for what the schedule should be. The device is
 * authoritative for what actually happened.
 */

const DATABASE_NAME = 'croniixx.db'

let handle: SQLite.SQLiteDatabase | null = null

export async function openDatabase(): Promise<SQLite.SQLiteDatabase> {
  if (handle) return handle
  handle = await SQLite.openDatabaseAsync(DATABASE_NAME)
  await migrate(handle)
  return handle
}

async function migrate(db: SQLite.SQLiteDatabase): Promise<void> {
  // WAL because the notification response handler writes to the outbox while
  // the UI is reading the schedule, and the default journal serialises those
  // into visible stalls.
  await db.execAsync('PRAGMA journal_mode = WAL;')
  await db.execAsync('PRAGMA foreign_keys = ON;')

  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS schedules (
      schedule_id       TEXT PRIMARY KEY,
      patient_id        TEXT NOT NULL,
      generated_at      TEXT NOT NULL,
      valid_from        TEXT NOT NULL,
      valid_until       TEXT NOT NULL,
      schedule_version  INTEGER NOT NULL,
      timezone          TEXT NOT NULL,
      phase_offset_min  INTEGER NOT NULL,
      provisional       INTEGER NOT NULL DEFAULT 0,
      coefficient_source TEXT NOT NULL,
      payload           TEXT NOT NULL,
      is_current        INTEGER NOT NULL DEFAULT 0,
      received_at       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS doses (
      entry_id          TEXT PRIMARY KEY,
      schedule_id       TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE CASCADE,
      medication_id     TEXT NOT NULL,
      display_name      TEXT NOT NULL,
      dose_amount       REAL NOT NULL,
      dose_unit         TEXT NOT NULL,
      drug_class        TEXT NOT NULL,
      window_start      TEXT NOT NULL,
      window_end        TEXT NOT NULL,
      target_time       TEXT NOT NULL,
      window_status     TEXT NOT NULL,
      rationale         TEXT,
      status            TEXT NOT NULL DEFAULT 'pending',
      taken_at          TEXT,
      notification_ids  TEXT
    );

    CREATE INDEX IF NOT EXISTS doses_target_idx ON doses (target_time);
    CREATE INDEX IF NOT EXISTS doses_status_idx ON doses (status);

    -- Outbox. Every acknowledgement lands here first and is replayed in order
    -- once the network returns. Attempt counts are kept so a permanently
    -- failing item can be surfaced rather than retried forever in silence.
    CREATE TABLE IF NOT EXISTS outbox (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      kind        TEXT NOT NULL,
      payload     TEXT NOT NULL,
      created_at  TEXT NOT NULL,
      attempts    INTEGER NOT NULL DEFAULT 0,
      last_error  TEXT
    );

    CREATE TABLE IF NOT EXISTS meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
  `)
}

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

export async function saveSchedule(schedule: AdaptiveSchedule): Promise<void> {
  const db = await openDatabase()

  await db.withTransactionAsync(async () => {
    // Exactly one schedule is current at a time. Two would mean two answers to
    // "when is my next dose", and the app would pick whichever query ran first.
    await db.runAsync('UPDATE schedules SET is_current = 0 WHERE patient_id = ?', [
      schedule.patient_id,
    ])

    await db.runAsync(
      `INSERT OR REPLACE INTO schedules
        (schedule_id, patient_id, generated_at, valid_from, valid_until, schedule_version,
         timezone, phase_offset_min, provisional, coefficient_source, payload, is_current, received_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)`,
      [
        schedule.schedule_id,
        schedule.patient_id,
        schedule.generated_at,
        schedule.valid_from,
        schedule.valid_until,
        schedule.schedule_version,
        schedule.timezone,
        schedule.phase.phase_offset_min,
        schedule.meta.provisional ? 1 : 0,
        schedule.meta.coefficient_source,
        JSON.stringify(schedule),
        new Date().toISOString(),
      ],
    )

    for (const entry of schedule.entries) {
      // A dose the patient already recorded keeps its status through a
      // schedule refresh. Resetting it to pending would ask them to take a
      // dose they have taken.
      await db.runAsync(
        `INSERT INTO doses
          (entry_id, schedule_id, medication_id, display_name, dose_amount, dose_unit,
           drug_class, window_start, window_end, target_time, window_status, rationale, status)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
         ON CONFLICT(entry_id) DO UPDATE SET
           window_start  = excluded.window_start,
           window_end    = excluded.window_end,
           target_time   = excluded.target_time,
           window_status = excluded.window_status,
           rationale     = excluded.rationale`,
        [
          entry.entry_id,
          schedule.schedule_id,
          entry.medication_id,
          entry.display_name,
          entry.dose_amount,
          entry.dose_unit,
          entry.drug_class,
          entry.window.start,
          entry.window.end,
          entry.window.target,
          entry.window.status,
          entry.window.rationale,
        ],
      )
    }
  })
}

export async function currentSchedule(): Promise<AdaptiveSchedule | null> {
  const db = await openDatabase()
  const row = await db.getFirstAsync<{ payload: string }>(
    'SELECT payload FROM schedules WHERE is_current = 1 ORDER BY generated_at DESC LIMIT 1',
  )
  if (!row) return null
  try {
    return JSON.parse(row.payload) as AdaptiveSchedule
  } catch {
    return null
  }
}

export interface LocalDose {
  entry_id: string
  schedule_id: string
  medication_id: string
  display_name: string
  dose_amount: number
  dose_unit: string
  drug_class: string
  window_start: string
  window_end: string
  target_time: string
  window_status: string
  rationale: string | null
  status: string
  taken_at: string | null
  notification_ids: string | null
}

/** Doses for the current schedule, in the order they come due. */
export async function upcomingDoses(): Promise<LocalDose[]> {
  const db = await openDatabase()
  return db.getAllAsync<LocalDose>(
    `SELECT d.* FROM doses d
     JOIN schedules s ON s.schedule_id = d.schedule_id
     WHERE s.is_current = 1
     ORDER BY d.target_time ASC`,
  )
}

export async function doseById(entryId: string): Promise<LocalDose | null> {
  const db = await openDatabase()
  return (await db.getFirstAsync<LocalDose>('SELECT * FROM doses WHERE entry_id = ?', [entryId])) ?? null
}

/**
 * Record a dose locally and queue the acknowledgement.
 *
 * Both writes happen in one transaction. A local status without an outbox row
 * means the server never learns the dose was taken, and an outbox row without
 * a local status means the patient is asked again for a dose they took.
 */
export async function recordDose(
  entryId: string,
  status: 'taken' | 'skipped',
  takenAt: Date = new Date(),
): Promise<void> {
  const db = await openDatabase()

  await db.withTransactionAsync(async () => {
    await db.runAsync('UPDATE doses SET status = ?, taken_at = ? WHERE entry_id = ?', [
      status,
      takenAt.toISOString(),
      entryId,
    ])

    await db.runAsync(
      'INSERT INTO outbox (kind, payload, created_at) VALUES (?, ?, ?)',
      [
        'dose_ack',
        JSON.stringify({ entry_id: entryId, status, taken_at: takenAt.toISOString() }),
        new Date().toISOString(),
      ],
    )
  })
}

export async function setNotificationIds(entryId: string, ids: string[]): Promise<void> {
  const db = await openDatabase()
  await db.runAsync('UPDATE doses SET notification_ids = ? WHERE entry_id = ?', [
    JSON.stringify(ids),
    entryId,
  ])
}

// ---------------------------------------------------------------------------
// Outbox
// ---------------------------------------------------------------------------

export interface OutboxItem {
  id: number
  kind: string
  payload: string
  created_at: string
  attempts: number
  last_error: string | null
}

export async function pendingOutbox(limit = 50): Promise<OutboxItem[]> {
  const db = await openDatabase()
  return db.getAllAsync<OutboxItem>(
    'SELECT * FROM outbox ORDER BY created_at ASC LIMIT ?',
    [limit],
  )
}

export async function clearOutboxItem(id: number): Promise<void> {
  const db = await openDatabase()
  await db.runAsync('DELETE FROM outbox WHERE id = ?', [id])
}

export async function markOutboxFailure(id: number, error: string): Promise<void> {
  const db = await openDatabase()
  await db.runAsync('UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?', [
    error,
    id,
  ])
}

export async function outboxDepth(): Promise<number> {
  const db = await openDatabase()
  const row = await db.getFirstAsync<{ count: number }>('SELECT COUNT(*) AS count FROM outbox')
  return row?.count ?? 0
}

// ---------------------------------------------------------------------------
// Meta
// ---------------------------------------------------------------------------

export async function setMeta(key: string, value: string): Promise<void> {
  const db = await openDatabase()
  await db.runAsync('INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)', [key, value])
}

export async function getMeta(key: string): Promise<string | null> {
  const db = await openDatabase()
  const row = await db.getFirstAsync<{ value: string }>('SELECT value FROM meta WHERE key = ?', [
    key,
  ])
  return row?.value ?? null
}

export const META_LAST_SYNC = 'last_sync_at'
export const META_PATIENT_ID = 'patient_id'

/** Doses that entered the app from a schedule entry, for tests and tooling. */
export function toLocalDose(entry: ScheduleEntry, scheduleId: string): LocalDose {
  return {
    entry_id: entry.entry_id,
    schedule_id: scheduleId,
    medication_id: entry.medication_id,
    display_name: entry.display_name,
    dose_amount: entry.dose_amount,
    dose_unit: entry.dose_unit,
    drug_class: entry.drug_class,
    window_start: entry.window.start,
    window_end: entry.window.end,
    target_time: entry.window.target,
    window_status: entry.window.status,
    rationale: entry.window.rationale,
    status: entry.status,
    taken_at: null,
    notification_ids: null,
  }
}

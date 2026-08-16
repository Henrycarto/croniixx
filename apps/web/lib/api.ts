import type {
  AdaptiveSchedule,
  ApiEnvelope,
  CircadianProfile,
  IngestionEvent,
  PhaseDrift,
  PhaseEstimate,
  PhaseHistoryPoint,
  WearableLinkStatus,
} from '@croniixx/shared-types'

/**
 * Typed client for the three Croniixx services.
 *
 * Every response is an envelope, so failures come back as data rather than as
 * thrown exceptions. A dashboard that renders a patient's schedule needs to be
 * able to show "the engine is unreachable" next to the data it does have,
 * which a throwing client makes awkward.
 */

const SYNC_URL = process.env.NEXT_PUBLIC_SYNC_URL ?? 'http://localhost:8001'
const ENGINE_URL = process.env.NEXT_PUBLIC_ENGINE_URL ?? 'http://localhost:8002'
const REMINDER_URL = process.env.NEXT_PUBLIC_REMINDER_URL ?? 'http://localhost:8003'

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE'
  body?: unknown
  /** Seconds to cache. Zero disables caching, which is the default for
   *  anything phase related: a stale phase estimate is a wrong one. */
  revalidate?: number
  signal?: AbortSignal
}

export async function call<T>(url: string, options: RequestOptions = {}): Promise<ApiEnvelope<T>> {
  const { method = 'GET', body, revalidate = 0, signal } = options

  try {
    const response = await fetch(url, {
      method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal,
      next: revalidate > 0 ? { revalidate } : { revalidate: 0 },
    })

    const payload = (await response.json()) as ApiEnvelope<T>
    return payload
  } catch (error) {
    return {
      data: null,
      error: {
        code: 'network_error',
        message: error instanceof Error ? error.message : 'Request failed',
      },
      meta: {
        service: 'web',
        generated_at: new Date().toISOString(),
      },
    }
  }
}

// ---------------------------------------------------------------------------
// Sync
// ---------------------------------------------------------------------------

export function getCircadianProfile(patientId: string, days = 14) {
  return call<CircadianProfile>(`${SYNC_URL}/ingest/profile/${patientId}?days=${days}`)
}

export function getWearableStatus(patientId: string) {
  return call<WearableLinkStatus[]>(`${SYNC_URL}/ingest/status/${patientId}`)
}

export function getIngestionFeed(patientId: string, limit = 50) {
  return call<IngestionEvent[]>(`${SYNC_URL}/ingest/feed/${patientId}?limit=${limit}`)
}

export function connectDevice(patientId: string, providers?: string[]) {
  return call<{ widget_url: string; session_id: string }>(`${SYNC_URL}/ingest/connect`, {
    method: 'POST',
    body: { patient_id: patientId, providers },
  })
}

export function requestBackfill(terraUserId: string, days = 14) {
  return call<{ requested: string[]; failed: string[] }>(`${SYNC_URL}/ingest/backfill`, {
    method: 'POST',
    body: { terra_user_id: terraUserId, days },
  })
}

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

export function getPhase(patientId: string, days = 14) {
  return call<PhaseEstimate>(`${ENGINE_URL}/circadian/phase/${patientId}?days=${days}`)
}

export function getPhaseDrift(patientId: string, baselineDays = 30) {
  return call<PhaseDrift>(`${ENGINE_URL}/circadian/drift/${patientId}?baseline_days=${baselineDays}`)
}

export function getPhaseHistory(patientId: string, days = 30) {
  return call<PhaseHistoryPoint[]>(`${ENGINE_URL}/circadian/history/${patientId}?days=${days}`)
}

export function getMethodInfo() {
  return call<{
    method_version: string
    coefficient_source: string
    clinically_validated: boolean
  }>(`${ENGINE_URL}/circadian/method`)
}

export function getCurrentSchedule(patientId: string) {
  return call<AdaptiveSchedule>(`${ENGINE_URL}/schedule/${patientId}/current`)
}

export function generateSchedule(patientId: string, horizonHours?: number) {
  return call<AdaptiveSchedule>(`${ENGINE_URL}/schedule/generate/${patientId}`, {
    method: 'POST',
    body: { horizon_hours: horizonHours, push_to_queue: true },
  })
}

// ---------------------------------------------------------------------------
// Reminders
// ---------------------------------------------------------------------------

export function getQueueStats() {
  return call<{
    queued: number
    claimed: number
    due_now: number
    dispatcher_running: boolean
  }>(`${REMINDER_URL}/remind/queue/stats`)
}

export const endpoints = { SYNC_URL, ENGINE_URL, REMINDER_URL }

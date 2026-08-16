export type Provider = 'OURA' | 'APPLE' | 'GARMIN' | 'WHOOP'

export type SleepStage = 'awake' | 'light' | 'deep' | 'rem' | 'unmeasurable'

export interface WearableLinkStatus {
  terra_user_id: string
  provider: Provider
  connected_at: string
  last_payload_at: string | null
  active: boolean
  scopes: string[]
  /** Seconds since the last payload. Null means nothing has arrived yet. */
  staleness_s: number | null
}

export interface IngestionEvent {
  at: string
  provider: Provider
  payload_type: string
  samples: number
  sleep_sessions: number
  warnings: string[]
}

export const PROVIDER_LABELS: Record<Provider, string> = {
  OURA: 'Oura Ring',
  APPLE: 'Apple Watch',
  GARMIN: 'Garmin',
  WHOOP: 'Whoop',
}

/**
 * What each device actually measures rather than what it reports.
 * The dashboard shows this so a clinician knows which numbers on the screen
 * were measured and which were derived.
 */
export const PROVIDER_HRV_INDEX: Record<Provider, 'rMSSD' | 'SDNN'> = {
  OURA: 'rMSSD',
  APPLE: 'SDNN',
  GARMIN: 'rMSSD',
  WHOOP: 'rMSSD',
}

/** A link quiet for longer than this is shown as drifting on the dashboard. */
export const STALE_AFTER_SECONDS = 60 * 60 * 30

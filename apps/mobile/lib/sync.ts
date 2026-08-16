import Constants from 'expo-constants'
import * as Network from 'expo-network'
import * as SecureStore from 'expo-secure-store'
import type { AdaptiveSchedule, ApiEnvelope } from '@croniixx/shared-types'

import {
  META_LAST_SYNC,
  META_PATIENT_ID,
  clearOutboxItem,
  getMeta,
  markOutboxFailure,
  pendingOutbox,
  saveSchedule,
  setMeta,
} from '@/db/local'
import { rescheduleLocalNotifications, registerForPush } from './notifications'

/**
 * Synchronisation with the Croniixx services.
 *
 * Every function here assumes the network is absent and treats its presence as
 * the exception. Nothing in the patient flow awaits a request, and a failed
 * sync leaves the device exactly as capable as it was before.
 */

const REMINDER_URL =
  (Constants.expoConfig?.extra?.reminderApiUrl as string | undefined) ?? 'http://localhost:8003'
const ENGINE_URL =
  (Constants.expoConfig?.extra?.engineApiUrl as string | undefined) ?? 'http://localhost:8002'

const TOKEN_KEY = 'croniixx_access_token'

// Requests are given a short deadline. On a weak connection an unbounded fetch
// holds the foreground sync open for minutes while the user waits on a spinner
// for data they already have on disk.
const REQUEST_TIMEOUT_MS = 8000

export async function storeAccessToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token)
}

export async function accessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY)
}

export async function isOnline(): Promise<boolean> {
  try {
    const state = await Network.getNetworkStateAsync()
    return Boolean(state.isConnected && state.isInternetReachable !== false)
  } catch {
    return false
  }
}

async function request<T>(url: string, init: RequestInit = {}): Promise<ApiEnvelope<T> | null> {
  const token = await accessToken()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    })
    return (await response.json()) as ApiEnvelope<T>
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}

export interface SyncResult {
  online: boolean
  scheduleUpdated: boolean
  notificationsScheduled: number
  outboxDrained: number
  outboxRemaining: number
}

/**
 * One full sync pass.
 *
 * Outbox first. The server's view of adherence should include what the patient
 * did offline before it hands back a new schedule, since a dose recorded at
 * 04:00 can change what the next schedule looks like.
 */
export async function sync(): Promise<SyncResult> {
  const online = await isOnline()
  if (!online) {
    const remaining = (await pendingOutbox(1000)).length
    return {
      online: false,
      scheduleUpdated: false,
      notificationsScheduled: 0,
      outboxDrained: 0,
      outboxRemaining: remaining,
    }
  }

  const drained = await drainOutbox()
  const patientId = await getMeta(META_PATIENT_ID)

  let scheduleUpdated = false
  let notificationsScheduled = 0

  if (patientId) {
    const response = await request<AdaptiveSchedule>(
      `${ENGINE_URL}/schedule/${patientId}/current`,
    )
    if (response?.data) {
      await saveSchedule(response.data)
      notificationsScheduled = await rescheduleLocalNotifications()
      scheduleUpdated = true
    }
    await setMeta(META_LAST_SYNC, new Date().toISOString())
  }

  return {
    online: true,
    scheduleUpdated,
    notificationsScheduled,
    outboxDrained: drained,
    outboxRemaining: (await pendingOutbox(1000)).length,
  }
}

/**
 * Replay queued acknowledgements in order.
 *
 * Stops at the first failure rather than skipping past it. Doses for one
 * medication arriving out of order at the server would give a misleading
 * adherence record, and the next attempt will start from the same place.
 */
export async function drainOutbox(): Promise<number> {
  const items = await pendingOutbox()
  let drained = 0

  for (const item of items) {
    if (item.kind !== 'dose_ack') {
      await clearOutboxItem(item.id)
      continue
    }

    const body = JSON.parse(item.payload) as {
      entry_id: string
      status: string
      taken_at: string
    }

    const response = await request<unknown>(`${ENGINE_URL}/schedule/dose/${body.entry_id}`, {
      method: 'POST',
      body: JSON.stringify({ status: body.status, taken_at: body.taken_at }),
    })

    if (response === null) {
      await markOutboxFailure(item.id, 'network unavailable')
      break
    }

    // A dose the server does not recognise is never going to be accepted, so
    // it is dropped rather than left blocking everything queued behind it.
    if (response.error && response.error.code !== 'unknown_dose') {
      await markOutboxFailure(item.id, response.error.message)
      break
    }

    await clearOutboxItem(item.id)
    drained += 1
  }

  return drained
}

/** Register this device with the reminder service so push can reach it. */
export async function registerDevice(patientId: string): Promise<boolean> {
  const token = await registerForPush()
  if (!token) return false

  const response = await request<unknown>(`${REMINDER_URL}/remind/devices`, {
    method: 'POST',
    body: JSON.stringify({
      patient_id: patientId,
      expo_push_token: token,
      platform: 'ios',
    }),
  })

  return response?.error === null
}

export async function lastSyncAt(): Promise<Date | null> {
  const value = await getMeta(META_LAST_SYNC)
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

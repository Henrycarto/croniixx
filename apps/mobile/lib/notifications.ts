import * as Device from 'expo-device'
import * as Notifications from 'expo-notifications'
import { Platform } from 'react-native'

import { setNotificationIds, upcomingDoses, type LocalDose } from '@/db/local'
import { colors } from './theme'

/**
 * Local notification scheduling.
 *
 * Push from the reminder service is the primary path, but it needs a network
 * at the moment the dose is due. Local notifications are scheduled from the
 * device's own copy of the schedule so a patient in a hospital basement or on
 * a flight still gets told. The two paths use the same collapse identifier, so
 * when both arrive the tray shows one alert rather than two.
 */

export const DOSE_CHANNEL = 'croniixx-doses'

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
})

export async function configureNotifications(): Promise<boolean> {
  if (!Device.isDevice) return false

  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync(DOSE_CHANNEL, {
      name: 'Medication doses',
      // MAX rather than DEFAULT. A dose window is minutes wide for some
      // agents, and a notification held back by the system until the next
      // batch is a missed dose.
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: colors.circadian,
      bypassDnd: false,
    })
  }

  const existing = await Notifications.getPermissionsAsync()
  if (existing.granted) return true

  const requested = await Notifications.requestPermissionsAsync({
    ios: { allowAlert: true, allowSound: true, allowBadge: true, allowCriticalAlerts: false },
  })
  return requested.granted
}

export async function registerForPush(): Promise<string | null> {
  if (!Device.isDevice) return null
  const granted = await configureNotifications()
  if (!granted) return null

  try {
    const token = await Notifications.getExpoPushTokenAsync()
    return token.data
  } catch {
    // A missing project id or an offline first launch both land here. The app
    // still works on local notifications alone, so this is not fatal.
    return null
  }
}

/**
 * Rebuild every local notification from the stored schedule.
 *
 * Cancel and reschedule rather than diff. A schedule supersedes rather than
 * patches, so the previous set of notifications is wrong in its entirety and
 * a partial update would leave a stale one behind.
 */
export async function rescheduleLocalNotifications(): Promise<number> {
  await Notifications.cancelAllScheduledNotificationsAsync()

  const doses = await upcomingDoses()
  const now = Date.now()
  let scheduled = 0

  for (const dose of doses) {
    if (dose.status !== 'pending') continue

    const ids: string[] = []
    for (const moment of reminderMoments(dose)) {
      if (moment.at.getTime() <= now) continue

      const id = await Notifications.scheduleNotificationAsync({
        content: {
          title: moment.title,
          body: moment.body,
          sound: 'default',
          data: {
            entry_id: dose.entry_id,
            schedule_id: dose.schedule_id,
            kind: moment.kind,
          },
          ...(Platform.OS === 'android' ? { channelId: DOSE_CHANNEL } : {}),
        },
        trigger: { date: moment.at, channelId: DOSE_CHANNEL },
      })
      ids.push(id)
      scheduled += 1
    }

    await setNotificationIds(dose.entry_id, ids)
  }

  return scheduled
}

export async function cancelForDose(dose: LocalDose): Promise<void> {
  if (!dose.notification_ids) return
  try {
    const ids = JSON.parse(dose.notification_ids) as string[]
    await Promise.all(ids.map((id) => Notifications.cancelScheduledNotificationAsync(id)))
  } catch {
    // A malformed id list is not worth failing a dose acknowledgement over.
  }
}

interface ReminderMoment {
  kind: 'window_open' | 'target' | 'window_closing'
  at: Date
  title: string
  body: string
}

/** The same three moments the server queue uses, computed on device. */
export function reminderMoments(dose: LocalDose): ReminderMoment[] {
  const dosage = `${trim(dose.dose_amount)} ${dose.dose_unit}`
  const start = new Date(dose.window_start)
  const target = new Date(dose.target_time)
  const end = new Date(dose.window_end)
  const closing = new Date(end.getTime() - 20 * 60 * 1000)

  const moments: ReminderMoment[] = [
    {
      kind: 'window_open',
      at: start,
      title: `${dose.display_name} window is open`,
      body: `${dosage}. Best moment is coming up.`,
    },
    {
      kind: 'target',
      at: target,
      title: `Take ${dose.display_name}`,
      body: `${dosage}. This is the calculated moment for your circadian phase.`,
    },
    {
      kind: 'window_closing',
      at: closing,
      title: `${dose.display_name} window closing`,
      body: `${dosage}. The optimal window closes shortly.`,
    },
  ]

  // Collapse anything within twenty minutes of an earlier reminder. Three
  // alerts inside half an hour reads as a malfunction and gets the app muted.
  const kept: ReminderMoment[] = []
  for (const moment of moments) {
    const tooClose = kept.some(
      (existing) => Math.abs(existing.at.getTime() - moment.at.getTime()) < 20 * 60 * 1000,
    )
    if (!tooClose) kept.push(moment)
  }
  return kept
}

function trim(value: number): string {
  return Number.isInteger(value) ? String(value) : String(value)
}

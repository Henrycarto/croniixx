import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'
import * as Notifications from 'expo-notifications'
import { useRouter } from 'expo-router'

import { openDatabase } from '@/db/local'
import { configureNotifications } from '@/lib/notifications'
import { drainOutbox, sync } from '@/lib/sync'
import { colors } from '@/lib/theme'

export default function RootLayout() {
  const router = useRouter()

  useEffect(() => {
    // The database opens before anything renders. Every screen reads from it,
    // and a screen that renders first would show an empty schedule for a frame
    // to a patient who has doses due.
    void openDatabase().then(() => {
      void configureNotifications()
      void sync()
    })
  }, [])

  useEffect(() => {
    // Tapping a dose notification opens that dose rather than the app's home
    // screen. A patient woken at 04:00 should not have to navigate.
    const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const entryId = response.notification.request.content.data?.entry_id
      if (typeof entryId === 'string') {
        router.push(`/reminder?entry=${encodeURIComponent(entryId)}`)
      }
    })
    return () => subscription.remove()
  }, [router])

  useEffect(() => {
    // The outbox is retried on a timer as well as on foreground, because a
    // patient can regain signal without touching the phone.
    const timer = setInterval(() => {
      void drainOutbox()
    }, 120_000)
    return () => clearInterval(timer)
  }, [])

  return (
    <>
      <StatusBar style="light" backgroundColor={colors.base} />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: colors.base },
          headerTintColor: colors.ink,
          // React Navigation only accepts font family, size, and weight here.
          headerTitleStyle: { fontFamily: 'SpaceMono', fontSize: 14 },
          headerShadowVisible: false,
          contentStyle: { backgroundColor: colors.base },
        }}
      >
        <Stack.Screen name="index" options={{ title: 'CRONIIXX' }} />
        <Stack.Screen name="schedule" options={{ title: 'SCHEDULE' }} />
        <Stack.Screen name="reminder" options={{ title: 'DOSE', presentation: 'modal' }} />
      </Stack>
    </>
  )
}

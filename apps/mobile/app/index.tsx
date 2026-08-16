import { useCallback, useEffect, useState } from 'react'
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native'
import { useFocusEffect, useRouter } from 'expo-router'
import type { AdaptiveSchedule } from '@croniixx/shared-types'

import { MedicationCard } from '@/components/MedicationCard'
import { OfflineBadge } from '@/components/OfflineBadge'
import { currentSchedule, outboxDepth, upcomingDoses, type LocalDose } from '@/db/local'
import { isOnline, lastSyncAt, sync } from '@/lib/sync'
import { colors, type } from '@/lib/theme'

/**
 * Home screen: the next dose, then the rest of the day.
 *
 * Everything on this screen comes from local SQLite. The sync call updates it
 * when a network exists, and the screen never waits on that call to render.
 */

export default function HomeScreen() {
  const router = useRouter()
  const [schedule, setSchedule] = useState<AdaptiveSchedule | null>(null)
  const [doses, setDoses] = useState<LocalDose[]>([])
  const [online, setOnline] = useState(false)
  const [queued, setQueued] = useState(0)
  const [syncedAt, setSyncedAt] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    const [loadedSchedule, loadedDoses, depth, connected, synced] = await Promise.all([
      currentSchedule(),
      upcomingDoses(),
      outboxDepth(),
      isOnline(),
      lastSyncAt(),
    ])
    setSchedule(loadedSchedule)
    setDoses(loadedDoses)
    setQueued(depth)
    setOnline(connected)
    setSyncedAt(synced)
  }, [])

  useFocusEffect(
    useCallback(() => {
      void load()
    }, [load]),
  )

  useEffect(() => {
    // A dose window opens and closes while the screen is open, so the state
    // labels have to move on their own.
    const timer = setInterval(() => void load(), 60_000)
    return () => clearInterval(timer)
  }, [load])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await sync()
    await load()
    setRefreshing(false)
  }, [load])

  const now = new Date()
  const pending = doses.filter((dose) => dose.status === 'pending')
  const next = pending.find((dose) => new Date(dose.window_end) >= now) ?? null
  const rest = pending.filter((dose) => dose.entry_id !== next?.entry_id)
  const done = doses.filter((dose) => dose.status !== 'pending')

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.circadian} />
      }
    >
      <OfflineBadge online={online} queuedItems={queued} lastSyncAt={syncedAt} />

      {schedule?.meta.provisional && (
        <Text style={styles.provisional}>
          This schedule is provisional. Timing is calculated but not yet clinically validated.
        </Text>
      )}

      <View style={styles.phaseBlock}>
        <Text style={styles.label}>YOUR CIRCADIAN PHASE</Text>
        <Text style={styles.phaseValue}>
          {schedule ? schedule.phase.offset_display : '--:--'}
        </Text>
        <Text style={styles.phaseNote}>
          {schedule
            ? schedule.phase.direction === 'delayed'
              ? 'Your body clock runs later than the calendar day.'
              : schedule.phase.direction === 'advanced'
                ? 'Your body clock runs earlier than the calendar day.'
                : 'Your body clock is close to the calendar day.'
            : 'No schedule downloaded yet.'}
        </Text>
      </View>

      {next && (
        <>
          <Text style={styles.sectionLabel}>NEXT DOSE</Text>
          <MedicationCard
            dose={next}
            now={now}
            onPress={(entryId) => router.push(`/reminder?entry=${entryId}`)}
          />
        </>
      )}

      {rest.length > 0 && (
        <>
          <Text style={styles.sectionLabel}>LATER TODAY</Text>
          {rest.map((dose) => (
            <MedicationCard
              key={dose.entry_id}
              dose={dose}
              now={now}
              onPress={(entryId) => router.push(`/reminder?entry=${entryId}`)}
            />
          ))}
        </>
      )}

      {done.length > 0 && (
        <>
          <Text style={styles.sectionLabel}>RECORDED</Text>
          {done.map((dose) => (
            <MedicationCard key={dose.entry_id} dose={dose} now={now} />
          ))}
        </>
      )}

      {doses.length === 0 && (
        <Text style={styles.empty}>
          No schedule on this device yet. Pull down to sync when you have a connection.
        </Text>
      )}

      <Pressable style={styles.link} onPress={() => router.push('/schedule')}>
        <Text style={styles.linkLabel}>FULL SCHEDULE</Text>
      </Pressable>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.base,
  },
  content: {
    padding: 16,
    paddingBottom: 48,
    gap: 4,
  },
  provisional: {
    fontFamily: type.mono,
    fontSize: 11,
    lineHeight: 17,
    color: colors.drift,
    borderWidth: 1,
    borderColor: 'rgba(245, 158, 11, 0.4)',
    backgroundColor: 'rgba(245, 158, 11, 0.06)',
    padding: 10,
    marginTop: 12,
  },
  phaseBlock: {
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    padding: 16,
    marginTop: 12,
    marginBottom: 20,
    gap: 6,
  },
  label: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.6,
    color: colors.inkFaint,
  },
  phaseValue: {
    fontFamily: type.mono,
    fontSize: 40,
    color: colors.circadian,
  },
  phaseNote: {
    fontFamily: type.sans,
    fontSize: 13,
    lineHeight: 19,
    color: colors.inkMuted,
  },
  sectionLabel: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.6,
    color: colors.inkFaint,
    marginTop: 18,
    marginBottom: 8,
  },
  empty: {
    fontFamily: type.sans,
    fontSize: 14,
    lineHeight: 21,
    color: colors.inkMuted,
    marginTop: 24,
  },
  link: {
    borderWidth: 1,
    borderColor: colors.lineStrong,
    paddingVertical: 14,
    alignItems: 'center',
    marginTop: 28,
  },
  linkLabel: {
    fontFamily: type.mono,
    fontSize: 12,
    letterSpacing: 1.6,
    color: colors.ink,
  },
})
